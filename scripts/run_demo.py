"""Offline end-to-end demo.

Drives the real FastAPI app with a TestClient: builds a GitHub pull_request
payload, signs it with HMAC-SHA256, POSTs the webhook (which validates the
signature and replay id), lets the LangGraph workflow run to the human-approval
pause, auto-approves, then prints a human-readable trace and shows the
tamper-evident audit record. No network, no API key.

    python scripts/run_demo.py
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

# Cosmetic: Starlette's TestClient warns that it uses httpx (harmless, and only
# relevant to this demo harness, not the app). Silence it so the sample capture
# stays clean. Must be set before the TestClient import, where it is emitted.
# Match by message only — the warning is attributed to fastapi.testclient, so a
# module= filter on starlette would miss it.
warnings.filterwarnings("ignore", message=r".*httpx.*")

# Ensure repo root on path when run as `python scripts/run_demo.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.audit import AuditLog  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import compute_signature  # noqa: E402
from app.tracing import reset_tracer  # noqa: E402

SECRET = "demo-webhook-secret"

# Braille spinner frames, borrowed from the sibling temporal-multi-agent demo.
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@contextlib.contextmanager
def spinner(label: str):
    """Threaded stderr spinner for the blocking demo calls.

    Adapted from temporal-multi-agent/run_demo.py: same braille frames, 10fps,
    elapsed-time readout, and line erase on exit. That demo could poll a live
    Temporal query for the stage; here the graph runs synchronously inside the
    HTTP call, so the label is fixed and the elapsed seconds are the live signal.

    Writes to stderr and no-ops when stderr is not a TTY, so piping/redirecting
    the demo (e.g. to capture SAMPLE_RUN.md) stays clean.
    """
    if not sys.stderr.isatty():
        yield
        return
    stop = threading.Event()

    def _run() -> None:
        started = time.monotonic()
        for i in itertools.count():
            if stop.is_set():
                break
            elapsed = time.monotonic() - started
            sys.stderr.write(f"\r{_FRAMES[i % len(_FRAMES)]} {label}… ({elapsed:.0f}s)\033[K")
            sys.stderr.flush()
            time.sleep(0.1)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        sys.stderr.write("\r\033[K")  # erase the line on the way out
        sys.stderr.flush()


def _provider_banner(settings: Settings) -> str:
    """Report the active provider, and hint if a key is set but not selected."""
    provider = get_provider(settings)
    line = f"Provider: {provider.name} / {provider.model}"
    if provider.name == "mock":
        if settings.anthropic_api_key and settings.agent_provider != "anthropic":
            line += "  (ANTHROPIC_API_KEY is set — add AGENT_PROVIDER=anthropic to use it)"
        elif settings.openai_api_key and settings.agent_provider != "openai":
            line += "  (OPENAI_API_KEY is set — add AGENT_PROVIDER=openai to use it)"
    return line


def _banner(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main() -> int:
    audit_path = os.path.join(tempfile.mkdtemp(prefix="demo-audit-"), "audit.jsonl")
    # Provider, API keys and models come from the environment (AGENT_PROVIDER,
    # ANTHROPIC_API_KEY / OPENAI_API_KEY, ...); only the demo-specific webhook
    # secret and audit path are overridden here. Defaults to the offline mock.
    settings = Settings(
        github_webhook_secret=SECRET,
        audit_log_path=audit_path,
    )
    tracer = reset_tracer()
    print(_provider_banner(settings))
    app = create_app(settings)
    client = TestClient(app)

    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/flight-controls"},
        "sender": {"login": "dev-eng-42"},
        "pull_request": {
            "number": 128,
            "title": "Add signed webhook endpoint and orders migration",
            "body": (
                "Introduces a GitHub webhook handler that validates external input, "
                "plus a database migration for the orders table."
            ),
        },
    }
    body = json.dumps(payload).encode("utf-8")
    signature = compute_signature(SECRET, body)
    delivery_id = "demo-delivery-0001"

    _banner("1. Signed GitHub webhook -> POST /webhooks/github")
    with spinner("running review (plan -> retrieve -> draft -> critique)"):
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
    print(f"HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    review_id = resp.json()["review_id"]

    _banner("2. Review paused at HITL gate -> GET /reviews/{id}")
    status = client.get(f"/reviews/{review_id}").json()
    print(f"status           : {status['status']}")
    print(f"prompt versions  : {status['prompt_versions']}")
    print(f"retrieved sources: {status['retrieved_sources']}")
    print(f"eval verdict     : passed={status['eval_result']['passed']} "
          f"score={status['eval_result']['score']}")
    print("trace spans      :")
    for span in status["trace"]["spans"]:
        print(f"    - {span['name']:14s} {span['duration_ms']:.3f} ms")
    lf_active = status["trace"]["langfuse_active"]
    lf_note = "sending spans to Langfuse" if lf_active else "offline no-op tracer records timing locally"
    print(f"langfuse active  : {lf_active} ({lf_note})")

    _banner("3. Human approves -> POST /reviews/{id}/approve")
    with spinner("finalizing (post-back + audit write)"):
        approve = client.post(
            f"/reviews/{review_id}/approve",
            json={"decision": "approve", "approver": "release-manager-1", "note": "LGTM"},
        ).json()
    print(f"final status : {approve['status']}")
    print("posted comment:")
    print("    " + approve["output"]["posted_comment"].replace("\n", "\n    "))

    _banner("4. Tamper-evident audit record (append-only, hash-chained)")
    audit = AuditLog(audit_path)
    records = audit.read_all()
    rec = records[-1]
    print(f"records in log   : {len(records)}")
    print(f"review_id        : {rec.review_id}")
    print(f"provider/model   : {rec.provider} / {rec.model}")
    print(f"prompt_versions  : {rec.prompt_versions}")
    print(f"retrieved_sources: {rec.retrieved_sources}")
    print(f"approver         : {rec.approval.approver} ({rec.approval.decision})")
    print(f"status           : {rec.status}")
    print(f"prev_hash        : {rec.prev_hash[:16]}...")
    print(f"record_hash      : {rec.record_hash[:16]}...")
    print(f"chain verifies   : {audit.verify_chain()}")

    if tracer.langfuse_active:
        _banner("5. Langfuse trace (LLM observability)")
        print("langfuse active  : True")
        print(f"trace url        : {tracer.trace_url}")
        tracer.flush()  # send batched spans before the process exits
    else:
        tracer.flush()  # no-op when offline; harmless

    _banner(f"Done — provider: {rec.provider} / {rec.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
