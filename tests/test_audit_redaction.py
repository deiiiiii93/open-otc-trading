"""Redaction before audit persistence (audit spec §5.1b)."""
import hashlib

from app.services.deep_agent.audit_redaction import redact_args, redact_text


def test_key_pattern_redaction_recursive():
    payload, redacted = redact_args(
        "book_position",
        {"api_key": "sk-123", "nested": {"PASSWORD": "p", "qty": 5}, "note": "ok"},
    )
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["PASSWORD"] == "[REDACTED]"
    assert payload["nested"]["qty"] == 5
    assert payload["note"] == "ok"
    assert redacted is True


def test_content_body_elision_for_fs_tools():
    body = "secret file contents " * 100
    payload, redacted = redact_args("write_file", {"file_path": "/a.txt", "content": body})
    elided = payload["content"]
    assert elided["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert elided["byte_len"] == len(body.encode())
    assert elided["head"] == body[:256]
    assert payload["file_path"] == "/a.txt"
    assert redacted is True


def test_code_body_elision_for_run_python_and_execute():
    payload, _ = redact_args("run_python", {"code": "print(1)" * 200, "writes_artifacts": True})
    assert set(payload["code"]) == {"sha256", "byte_len", "head"}
    payload, _ = redact_args("execute", {"command": "curl -H 'Authorization: Bearer x'"})
    assert set(payload["command"]) == {"sha256", "byte_len", "head"}


def test_clean_args_unchanged_and_size_cap():
    payload, redacted = redact_args("book_position", {"underlying": "AAPL", "qty": 1})
    assert payload == {"underlying": "AAPL", "qty": 1}
    assert redacted is False
    big, redacted = redact_args("book_position", {"blob": "x" * 20000})
    assert big["__truncated__"] is True
    assert redacted is True


def test_redact_text_caps_and_none():
    assert redact_text(None) is None
    assert len(redact_text("y" * 5000)) <= 2020  # cap + marker
