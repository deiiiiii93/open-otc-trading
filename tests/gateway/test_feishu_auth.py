"""Tests for Feishu event verification (AES-256-CBC + verification token check)."""
from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest

from app.services.gateway.connectors.feishu import verify_event
from app.services.gateway.config import GatewayConfig

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_ENCRYPT_KEY = "test_key_12345"
_VERIFICATION_TOKEN = "test_vtoken_xyz"
_WRONG_TOKEN = "wrong_token_000"


def _make_config(
    encrypt_key: str | None = _ENCRYPT_KEY,
    verification_token: str | None = _VERIFICATION_TOKEN,
) -> GatewayConfig:
    """Build a minimal GatewayConfig with just the Feishu auth fields set."""
    return GatewayConfig(
        gateway_default_desk_user="desk_user",
        gateway_linking_code_ttl_s=300,
        gateway_card_action_ttl_s=300,
        gateway_max_inbound_chars=4096,
        gateway_max_queued_per_chat=20,
        gateway_queue_max_age_s=3600,
        gateway_dedupe_ttl_s=60,
        gateway_dedupe_lease_s=10,
        gateway_lock_lease_s=10,
        gateway_code_issue_per_min=10,
        gateway_flush_interval_ms=200,
        gateway_flush_chars=500,
        gateway_web_base_url=None,
        gateway_enabled_connectors="feishu",
        feishu_app_id="cli_abc",
        feishu_app_secret="secret_abc",
        feishu_verification_token=verification_token,
        feishu_encrypt_key=encrypt_key,
    )


# ---------------------------------------------------------------------------
# Encryption helper (mirrors the implementation)
# ---------------------------------------------------------------------------

def _encrypt_payload(payload: dict, encrypt_key: str) -> bytes:
    """AES-256-CBC encrypt a JSON payload in the Feishu scheme.

    Steps:
    1. key = sha256(encrypt_key.encode()).digest()  → 32 bytes
    2. iv = random 16 bytes
    3. plaintext = json.dumps(payload).encode() + PKCS7 padding to 16-byte boundary
    4. ciphertext = AES-256-CBC(key, iv, plaintext)
    5. encoded = base64(iv + ciphertext)
    6. body = json.dumps({"encrypt": encoded})
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    plaintext = json.dumps(payload).encode()

    # PKCS7 padding to 16-byte boundary
    pad_len = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    encoded = base64.b64encode(iv + ciphertext).decode()
    return json.dumps({"encrypt": encoded}).encode()


# ---------------------------------------------------------------------------
# Helpers to build test events
# ---------------------------------------------------------------------------

def _make_event_with_token(token: str) -> dict:
    """Build a minimal Feishu v2 event dict with the given header.token."""
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_test_001",
            "event_type": "im.message.receive_v1",
            "tenant_key": "TENANT_TEST",
            "app_id": "cli_abc",
            "token": token,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": {
                "message_id": "om_xxx",
                "chat_id": "oc_xxx",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text": "hi"}',
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests: verify_event returns True for correctly encrypted + correct token
# ---------------------------------------------------------------------------


def test_verify_event_correct_key_and_token():
    """Correctly encrypted body with matching verification token → True."""
    payload = _make_event_with_token(_VERIFICATION_TOKEN)
    raw_body = _encrypt_payload(payload, _ENCRYPT_KEY)
    config = _make_config()
    assert verify_event(raw_body, config) is True


def test_verify_event_wrong_token_returns_false():
    """Correct encryption but mismatched token → False."""
    payload = _make_event_with_token(_WRONG_TOKEN)
    raw_body = _encrypt_payload(payload, _ENCRYPT_KEY)
    config = _make_config()
    assert verify_event(raw_body, config) is False


def test_verify_event_garbage_bytes_returns_false():
    """Completely garbage (undecryptable) bytes → False."""
    raw_body = b"not-json-at-all-garbage-12345"
    config = _make_config()
    assert verify_event(raw_body, config) is False


def test_verify_event_wrong_encrypt_key_returns_false():
    """Body encrypted with a different key → False (decryption produces garbage JSON)."""
    payload = _make_event_with_token(_VERIFICATION_TOKEN)
    raw_body = _encrypt_payload(payload, "completely_different_key")
    config = _make_config()
    assert verify_event(raw_body, config) is False


def test_verify_event_missing_encrypt_key_returns_false():
    """Config with no encrypt_key → False immediately."""
    payload = _make_event_with_token(_VERIFICATION_TOKEN)
    raw_body = _encrypt_payload(payload, _ENCRYPT_KEY)
    config = _make_config(encrypt_key=None)
    assert verify_event(raw_body, config) is False


def test_verify_event_missing_verification_token_returns_false():
    """Config with no verification_token → False immediately."""
    payload = _make_event_with_token(_VERIFICATION_TOKEN)
    raw_body = _encrypt_payload(payload, _ENCRYPT_KEY)
    config = _make_config(verification_token=None)
    assert verify_event(raw_body, config) is False


def test_verify_event_empty_body_returns_false():
    """Empty bytes → False."""
    config = _make_config()
    assert verify_event(b"", config) is False


def test_verify_event_json_missing_encrypt_field_returns_false():
    """Valid JSON but no 'encrypt' key → False."""
    config = _make_config()
    raw_body = json.dumps({"foo": "bar"}).encode()
    assert verify_event(raw_body, config) is False


def test_verify_event_multiple_calls_deterministic():
    """Same payload encrypted twice with different IVs should both verify True."""
    payload = _make_event_with_token(_VERIFICATION_TOKEN)
    config = _make_config()
    # Two different encryptions (different random IVs)
    raw1 = _encrypt_payload(payload, _ENCRYPT_KEY)
    raw2 = _encrypt_payload(payload, _ENCRYPT_KEY)
    # IVs differ, so ciphertexts differ
    assert raw1 != raw2
    assert verify_event(raw1, config) is True
    assert verify_event(raw2, config) is True


def test_verify_event_top_level_token_fallback():
    """Feishu v1-style events use top-level 'token' field; should still verify."""
    # Build a payload WITHOUT header.token but WITH top-level token
    payload = {
        "token": _VERIFICATION_TOKEN,
        "challenge": "xxx",
        "type": "url_verification",
    }
    raw_body = _encrypt_payload(payload, _ENCRYPT_KEY)
    config = _make_config()
    assert verify_event(raw_body, config) is True
