"""Risk domain service.

Facade over ``quantark.py`` (in-memory risk calc) and ``risk_engine.py``
(persisted async risk runs).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RiskRun
from app.schemas import PortfolioPositionSpec, PricingEnvironmentSnapshot
from app.services.audit import record_audit
from app.services.domains import positions as positions_svc
from app.services.quantark import calculate_portfolio_risk, recommend_hedge as _recommend_hedge
from app.services.batch_pricing import (
    execute_batch_pricing_task,
    queue_batch_pricing,
)
from app.services.task_runner import submit_async_task

_SECONDS_PER_POSITION = 0.5


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def estimate_run_seconds(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    session: Session | None = None,
) -> float:
    """Cost estimate for ``run``. ~0.5s per position."""
    if position_ids is not None:
        return len(_normalize_position_ids(position_ids)) * _SECONDS_PER_POSITION
    with _session_scope(session) as sess:
        rows = positions_svc.list_filtered(portfolio_id=portfolio_id, session=sess)
        return len(rows) * _SECONDS_PER_POSITION


def calculate_risk(
    *,
    positions: list[PortfolioPositionSpec],
    market: PricingEnvironmentSnapshot,
) -> dict[str, Any]:
    """In-memory risk calc on a supplied position snapshot. No DB."""
    portfolio = SimpleNamespace(
        positions=[
            SimpleNamespace(id=index + 1, **position.model_dump(mode="python"))
            for index, position in enumerate(positions)
        ]
    )
    return calculate_portfolio_risk(portfolio, market)


def run(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_profile_id: int | None = None,
    method: str = "summary",
    session: Session | None = None,
) -> dict[str, Any]:
    """Queue an audited async portfolio or scoped-position risk run."""
    with _session_scope(session) as sess:
        risk_run, task = queue_batch_pricing(
            sess,
            portfolio_id=portfolio_id,
            method=method,
            position_ids=position_ids,
            pricing_parameter_profile_id=pricing_profile_id,
        )
        scoped_position_ids = risk_run.resolved_position_ids
        record_audit(
            sess,
            event_type="batch_pricing.queued",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=portfolio_id,
            payload={
                "risk_run_id": risk_run.id,
                "task_id": task.id,
                "method": method,
                "position_ids": scoped_position_ids,
                "position_count": len(scoped_position_ids)
                if scoped_position_ids is not None
                else None,
                "pricing_parameter_profile_id": pricing_profile_id,
                "source": "agent_confirmed",
            },
        )
        sess.commit()
        settings = database.settings
        submit_async_task(
            execute_batch_pricing_task,
            task.id,
            risk_run.id,
            database.SessionLocal,
            settings=settings,
        )
        return {
            "portfolio_id": portfolio_id,
            "method": method,
            "position_ids": scoped_position_ids,
            "position_count": len(scoped_position_ids)
            if scoped_position_ids is not None
            else None,
            "pricing_parameter_profile_id": risk_run.pricing_parameter_profile_id,
            "status": task.status,
            "risk_run_id": risk_run.id,
            "task_id": task.id,
            "message": "Batch pricing run queued (risk metrics + valuation). Use the Tasks page or /api/tasks/{task_id} to monitor completion.",
        }


def _normalize_position_ids(position_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    for raw_id in position_ids:
        position_id = int(raw_id)
        if position_id <= 0:
            raise ValueError("position_ids must contain positive ids")
        if position_id not in normalized:
            normalized.append(position_id)
    if not normalized:
        raise ValueError("position_ids must not be empty")
    return normalized


def get_latest_run(
    *,
    portfolio_id: int,
    session: Session | None = None,
) -> RiskRun | None:
    """Return the latest completed risk run for a portfolio, or None."""
    with _session_scope(session) as sess:
        return (
            sess.query(RiskRun)
            .filter(
                RiskRun.portfolio_id == portfolio_id,
                RiskRun.status.in_(("completed", "completed_with_errors")),
            )
            .order_by(RiskRun.created_at.desc(), RiskRun.id.desc())
            .first()
        )


def recommend_hedge(*, risk: dict[str, Any]) -> dict[str, Any]:
    """Recommend a hedge from calculated risk metrics."""
    return _recommend_hedge(risk)


__all__ = [
    "estimate_run_seconds",
    "calculate_risk",
    "run",
    "get_latest_run",
    "recommend_hedge",
]
