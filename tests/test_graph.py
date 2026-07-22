"""The LangGraph workflow: pause at HITL, resume, reject, retry/guardrail."""

from app.audit import AuditLog
from app.graph import MAX_ATTEMPTS, build_graph, resume_review, start_review
from app.llm import MockProvider
from app.models import ApprovalDecision, ReviewOutput, ReviewState, ReviewStatus

from tests.conftest import FlakyDraftProvider, MalformedDraftProvider


def _graph(tmp_path, provider=None):
    audit = AuditLog(tmp_path / "audit.jsonl")
    return build_graph(provider=provider or MockProvider(), audit_log=audit), audit


def test_happy_path_pauses_at_approval(tmp_path, state):
    graph, _ = _graph(tmp_path)
    paused = start_review(graph, state, thread_id="t1")
    assert paused.status is ReviewStatus.AWAITING_APPROVAL
    assert paused.draft is not None
    assert paused.eval_result is not None and paused.eval_result.passed
    assert paused.retrieved  # sources were retrieved for provenance
    assert paused.output is None  # not finalized yet — the gate is real


def test_resume_approve_finalizes(tmp_path, state):
    graph, audit = _graph(tmp_path)
    start_review(graph, state, thread_id="t2")
    final = resume_review(graph, "t2", ApprovalDecision(decision="approve", approver="bob"))
    assert final.status is ReviewStatus.APPROVED
    assert final.output is not None
    assert final.output.status == "approved"
    # Output passes Pydantic validation (round-trip).
    ReviewOutput.model_validate(final.output.model_dump())
    # Exactly one audit record was written, and the chain verifies.
    assert len(audit.read_all()) == 1
    assert audit.verify_chain()


def test_reject_stops(tmp_path, state):
    graph, audit = _graph(tmp_path)
    start_review(graph, state, thread_id="t3")
    final = resume_review(graph, "t3", ApprovalDecision(decision="reject", approver="carol"))
    assert final.status is ReviewStatus.REJECTED
    assert final.output.status == "rejected"
    # A rejection is still audited (who rejected, when, on what basis).
    rec = audit.read_all()[-1]
    assert rec.status == "rejected"
    assert rec.approval.approver == "carol"


def test_retry_path_recovers_a_weak_first_draft(tmp_path, state):
    """A failing critique loops back to draft; the second draft passes."""
    provider = FlakyDraftProvider(weak_attempts=1)
    graph, _ = _graph(tmp_path, provider=provider)
    paused = start_review(graph, state, thread_id="t4")
    assert provider.draft_calls == 2  # it re-drafted
    assert paused.attempts == 2
    assert paused.eval_result.passed is True


def test_guardrail_caps_retries(tmp_path, state):
    """A permanently weak draft must not loop forever; attempts are capped."""
    provider = FlakyDraftProvider(weak_attempts=99)
    graph, _ = _graph(tmp_path, provider=provider)
    paused = start_review(graph, state, thread_id="t5")
    assert paused.attempts == MAX_ATTEMPTS
    assert paused.eval_result.passed is False  # proceeds, but the verdict is honest


def test_malformed_draft_fails_closed(tmp_path, state):
    """Unparseable draft output never yields a passing review."""
    graph, _ = _graph(tmp_path, provider=MalformedDraftProvider())
    paused = start_review(graph, state, thread_id="t6")
    assert paused.draft is None
    assert paused.errors  # validation failures were recorded
    assert paused.eval_result.passed is False
