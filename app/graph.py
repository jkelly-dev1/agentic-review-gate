"""The LangGraph review workflow.

    plan -> retrieve -> draft -> critique -> await_approval -[interrupt]- finalize

- State is the Pydantic `ReviewState`, so every node's output is validated.
- `draft` retries when its output fails schema validation (an output guardrail).
- `critique` is the LLM-as-judge; a failing verdict loops back to `draft` up to
  MAX_ATTEMPTS (a retry/guardrail path that also cannot loop forever).
- The graph is compiled with a checkpointer and `interrupt_before=["finalize"]`,
  so it PAUSES for human-in-the-loop approval. `resume_review()` injects the
  ApprovalDecision and continues to `finalize`, which posts back and writes the
  tamper-evident audit record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from app.audit import AuditLog
from app.config import get_settings
from app.llm import LLMProvider, get_provider
from app.models import (
    ApprovalDecision,
    EvalResult,
    GitHubWebhookEvent,
    RetrievedChunk,
    ReviewDraft,
    ReviewOutput,
    ReviewState,
    ReviewStatus,
    TraceRecord,
)
from app.prompts import PromptRegistry
from app.retrieval import format_context, score_chunks
from app.tracing import get_tracer

MAX_ATTEMPTS = 2

# Register our Pydantic state models with the checkpointer serializer so they
# round-trip through msgpack without deprecation warnings.
_ALLOWED_MODELS = [
    GitHubWebhookEvent,
    RetrievedChunk,
    ReviewDraft,
    EvalResult,
    ApprovalDecision,
    ReviewOutput,
    ReviewStatus,
]


def make_checkpointer() -> MemorySaver:
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)
    return MemorySaver(serde=serde)


def _default_poster(review_id: str, comment: str) -> None:
    """Stand-in for the GitHub post-back. Real impl would call the REST API."""
    # Offline: the comment lives in the audit record and the ReviewOutput.
    return None


@dataclass
class GraphContext:
    provider: LLMProvider
    registry: PromptRegistry
    audit: AuditLog
    poster: Callable[[str, str], None]


def _parse_json(text: str) -> dict:
    """Parse a JSON object from a model response, tolerating markdown fences and
    surrounding prose.

    The mock provider returns clean JSON, but real models often wrap output in
    ```json ... ``` or add a sentence around it. This keeps the graph robust on
    the live path without changing the mock/offline behavior.
    """
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]  # drop the ``` or ```json opening line
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last resort: extract the outermost {...} object.
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start : end + 1])
        raise


def _build_nodes(ctx: GraphContext):
    tracer = get_tracer()

    def plan_node(state: ReviewState) -> dict:
        with tracer.span("plan", review_id=state.review_id):
            prompt = ctx.registry.get("plan")
            out = ctx.provider.complete(
                prompt_name="plan", rendered_prompt=prompt.body, context=state.event.content
            )
            steps = _parse_json(out).get("steps", [])
            versions = dict(state.prompt_versions)
            versions["plan"] = prompt.version
            return {
                "plan": steps,
                "prompt_versions": versions,
                "status": ReviewStatus.RETRIEVING,
            }

    def retrieve_node(state: ReviewState) -> dict:
        with tracer.span("retrieve", review_id=state.review_id):
            chunks = score_chunks(state.event.content, k=3)
            return {"retrieved": chunks, "status": ReviewStatus.DRAFTING}

    def draft_node(state: ReviewState) -> dict:
        with tracer.span("draft", review_id=state.review_id, attempt=state.attempts + 1):
            prompt = ctx.registry.get("draft")
            context = format_context(state.retrieved)
            versions = dict(state.prompt_versions)
            versions["draft"] = prompt.version
            draft: ReviewDraft | None = None
            errors = list(state.errors)
            # Output-validation guardrail: retry parsing/validation locally.
            for _ in range(MAX_ATTEMPTS):
                raw = ctx.provider.complete(
                    prompt_name="draft", rendered_prompt=prompt.body, context=context
                )
                try:
                    draft = ReviewDraft.model_validate(_parse_json(raw))
                    break
                except (ValidationError, json.JSONDecodeError) as exc:
                    errors.append(f"draft parse/validation failed: {exc}")
            if draft is None:
                return {
                    "status": ReviewStatus.FAILED,
                    "errors": errors,
                    "attempts": state.attempts + 1,
                }
            return {
                "draft": draft,
                "prompt_versions": versions,
                "attempts": state.attempts + 1,
                "errors": errors,
                "status": ReviewStatus.CRITIQUING,
            }

    def critique_node(state: ReviewState) -> dict:
        with tracer.span("critique", review_id=state.review_id):
            # Output guardrail: a missing/failed draft can never be judged as
            # passing. Fail closed without spending a judge call.
            if state.draft is None:
                return {
                    "eval_result": EvalResult(
                        passed=False, score=0.0, rationale="no valid draft to critique"
                    ),
                    "status": ReviewStatus.AWAITING_APPROVAL,
                }
            prompt = ctx.registry.get("critique")
            context = state.draft.model_dump_json()
            versions = dict(state.prompt_versions)
            versions["critique"] = prompt.version
            raw = ctx.provider.complete(
                prompt_name="critique", rendered_prompt=prompt.body, context=context
            )
            evalr = EvalResult.model_validate(_parse_json(raw))
            return {
                "eval_result": evalr,
                "prompt_versions": versions,
                "status": ReviewStatus.AWAITING_APPROVAL,
            }

    def await_approval_node(state: ReviewState) -> dict:
        # No-op marker node. The interrupt fires *before* `finalize`, so control
        # returns to the caller here for the human decision.
        return {"status": ReviewStatus.AWAITING_APPROVAL}

    def finalize_node(state: ReviewState) -> dict:
        with tracer.span("finalize", review_id=state.review_id):
            approval = state.approval or ApprovalDecision(
                decision="reject", approver="system", note="no approval supplied"
            )
            status = ReviewStatus.APPROVED if approval.decision == "approve" else ReviewStatus.REJECTED
            draft = state.draft
            evalr = state.eval_result or EvalResult(passed=False, score=0.0)
            verdict = "APPROVED" if status is ReviewStatus.APPROVED else "REJECTED"
            comment = (
                f"[{verdict}] Automated standards review for {state.event.repo}"
                f"#{state.event.number}\n\n{draft.summary if draft else '(no draft)'}\n\n"
                f"Findings: {len(draft.findings) if draft else 0}; "
                f"risk={draft.risk if draft else 'n/a'}; "
                f"sources={draft.cited_sources if draft else []}; "
                f"approver={approval.approver}"
            )
            output = ReviewOutput(
                review_id=state.review_id,
                status=status.value,  # type: ignore[arg-type]
                posted_comment=comment,
                draft=draft or ReviewDraft(summary="(no draft produced)", risk="high"),
                eval_result=evalr,
            )
            ctx.poster(state.review_id, comment)
            record = TraceRecord(
                review_id=state.review_id,
                delivery_id=state.event.delivery_id,
                repo=state.event.repo,
                number=state.event.number,
                provider=ctx.provider.name,
                model=ctx.provider.model,
                prompt_versions=state.prompt_versions,
                input_summary=state.event.title,
                retrieved_sources=[c.source_id for c in state.retrieved],
                eval_result=evalr,
                approval=approval,
                status=status.value,  # type: ignore[arg-type]
            )
            ctx.audit.write_trace_record(record)
            return {"output": output, "status": status}

    return {
        "plan": plan_node,
        "retrieve": retrieve_node,
        "draft": draft_node,
        "critique": critique_node,
        "await_approval": await_approval_node,
        "finalize": finalize_node,
    }


def _after_critique(state: ReviewState) -> str:
    """Guardrail/retry edge: re-draft on a failing verdict, but cap attempts."""
    evalr = state.eval_result
    if evalr is not None and not evalr.passed and state.attempts < MAX_ATTEMPTS:
        return "retry"
    return "proceed"


def build_graph(
    provider: LLMProvider | None = None,
    audit_log: AuditLog | None = None,
    poster: Callable[[str, str], None] | None = None,
    registry: PromptRegistry | None = None,
    checkpointer: MemorySaver | None = None,
    settings=None,
):
    """Compile the review StateGraph. Pauses before `finalize` for HITL approval.

    When `provider` is omitted, it is selected from `settings` (falling back to
    the global settings), so the caller's Settings — not just process env — drives
    which LLM provider runs.
    """
    settings = settings or get_settings()
    ctx = GraphContext(
        provider=provider or get_provider(settings),
        registry=registry or PromptRegistry(),
        audit=audit_log or AuditLog(settings.audit_log_path),
        poster=poster or _default_poster,
    )
    nodes = _build_nodes(ctx)

    g = StateGraph(ReviewState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.set_entry_point("plan")
    g.add_edge("plan", "retrieve")
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", "critique")
    g.add_conditional_edges(
        "critique", _after_critique, {"retry": "draft", "proceed": "await_approval"}
    )
    g.add_edge("await_approval", "finalize")
    g.add_edge("finalize", END)

    return g.compile(
        checkpointer=checkpointer or make_checkpointer(),
        interrupt_before=["finalize"],
    )


# ---------------------------------------------------------------------------
# Run helpers: pause at approval, resume with a decision.
# ---------------------------------------------------------------------------
def start_review(graph, state: ReviewState, thread_id: str) -> ReviewState:
    """Run the graph until it pauses before `finalize`. Returns paused state."""
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(state, config)
    snapshot = graph.get_state(config)
    return ReviewState.model_validate(snapshot.values)


def resume_review(graph, thread_id: str, decision: ApprovalDecision) -> ReviewState:
    """Inject the human decision and run `finalize` to completion."""
    config = {"configurable": {"thread_id": thread_id}}
    graph.update_state(config, {"approval": decision})
    graph.invoke(None, config)
    snapshot = graph.get_state(config)
    return ReviewState.model_validate(snapshot.values)


def run_review_auto(graph, state: ReviewState, thread_id: str, decision: ApprovalDecision) -> ReviewState:
    """Convenience for evals/demo: start, then immediately apply `decision`."""
    start_review(graph, state, thread_id)
    return resume_review(graph, thread_id, decision)
