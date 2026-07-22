"""Tamper-evident audit log: the chain verifies, and any edit breaks it."""

import json

from app.audit import AuditLog, GENESIS_HASH
from app.models import ApprovalDecision, EvalResult, TraceRecord


def _record(review_id: str, status: str = "approved") -> TraceRecord:
    return TraceRecord(
        review_id=review_id,
        delivery_id=f"d-{review_id}",
        repo="acme/widgets",
        number=1,
        provider="mock",
        model="mock-deterministic-v1",
        prompt_versions={"plan": "v1", "draft": "v1", "critique": "v1"},
        input_summary="a change",
        retrieved_sources=["SEC-STD"],
        eval_result=EvalResult(passed=True, score=0.9),
        approval=ApprovalDecision(decision="approve", approver="bob"),
        status=status,
    )


def test_chain_verifies_for_multiple_records(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    first = log.write_trace_record(_record("r1"))
    second = log.write_trace_record(_record("r2"))
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash  # links to predecessor
    assert log.verify_chain()


def test_tampering_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write_trace_record(_record("r1"))
    log.write_trace_record(_record("r2", status="approved"))
    assert log.verify_chain()

    # Tamper with the first record's content (flip an approval), keep its hash.
    lines = path.read_text().splitlines()
    rec0 = json.loads(lines[0])
    rec0["status"] = "rejected"
    rec0["approval"]["decision"] = "reject"
    lines[0] = json.dumps(rec0)
    path.write_text("\n".join(lines) + "\n")

    assert log.verify_chain() is False  # tamper detected


def test_reordering_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write_trace_record(_record("r1"))
    log.write_trace_record(_record("r2"))
    lines = path.read_text().splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n")
    assert log.verify_chain() is False
