# Batch Pricing Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `batch_pricing` task prices a portfolio once (with Greeks) and persists BOTH the per-position valuation run (Positions page) and the risk metrics (Risk page); the Tasks UI links each batch task to an inline risk-report dialog; PV (market value) and PnL render everywhere risk metrics render.

**Architecture:** New `backend/app/services/batch_pricing.py` queues a `RiskRun` + `TaskRun(kind="batch_pricing")` and an executor that mirrors the old risk executor (`calculate_portfolio_risk` one pass) then fans the per-position risk rows out into a `PositionValuationRun`. A new `POST /api/batch-pricing/runs` endpoint replaces `POST /api/portfolios/{id}/positions/price-task` and `POST /api/risk/runs` (both deleted). Frontend: Positions, Risk, and HedgeStrategy pages post to the new endpoint; Tasks page gains a `RiskReportDialog`; `GreeksSummary` gains Market Value (PV) + PnL tiles.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (repo root `tests/`, `pythonpath = ["backend"]`); React + TypeScript + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-06-06-batch-pricing-unification-design.md`

**Branch / isolation:** Work on `feature/batch-pricing-unification` (already exists, spec committed). A concurrent agent shares this repo and churns the shared HEAD — execute this plan in a **git worktree** checked out on that branch. Backend tests: run `pytest` from the worktree root (NOT `python -c`, which imports `app` from the main checkout via the venv `.pth`). Frontend tests: `cd frontend && npx vitest run <file>`.

**No DB migration:** `TaskRun.kind` is a free string; the valuation-run link rides in `TaskRun.result_payload` JSON; the existing `risk_run_id` FK covers the dialog link.

---

## File map

Backend:
- Create `backend/app/services/batch_pricing.py` — queue + executor + valuation fan-out
- Modify `backend/app/models.py:54-58` — add `TaskKind.BATCH_PRICING`
- Modify `backend/app/schemas.py` — add `BatchPricingRunRequest`; delete `RiskRunRequest` (~line 1073)
- Modify `backend/app/main.py` — add endpoint; delete `queue_portfolio_positions_pricing_endpoint` (~2629-2705) and `create_risk_run` (~2934-2971); trim imports
- Modify `backend/app/services/domains/risk.py:65-122` — `run()` queues the batch task; audit event `batch_pricing.queued`
- Modify `backend/app/services/risk_engine.py` — delete `queue_portfolio_risk` (448-493), `execute_risk_run_task` (496-505), `_execute_risk_run_task` (508-611); keep `run_portfolio_risk` (sync, test-harness), all `_`-helpers, `pricing_position_markets`
- Modify `backend/app/services/position_pricer.py` — delete `queue_position_pricing` (312-337), `execute_position_pricing_task` (340-412); trim now-unused imports

Backend tests:
- Create `tests/test_batch_pricing.py`
- Modify `tests/test_api.py` (~1655-1700 risk flow, ~1825-1860 price-task flow)
- Modify `tests/test_audit_endpoint.py` (whole file is two small tests)
- Modify `tests/test_risk_engine.py:704-773` (queue/execute import migration)
- Modify `tests/test_position_pricer_parallel.py:198-~240` (delete migrated test)
- Modify `tests/test_page_context_schema.py:53`, `tests/test_skill_rewrite_regression.py:237` (endpoint literals)

Frontend:
- Modify `frontend/src/components/GreeksSummary.tsx` + `.css` + `.test.tsx` — PV/PnL tiles, optional promote
- Modify `frontend/src/components/PnlAttribution.tsx` — optional promote
- Create `frontend/src/components/RiskReportDialog.tsx` + `.css` + `.test.tsx`
- Modify `frontend/src/routes/Tasks.tsx` + `.css` + `.test.tsx` — link button + dialog + `batch_pricing` label
- Modify `frontend/src/routes/Risk.live.tsx`, `Risk.tsx`, `Risk.live.test.tsx` — endpoint, label, declared action
- Modify `frontend/src/routes/Positions.live.tsx`, `Positions.tsx`, `Positions.live.test.tsx` — endpoint, response shape, label, declared action
- Modify `frontend/src/routes/HedgeStrategy.live.tsx:166` + `HedgeStrategy.live.test.tsx` — endpoint
- Modify `frontend/src/components/ActionProposal.tsx:143-149` — `batch_pricing` task-kind label

---

### Task 1: `TaskKind.BATCH_PRICING` + `queue_batch_pricing`

**Files:**
- Modify: `backend/app/models.py:54-58`
- Create: `backend/app/services/batch_pricing.py`
- Create: `tests/test_batch_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_pricing.py`:

```python
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.quantark import ensure_quantark_path


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path(get_settings())


def _batch_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _add_vanilla_portfolio(session, *, quantity=5.0, entry_price=8.0):
    from app.models import Portfolio, Position

    portfolio = Portfolio(name="P", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        source_trade_id="T-BATCH-1",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        quantity=quantity,
        entry_price=entry_price,
    )
    session.add(position)
    session.flush()
    return portfolio, position


def test_queue_batch_pricing_creates_linked_run_and_task(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(session)
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()

        assert run.status == "queued"
        assert run.portfolio_id == portfolio.id
        assert task.kind == "batch_pricing"
        assert task.status == "queued"
        assert task.portfolio_id == portfolio.id
        assert task.risk_run_id == run.id


def test_queue_batch_pricing_rejects_unknown_portfolio(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        with pytest.raises(ValueError, match="Portfolio not found"):
            queue_batch_pricing(session, portfolio_id=9999)


def test_queue_batch_pricing_scopes_position_ids(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import Position
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        portfolio, pos_a = _add_vanilla_portfolio(session)
        pos_b = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=2.0,
            entry_price=12.0,
        )
        session.add(pos_b)
        session.flush()

        run, _task = queue_batch_pricing(
            session, portfolio_id=portfolio.id, position_ids=[pos_b.id]
        )
        assert run.resolved_position_ids == [pos_b.id]

        with pytest.raises(ValueError, match="not in portfolio"):
            queue_batch_pricing(
                session, portfolio_id=portfolio.id, position_ids=[987654]
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch_pricing.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'app.services.batch_pricing'`

- [ ] **Step 3: Add the enum member**

In `backend/app/models.py`, change:

```python
class TaskKind(str, Enum):
    POSITION_PRICING = "position_pricing"
    RISK_RUN = "risk_run"
    REPORT_JOB = "report_job"
    HEDGE_LOAD = "hedge_instrument_load"
```

to:

```python
class TaskKind(str, Enum):
    BATCH_PRICING = "batch_pricing"
    # position_pricing / risk_run are legacy kinds: no longer created, kept so
    # historical task rows keep their labels and filters.
    POSITION_PRICING = "position_pricing"
    RISK_RUN = "risk_run"
    REPORT_JOB = "report_job"
    HEDGE_LOAD = "hedge_instrument_load"
```

- [ ] **Step 4: Create `backend/app/services/batch_pricing.py` with the queue function**

```python
"""Unified batch pricing.

One pricing pass (``calculate_portfolio_risk``, Greeks included) persists BOTH
outputs: the ``RiskRun`` metrics (Risk page) and a ``PositionValuationRun``
with per-position results (Positions page). Replaces the separate
``position_pricing`` and ``risk_run`` task paths.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    Portfolio,
    Position,
    PositionValuationResult,
    PositionValuationRun,
    RiskRun,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from ..schemas import PricingEnvironmentSnapshot
from .quantark import RISK_GREEK_KEYS, calculate_portfolio_risk
from .risk_engine import (
    _pricing_position_context,
    _resolve_risk_positions,
    _risk_completion_message,
    _risk_error_payload,
    _risk_status_from_metrics,
)
from .task_runner import (
    mark_task_finished,
    mark_task_running,
    update_task_progress,
)


def queue_batch_pricing(
    session: Session,
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    market_snapshot_id: int | None = None,
    method: str = "summary",
) -> tuple[RiskRun, TaskRun]:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    scoped_position_ids: list[int] | None = None
    if position_ids is not None:
        scoped_position_ids = [
            position.id
            for position in _resolve_risk_positions(
                portfolio,
                session,
                position_ids=position_ids,
            )
        ]
    run = RiskRun(
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        market_snapshot_id=market_snapshot_id,
        method=method,
        status=TaskStatus.QUEUED.value,
        metrics={},
        scenario_cells=None,
        resolved_position_ids=scoped_position_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.BATCH_PRICING.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio.id,
        risk_run_id=run.id,
        progress_current=0,
        progress_total=0,
        message="Queued batch pricing run",
    )
    session.add(task)
    session.flush()
    return run, task
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_batch_pricing.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/services/batch_pricing.py tests/test_batch_pricing.py
git commit -m "feat(batch-pricing): TaskKind.BATCH_PRICING + queue_batch_pricing"
```

---

### Task 2: Executor — one pass, both outputs (happy path)

**Files:**
- Modify: `backend/app/services/batch_pricing.py`
- Test: `tests/test_batch_pricing.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_batch_pricing.py`)

Uses real BlackScholes pricing with default env (spot=100, vol=0.2, r=0.03) and
NON-default quantity/entry_price so equality assertions are meaningful.

```python
def test_execute_batch_pricing_writes_both_outputs(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(
            session, quantity=5.0, entry_price=8.0
        )
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, position.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        # Risk side: metrics persisted, status synced.
        risk_run = session.get(RiskRun, run_id)
        assert risk_run.status == "completed"
        totals = risk_run.metrics["totals"]
        assert totals["market_value"] != 0.0
        risk_row = risk_run.metrics["positions"][0]
        assert risk_row["position_id"] == pos_id
        assert risk_row["pricing_ok"] is True

        # Task side: terminal status + both run ids in result_payload.
        task = session.get(TaskRun, task_id)
        assert task.status == "completed"
        assert task.progress_total == 1
        assert task.progress_current == 1
        assert task.result_payload["risk_run_id"] == run_id
        valuation_run_id = task.result_payload["valuation_run_id"]

        # Valuation side: same pass fanned out into a PositionValuationRun.
        valuation_run = session.get(PositionValuationRun, valuation_run_id)
        assert valuation_run.portfolio_id == portfolio.id
        assert valuation_run.status == "completed"
        assert valuation_run.summary["positions"] == 1
        assert valuation_run.summary["priced"] == 1
        assert valuation_run.summary["failed"] == 0
        # Summary totals mirror the risk totals (same single pass).
        assert valuation_run.summary["market_value"] == pytest.approx(
            totals["market_value"]
        )
        assert valuation_run.summary["pnl"] == pytest.approx(totals["pnl"])
        assert valuation_run.summary["delta"] == pytest.approx(totals["delta"])
        assert valuation_run.summary["vega"] == pytest.approx(totals["vega"])

        result = valuation_run.results[0]
        assert result.position_id == pos_id
        assert result.source_trade_id == "T-BATCH-1"
        assert result.ok is True
        assert result.price == pytest.approx(risk_row["price"])
        # quantity=5 (non-default) pins the scaling
        assert result.market_value == pytest.approx(risk_row["price"] * 5.0)
        # entry_price=8 (non-default) pins pnl = (price - entry) * qty
        assert result.pnl == pytest.approx((risk_row["price"] - 8.0) * 5.0)
        for greek in ("delta", "gamma", "vega", "theta", "rho", "rho_q"):
            assert result.result_payload[greek] == pytest.approx(risk_row[greek])
        assert result.market_inputs["spot"] == pytest.approx(100.0)
        assert result.market_inputs["volatility"] == pytest.approx(0.20)
        assert result.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch_pricing.py::test_execute_batch_pricing_writes_both_outputs -v`
Expected: FAIL with `ImportError: cannot import name 'execute_batch_pricing_task'`

- [ ] **Step 3: Implement the executor** (append to `backend/app/services/batch_pricing.py`)

```python
def execute_batch_pricing_task(
    task_id: int,
    risk_run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute_batch_pricing_task(session, task_id, risk_run_id)
    finally:
        session.close()


def _execute_batch_pricing_task(
    session: Session, task_id: int, risk_run_id: int
) -> None:
    try:
        run = session.get(RiskRun, risk_run_id)
        if run is None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=f"Risk run not found: {risk_run_id}",
            )
            session.commit()
            return
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=f"Portfolio not found: {run.portfolio_id}",
            )
            session.commit()
            return

        resolved = _resolve_risk_positions(
            portfolio,
            session,
            position_ids=run.resolved_position_ids,
        )
        position_ids = [p.id for p in resolved]
        run.resolved_position_ids = position_ids
        total = len(position_ids)
        mark_task_running(
            session,
            task_id,
            message=f"Pricing {total} positions",
            total=total,
        )
        session.commit()

        portfolio_like = SimpleNamespace(
            id=portfolio.id,
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            positions=resolved,
        )

        def _progress(current: int, total_positions: int) -> None:
            update_task_progress(
                session,
                task_id,
                current=current,
                total=total_positions,
                message=f"Priced {current} of {total_positions} positions",
            )
            session.commit()

        position_markets, pricing_failures, pricing_diagnostics = (
            _pricing_position_context(
                session,
                resolved,
                pricing_parameter_profile_id=run.pricing_parameter_profile_id,
                # RiskRun has no valuation_date column; created_at keeps
                # assumption/quote resolution as-of queue time (same contract
                # the old risk executor had).
                valuation_date=run.created_at,
            )
        )
        metrics = calculate_portfolio_risk(
            portfolio_like,  # type: ignore[arg-type]
            position_markets=position_markets,
            pricing_failures=pricing_failures,
            pricing_diagnostics=pricing_diagnostics,
            max_workers=get_settings().risk_parallel_workers,
            progress_callback=_progress,
        )
        status = _risk_status_from_metrics(metrics)
        run.metrics = metrics
        run.status = status

        valuation_run = _persist_valuation_run(
            session,
            run=run,
            resolved=resolved,
            metrics=metrics,
            position_markets=position_markets,
            pricing_diagnostics=pricing_diagnostics,
        )

        update_task_progress(
            session,
            task_id,
            current=total,
            total=total,
            message=_risk_completion_message(metrics, status),
        )
        result_payload: dict[str, Any] = {
            "risk_run_id": run.id,
            "valuation_run_id": valuation_run.id,
            **(_risk_error_payload(metrics) or {}),
        }
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=_risk_completion_message(metrics, status),
            result_payload=result_payload,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(RiskRun, risk_run_id)
        if run is not None:
            run.status = TaskStatus.FAILED.value
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.FAILED.value,
            message="Batch pricing run failed",
            error=str(exc),
        )
        session.commit()


_MARKET_DIAGNOSTIC_KEYS = (
    "market_input_source",
    "quote_age_days",
    "pricing_parameter_profile_id",
    "pricing_parameter_row_id",
    "pricing_parameter_match_type",
    "missing_pricing_fields",
)


def _market_inputs_for_position(
    market: PricingEnvironmentSnapshot | None,
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Valuation-result market_inputs from the risk pass's resolved snapshot.

    Mirrors the shape the Positions page reads (spot/rate/dividend_yield/
    volatility/valuation_date + source diagnostics)."""
    inputs: dict[str, Any] = {}
    if market is not None:
        inputs.update(
            {
                "valuation_date": market.valuation_date.isoformat(),
                "spot": market.spot,
                "rate": market.rate,
                "dividend_yield": market.dividend_yield,
                "volatility": market.volatility,
                "asset_name": market.asset_name,
            }
        )
    if diagnostics:
        for key in _MARKET_DIAGNOSTIC_KEYS:
            if key in diagnostics:
                inputs[key] = diagnostics[key]
    return inputs


def _persist_valuation_run(
    session: Session,
    *,
    run: RiskRun,
    resolved: list[Position],
    metrics: dict[str, Any],
    position_markets: dict[int, PricingEnvironmentSnapshot],
    pricing_diagnostics: dict[int, dict[str, Any]],
) -> PositionValuationRun:
    """Fan the per-position risk rows out into a PositionValuationRun."""
    rows_by_id = {
        row.get("position_id"): row for row in (metrics.get("positions") or [])
    }
    overrides: dict[str, Any] = {}
    if run.pricing_parameter_profile_id is not None:
        overrides["pricing_parameter_profile_id"] = run.pricing_parameter_profile_id
    valuation_run = PositionValuationRun(
        portfolio_id=run.portfolio_id,
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
        market_source_path=None,
        valuation_date=run.created_at,
        overrides=overrides,
        summary={},
        status="running",
        resolved_position_ids=[p.id for p in resolved],
    )
    session.add(valuation_run)
    session.flush()

    totals = {
        "positions": 0,
        "priced": 0,
        "failed": 0,
        "market_value": 0.0,
        "pnl": 0.0,
        "delta": 0.0,
        "vega": 0.0,
    }
    for position in resolved:
        row = rows_by_id.get(position.id)
        if row is None:
            continue
        totals["positions"] += 1
        ok = bool(row.get("pricing_ok"))
        result_payload: dict[str, Any] = {
            greek: float(row.get(greek) or 0.0) for greek in RISK_GREEK_KEYS
        }
        if row.get("gross_notional") is not None:
            result_payload["gross_notional"] = row["gross_notional"]
        if row.get("greeks_error"):
            result_payload["greeks_error"] = row["greeks_error"]
        session.add(
            PositionValuationResult(
                valuation_run_id=valuation_run.id,
                position_id=position.id,
                source_trade_id=row.get("source_trade_id"),
                ok=ok,
                price=row.get("price") if ok else None,
                market_value=row.get("market_value") if ok else None,
                pnl=row.get("pnl") if ok else None,
                market_inputs=_market_inputs_for_position(
                    position_markets.get(position.id),
                    pricing_diagnostics.get(position.id),
                ),
                result_payload=result_payload,
                error=row.get("pricing_error"),
            )
        )
        if ok:
            totals["priced"] += 1
            totals["market_value"] += float(row.get("market_value") or 0.0)
            totals["pnl"] += float(row.get("pnl") or 0.0)
            totals["delta"] += float(row.get("delta") or 0.0)
            totals["vega"] += float(row.get("vega") or 0.0)
        else:
            totals["failed"] += 1
    valuation_run.summary = totals
    valuation_run.status = (
        "completed" if totals["failed"] == 0 else "completed_with_errors"
    )
    session.flush()
    return valuation_run
```

Note: `Position` must be added to the `..models` import list in this module
(it is already in the import block written in Task 1 — verify).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_batch_pricing.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/batch_pricing.py tests/test_batch_pricing.py
git commit -m "feat(batch-pricing): executor persists RiskRun metrics + PositionValuationRun in one pass"
```

---

### Task 3: Executor failure path (`completed_with_errors`)

**Files:**
- Test: `tests/test_batch_pricing.py` (no implementation change expected)

- [ ] **Step 1: Write the test** (append to `tests/test_batch_pricing.py`)

```python
def test_execute_batch_pricing_completed_with_errors(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services import batch_pricing
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "underlying": position.underlying,
                    "product_type": position.product_type,
                    "price": 0.0,
                    "market_value": 0.0,
                    "pnl": 0.0,
                    "pricing_ok": False,
                    "pricing_error": "no market quote for AAPL",
                    "greeks_ok": True,
                    "greeks_error": None,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        batch_pricing, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(session)
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, position.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert task.status == "completed_with_errors"
        # Both run ids AND the structured error payload coexist.
        assert task.result_payload["risk_run_id"] == run_id
        failing = task.result_payload["errors"]["positions"]
        assert failing[0]["position_id"] == pos_id
        assert failing[0]["pricing_error"] == "no market quote for AAPL"

        risk_run = session.get(RiskRun, run_id)
        assert risk_run.status == "completed_with_errors"

        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        assert valuation_run.status == "completed_with_errors"
        assert valuation_run.summary["failed"] == 1
        result = valuation_run.results[0]
        assert result.ok is False
        assert result.price is None
        assert result.error == "no market quote for AAPL"
```

- [ ] **Step 2: Run test — expect PASS** (the Task 2 implementation already covers this)

Run: `pytest tests/test_batch_pricing.py -v`
Expected: 5 PASS. If it fails, fix the executor (not the test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_batch_pricing.py
git commit -m "test(batch-pricing): completed_with_errors fans out to task, risk run, and valuation run"
```

---

### Task 4: API — `POST /api/batch-pricing/runs`, delete old POST endpoints

**Files:**
- Modify: `backend/app/schemas.py` (add request model ~line 1073, delete `RiskRunRequest`)
- Modify: `backend/app/main.py`
- Test: `tests/test_api.py`, `tests/test_audit_endpoint.py`

- [ ] **Step 1: Update `tests/test_audit_endpoint.py`**

Replace both POSTs to `/api/risk/runs` with the new endpoint and event type.
The file has two tests; change:

```python
        "/api/risk/runs", json={"portfolio_id": portfolio["id"], "method": "summary"}
```
→ (both occurrences)
```python
        "/api/batch-pricing/runs", json={"portfolio_id": portfolio["id"]}
```

and:
```python
    assert len(events) >= 2  # portfolio.created + risk.run.queued at minimum
...
    assert "risk.run.queued" in types
...
    res = client.get("/api/audit/events?event_type=risk.run.queued")
...
    assert events[0]["event_type"] == "risk.run.queued"
```
→
```python
    assert len(events) >= 2  # portfolio.created + batch_pricing.queued at minimum
...
    assert "batch_pricing.queued" in types
...
    res = client.get("/api/audit/events?event_type=batch_pricing.queued")
...
    assert events[0]["event_type"] == "batch_pricing.queued"
```

- [ ] **Step 2: Update `tests/test_api.py` risk flow (~lines 1655-1700)**

In the test that currently POSTs `/api/risk/runs`:

```python
    risk = client.post(
        "/api/risk/runs",
        json={"portfolio_id": portfolio["id"], "method": "summary"},
    )
```
→
```python
    risk = client.post(
        "/api/batch-pricing/runs",
        json={"portfolio_id": portfolio["id"]},
    )
```

After the existing `task = wait_task(...)` assertions, add the unification
assertions:

```python
    assert task["kind"] == "batch_pricing"
    assert task["result_payload"]["risk_run_id"] == risk_run_id
    # The SAME task also produced a valuation run for the Positions page.
    valuation_run_id = task["result_payload"]["valuation_run_id"]
    runs = client.get(f"/api/portfolios/{portfolio['id']}/runs")
    assert runs.status_code == 200
    assert any(run["id"] == valuation_run_id for run in runs.json())
```

- [ ] **Step 3: Update `tests/test_api.py` price-task flow (~lines 1825-1860)**

Replace the `price-task` block:

```python
    queued_pricing = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price-task",
        json={
            "valuation_date": "2026-04-30T00:00:00",
            "spot": 101.0,
            "r": 0.02,
            "q": 0.03,
            "vol": 0.25,
        },
    )
    assert queued_pricing.status_code == 200
    queued_task = queued_pricing.json()
    assert queued_task["kind"] == "position_pricing"
    assert queued_task["portfolio_id"] == portfolio["id"]
    completed_task = wait_task(client, queued_task["id"])
    assert completed_task["status"] == "completed"
    assert completed_task["portfolio_id"] == portfolio["id"]
```
with:
```python
    queued_pricing = client.post(
        "/api/batch-pricing/runs",
        json={"portfolio_id": portfolio["id"]},
    )
    assert queued_pricing.status_code == 200
    queued_run = queued_pricing.json()
    assert queued_run["task_id"]
    completed_task = wait_task(client, queued_run["task_id"])
    assert completed_task["kind"] == "batch_pricing"
    assert completed_task["status"] in {"completed", "completed_with_errors"}
    assert completed_task["portfolio_id"] == portfolio["id"]
    # Old endpoints are gone.
    assert client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price-task", json={}
    ).status_code in {404, 405}
    assert client.post(
        "/api/risk/runs", json={"portfolio_id": portfolio["id"]}
    ).status_code in {404, 405}
```

Keep the following `runs = client.get(...)` assertion but loosen it (the old
overrides-based assertion `summary["priced"] == 1` may now match the batch
run): keep `assert any(run["summary"].get("priced", 0) >= 1 for run in runs.json())`.

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_audit_endpoint.py "tests/test_api.py::"<the two updated test names>"" -v`
(Use the actual test function names found at those line ranges.)
Expected: FAIL with 404 on `/api/batch-pricing/runs`

- [ ] **Step 5: Add the request schema** in `backend/app/schemas.py`

Replace `RiskRunRequest` (~line 1073):

```python
class RiskRunRequest(BaseModel):
    portfolio_id: int
    position_ids: list[int] | None = Field(default=None, min_length=1)
    pricing_parameter_profile_id: int | None = None
    market_snapshot_id: int | None = None
    method: Literal["summary", "var_proxy"] = "summary"
```
with:
```python
class BatchPricingRunRequest(BaseModel):
    portfolio_id: int
    position_ids: list[int] | None = Field(default=None, min_length=1)
    pricing_parameter_profile_id: int | None = None
    market_snapshot_id: int | None = None
```

- [ ] **Step 6: Rewire `backend/app/main.py`**

1. In the big `.schemas` import block: replace `RiskRunRequest` with
   `BatchPricingRunRequest`.
2. In the `.services.position_pricer` import (lines 183-188): remove
   `execute_position_pricing_task` and `queue_position_pricing` (keep
   `MarketOverrides`, `price_portfolio_positions`).
3. Delete the whole `queue_portfolio_positions_pricing_endpoint` function
   (the `@app.post(".../positions/price-task")` block, ~lines 2629-2705).
4. Replace the `create_risk_run` function (~lines 2934-2971) with:

```python
    @app.post("/api/batch-pricing/runs", response_model=RiskRunOut)
    def create_batch_pricing_run(
        payload: BatchPricingRunRequest, session: Session = Depends(get_db)
    ):
        from .services.batch_pricing import (
            execute_batch_pricing_task,
            queue_batch_pricing,
        )

        try:
            run, task = queue_batch_pricing(
                session,
                portfolio_id=payload.portfolio_id,
                position_ids=payload.position_ids,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                market_snapshot_id=payload.market_snapshot_id,
            )
        except ValueError as exc:
            status_code = 404 if "Portfolio not found" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="batch_pricing.queued",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=run.portfolio_id,
            payload={
                "risk_run_id": run.id,
                "task_id": task.id,
                "position_ids": run.resolved_position_ids,
                "pricing_parameter_profile_id": run.pricing_parameter_profile_id,
            },
        )
        session.commit()
        submit_async_task(
            execute_batch_pricing_task,
            task.id,
            run.id,
            database.SessionLocal,
            settings=active_settings,
        )
        return _risk_run_out(run)
```

(`GET /api/risk/runs/{run_id}` and `GET .../risk-runs/latest` directly below
stay untouched.)

- [ ] **Step 7: Run the updated tests**

Run: `pytest tests/test_audit_endpoint.py tests/test_api.py -v -k "risk or pricing or audit"`
Expected: PASS (and no import errors elsewhere in test_api.py)

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_api.py tests/test_audit_endpoint.py
git commit -m "feat(api): POST /api/batch-pricing/runs replaces price-task and risk/runs POSTs"
```

---

### Task 5: Rewire agent path; delete old task paths; migrate their tests

**Files:**
- Modify: `backend/app/services/domains/risk.py:65-122`
- Modify: `backend/app/services/risk_engine.py` (delete 448-611: `queue_portfolio_risk`, `execute_risk_run_task`, `_execute_risk_run_task`)
- Modify: `backend/app/services/position_pricer.py` (delete 312-412: `queue_position_pricing`, `execute_position_pricing_task`)
- Test: `tests/test_risk_engine.py:704-773`, `tests/test_position_pricer_parallel.py:198+`

- [ ] **Step 1: Migrate `tests/test_risk_engine.py::test_risk_run_completed_with_errors_records_result_payload`**

In that test (lines 704-773), change:

```python
    from app.services import risk_engine
    from app.services.risk_engine import execute_risk_run_task, queue_portfolio_risk
```
→
```python
    from app.services import batch_pricing
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )
```

```python
    monkeypatch.setattr(
        risk_engine, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )
```
→
```python
    monkeypatch.setattr(
        batch_pricing, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )
```

```python
        run, task = queue_portfolio_risk(
            session, portfolio_id=portfolio.id, method="summary"
        )
```
→
```python
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
```

```python
    execute_risk_run_task(task_id, run_id, session_factory=database.SessionLocal)
```
→
```python
    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)
```

(The other `test_risk_run_*` tests use the **sync** `run_portfolio_risk`,
which stays — do not touch them.)

- [ ] **Step 2: Delete `tests/test_position_pricer_parallel.py::test_execute_position_pricing_task_updates_progress`**

Remove the whole function (starts line 198). Its behavior (task progress
updates) is covered by `test_execute_batch_pricing_writes_both_outputs`
(progress_total/progress_current assertions).

- [ ] **Step 3: Rewire `backend/app/services/domains/risk.py`**

Replace the import:
```python
from app.services.risk_engine import execute_risk_run_task, queue_portfolio_risk
```
→
```python
from app.services.batch_pricing import (
    execute_batch_pricing_task,
    queue_batch_pricing,
)
```

In `run()`, replace:
```python
        risk_run, task = queue_portfolio_risk(
            sess,
            portfolio_id=portfolio_id,
            method=method,
            position_ids=position_ids,
            pricing_parameter_profile_id=pricing_profile_id,
        )
```
→
```python
        risk_run, task = queue_batch_pricing(
            sess,
            portfolio_id=portfolio_id,
            method=method,
            position_ids=position_ids,
            pricing_parameter_profile_id=pricing_profile_id,
        )
```

Change the audit `event_type="risk.run.queued"` → `event_type="batch_pricing.queued"`
(keep all payload keys including `"source": "agent_confirmed"`).

Replace the submit:
```python
        submit_async_task(
            execute_risk_run_task,
            task.id,
            risk_run.id,
            database.SessionLocal,
            settings=settings,
        )
```
→
```python
        submit_async_task(
            execute_batch_pricing_task,
            task.id,
            risk_run.id,
            database.SessionLocal,
            settings=settings,
        )
```

Update the returned `message` to:
```python
            "message": "Batch pricing run queued (risk metrics + valuation). Use the Tasks page or /api/tasks/{task_id} to monitor completion.",
```

(The `run_risk` agent tool keeps its name and schema — no prompt/skill/
allowlist churn.)

- [ ] **Step 4: Delete the old task paths**

In `backend/app/services/risk_engine.py`:
- Delete `queue_portfolio_risk` (lines 448-493), `execute_risk_run_task`
  (496-505), `_execute_risk_run_task` (508-611).
- Keep `run_portfolio_risk`, `_resolve_risk_positions`,
  `_pricing_position_context`, `pricing_position_markets`,
  `_risk_status_from_metrics`, `_risk_completion_message`,
  `_risk_error_payload`, `compute_position_greeks`, `compute_portfolio_greeks`.
- Trim imports that are now unused: `sessionmaker` (from sqlalchemy.orm),
  `database`, `TaskKind`, `TaskRun`, and the whole
  `from .task_runner import (...)` block — verify each with a grep inside the
  file before removing.

In `backend/app/services/position_pricer.py`:
- Delete `queue_position_pricing` (lines 312-337) and
  `execute_position_pricing_task` (340-412).
- Trim now-unused imports: `TaskKind`, `TaskRun`, `TaskStatus` from
  `..models`, and `from .task_runner import mark_task_finished,
  mark_task_running, update_task_progress` — verify with grep first.

- [ ] **Step 5: Run the touched suites**

Run: `pytest tests/test_risk_engine.py tests/test_position_pricer_parallel.py tests/test_batch_pricing.py tests/test_api.py -q`
Expected: PASS. Then guard against stragglers:

Run: `grep -rn "queue_portfolio_risk\|execute_risk_run_task\|queue_position_pricing\|execute_position_pricing_task" backend/app tests`
Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/risk.py backend/app/services/risk_engine.py backend/app/services/position_pricer.py tests/test_risk_engine.py tests/test_position_pricer_parallel.py
git commit -m "refactor(batch-pricing): agent run_risk queues unified task; delete legacy task paths"
```

---

### Task 6: Endpoint-literal cleanups + full backend suite

**Files:**
- Modify: `tests/test_page_context_schema.py:53`
- Modify: `tests/test_skill_rewrite_regression.py:237`

- [ ] **Step 1: Update the literals** (both are schema-fixture strings, not live calls)

`tests/test_page_context_schema.py:53` and
`tests/test_skill_rewrite_regression.py:237`:
```python
        backend_endpoint="POST /api/risk/runs",
```
→
```python
        backend_endpoint="POST /api/batch-pricing/runs",
```

- [ ] **Step 2: Run the two files**

Run: `pytest tests/test_page_context_schema.py tests/test_skill_rewrite_regression.py -q`
Expected: PASS

- [ ] **Step 3: Run the FULL backend suite**

Run: `pytest -q`
Expected: all pass (~1230 tests; note there is a known pre-existing
env-failure list — compare any failures against `git stash` / main before
blaming this change).

- [ ] **Step 4: Commit**

```bash
git add tests/test_page_context_schema.py tests/test_skill_rewrite_regression.py
git commit -m "test: update declared-action endpoint literals to /api/batch-pricing/runs"
```

---

### Task 7: `GreeksSummary` — Market Value (PV) + PnL tiles, optional promote

**Files:**
- Modify: `frontend/src/components/GreeksSummary.tsx`
- Modify: `frontend/src/components/GreeksSummary.css`
- Test: `frontend/src/components/GreeksSummary.test.tsx`

- [ ] **Step 1: Write the failing tests** (append to `GreeksSummary.test.tsx`, reusing the file's existing totals fixtures)

```tsx
  it('renders Market Value (PV) and PnL tiles from totals', () => {
    render(
      <GreeksSummary
        totals={{
          market_value: 1234.56,
          pnl: -78.9,
          delta_proxy: 0,
          gross_notional: 0,
          one_day_var_proxy: 0,
        }}
        onPromoteToReport={vi.fn()}
      />,
    );
    expect(screen.getByText('Market Value (PV)')).toBeInTheDocument();
    expect(screen.getByText('PnL')).toBeInTheDocument();
    expect(screen.getByText('+1,234.56')).toBeInTheDocument();
    expect(screen.getByText('-78.90')).toBeInTheDocument();
  });

  it('renders PV and PnL inside each currency bucket', () => {
    render(
      <GreeksSummary
        totals={null}
        byCurrency={{
          CNY: { market_value: 11.5, pnl: 2.25, vega: 1 },
          USD: { market_value: -3.75, pnl: 0.5, vega: 2 },
        }}
        onPromoteToReport={vi.fn()}
      />,
    );
    expect(screen.getAllByText('Market Value (PV)')).toHaveLength(2);
    expect(screen.getAllByText('PnL')).toHaveLength(2);
  });

  it('hides the promote button when onPromoteToReport is omitted', () => {
    render(
      <GreeksSummary
        totals={{
          market_value: 1,
          pnl: 1,
          delta_proxy: 0,
          gross_notional: 0,
          one_day_var_proxy: 0,
        }}
      />,
    );
    expect(screen.queryByLabelText('Promote to Report')).not.toBeInTheDocument();
  });
```

(Adjust expected formatted strings to whatever `formatSignedNumber` produces —
check an existing assertion in this file for the exact format, e.g. thousands
separators and sign prefix, before finalizing.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/GreeksSummary.test.tsx`
Expected: FAIL (`Market Value (PV)` not found)

- [ ] **Step 3: Implement**

In `GreeksSummary.tsx`:

1. Props: `onPromoteToReport?: () => void;`
2. `GreekTiles` gains the two leading tiles:

```tsx
function GreekTiles({ greeks }: { greeks: CurrencyGreeks }) {
  const marketValue = greeks.market_value ?? 0;
  const pnl = greeks.pnl ?? 0;
  const deltaCash = greeks.delta_cash ?? greeks.delta ?? greeks.delta_proxy ?? 0;
  const gammaCash = greeks.gamma_cash ?? greeks.gamma ?? 0;
  const vega = greeks.vega ?? 0;
  const theta = greeks.theta ?? 0;
  const rho = greeks.rho ?? 0;
  const rhoQ = greeks.rho_q ?? 0;

  return (
    <div className="wl-greeks__tiles">
      <Tile label="Market Value (PV)" value={formatSignedNumber(marketValue)} variant={marketValue >= 0 ? 'pos' : 'neg'} />
      <Tile label="PnL" value={formatSignedNumber(pnl)} variant={pnl >= 0 ? 'pos' : 'neg'} />
      <Tile label="Delta Cash" value={formatSignedNumber(deltaCash)} variant={deltaCash >= 0 ? 'pos' : 'neg'} />
      <Tile label="Gamma Cash" value={formatSignedNumber(gammaCash)} variant={gammaCash >= 0 ? 'pos' : 'neg'} />
      <Tile label="Vega" value={formatSignedNumber(vega)} variant={vega >= 0 ? 'pos' : 'neg'} />
      <Tile label="Theta" value={formatSignedNumber(theta)} variant={theta >= 0 ? 'pos' : 'neg'} />
      <Tile label="Rho" value={formatSignedNumber(rho)} variant={rho >= 0 ? 'pos' : 'neg'} />
      <Tile label="RhoQ" value={formatSignedNumber(rhoQ)} variant={rhoQ >= 0 ? 'pos' : 'neg'} />
    </div>
  );
}
```

3. Promote button renders conditionally:

```tsx
        {onPromoteToReport && (
          <button
            type="button"
            className="wl-greeks__promote"
            aria-label="Promote to Report"
            onClick={onPromoteToReport}
          >
            ↗
          </button>
        )}
```

4. Skeleton block: render 8 `<Skeleton height={56} />` instead of 6.

In `GreeksSummary.css`, change the tile grids from 6 to 4 columns (8 tiles =
2 clean rows):

```css
.wl-greeks__tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--gap-2);
}
.wl-greeks__skeletons { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--gap-2); }
```
(Keep the existing 760px/480px media-query overrides as they are.)

- [ ] **Step 4: Run the component tests (existing + new)**

Run: `cd frontend && npx vitest run src/components/GreeksSummary.test.tsx`
Expected: PASS. If existing tests assert exact tile counts/order, update them
to include the two new leading tiles.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/GreeksSummary.tsx frontend/src/components/GreeksSummary.css frontend/src/components/GreeksSummary.test.tsx
git commit -m "feat(risk-ui): Market Value (PV) + PnL tiles in GreeksSummary; promote button optional"
```

---

### Task 8: `RiskReportDialog` (+ optional promote on `PnlAttribution`)

**Files:**
- Modify: `frontend/src/components/PnlAttribution.tsx`
- Create: `frontend/src/components/RiskReportDialog.tsx`
- Create: `frontend/src/components/RiskReportDialog.css`
- Test: `frontend/src/components/RiskReportDialog.test.tsx`

- [ ] **Step 1: Make `PnlAttribution.onPromoteToReport` optional**

In `PnlAttribution.tsx` change the prop type and conditionally render the
button:

```tsx
type Props = {
  positions: AttributionPosition[];
  onPromoteToReport?: () => void;
};
```
and wrap the existing promote `<button …className="wl-attr__promote"…>` in
`{onPromoteToReport && ( … )}`.

- [ ] **Step 2: Write the failing dialog test**

Create `frontend/src/components/RiskReportDialog.test.tsx`:

```tsx
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RiskReportDialog } from './RiskReportDialog';

const run = {
  id: 7,
  portfolio_id: 3,
  method: 'summary',
  status: 'completed',
  created_at: '2026-06-06T08:00:00Z',
  metrics: {
    totals: {
      market_value: 1500.5,
      pnl: 250.25,
      delta_proxy: 0,
      gross_notional: 0,
      one_day_var_proxy: 0,
      vega: 12,
    },
    by_currency: null,
    positions: [
      {
        position_id: 11,
        source_trade_id: 'T-77',
        underlying: 'AAPL',
        product_type: 'EuropeanVanillaOption',
        quantity: 5,
        price: 10.5,
        market_value: 52.5,
        gross_notional: 500,
        pnl: 12.5,
        delta_proxy: 0.5,
        pricing_ok: true,
        pricing_error: null,
      },
    ],
  },
};

function mockFetch(payload: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => { vi.restoreAllMocks(); });

describe('RiskReportDialog', () => {
  it('fetches the risk run and shows PV, PnL, and attribution', async () => {
    const fetchMock = mockFetch(run);
    render(<RiskReportDialog riskRunId={7} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Market Value (PV)')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith('/api/risk/runs/7', expect.anything());
    expect(screen.getByText('PnL')).toBeInTheDocument();
    expect(screen.getByText(/RISK REPORT · RUN #7/)).toBeInTheDocument();
    expect(screen.getByText(/Portfolio #3/)).toBeInTheDocument();
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('does not fetch when closed', () => {
    const fetchMock = mockFetch(run);
    render(<RiskReportDialog riskRunId={7} open={false} onClose={() => {}} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an error message when the fetch fails', async () => {
    mockFetch({ detail: 'boom' }, false);
    render(<RiskReportDialog riskRunId={7} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Could not load risk run/)).toBeInTheDocument());
  });
});
```

(Check how `api()` in `frontend/src/api/client.ts` issues requests — if it
passes no init object, relax the `toHaveBeenCalledWith` to match the actual
call signature.)

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/RiskReportDialog.test.tsx`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement `RiskReportDialog.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Modal } from './Modal';
import { Skeleton } from './Skeleton';
import { GreeksSummary, type CurrencyGreeks, type GreeksTotals } from './GreeksSummary';
import { PnlAttribution, type AttributionPosition } from './PnlAttribution';
import './RiskReportDialog.css';

export type RiskReportRun = {
  id: number;
  portfolio_id: number;
  method: string;
  status: string;
  created_at: string;
  metrics: {
    totals?: GreeksTotals | null;
    by_currency?: Record<string, CurrencyGreeks> | null;
    positions?: AttributionPosition[];
  };
};

type Props = {
  riskRunId: number | null;
  open: boolean;
  onClose: () => void;
};

export function RiskReportDialog({ riskRunId, open, onClose }: Props) {
  const [run, setRun] = useState<RiskReportRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || riskRunId == null) return undefined;
    let cancelled = false;
    setRun(null);
    setError(null);
    api<RiskReportRun>(`/api/risk/runs/${riskRunId}`)
      .then((result) => { if (!cancelled) setRun(result); })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [open, riskRunId]);

  return (
    <Modal
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title={riskRunId != null ? `RISK REPORT · RUN #${riskRunId}` : 'RISK REPORT'}
      layoutKey="risk-report"
    >
      <div className="wl-risk-report">
        {error ? (
          <p className="wl-risk-report__error">Could not load risk run: {error}</p>
        ) : !run ? (
          <Skeleton height={220} />
        ) : (
          <>
            <div className="wl-risk-report__meta">
              <span>Portfolio #{run.portfolio_id}</span>
              <span>{run.method}</span>
              <span>{run.status.replaceAll('_', ' ')}</span>
              <span>{formatDateTime(run.created_at)}</span>
            </div>
            <GreeksSummary
              totals={run.metrics.totals ?? null}
              byCurrency={run.metrics.by_currency ?? null}
            />
            <PnlAttribution positions={run.metrics.positions ?? []} />
          </>
        )}
      </div>
    </Modal>
  );
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
```

Create `RiskReportDialog.css` (match the app's flat token style):

```css
.wl-risk-report { display: flex; flex-direction: column; gap: var(--gap-3); min-width: 560px; max-width: 860px; }
.wl-risk-report__meta { display: flex; gap: var(--gap-3); flex-wrap: wrap; color: var(--ink-2); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.wl-risk-report__error { color: var(--negative, #b00020); margin: 0; }
```
(Check `frontend/src/components/TaskErrorDialog.css` for the actual token
names used for muted/negative colors and mirror them.)

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/components/RiskReportDialog.test.tsx src/components/PnlAttribution.test.tsx 2>/dev/null || npx vitest run src/components/RiskReportDialog.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/RiskReportDialog.tsx frontend/src/components/RiskReportDialog.css frontend/src/components/RiskReportDialog.test.tsx frontend/src/components/PnlAttribution.tsx
git commit -m "feat(tasks-ui): RiskReportDialog renders a risk run with PV fields"
```

---

### Task 9: Tasks page — risk link opens the dialog

**Files:**
- Modify: `frontend/src/routes/Tasks.tsx`
- Modify: `frontend/src/routes/Tasks.css`
- Test: `frontend/src/routes/Tasks.test.tsx`

- [ ] **Step 1: Write the failing tests** (append to `Tasks.test.tsx`, following the file's existing fixture helpers)

```tsx
  it('labels batch_pricing tasks', () => {
    // Render a task with kind: 'batch_pricing' using the existing fixture
    // pattern in this file, then:
    expect(screen.getByText(/Batch pricing/)).toBeInTheDocument();
  });

  it('opens the risk report dialog when the risk link is clicked', async () => {
    // Mock fetch for /api/risk/runs/2 returning a completed run with
    // metrics.totals.market_value, then render a task with risk_run_id: 2.
    await userEvent.click(screen.getByRole('button', { name: 'Risk #2' }));
    await waitFor(() => expect(screen.getByText('Market Value (PV)')).toBeInTheDocument());
  });

  it('renders no risk button for tasks without risk_run_id', () => {
    expect(screen.queryByRole('button', { name: /Risk #/ })).not.toBeInTheDocument();
  });
```

(Write these against the concrete fixture helpers already in the file — read
`Tasks.test.tsx:1-85` first and reuse its `render`/fixture conventions.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/routes/Tasks.test.tsx`
Expected: new tests FAIL

- [ ] **Step 3: Implement in `Tasks.tsx`**

1. Imports: add `RiskReportDialog`.
2. State: `const [riskReportRunId, setRiskReportRunId] = useState<number | null>(null);`
3. Links column render becomes:
   `render: (task) => <LinkCell task={task} onOpenRiskReport={setRiskReportRunId} />,`
   and the `columns` `useMemo` deps stay `[]` (the setter is stable).
4. `LinkCell` becomes:

```tsx
function LinkCell({
  task,
  onOpenRiskReport,
}: {
  task: TaskRun;
  onOpenRiskReport: (riskRunId: number) => void;
}) {
  return (
    <div className="wl-tasks__links">
      {task.portfolio_id != null && <span>Portfolio #{task.portfolio_id}</span>}
      {task.risk_run_id != null && (
        <button
          type="button"
          className="wl-tasks__link-button"
          onClick={() => onOpenRiskReport(task.risk_run_id!)}
        >
          Risk #{task.risk_run_id}
        </button>
      )}
      {task.report_job_id != null && <span>Report #{task.report_job_id}</span>}
    </div>
  );
}
```

5. Render the dialog next to `TaskErrorDialog`:

```tsx
      <RiskReportDialog
        riskRunId={riskReportRunId}
        open={riskReportRunId !== null}
        onClose={() => setRiskReportRunId(null)}
      />
```

6. `labelKind` gains: `if (kind === 'batch_pricing') return 'Batch pricing';`
   (keep the legacy labels).

7. `Tasks.css` add a link-button style consistent with
   `.wl-tasks__error-button` (look at that rule and mirror it):

```css
.wl-tasks__link-button {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent, inherit);
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/routes/Tasks.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Tasks.tsx frontend/src/routes/Tasks.css frontend/src/routes/Tasks.test.tsx
git commit -m "feat(tasks-ui): risk link opens inline risk report dialog; batch_pricing label"
```

---

### Task 10: Risk page → new endpoint + label

**Files:**
- Modify: `frontend/src/routes/Risk.live.tsx:228`
- Modify: `frontend/src/routes/Risk.tsx:118-122,190-192`
- Test: `frontend/src/routes/Risk.live.test.tsx`

- [ ] **Step 1: Update `Risk.live.test.tsx`**

Replace every `'/api/risk/runs'` POST-mock/assertion URL with
`'/api/batch-pricing/runs'` (lines ~207-208, 218, 238-239; GET mocks for
`/api/risk/runs/8` etc. stay). Also update any button-name queries from
`Run Risk` to `Run Batch Pricing` (search the file for `Run Risk`).

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/routes/Risk.live.test.tsx`
Expected: FAIL (still posting to the old URL / old label)

- [ ] **Step 3: Implement**

`Risk.live.tsx` `runRiskForPortfolio`:
```tsx
      const res = await api<RiskRunResponse>('/api/batch-pricing/runs', {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });
```
and slim the request body type (the endpoint has no `method` field):
```tsx
    const requestBody: { portfolio_id: number; pricing_parameter_profile_id?: number } = {
      portfolio_id: portfolioId,
    };
```

`Risk.tsx`:
- Button: `{running ? 'Batch Running' : 'Run Batch Pricing ⌘R'}`
- Declared action: `backend_endpoint: 'POST /api/batch-pricing/runs'` (keep
  `name: 'run_risk'` — the agent tool name is unchanged).

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/routes/Risk.live.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Risk.live.tsx frontend/src/routes/Risk.tsx frontend/src/routes/Risk.live.test.tsx
git commit -m "feat(risk-ui): Run Batch Pricing via /api/batch-pricing/runs"
```

---

### Task 11: Positions page + HedgeStrategy + ActionProposal label

**Files:**
- Modify: `frontend/src/routes/Positions.live.tsx:159-177`
- Modify: `frontend/src/routes/Positions.tsx:298-303,578-582`
- Modify: `frontend/src/routes/HedgeStrategy.live.tsx:166`
- Modify: `frontend/src/components/ActionProposal.tsx:143-149`
- Test: `frontend/src/routes/Positions.live.test.tsx`, `frontend/src/routes/HedgeStrategy.live.test.tsx`

- [ ] **Step 1: Update `Positions.live.test.tsx`**

The price-task mocks (lines ~324, ~354) become batch-pricing mocks returning a
**run** (not a task):

```tsx
      if (url === '/api/batch-pricing/runs' && init?.method === 'POST') {
        return response({ id: 8, portfolio_id: 1, status: 'queued', task_id: 99, metrics: {}, created_at: '2026-06-06T00:00:00Z' });
      }
      if (url === '/api/tasks/99') return response(completedPricingTask);
```
Update assertions that referenced the old URL, and any button queries from
`Run Pricing` → `Run Batch Pricing`.

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/routes/Positions.live.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement `Positions.live.tsx`**

Replace `handleRunPricing`:

```tsx
  const handleRunPricing = async () => {
    if (!portfolio) return;
    const portfolioId = portfolio.id;
    const requestId = ++latestPricingTaskRequestIdRef.current;
    try {
      const run = await api<{ id: number; status: string; task_id: number | null }>(
        '/api/batch-pricing/runs',
        {
          method: 'POST',
          body: JSON.stringify({
            portfolio_id: portfolioId,
            ...pricingProfileRequestBody(selectedPricingProfileId),
          }),
        },
      );
      if (run.task_id != null && ACTIVE_TASK_STATUSES.has(run.status)) {
        setFeedback(`Task #${run.task_id} queued: batch pricing started`);
        void pollPricingTask(run.task_id, portfolioId, importPortfolioId, requestId);
        return;
      }
      setFeedback(`Batch pricing run #${run.id} ${run.status.replaceAll('_', ' ')}`);
      await load(true, portfolioId, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not run batch pricing: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
```
(`pollPricingTask`, `taskFeedback`, and `load` are unchanged; `TaskRun` import
stays — it is still used by `fetchTask`.)

`Positions.tsx`:
- Button label: `Run Batch Pricing` (keep the `<Calculator>` icon).
- Declared action `price_portfolio_positions` →
  `backend_endpoint: 'POST /api/batch-pricing/runs'`.

`HedgeStrategy.live.tsx:166`: `'/api/risk/runs'` → `'/api/batch-pricing/runs'`
(POST only; the GET `/api/risk/runs/${id}` polls stay).

`HedgeStrategy.live.test.tsx`: update the POST mock keys/assertions (lines
~63, ~182, ~218) from `/api/risk/runs` POST to `/api/batch-pricing/runs` —
careful: line 63's `'/api/risk/runs'` key may also serve GET lookups; split if
needed so GET `/api/risk/runs/15` keeps working.

`ActionProposal.tsx` `taskKindLabel`: add as the first line
`if (kind === 'batch_pricing') return 'Batch pricing run';`

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/routes/Positions.live.test.tsx src/routes/HedgeStrategy.live.test.tsx src/components/ActionProposal.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Positions.live.tsx frontend/src/routes/Positions.tsx frontend/src/routes/HedgeStrategy.live.tsx frontend/src/routes/HedgeStrategy.live.test.tsx frontend/src/routes/Positions.live.test.tsx frontend/src/components/ActionProposal.tsx
git commit -m "feat(positions-ui): Run Batch Pricing via unified endpoint; hedge tab + labels follow"
```

---

### Task 12: Full verification

- [ ] **Step 1: Full backend suite**

Run: `pytest -q`
Expected: pass (modulo the documented pre-existing env failures — verify any
failure also occurs on the branch base before attributing it).

- [ ] **Step 2: Full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: pass. Also run `npx tsc --noEmit -p .` if the repo has a typecheck
script (`grep '"typecheck"' frontend/package.json` — use it if present).

- [ ] **Step 3: Residual-reference sweep**

Run:
```bash
grep -rn "price-task" backend frontend/src tests
grep -rn "POST /api/risk/runs\|'/api/risk/runs'" frontend/src | grep -v "/api/risk/runs/"
```
Expected: no matches (GET `/api/risk/runs/{id}` references are fine).

- [ ] **Step 4: Final commit (if any stragglers were fixed)**

```bash
git add -A
git commit -m "chore(batch-pricing): final sweep after unification"
```

---

## Self-review checklist (done at planning time)

- **Spec coverage:** unified task (Tasks 1-3), new endpoint + deletions + audit (Task 4), agent tool rewiring + legacy-path removal (Task 5), Tasks dialog link (Tasks 8-9), PV on Risk page + dialog (Tasks 7-8), page rewiring incl. the spec-missed HedgeStrategy caller (Tasks 10-11), tests throughout.
- **Out of scope honored:** sync `POST .../positions/price`, `price_positions` agent tool, scenarios endpoint, report jobs untouched. `run_portfolio_risk` (sync) deliberately kept — 17 tests use it as the profile-resolution harness.
- **Type consistency:** `queue_batch_pricing` returns `(RiskRun, TaskRun)` everywhere; executor signature `(task_id, risk_run_id, session_factory)` matches all call sites; frontend dialog consumes `GET /api/risk/runs/{id}` (unchanged endpoint).
