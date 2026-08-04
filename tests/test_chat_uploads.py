"""Chat attachments backend seam (Task 8).

Covers three surfaces:
(a) `POST /api/chat/uploads` — stores the file under
    `settings.artifact_dir/uploads/chat/` (reusing the existing `_store_upload`
    helper) and returns path/filename/sha256/byte_len.
(b) `AgentMessageCreate.attachments` round-trips a list of `AgentAttachmentIn`.
(c) `_attachment_manifest` — the module-level helper that appends an
    attachment manifest (with a `parse_trade_confirmation` hint) to the
    content handed to the agent run, leaving the persisted user message's
    `payload.content` untouched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.main import _attachment_manifest
from app.schemas import AgentAttachmentIn, AgentMessageCreate


def test_upload_chat_attachment_stores_file_and_returns_metadata(client, settings):
    content = b"hello trade confirmation bytes"
    resp = client.post(
        "/api/chat/uploads",
        files={"file": ("confirmation.pdf", content, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["filename"] == "confirmation.pdf"
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["byte_len"] == len(content)

    stored_path = Path(body["path"])
    assert stored_path.is_file()
    assert stored_path.read_bytes() == content

    chat_uploads_dir = (settings.artifact_dir / "uploads" / "chat").resolve()
    assert chat_uploads_dir in stored_path.resolve().parents


def test_agent_message_create_round_trips_attachments():
    payload = AgentMessageCreate(
        content="x",
        attachments=[{"path": "/p", "filename": "f.pdf"}],
    )
    assert payload.attachments is not None
    assert len(payload.attachments) == 1
    attachment = payload.attachments[0]
    assert isinstance(attachment, AgentAttachmentIn)
    assert attachment.path == "/p"
    assert attachment.filename == "f.pdf"
    assert attachment.sha256 is None


def test_agent_message_create_attachments_default_none():
    payload = AgentMessageCreate(content="x")
    assert payload.attachments is None


def test_attachment_manifest_unchanged_when_no_attachments():
    assert _attachment_manifest("hello there", None) == "hello there"
    assert _attachment_manifest("hello there", []) == "hello there"


def test_attachment_manifest_appends_filenames_paths_and_hint():
    attachments = [
        AgentAttachmentIn(path="/artifacts/uploads/chat/a.pdf", filename="a.pdf"),
        AgentAttachmentIn(path="/artifacts/uploads/chat/b.docx", filename="b.docx"),
    ]
    result = _attachment_manifest("please book these trades", attachments)

    assert result.startswith("please book these trades")
    assert "please book these trades" in result
    for attachment in attachments:
        assert attachment.filename in result
        assert attachment.path in result
    assert "parse_trade_confirmation" in result
