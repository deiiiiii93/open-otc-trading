"""Durable extraction-run state machine (spec §Data model 2, §enqueue_run)."""
from __future__ import annotations

from dataclasses import dataclass

from app.models import MemoryExtractionRun
from .config import MemoryConfig


def session_run_key(session_id: int) -> str:
    return f"session:{session_id}"


def correction_run_key(session_id: int, trigger_message_id: int) -> str:
    # trigger_message_id is a DURABLE integer AgentMessage id (matches the
    # Integer column memory_extraction_runs.trigger_message_id).
    return f"corr:{session_id}:{trigger_message_id}"


@dataclass(frozen=True)
class RunSpec:
    run_key: str
    kind: str
    session_id: int
    thread_id: int | None
    persona: str | None
    book_scope_id: str | None
    trigger_message_id: int | None


class ExtractionRunStore:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config

    def get(self, session, run_key):
        return session.get(MemoryExtractionRun, run_key)

    def enqueue_run(self, session, spec: RunSpec) -> bool:
        row = self.get(session, spec.run_key)
        if row is None:
            session.add(MemoryExtractionRun(
                run_key=spec.run_key, kind=spec.kind, session_id=spec.session_id,
                thread_id=spec.thread_id, persona=spec.persona,
                book_scope_id=spec.book_scope_id,
                trigger_message_id=spec.trigger_message_id, status="pending", attempts=0))
            session.flush()
            return True
        if row.status == "succeeded":
            return False
        if row.status == "pending":
            return True
        if row.attempts < self.config.max_extract_attempts:
            row.status = "pending"
            session.flush()
            return True
        return False

    def mark_succeeded(self, session, run_key, last_message_id) -> None:
        row = self.get(session, run_key)
        if row is None:
            return
        row.status = "succeeded"
        if last_message_id is not None:
            row.last_extracted_message_id = last_message_id
        session.flush()

    def mark_failed(self, session, run_key, error) -> None:
        row = self.get(session, run_key)
        if row is None:
            return
        row.status = "failed"
        row.attempts = (row.attempts or 0) + 1
        row.last_error = str(error)[:500]
        session.flush()

    def eligible_runs(self, session):
        return (session.query(MemoryExtractionRun)
                .filter(MemoryExtractionRun.status.in_(("pending", "failed")))
                .filter((MemoryExtractionRun.status == "pending") |
                        (MemoryExtractionRun.attempts < self.config.max_extract_attempts))
                .all())
