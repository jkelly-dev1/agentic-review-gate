"""FastAPI surface for the review agent.

Routes:
  POST /webhooks/github     verify signature + replay -> start review -> 202
  POST /reviews/{id}/approve  HITL decision -> resume graph -> result
  GET  /reviews/{id}        status + trace (spans, prompt versions, sources)
  GET  /healthz             liveness
  GET  /metrics             basic counters

The webhook and approval routes are rate limited by a small in-memory
fixed-window limiter (swap for slowapi/Redis in production). Reviews run
synchronously with the deterministic mock provider, so the demo and tests are
fully offline and stable.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.audit import AuditLog
from app.config import get_settings
from app.graph import build_graph, resume_review, start_review
from app.models import ApprovalDecision, GitHubWebhookEvent, ReviewState, ReviewStatus
from app.security import (
    ReplayError,
    ReplayGuard,
    SignatureError,
    verify_github_signature,
)
from app.tracing import get_tracer


# ---------------------------------------------------------------------------
# Small in-memory fixed-window rate limiter (swappable for slowapi/Redis).
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits[key] if now - t < self.window]
            if len(hits) >= self.max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _event_from_payload(payload: dict, delivery_id: str, event_type: str) -> GitHubWebhookEvent:
    """Map a GitHub-style PR/issue payload to our event model."""
    repo = payload.get("repository", {}).get("full_name", "unknown/unknown")
    sender = payload.get("sender", {}).get("login", "unknown")
    action = payload.get("action", "opened")
    if event_type == "issues":
        obj = payload.get("issue", {})
        evt = "issues"
    else:
        obj = payload.get("pull_request", {})
        evt = "pull_request"
    return GitHubWebhookEvent(
        delivery_id=delivery_id,
        event=evt,  # type: ignore[arg-type]
        action=action,
        repo=repo,
        number=obj.get("number", 1),
        title=obj.get("title", "(no title)"),
        body=obj.get("body") or "",
        sender=sender,
    )


def create_app(settings=None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="agentic-review-gate", version="0.1.0")

    audit = AuditLog(settings.audit_log_path)
    graph = build_graph(audit_log=audit, settings=settings)
    replay_guard = ReplayGuard(ttl_seconds=settings.replay_ttl_seconds)
    limiter = RateLimiter(settings.rate_limit_max_requests, settings.rate_limit_window_seconds)

    # In-memory review registry: review_id -> {thread_id, state, event}.
    reviews: dict[str, dict[str, Any]] = {}
    metrics: dict[str, int] = defaultdict(int)

    app.state.audit = audit
    app.state.graph = graph
    app.state.reviews = reviews
    app.state.metrics = metrics
    app.state.replay_guard = replay_guard

    def rate_limit(request: Request) -> None:
        key = f"{request.url.path}:{request.client.host if request.client else 'unknown'}"
        if not limiter.check(key):
            metrics["rate_limited"] += 1
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(
        request: Request,
        _: None = Depends(rate_limit),
        x_hub_signature_256: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_github_event: str = Header(default="pull_request"),
    ):
        body = await request.body()
        metrics["webhooks_received"] += 1
        try:
            verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256)
        except SignatureError as exc:
            metrics["signature_failures"] += 1
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            replay_guard.check_and_remember(x_github_delivery or "")
        except ReplayError as exc:
            metrics["replays_rejected"] += 1
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SignatureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        payload = await request.json()
        event = _event_from_payload(payload, x_github_delivery, x_github_event)
        review_id = f"rev_{uuid.uuid4().hex[:12]}"
        state = ReviewState(review_id=review_id, event=event)
        paused = start_review(graph, state, thread_id=review_id)
        reviews[review_id] = {"thread_id": review_id, "state": paused, "event": event}
        metrics["reviews_started"] += 1
        return {
            "review_id": review_id,
            "status": paused.status.value,
            "message": "review started; awaiting human approval",
        }

    @app.post("/reviews/{review_id}/approve")
    async def approve(review_id: str, decision: ApprovalDecision, _: None = Depends(rate_limit)):
        if review_id not in reviews:
            raise HTTPException(status_code=404, detail="unknown review id")
        entry = reviews[review_id]
        if entry["state"].status not in (ReviewStatus.AWAITING_APPROVAL,):
            raise HTTPException(status_code=409, detail=f"review is {entry['state'].status.value}")
        final = resume_review(graph, entry["thread_id"], decision)
        entry["state"] = final
        metrics["approved" if decision.decision == "approve" else "rejected"] += 1
        return {
            "review_id": review_id,
            "status": final.status.value,
            "output": final.output.model_dump(mode="json") if final.output else None,
        }

    @app.get("/reviews/{review_id}")
    async def get_review(review_id: str):
        if review_id not in reviews:
            raise HTTPException(status_code=404, detail="unknown review id")
        state: ReviewState = reviews[review_id]["state"]
        tracer = get_tracer()
        return {
            "review_id": review_id,
            "status": state.status.value,
            "prompt_versions": state.prompt_versions,
            "retrieved_sources": [c.source_id for c in state.retrieved],
            "eval_result": state.eval_result.model_dump() if state.eval_result else None,
            "output": state.output.model_dump(mode="json") if state.output else None,
            "trace": {
                "langfuse_active": tracer.langfuse_active,
                "spans": tracer.local.summary(),
                "total_ms": tracer.local.total_ms,
            },
        }

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "provider": settings.agent_provider}

    @app.get("/metrics")
    async def get_metrics():
        return JSONResponse(dict(metrics))

    return app


app = create_app()
