"""Pydantic v2 models: the contract between webhook, graph, and audit log.

These are deliberately strict. Output validation in the graph runs the model's
own validators, so a malformed draft or trace record is rejected rather than
written to the audit log (see tests/test_output_validation.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GitHubWebhookEvent(BaseModel):
    """The subset of a GitHub PR/issue webhook payload the agent acts on."""

    delivery_id: str = Field(..., description="X-GitHub-Delivery UUID, dedupe key")
    event: Literal["pull_request", "issues"] = "pull_request"
    action: str = "opened"
    repo: str = Field(..., description="owner/name")
    number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    body: str = ""
    sender: str = Field(..., min_length=1)

    @property
    def content(self) -> str:
        return f"{self.title}\n\n{self.body}".strip()


class RetrievedChunk(BaseModel):
    """A cited snippet from the standards corpus (provenance for the analysis)."""

    source_id: str
    score: float = Field(..., ge=0.0)
    text: str = Field(..., min_length=1)


class ReviewDraft(BaseModel):
    """The agent's drafted analysis, before self-critique/finalization."""

    summary: str = Field(..., min_length=1)
    findings: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "medium"
    cited_sources: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _no_placeholder(cls, v: str) -> str:
        if v.strip().upper() in {"TODO", "TBD", ""}:
            raise ValueError("summary must not be a placeholder")
        return v


class EvalResult(BaseModel):
    """Verdict from the self-critique / LLM-judge step."""

    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    rubric: dict[str, bool] = Field(default_factory=dict)
    rationale: str = ""


class ApprovalDecision(BaseModel):
    """The human-in-the-loop decision that resumes a paused review."""

    decision: Literal["approve", "reject"]
    approver: str = Field(..., min_length=1)
    note: str = ""
    decided_at: datetime = Field(default_factory=_utcnow)


class ReviewOutput(BaseModel):
    """The final, validated result posted back to GitHub."""

    review_id: str
    status: Literal["approved", "rejected"]
    posted_comment: str = Field(..., min_length=1)
    draft: ReviewDraft
    eval_result: EvalResult


class TraceRecord(BaseModel):
    """The traceability schema written to the append-only audit log.

    Every field an auditor needs to reconstruct *what happened and why*:
    prompt versions used, model id, the input, the retrieved sources, the eval
    verdict, the human approver, and timestamps. `prev_hash`/`record_hash` form
    the tamper-evident chain (see app/audit.py).
    """

    review_id: str
    delivery_id: str
    repo: str
    number: int
    provider: str
    model: str
    prompt_versions: dict[str, str]
    input_summary: str
    retrieved_sources: list[str]
    eval_result: EvalResult
    approval: ApprovalDecision | None = None
    status: Literal["approved", "rejected"]
    created_at: datetime = Field(default_factory=_utcnow)
    # Chain fields (populated by the audit writer).
    prev_hash: str = ""
    record_hash: str = ""

    def payload_for_hash(self) -> dict[str, Any]:
        """Everything except record_hash itself, canonicalized for hashing."""
        data = self.model_dump(mode="json", exclude={"record_hash"})
        return data


class ReviewStatus(str, Enum):
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    DRAFTING = "drafting"
    CRITIQUING = "critiquing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewState(BaseModel):
    """The LangGraph state object threaded through every node.

    Kept as a Pydantic model so each node's output can be validated. The graph
    stores/loads dicts of this via the checkpointer; helpers convert both ways.
    """

    review_id: str
    event: GitHubWebhookEvent
    status: ReviewStatus = ReviewStatus.PLANNING
    plan: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    draft: ReviewDraft | None = None
    eval_result: EvalResult | None = None
    approval: ApprovalDecision | None = None
    output: ReviewOutput | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    attempts: int = 0
    errors: list[str] = Field(default_factory=list)
