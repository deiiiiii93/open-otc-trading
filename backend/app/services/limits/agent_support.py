"""Derivation of a monitoring envelope from the latest completed risk run.

Shared by the ``run_limit_monitoring`` agent tool and the arena determinism
driver so the live tool path and the harvested path are byte-identical.

Why these defaults: ``source_planner._matches_identity`` requires EXACT
equality on profile, engine, snapshot, evidence id, and the envelope
``valuation_as_of`` versus the source run's valuation, and
``source_planner._is_fresh`` treats profile-dated runs as fresh only when no
age cap is set — so the envelope must mirror the source run's identity
verbatim for ``reuse_only`` selection to succeed.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import RiskRun
from .errors import LimitValidationError
from .source_planner import source_valuation_at

_TERMINAL = ("completed", "completed_with_errors")


def derive_monitoring_envelope(session: Session, portfolio_id: int) -> dict[str, Any]:
    """Return ``queue_limit_monitoring`` kwargs mirroring the latest risk run."""
    run = session.execute(
        select(RiskRun)
        .where(
            RiskRun.portfolio_id == portfolio_id,
            RiskRun.status.in_(_TERMINAL),
        )
        .order_by(RiskRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        raise LimitValidationError(
            f"no completed risk run for portfolio {portfolio_id} — run risk first"
        )
    metadata = dict((run.metrics or {}).get("source_metadata") or {})
    evidence_id = metadata.get("effective_market_evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        raise LimitValidationError(
            f"latest risk run {run.id} has no market-evidence id — re-run risk first"
        )
    return {
        "pricing_parameter_profile_id": run.pricing_parameter_profile_id,
        "engine_config_id": run.engine_config_id,
        "market_snapshot_id": None,
        "effective_market_evidence_id": evidence_id,
        "valuation_as_of": source_valuation_at(run),
        "max_source_age_seconds": None,
    }
