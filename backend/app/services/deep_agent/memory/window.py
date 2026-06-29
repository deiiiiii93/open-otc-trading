# backend/app/services/deep_agent/memory/window.py
"""Extractor input window from AgentMessage (spec §Extractor input window)."""
from __future__ import annotations

import tiktoken

from .config import MemoryConfig


def load_extraction_window(session_id, after_message_id, config: MemoryConfig):
    from app import database
    from app.models import AgentMessage

    try:
        with database.SessionLocal() as session:
            q = session.query(AgentMessage).filter(AgentMessage.session_id == session_id)
            if after_message_id is not None:
                q = q.filter(AgentMessage.id > after_message_id)
            rows = q.order_by(AgentMessage.id.asc()).all()
            window = [{"id": r.id, "role": r.role, "content": r.content}
                      for r in rows if r.role != "system"]
        window = window[-config.extract_window_messages:]
        enc = tiktoken.get_encoding(config.tiktoken_encoder)
        total = sum(len(enc.encode(m["content"] or "")) for m in window)
        while window and total > config.extract_window_tokens:
            total -= len(enc.encode(window.pop(0)["content"] or ""))
        return window
    except Exception:  # noqa: BLE001
        return None
