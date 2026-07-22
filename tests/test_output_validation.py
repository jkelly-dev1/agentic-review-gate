"""Pydantic output validation rejects malformed model output."""

import pytest
from pydantic import ValidationError

from app.models import ReviewDraft, ReviewOutput, TraceRecord


def test_draft_rejects_placeholder_summary():
    with pytest.raises(ValidationError):
        ReviewDraft.model_validate({"summary": "TODO"})


def test_draft_rejects_empty_summary():
    with pytest.raises(ValidationError):
        ReviewDraft.model_validate({"summary": ""})


def test_draft_rejects_invalid_risk():
    with pytest.raises(ValidationError):
        ReviewDraft.model_validate({"summary": "ok", "risk": "catastrophic"})


def test_review_output_requires_comment():
    draft = ReviewDraft(summary="a real summary", risk="low")
    from app.models import EvalResult

    with pytest.raises(ValidationError):
        ReviewOutput(
            review_id="r1",
            status="approved",
            posted_comment="",  # min_length=1
            draft=draft,
            eval_result=EvalResult(passed=True, score=1.0),
        )


def test_eval_result_score_bounds():
    from app.models import EvalResult

    with pytest.raises(ValidationError):
        EvalResult(passed=True, score=1.5)  # > 1.0


def test_trace_record_requires_status_enum():
    with pytest.raises(ValidationError):
        TraceRecord.model_validate(
            {
                "review_id": "r1",
                "delivery_id": "d1",
                "repo": "a/b",
                "number": 1,
                "provider": "mock",
                "model": "m",
                "prompt_versions": {},
                "input_summary": "x",
                "retrieved_sources": [],
                "eval_result": {"passed": True, "score": 0.9},
                "status": "maybe",  # not in {approved, rejected}
            }
        )
