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
