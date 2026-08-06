# Report Module — Sub-project B1 (continued): Limits, Desk & Governance Blocks

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the block registry with the limits, scenario, RFQ, position and governance producers, so every key referenced by the four seeded templates resolves.

**Architecture:** Same pattern as `2026-08-06-report-module-b-registry-and-blocks.md` Tasks 2–3 — a module-level `_load_*` function that tests patch, one producer per key returning the tri-state `BlockResult`, registration asserted by shape.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.2
**Depends on:** `2026-08-06-report-module-b-registry-and-blocks.md` Tasks 1–3 complete and green.

## Global Constraints

- **Numbers never come from an LLM.** No LLM call appears anywhere in this plan.
- **Producers never raise for missing data.** Return `BlockResult.empty(reason)` or `.unavailable(reason)`.
- **`empty` vs `unavailable` is load-bearing.** `scenario.latest_grid` is the canonical `unavailable` case: `scenario_test_runs` has zero rows in this database, and the report must say the stress check did not run rather than implying no stress was found.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail`.
- **`_session_scope` pattern** as in `backend/app/services/domains/risk.py:29-36`.

---

### Task 4: Limits and scenario blocks

**Files:**
- Modify: `backend/app/services/reporting/blocks/limits.py` (currently empty)
- Test: `tests/test_reporting_blocks_limits.py`

**Interfaces:**
- Consumes: `report_block`, `BlockContext`, `BlockResult`, `BlockShape`
- Produces four registered keys:
  - `limits.utilization` → `SCALARS_WITH_PRIOR`, data `{rows: [{scope_label, observed_value, utilization, headroom, governing_boundary, status}], worst_utilization}`
  - `limits.breaches` → `ITEMS`, data `{items: [{scope_label, status, observed_value, governing_boundary, reason}]}`
  - `limits.incidents` → `ROWS`, data `{rows: [{incident_id, scope_label, severity, status, first_seen_at, owner, row_version}]}`
  - `scenario.latest_grid` → `ROWS`, data `{rows: [...]}` — returns `unavailable` while `scenario_test_runs` is empty
- Module-level loaders tests patch: `_load_latest_evaluations(ctx)`, `_load_incidents(ctx)`, `_load_latest_scenario_run(ctx)`

**Domain rule:** an evaluation whose `status` is `unknown` or `incomplete_scope` is **not** a pass. `limits.breaches` counts those separately as `indeterminate`, because "we could not evaluate this limit" must never render as "this limit is fine".

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_blocks_limits.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def _evaluation(scope_label, status, observed=800.0, utilization=1.34,
                headroom=-200.0, boundary="hard_upper", reason=None):
    return {
        "scope_label": scope_label, "status": status,
        "observed_value": observed, "utilization": utilization,
        "headroom": headroom, "governing_boundary": boundary,
        "reason": reason, "reason_code": None,
        "coverage_ratio": 1.0,
    }


def _incident(incident_id=1, status="open", severity="hard"):
    return {
        "incident_id": incident_id, "scope_label": "Desk Control Book / net delta",
        "severity": severity, "status": status,
        "first_seen_at": "2026-08-05T09:00:00", "last_seen_at": "2026-08-06T09:00:00",
        "owner": "desk_user", "row_version": 3,
    }


def test_all_four_blocks_are_registered_with_declared_shapes():
    assert get_block("limits.utilization").shape.value == "scalars_with_prior"
    assert get_block("limits.breaches").shape.value == "items"
    assert get_block("limits.incidents").shape.value == "rows"
    assert get_block("scenario.latest_grid").shape.value == "rows"


def test_utilization_reports_rows_and_the_worst_offender(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "breach", utilization=1.34),
         _evaluation("vega", "ok", utilization=0.42, headroom=500.0)],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.utilization", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert len(result.data["rows"]) == 2
    assert result.data["worst_utilization"] == pytest.approx(1.34)
    assert result.provenance["monitoring_run_id"] == 2


def test_breaches_are_items_when_present(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "breach"), _evaluation("vega", "ok")],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert [item["scope_label"] for item in result.data["items"]] == ["net delta"]
    assert result.data["indeterminate_count"] == 0


def test_no_breaches_is_EMPTY_not_unavailable(monkeypatch):
    """The check ran and found nothing. That is a real, affirmative result."""
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "ok", utilization=0.3)],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "empty"
    assert "no limit" in result.reason.lower()


def test_no_monitoring_run_is_UNAVAILABLE_not_empty(monkeypatch):
    """The check did not run. That must never read as 'the book is fine'."""
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (None, {}))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "unavailable"
    assert "monitoring" in result.reason.lower()


def test_indeterminate_evaluations_are_counted_not_treated_as_passes(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "unknown", reason="missing:spot"),
         _evaluation("vega", "incomplete_scope", reason="2 of 5 priced")],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["items"] == []
    assert result.data["indeterminate_count"] == 2
    assert len(result.data["indeterminate"]) == 2


def test_incidents_are_rows_with_row_version_for_optimistic_locking(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_incidents",
                        lambda ctx: ([_incident()], {"portfolio_id": 2}))
    result = resolve_block("limits.incidents", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["rows"][0]["row_version"] == 3
    assert result.data["open_count"] == 1


def test_no_incidents_is_empty(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_incidents", lambda ctx: ([], {}))
    result = resolve_block("limits.incidents", BlockContext(portfolio_id=2))
    assert result.status == "empty"


def test_scenario_grid_is_unavailable_while_no_stress_runs_exist(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (None, {}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    assert result.status == "unavailable"
    assert "scenario" in result.reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_limits.py -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'shape'`

- [ ] **Step 3: Write minimal implementation**

Replace `backend/app/services/reporting/blocks/limits.py`:

```python
"""Limits, incident and stress block producers.

The tri-state matters most here. A limit evaluation that could not be computed
(`unknown`, `incomplete_scope`) is neither a pass nor a breach: it is counted
as indeterminate and surfaced, because "we could not evaluate this limit" must
never render as "this limit is fine".
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import (
    LimitEvaluation,
    LimitIncident,
    LimitMonitoringRun,
    ScenarioTestRun,
)

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_BREACH_STATUSES = frozenset({"breach", "hard_breach", "warning"})
_INDETERMINATE_STATUSES = frozenset({"unknown", "incomplete_scope"})

_NO_MONITORING = (
    "no limit monitoring run exists for this portfolio, so limit status could "
    "not be evaluated"
)
_NO_SCENARIO = (
    "no scenario test run exists for this portfolio, so no stress grid could "
    "be reported"
)


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _load_latest_evaluations(
    ctx: BlockContext,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Latest monitoring run's evaluations, as plain dicts."""
    with _session_scope() as session:
        run = (
            session.query(LimitMonitoringRun)
            .filter(LimitMonitoringRun.portfolio_id == ctx.portfolio_id)
            .order_by(LimitMonitoringRun.id.desc())
            .first()
        )
        if run is None:
            return None, {}
        rows = (
            session.query(LimitEvaluation)
            .filter(LimitEvaluation.monitoring_run_id == run.id)
            .all()
        )
        evaluations = [
            {
                "scope_label": row.scope_label,
                "status": row.status,
                "observed_value": row.observed_value,
                "utilization": row.utilization,
                "headroom": row.headroom,
                "governing_boundary": row.governing_boundary,
                "reason": row.reason,
                "reason_code": row.reason_code,
                "coverage_ratio": row.coverage_ratio,
            }
            for row in rows
        ]
        return evaluations, {
            "monitoring_run_id": run.id,
            "evaluated_at": rows[0].evaluated_at.isoformat() if rows else None,
        }


def _load_incidents(
    ctx: BlockContext,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(LimitIncident)
            .filter(LimitIncident.portfolio_id == ctx.portfolio_id)
            .order_by(LimitIncident.first_seen_at.desc(), LimitIncident.id.desc())
            .all()
        )
        incidents = [
            {
                "incident_id": row.id,
                "scope_label": row.scope_label,
                "severity": row.severity,
                "status": row.status,
                "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "owner": row.owner,
                "row_version": row.row_version,
            }
            for row in rows
        ]
        return incidents, {"portfolio_id": ctx.portfolio_id}


def _load_latest_scenario_run(
    ctx: BlockContext,
) -> tuple[Any | None, dict[str, Any]]:
    with _session_scope() as session:
        run = (
            session.query(ScenarioTestRun)
            .filter(ScenarioTestRun.portfolio_id == ctx.portfolio_id)
            .order_by(ScenarioTestRun.id.desc())
            .first()
        )
        if run is None:
            return None, {}
        return run, {"scenario_test_run_id": run.id}


@report_block(
    key="limits.utilization", title="Limit utilisation",
    shape=BlockShape.SCALARS_WITH_PRIOR, domain="limits",
    description="Per-limit observed value, utilisation and headroom from the latest monitoring run.",
)
def limits_utilization(ctx: BlockContext) -> BlockResult:
    evaluations, provenance = _load_latest_evaluations(ctx)
    if evaluations is None:
        return BlockResult.unavailable(_NO_MONITORING)
    if not evaluations:
        return BlockResult.empty(
            "the monitoring run evaluated no limits", provenance=provenance
        )
    utilizations = [
        row["utilization"] for row in evaluations
        if isinstance(row.get("utilization"), (int, float))
    ]
    return BlockResult.ok(
        data={
            "rows": evaluations,
            "worst_utilization": max(utilizations) if utilizations else None,
        },
        provenance=provenance,
    )


@report_block(
    key="limits.breaches", title="Limit breaches", shape=BlockShape.ITEMS,
    domain="limits",
    description="Limits currently in breach or warning, plus any that could not be evaluated.",
)
def limits_breaches(ctx: BlockContext) -> BlockResult:
    evaluations, provenance = _load_latest_evaluations(ctx)
    if evaluations is None:
        return BlockResult.unavailable(_NO_MONITORING)

    breached = [
        row for row in evaluations if (row.get("status") or "") in _BREACH_STATUSES
    ]
    indeterminate = [
        row for row in evaluations
        if (row.get("status") or "") in _INDETERMINATE_STATUSES
    ]

    if not breached and not indeterminate:
        return BlockResult.empty(
            "no limit is in breach or warning on the latest monitoring run",
            provenance=provenance,
        )

    return BlockResult.ok(
        data={
            "items": breached,
            "breach_count": len(breached),
            "indeterminate": indeterminate,
            "indeterminate_count": len(indeterminate),
        },
        provenance=provenance,
    )


@report_block(
    key="limits.incidents", title="Limit incidents", shape=BlockShape.ROWS,
    domain="limits",
    description="Limit incidents for this portfolio with their lifecycle status.",
)
def limits_incidents(ctx: BlockContext) -> BlockResult:
    incidents, provenance = _load_incidents(ctx)
    if not incidents:
        return BlockResult.empty(
            "no limit incident has been raised for this portfolio",
            provenance=provenance,
        )
    open_count = sum(
        1 for row in incidents
        if row.get("status") in ("open", "acknowledged")
    )
    return BlockResult.ok(
        data={"rows": incidents, "open_count": open_count,
              "total_count": len(incidents)},
        provenance=provenance,
    )


@report_block(
    key="scenario.latest_grid", title="Latest stress grid", shape=BlockShape.ROWS,
    domain="limits",
    description="Results of the most recent scenario test run for this portfolio.",
)
def scenario_latest_grid(ctx: BlockContext) -> BlockResult:
    run, provenance = _load_latest_scenario_run(ctx)
    if run is None:
        return BlockResult.unavailable(_NO_SCENARIO)
    results = getattr(run, "results", None) or {}
    rows = results.get("rows") or results.get("cells") or []
    if not rows:
        return BlockResult.empty(
            "the scenario run recorded no result rows", provenance=provenance
        )
    return BlockResult.ok(data={"rows": list(rows)}, provenance=provenance)


__all__ = [
    "limits_utilization",
    "limits_breaches",
    "limits_incidents",
    "scenario_latest_grid",
]
```

Before running, confirm the model class names resolve:

```bash
.venv/bin/python -c "from app.models import LimitEvaluation, LimitIncident, LimitMonitoringRun, ScenarioTestRun; print('ok')"
```

If any name differs, correct the import to the real class name — the underlying tables are
`limit_evaluations`, `limit_incidents`, `limit_monitoring_runs`, `scenario_test_runs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_limits.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/blocks/limits.py \
        tests/test_reporting_blocks_limits.py
git commit -m "feat(reporting): limits, incident and stress block producers"
```

---

### Task 5: RFQ, position and governance blocks

**Files:**
- Modify: `backend/app/services/reporting/blocks/desk.py` (currently empty)
- Test: `tests/test_reporting_blocks_desk.py`

**Interfaces:**
- Consumes: `report_block`, `diff_metrics`, `load_run_pair`
- Produces five registered keys:
  - `rfq.open_pipeline` → `ROWS`, data `{rows: [{rfq_id, client_name, status, created_at, latest_price}], total_count}`
  - `rfq.pending_approvals` → `ROWS`, data `{rows: [{rfq_id, version, created_by, created_at}], total_count}`
  - `positions.changes` → `ROWS`, data `{added: [...], removed: [...], added_count, removed_count}`
  - `positions.barrier_proximity` → `ITEMS`, data `{items: [{position_id, underlying, nearest_barrier_kind, nearest_barrier_level, days_to_nearest}]}`
  - `audit.write_actions_summary` → `ROWS`, data `{rows: [{tool_name, kind, status, actor, persona, count}], total_actions, denied_count, window_days}`
- Module-level loaders tests patch: `_load_open_rfqs`, `_load_pending_approvals`, `_load_barrier_states`, `_load_write_actions`, `_load_run_pair_metrics`

**Domain rule:** `audit.write_actions_summary` aggregates by `(tool_name, kind, status)` over a
window (default 1 day, `params.window_days`) and reports `denied_count` separately — a board
needs to see refused actions, not only successful ones.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_blocks_desk.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def test_all_five_blocks_are_registered_with_declared_shapes():
    assert get_block("rfq.open_pipeline").shape.value == "rows"
    assert get_block("rfq.pending_approvals").shape.value == "rows"
    assert get_block("positions.changes").shape.value == "rows"
    assert get_block("positions.barrier_proximity").shape.value == "items"
    assert get_block("audit.write_actions_summary").shape.value == "rows"


def test_open_pipeline_lists_live_rfqs(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_open_rfqs", lambda ctx: [
        {"rfq_id": 12, "client_name": "Acme", "status": "priced",
         "created_at": "2026-08-06T09:00:00", "latest_price": 34.19},
    ])
    result = resolve_block("rfq.open_pipeline", BlockContext(portfolio_id=1))
    assert result.status == "ok"
    assert result.data["rows"][0]["rfq_id"] == 12
    assert result.data["total_count"] == 1


def test_open_pipeline_with_no_live_rfqs_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_open_rfqs", lambda ctx: [])
    result = resolve_block("rfq.open_pipeline", BlockContext(portfolio_id=1))
    assert result.status == "empty"


def test_positions_changes_reports_added_and_removed(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_run_pair_metrics", lambda ctx: (
        {"positions": [{"position_id": 1, "underlying": "AAPL", "market_value": 1.0}],
         "valuation_as_of": "2026-08-04T00:00:00"},
        {"positions": [{"position_id": 2, "underlying": "TSLA", "market_value": 2.0}],
         "valuation_as_of": "2026-08-05T00:00:00"},
        {"risk_run_id": 36, "compare_to_run_id": 35},
    ))
    result = resolve_block("positions.changes",
                           BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "ok"
    assert [row["position_id"] for row in result.data["added"]] == [2]
    assert [row["position_id"] for row in result.data["removed"]] == [1]
    assert result.data["added_count"] == 1


def test_positions_changes_with_identical_membership_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    same = {"positions": [{"position_id": 1, "underlying": "AAPL", "market_value": 1.0}],
            "valuation_as_of": "2026-08-04T00:00:00"}
    monkeypatch.setattr(desk, "_load_run_pair_metrics",
                        lambda ctx: (same, same, {"risk_run_id": 36}))
    result = resolve_block("positions.changes",
                           BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "empty"


def test_barrier_proximity_sorts_nearest_first(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_barrier_states", lambda ctx: [
        {"position_id": 5, "underlying": "TSLA", "nearest_barrier_kind": "knock_out",
         "nearest_barrier_level": 120.0, "nearest_barrier_date": "2026-09-01",
         "days_to_nearest": 26},
        {"position_id": 4, "underlying": "AAPL", "nearest_barrier_kind": "knock_in",
         "nearest_barrier_level": 80.0, "nearest_barrier_date": "2026-08-10",
         "days_to_nearest": 4},
    ])
    result = resolve_block("positions.barrier_proximity", BlockContext(portfolio_id=1))
    assert result.status == "ok"
    assert [item["position_id"] for item in result.data["items"]] == [4, 5]


def test_write_actions_summary_aggregates_and_counts_denials(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_write_actions", lambda ctx, window_days: [
        {"tool_name": "book_position", "kind": "execution", "status": "ok",
         "actor": "desk_user", "persona": "trader"},
        {"tool_name": "book_position", "kind": "execution", "status": "ok",
         "actor": "desk_user", "persona": "trader"},
        {"tool_name": "book_hedge", "kind": "execution", "status": "denied",
         "actor": "desk_user", "persona": "risk_manager"},
    ])
    result = resolve_block("audit.write_actions_summary",
                           BlockContext(portfolio_id=1, params={"window_days": 1}))
    assert result.status == "ok"
    assert result.data["total_actions"] == 3
    assert result.data["denied_count"] == 1
    booked = next(r for r in result.data["rows"] if r["tool_name"] == "book_position")
    assert booked["count"] == 2
    assert result.data["window_days"] == 1


def test_write_actions_summary_with_no_activity_is_empty(monkeypatch):
    from app.services.reporting.blocks import desk

    monkeypatch.setattr(desk, "_load_write_actions", lambda ctx, window_days: [])
    result = resolve_block("audit.write_actions_summary", BlockContext(portfolio_id=1))
    assert result.status == "empty"
    assert "no write-class" in result.reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_desk.py -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'shape'`

- [ ] **Step 3: Write minimal implementation**

Replace `backend/app/services/reporting/blocks/desk.py`:

```python
"""Desk-activity and governance block producers.

``audit.write_actions_summary`` is the governance block: the fail-closed audit
trail records every write-class action an agent took, and until now nothing in
the product reported on it. A board needs refused actions surfaced alongside
successful ones, so denials are counted separately rather than filtered out.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RFQ, AgentActionAudit, Position, PositionBarrierState, RFQQuoteVersion
from app.services.pnl import diff_metrics, load_run_pair

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_LIVE_RFQ_STATUSES = ("draft", "requested", "priced", "quoted", "pending")
_DEFAULT_WINDOW_DAYS = 1
_NO_PAIR = "no prior completed risk run to compare membership against"


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _load_open_rfqs(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(RFQ)
            .filter(RFQ.status.in_(_LIVE_RFQ_STATUSES))
            .order_by(RFQ.created_at.desc())
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            quote = row.quote_payload or {}
            out.append({
                "rfq_id": row.id,
                "client_name": row.client_name,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "latest_price": quote.get("unit_price") or quote.get("achieved_price"),
            })
        return out


def _load_pending_approvals(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(RFQQuoteVersion)
            .filter(RFQQuoteVersion.approved_at.is_(None))
            .filter(RFQQuoteVersion.status.in_(("priced", "pending_approval")))
            .order_by(RFQQuoteVersion.created_at.desc())
            .all()
        )
        return [
            {
                "rfq_id": row.rfq_id,
                "version": row.version,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            }
            for row in rows
        ]


def _load_barrier_states(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(PositionBarrierState, Position)
            .join(Position, Position.id == PositionBarrierState.position_id)
            .filter(Position.portfolio_id == ctx.portfolio_id)
            .all()
        )
        return [
            {
                "position_id": state.position_id,
                "underlying": position.underlying,
                "nearest_barrier_kind": state.nearest_barrier_kind,
                "nearest_barrier_level": state.nearest_barrier_level,
                "nearest_barrier_date": (
                    state.nearest_barrier_date.isoformat()
                    if state.nearest_barrier_date else None
                ),
                "days_to_nearest": state.days_to_nearest,
            }
            for state, position in rows
        ]


def _load_write_actions(
    ctx: BlockContext, window_days: int
) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=window_days
    )
    with _session_scope() as session:
        rows = (
            session.query(AgentActionAudit)
            .filter(AgentActionAudit.occurred_at >= since)
            .order_by(AgentActionAudit.occurred_at.desc())
            .all()
        )
        return [
            {
                "tool_name": row.tool_name,
                "kind": row.kind,
                "status": row.status,
                "actor": row.actor,
                "persona": row.persona,
            }
            for row in rows
        ]


def _load_run_pair_metrics(
    ctx: BlockContext,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    with _session_scope() as session:
        before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id,
            risk_run_id=ctx.risk_run_id,
            compare_to_run_id=ctx.compare_to_run_id,
            session=session,
        )
        if before is None or after is None:
            return None, None, {}
        return (
            before.metrics or {},
            after.metrics or {},
            {"risk_run_id": after.id, "compare_to_run_id": before.id},
        )


@report_block(
    key="rfq.open_pipeline", title="Open RFQ pipeline", shape=BlockShape.ROWS,
    domain="rfq", description="Client RFQs that are still live.",
)
def rfq_open_pipeline(ctx: BlockContext) -> BlockResult:
    rows = _load_open_rfqs(ctx)
    if not rows:
        return BlockResult.empty("no client RFQ is currently live")
    return BlockResult.ok(data={"rows": rows, "total_count": len(rows)})


@report_block(
    key="rfq.pending_approvals", title="Quotes awaiting approval",
    shape=BlockShape.ROWS, domain="rfq",
    description="Quote versions priced but not yet approved or released.",
)
def rfq_pending_approvals(ctx: BlockContext) -> BlockResult:
    rows = _load_pending_approvals(ctx)
    if not rows:
        return BlockResult.empty("no quote version is awaiting approval")
    return BlockResult.ok(data={"rows": rows, "total_count": len(rows)})


@report_block(
    key="positions.changes", title="Book changes", shape=BlockShape.ROWS,
    requires=("compare_to_run_id",), domain="positions",
    description="Positions added to or removed from the book between two governed runs.",
)
def positions_changes(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)

    diff = diff_metrics(before, after)
    before_rows = {
        int(row["position_id"]): row for row in before.get("positions") or []
        if row.get("position_id") is not None
    }
    after_rows = {
        int(row["position_id"]): row for row in after.get("positions") or []
        if row.get("position_id") is not None
    }

    added = [
        {"position_id": pid,
         "underlying": (after_rows.get(pid) or {}).get("underlying"),
         "market_value": (after_rows.get(pid) or {}).get("market_value")}
        for pid in diff["membership"]["added"]
    ]
    removed = [
        {"position_id": pid,
         "underlying": (before_rows.get(pid) or {}).get("underlying"),
         "market_value": (before_rows.get(pid) or {}).get("market_value")}
        for pid in diff["membership"]["removed"]
    ]

    if not added and not removed:
        return BlockResult.empty(
            "the book held the same positions across both runs",
            provenance=provenance,
        )

    return BlockResult.ok(
        data={"added": added, "removed": removed,
              "added_count": len(added), "removed_count": len(removed)},
        provenance=provenance,
    )


@report_block(
    key="positions.barrier_proximity", title="Barrier and KO watch",
    shape=BlockShape.ITEMS, domain="positions",
    description="Positions ranked by how close they sit to their nearest barrier.",
)
def positions_barrier_proximity(ctx: BlockContext) -> BlockResult:
    rows = _load_barrier_states(ctx)
    if not rows:
        return BlockResult.empty(
            "no position in this book carries a tracked barrier"
        )
    rows.sort(
        key=lambda row: (
            row["days_to_nearest"] if row.get("days_to_nearest") is not None else 10**9
        )
    )
    return BlockResult.ok(data={"items": rows, "total_count": len(rows)})


@report_block(
    key="audit.write_actions_summary", title="Agent actions taken",
    shape=BlockShape.ROWS, domain="audit",
    description="Write-class agent actions from the audit trail, aggregated by tool and outcome.",
)
def audit_write_actions_summary(ctx: BlockContext) -> BlockResult:
    window_days = int(ctx.params.get("window_days") or _DEFAULT_WINDOW_DAYS)
    actions = _load_write_actions(ctx, window_days)
    if not actions:
        return BlockResult.empty(
            f"no write-class agent action was recorded in the last {window_days} day(s)"
        )

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    denied = 0
    for action in actions:
        if action.get("status") == "denied":
            denied += 1
        key = (
            action.get("tool_name"), action.get("kind"), action.get("status"),
            action.get("actor"), action.get("persona"),
        )
        row = grouped.setdefault(
            key,
            {"tool_name": key[0], "kind": key[1], "status": key[2],
             "actor": key[3], "persona": key[4], "count": 0},
        )
        row["count"] += 1

    rows = sorted(grouped.values(), key=lambda row: row["count"], reverse=True)
    return BlockResult.ok(
        data={"rows": rows, "total_actions": len(actions),
              "denied_count": denied, "window_days": window_days}
    )


__all__ = [
    "rfq_open_pipeline",
    "rfq_pending_approvals",
    "positions_changes",
    "positions_barrier_proximity",
    "audit_write_actions_summary",
]
```

Before running, confirm the model class names resolve:

```bash
.venv/bin/python -c "from app.models import RFQ, AgentActionAudit, Position, PositionBarrierState, RFQQuoteVersion; print('ok')"
```

If any name differs, correct the import — the underlying tables are `rfqs`,
`agent_action_audits`, `positions`, `position_barrier_state`, `rfq_quote_versions`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_desk.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Verify the full registry is populated**

Run:

```bash
.venv/bin/python -c "
from app.services.reporting import blocks
from app.services.reporting.registry import list_blocks
keys = [s.key for s in list_blocks()]
print(len(keys)); [print(' ', k) for k in keys]
"
```

Expected: 18 keys — `audit.write_actions_summary`, `coverage.evidence`, `limits.breaches`,
`limits.incidents`, `limits.utilization`, `pnl.by_position`, `pnl.daily`, `pnl.explain`,
`pnl.inception`, `positions.barrier_proximity`, `positions.changes`, `rfq.open_pipeline`,
`rfq.pending_approvals`, `risk.exposure_by_underlying`, `risk.exposure_diff`,
`risk.greeks_by_bucket`, `risk.totals`, `scenario.latest_grid`.

- [ ] **Step 6: Run every reporting suite together**

Run: `.venv/bin/python -m pytest tests/test_reporting_registry.py tests/test_reporting_blocks_risk.py tests/test_reporting_blocks_pnl.py tests/test_reporting_blocks_limits.py tests/test_reporting_blocks_desk.py -q`
Expected: PASS, 41 tests

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/reporting/blocks/desk.py \
        tests/test_reporting_blocks_desk.py
git commit -m "feat(reporting): RFQ, position and governance block producers"
```

---

## Self-Review

**Spec coverage (§5.2 block table):** all 18 declared keys are now registered — five in
`risk.py` (B1 Task 2), four in `pnl.py` (B1 Task 3), four in `limits.py` (Task 4), five in
`desk.py` (Task 5). Step 5 of Task 5 asserts the exact count and key list so a missed producer
fails visibly rather than surfacing later as an unresolvable template.

**Placeholder scan:** No TBD/TODO. The two "confirm the model class names resolve" steps are
runnable commands with a stated fallback, not vague instructions — the ORM class names for
these tables could not be verified from the table inventory alone, so the plan makes the check
explicit rather than guessing and failing at import time.

**Type consistency:**
- `report_block(key=, title=, shape=, requires=, domain=, description=)` — keyword-only,
  matching B1 Task 1.
- `BlockResult.ok(data=...)`, `.empty(reason, provenance=...)`, `.unavailable(reason)` — matches
  Plan A Task 1 constructors.
- `_load_run_pair_metrics(ctx) -> (before | None, after | None, provenance)` — same arity in
  `risk.py`, `pnl.py` and `desk.py`, and patched with that arity in all three test files.
- `_load_write_actions(ctx, window_days)` takes two positional arguments in both the
  implementation and the test's monkeypatch lambda.
- `diff_metrics(before, after)` argument order matches Plan A Task 2.
- `ctx.params.get(...)` used for `top_n` and `window_days`; both are declared as optional
  template params, not `requires` entries, so a template may omit them.
