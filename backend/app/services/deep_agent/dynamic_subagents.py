"""Shared constants + helpers for the dynamic-subagents pilot slice.

Governance primitives for QuickJS ``task()`` fan-out. Enablement is gated on an
allowlisted, seed-owned workflow; the model/tool payloads can never authorize
themselves (attribution is stamped server-side into ``configurable``).
"""
from __future__ import annotations

# Server-owned allowlist. A workflow may carry ``dynamic_subagents`` ONLY if its
# slug is here AND its persisted row has ``source == "seed"`` (checked at save time).
DYNAMIC_SUBAGENTS_ALLOWLIST: frozenset[str] = frozenset({"morning-risk-breach-commentary"})

# Per-eval PTC backstop for the QuickJS interpreter (lowered from the lib default 64).
MAX_PTC_CALLS: int = 24

# configurable keys (server-set only — never derived from model/tool input).
FANOUT_ATTRIBUTION_KEY = "fanout_attribution"
FANOUT_ATTRIBUTION_CASE3 = "case3_workflow"
FANOUT_WORKFLOW_ID_KEY = "fanout_workflow_slug"


def is_allowlisted(slug: str | None) -> bool:
    """True iff ``slug`` is a server-owned dynamic-subagents workflow."""
    return bool(slug) and slug in DYNAMIC_SUBAGENTS_ALLOWLIST


def fanout_attribution_extra(*, slug: str | None, source: str | None) -> dict[str, str]:
    """Server-derived attribution for ``configurable``.

    Stamps ONLY when the run is an allowlisted slug persisted with ``source == 'seed'``.
    Never trusts the model or the runtime router id (``Workflow.id`` is an int, not a slug).
    """
    if source == "seed" and is_allowlisted(slug):
        return {
            FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
            FANOUT_WORKFLOW_ID_KEY: slug,  # type: ignore[dict-item]  # slug is truthy here
        }
    return {}


def reconcile_fanout_coverage(
    scoped_ids: list[str], records: list[dict]
) -> dict:
    """Guarantee exactly one terminal record per scoped id.

    - first record per id wins (duplicates collapse);
    - records for ids not in scope are ignored;
    - any scoped id with no record becomes ``{"position_id": id, "status": "failed"}``.

    Coverage is thus independent of how many dispatches actually returned — a fan-out
    truncated by ``max_ptc_calls`` or hit by subagent errors can never silently drop a
    scoped breach; it surfaces as ``failed`` instead.
    """
    seen: dict[str, dict] = {}
    scoped = list(dict.fromkeys(scoped_ids))  # de-dupe, preserve order
    scoped_set = set(scoped)
    for rec in records:
        pid = rec.get("position_id")
        if pid in scoped_set and pid not in seen:
            seen[pid] = rec
    out_records: list[dict] = []
    failed: list[str] = []
    for pid in scoped:
        rec = seen.get(pid) or {"position_id": pid, "status": "failed"}
        if rec.get("status") == "failed":
            failed.append(pid)
        out_records.append(rec)
    return {
        "records": out_records,
        "failed_ids": failed,
        "covered": len(seen),
        "total": len(scoped),
    }
