"""Thin Langfuse wrapper for LLM observability.

When LANGFUSE_* env is configured, `@traced` / `span()` emit real Langfuse spans
(the SDK is imported lazily). When it is not, they degrade to a local no-op
tracer that STILL records step name, timing, and a running duration total — so
the traceability story works fully offline and tests can assert on spans. This
module never raises because Langfuse is absent or misconfigured.
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from app.config import get_settings


@dataclass
class Span:
    name: str
    start: float
    end: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end is None:
            return 0.0
        return round((self.end - self.start) * 1000, 3)


@dataclass
class LocalTracer:
    """Offline tracer: records spans in memory so timing is still auditable."""

    spans: list[Span] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[Span]:
        s = Span(name=name, start=time.perf_counter(), metadata=dict(metadata))
        self.spans.append(s)
        try:
            yield s
        finally:
            s.end = time.perf_counter()

    @property
    def total_ms(self) -> float:
        return round(sum(s.duration_ms for s in self.spans), 3)

    def summary(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "duration_ms": s.duration_ms, **s.metadata} for s in self.spans]


class Tracer:
    """Front door. Always holds a LocalTracer; also emits to Langfuse if enabled.

    On the Langfuse path (SDK v3/v4 — OpenTelemetry-based) every node span is
    attached to one shared trace_id, so a single review shows up as one trace in
    the Langfuse UI. Spans are batched: call flush() before the process exits so
    they are sent. Any Langfuse failure degrades to the offline local tracer and
    never breaks the request path.
    """

    def __init__(self) -> None:
        self.local = LocalTracer()
        self._lf = self._maybe_langfuse()
        self._trace_id: str | None = None
        if self._lf is not None:  # pragma: no cover - needs live Langfuse
            try:
                self._trace_id = self._lf.create_trace_id()
            except Exception:
                self._lf = None

    def _maybe_langfuse(self):
        settings = get_settings()
        if not settings.langfuse_enabled:
            return None
        try:  # pragma: no cover - only when Langfuse configured + installed
            from langfuse import Langfuse

            # Note: we deliberately do NOT gate on client.auth_check() — it is a
            # one-shot call that intermittently returns 401 on Langfuse cloud even
            # with valid keys, which would falsely disable tracing. Span export
            # runs through the SDK's batched exporter with its own retry/backoff.
            return Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception:
            return None

    @property
    def langfuse_active(self) -> bool:
        return self._lf is not None

    @property
    def trace_url(self) -> str | None:  # pragma: no cover - needs live Langfuse
        if self._lf is None or self._trace_id is None:
            return None
        try:
            return self._lf.get_trace_url(trace_id=self._trace_id)
        except Exception:
            return None

    def flush(self) -> None:
        if self._lf is not None:  # pragma: no cover - needs live Langfuse
            try:
                self._lf.flush()
            except Exception:
                pass

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[Span]:
        obs = None
        if self._lf is not None:  # pragma: no cover - needs live Langfuse
            try:
                obs = self._lf.start_observation(
                    trace_context={"trace_id": self._trace_id},
                    name=name,
                    as_type="span",
                    metadata=metadata,
                )
            except Exception:
                obs = None
        with self.local.span(name, **metadata) as s:
            try:
                yield s
            finally:
                if obs is not None:  # pragma: no cover
                    try:
                        obs.end()
                    except Exception:
                        pass


_TRACER: Tracer | None = None


def get_tracer() -> Tracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer()
    return _TRACER


def reset_tracer() -> Tracer:
    """Fresh tracer (used by the demo/tests to isolate span collections)."""
    global _TRACER
    _TRACER = Tracer()
    return _TRACER


def traced(name: str | None = None) -> Callable:
    """Decorator that wraps a function call in a span."""

    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with get_tracer().span(span_name):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
