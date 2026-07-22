"""Webhook security: HMAC signature verification + replay protection.

Signatures are computed here with the stdlib `hmac`, exactly as GitHub does, so
these tests exercise the real verification path rather than a stub.
"""

import hashlib
import hmac

import pytest

from app.security import (
    ReplayError,
    ReplayGuard,
    SignatureError,
    compute_signature,
    verify_github_signature,
)

SECRET = "top-secret"
BODY = b'{"action":"opened","number":7}'


def _github_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    header = _github_signature(SECRET, BODY)
    # Should not raise.
    verify_github_signature(SECRET, BODY, header)


def test_our_signature_matches_github_scheme():
    assert compute_signature(SECRET, BODY) == _github_signature(SECRET, BODY)


def test_tampered_body_fails():
    header = _github_signature(SECRET, BODY)
    with pytest.raises(SignatureError):
        verify_github_signature(SECRET, BODY + b"tamper", header)


def test_wrong_secret_fails():
    header = _github_signature("other-secret", BODY)
    with pytest.raises(SignatureError):
        verify_github_signature(SECRET, BODY, header)


def test_missing_header_fails():
    with pytest.raises(SignatureError):
        verify_github_signature(SECRET, BODY, None)


def test_non_sha256_scheme_fails():
    sha1 = hmac.new(SECRET.encode(), BODY, hashlib.sha1).hexdigest()
    with pytest.raises(SignatureError):
        verify_github_signature(SECRET, BODY, f"sha1={sha1}")


def test_replayed_delivery_id_is_rejected():
    guard = ReplayGuard(ttl_seconds=600)
    guard.check_and_remember("delivery-abc")  # first time: ok
    with pytest.raises(ReplayError):
        guard.check_and_remember("delivery-abc")  # replay: rejected


def test_distinct_delivery_ids_allowed():
    guard = ReplayGuard(ttl_seconds=600)
    guard.check_and_remember("d1")
    guard.check_and_remember("d2")  # different id: ok


def test_missing_delivery_id_rejected():
    guard = ReplayGuard(ttl_seconds=600)
    with pytest.raises(SignatureError):
        guard.check_and_remember("")
