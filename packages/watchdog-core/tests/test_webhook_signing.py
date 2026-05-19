"""HMAC signing / verification — golden vectors and round-trips."""

from __future__ import annotations

import hashlib
import hmac
import time

from watchdog_core.alerting.webhook_dispatcher import sign_payload, verify_signature


def test_sign_payload_matches_hashlib_golden_vector() -> None:
    """The implementation must match what hashlib produces directly.

    Computed by hand: HMAC-SHA256("shhh", "1700000000." + body).
    """
    payload = b'{"alert_id":"abc","severity":"high"}'
    secret = "shhh"  # noqa: S105 — test fixture, not a real credential
    ts = 1700000000

    signing_string = b"1700000000." + payload
    expected_hex = hmac.new(b"shhh", signing_string, hashlib.sha256).hexdigest()
    expected_header = f"t={ts},v1={expected_hex}"

    assert sign_payload(payload, secret, ts) == expected_header


def test_verify_signature_round_trip_succeeds() -> None:
    payload = b'{"x":1}'
    secret = "shhh"  # noqa: S105 — test fixture, not a real credential
    ts = int(time.time())
    header = sign_payload(payload, secret, ts)

    assert verify_signature(payload, header, secret)


def test_verify_signature_rejects_tampered_payload() -> None:
    secret = "shhh"  # noqa: S105 — test fixture, not a real credential
    ts = int(time.time())
    original = b'{"amount":100}'
    tampered = b'{"amount":1000000}'
    header = sign_payload(original, secret, ts)

    assert not verify_signature(tampered, header, secret)


def test_verify_signature_rejects_wrong_secret() -> None:
    payload = b'{"x":1}'
    ts = int(time.time())
    header = sign_payload(payload, "correct-secret", ts)

    assert not verify_signature(payload, header, "wrong-secret")


def test_verify_signature_rejects_old_timestamp() -> None:
    payload = b'{"x":1}'
    secret = "shhh"  # noqa: S105 — test fixture, not a real credential
    old_ts = int(time.time()) - 3600  # 1 hour old
    header = sign_payload(payload, secret, old_ts)

    assert not verify_signature(payload, header, secret, tolerance_s=300)


def test_verify_signature_rejects_malformed_header() -> None:
    secret = "shhh"  # noqa: S105 — test fixture, not a real credential
    assert not verify_signature(b"x", "garbage", secret)
    assert not verify_signature(b"x", "t=notanumber,v1=abc", secret)
    assert not verify_signature(b"x", "v1=onlysig", secret)
