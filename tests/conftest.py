"""Shared fixtures/helpers for the test suite."""

import json

import pytest

from app.llm import MockProvider
from app.models import GitHubWebhookEvent, ReviewState


@pytest.fixture(autouse=True)
def _offline_langfuse(monkeypatch):
    """Force every test onto the offline tracer.

    A real `.env` may carry LANGFUSE_* keys (used to demo live observability).
    Tests must never authenticate to or emit spans at a live Langfuse project,
    so we stub the activation to return None and reset the shared tracer.
    """
    monkeypatch.setattr("app.tracing.Tracer._maybe_langfuse", lambda self: None)
    from app.tracing import reset_tracer

    reset_tracer()


@pytest.fixture
def event() -> GitHubWebhookEvent:
    return GitHubWebhookEvent(
        delivery_id="test-delivery",
        repo="acme/widgets",
        number=42,
        title="Add signed webhook endpoint and orders migration with logging",
        body="Validates external input, adds a migration and structured logging.",
        sender="alice",
    )


@pytest.fixture
def state(event) -> ReviewState:
    return ReviewState(review_id="rev_test", event=event)


class FlakyDraftProvider(MockProvider):
    """Weak draft on the first attempt, strong afterwards.

    Proves the critique->draft retry loop is load-bearing: with retries the run
    ends above the bar; the permanently-weak variant below shows the guardrail
    caps attempts instead of looping forever.
    """

    def __init__(self, weak_attempts: int = 1) -> None:
        super().__init__()
        self.weak_attempts = weak_attempts
        self.draft_calls = 0

    def complete(self, *, prompt_name, rendered_prompt, context):
        if prompt_name == "draft":
            self.draft_calls += 1
            if self.draft_calls <= self.weak_attempts:
                return json.dumps(
                    {"summary": "Looks fine.", "findings": [], "risk": "low", "cited_sources": []}
                )
        return super().complete(
            prompt_name=prompt_name, rendered_prompt=rendered_prompt, context=context
        )


class MalformedDraftProvider(MockProvider):
    """Always returns unparseable output for the draft step."""

    def complete(self, *, prompt_name, rendered_prompt, context):
        if prompt_name == "draft":
            return "this is not json { and never will be"
        return super().complete(
            prompt_name=prompt_name, rendered_prompt=rendered_prompt, context=context
        )
