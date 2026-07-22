"""GitHub webhook security: HMAC signature verification + replay protection.

GitHub signs each webhook body with HMAC-SHA256 using the shared secret and
sends it as `X-Hub-Signature-256: sha256=<hex>`. We recompute it and compare
with `hmac.compare_digest` (constant-time, so we don't leak the signature via
timing). We also dedupe by `X-GitHub-Delivery` with a TTL so a captured request
can't be replayed.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from threading import Lock


class SignatureError(Exception):
    """Raised when a webhook signature is missing, malformed, or invalid."""


class ReplayError(Exception):
    """Raised when a delivery id has already been seen within the TTL window."""


def compute_signature(secret: str, body: bytes) -> str:
    """Return the `sha256=<hex>` header value GitHub would send for this body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_github_signature(secret: str, body: bytes, header: str | None) -> None:
    """Verify an `X-Hub-Signature-256` header. Raises SignatureError on failure.

    Rejects: missing header, wrong scheme (not `sha256=`), and any body that
    does not match the recomputed HMAC. The comparison is constant-time.
    """
    if not header:
        raise SignatureError("missing X-Hub-Signature-256 header")
    if not header.startswith("sha256="):
        raise SignatureError("unsupported signature scheme (expected sha256=)")
    expected = compute_signature(secret, body)
    # compare_digest over the full "sha256=<hex>" strings; equal length always.
    if not hmac.compare_digest(expected, header):
        raise SignatureError("signature mismatch")


class ReplayGuard:
    """In-memory delivery-id dedupe with a TTL.

    Swappable for Redis/DB in a multi-replica deployment; the interface is just
    `check_and_remember(delivery_id)`. Thread-safe for the single-process demo.
    """

    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = Lock()

    def _evict(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts > self.ttl]
        for k in expired:
            del self._seen[k]

    def check_and_remember(self, delivery_id: str) -> None:
        """Record `delivery_id`; raise ReplayError if seen within the TTL."""
        if not delivery_id:
            raise SignatureError("missing X-GitHub-Delivery id")
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            if delivery_id in self._seen:
                raise ReplayError(f"replayed delivery id: {delivery_id}")
            self._seen[delivery_id] = now
