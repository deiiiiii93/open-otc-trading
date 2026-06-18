"""Canonical workflow snapshot capture helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import DomainEvent, Workflow
from .payload_registry import validate_event_payload

SCOPE_REGISTRY: dict[str, str] = {
    "workspace_meta": "workspace_meta",
    "ad_hoc": "ad_hoc",
    "rfq": "rfq",
    "reporting": "reporting",
    "portfolio_pricing": "portfolio_pricing",
}

_INTENT_SCOPE_DEFAULTS: dict[str, str] = {
    "workspace_meta": "workspace_meta",
    "ad_hoc": "ad_hoc",
    "rfq": "rfq",
    "reporting": "reporting",
    "portfolio_pricing": "portfolio_pricing",
}


def snapshot_for_workflow(
    *,
    intent: str,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(base or {})
    scope_kind = snapshot.get("scope_kind") or _INTENT_SCOPE_DEFAULTS.get(
        intent, "ad_hoc"
    )
    if scope_kind not in SCOPE_REGISTRY:
        raise ValueError(f"Unknown workflow snapshot scope_kind: {scope_kind}")
    snapshot["scope_kind"] = scope_kind
    snapshot.setdefault("captured_at", datetime.utcnow().isoformat())
    return snapshot


def capture_workflow_snapshot(
    session: Session,
    *,
    workflow: Workflow,
    snapshot: dict[str, Any] | None = None,
    previous_snapshot: dict[str, Any] | None = None,
    source: str,
) -> dict[str, Any]:
    captured = snapshot_for_workflow(
        intent=workflow.intent,
        base=snapshot if snapshot is not None else workflow.canonical_snapshot_ids,
    )
    workflow.canonical_snapshot_ids = captured
    payload = validate_event_payload(
        kind="snapshot_captured",
        schema_version=1,
        payload={
            "workflow_id": workflow.id,
            "snapshot": captured,
            "previous_snapshot": previous_snapshot,
            "source": source,
        },
    )
    session.add(
        DomainEvent(
            workflow_id=workflow.id,
            kind="snapshot_captured",
            schema_version=1,
            payload=payload,
            actor="system",
        )
    )
    return captured
