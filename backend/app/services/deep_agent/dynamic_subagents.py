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
# Server-stamped, validated workflow launch args (e.g. portfolio_id). Tools read
# scope selectors from here rather than trusting model-supplied tool arguments.
FANOUT_LAUNCH_ARGS_KEY = "fanout_launch_args"


def is_allowlisted(slug: str | None) -> bool:
    """True iff ``slug`` is a server-owned dynamic-subagents workflow."""
    return bool(slug) and slug in DYNAMIC_SUBAGENTS_ALLOWLIST


def fanout_attribution_extra(
    *, slug: str | None, source: str | None, launch_args: dict | None = None
) -> dict:
    """Server-derived attribution (+ validated launch args) for ``configurable``.

    Stamps ONLY when the run is an allowlisted slug persisted with ``source == 'seed'``.
    Never trusts the model or the runtime router id (``Workflow.id`` is an int, not a slug).
    ``launch_args`` are the *validated* workflow launch params; tools read scope
    selectors (e.g. ``portfolio_id``) from here instead of model-supplied arguments.
    """
    if source == "seed" and is_allowlisted(slug):
        out: dict = {
            FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
            FANOUT_WORKFLOW_ID_KEY: slug,
        }
        if launch_args:
            out[FANOUT_LAUNCH_ARGS_KEY] = dict(launch_args)
        return out
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
    # Normalize ids to str on BOTH sides: scope ids come from the DB (often ints)
    # while subagents may echo int or str position ids — mismatched types would
    # wrongly mark a covered breach as failed.
    seen: dict[str, dict] = {}
    scoped = list(dict.fromkeys(str(s) for s in scoped_ids))  # de-dupe, preserve order
    scoped_set = set(scoped)
    for rec in records:
        raw = rec.get("position_id")
        pid = str(raw) if raw is not None else None
        if pid in scoped_set and pid not in seen:
            seen[pid] = rec
    out_records: list[dict] = []
    failed: list[str] = []
    for pid in scoped:
        rec = seen.get(pid)
        if rec is not None and _is_success_record(rec):
            out_records.append(rec)
        else:
            # Missing, malformed, or non-terminal-success -> explicit failed record.
            failed.append(pid)
            out_records.append({"position_id": pid, "status": "failed"})
    return {
        "records": out_records,
        "failed_ids": failed,
        "covered": len(scoped) - len(failed),
        "total": len(scoped),
    }


def _is_success_record(rec: dict) -> bool:
    """A valid per-breach success carries a severity + commentary and is not a
    failed/error terminal. Anything else is treated as failed (never silently covered)."""
    if rec.get("status") in ("failed", "error"):
        return False
    return bool(rec.get("severity")) and bool(rec.get("commentary"))
