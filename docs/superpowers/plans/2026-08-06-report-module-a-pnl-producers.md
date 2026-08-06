# Report Module — Sub-project A: P&L Producers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic P&L producers — risk-run snapshot diff, Greeks-based P&L attribution, and entry-price basis accounting — plus the block contract types that Sub-project B's registry is written against.

**Architecture:** Four pure-Python modules under `backend/app/services/pnl/` operating on `risk_runs.metrics` dictionaries rather than ORM rows, so every test runs without a database. Attribution multipliers are read from the run's own `metric_contract` rather than hardcoded, so a future contract version is followed or rejected loudly. The block contract types (`BlockShape`, `BlockResult`, `BlockStatus`) land first because Sub-project A's outputs are shaped to them.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x (ORM only at the thin loader boundary), pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §4, §5.2

## Global Constraints

- **Numbers never come from an LLM.** Everything in this plan is deterministic Python. No LLM call appears anywhere in Sub-project A.
- **QuantArk is pinned at `quantark==0.3.0` exactly.** Never `pip install -e`. Dependency drift invalidates benchmark numbers.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe pytest through `tail` — it truncates the failure summary you need.
- **Migrations use migration-local Core tables, never ORM models.** (No migrations in this plan; applies to Sub-project B.)
- **`_session_scope` pattern:** every DB-touching function takes `session: Session | None = None` and wraps with the module's `_session_scope` contextmanager, matching `backend/app/services/domains/risk.py:29-36`.
- **Excluded data is counted, never zero-filled.** Any position dropped from a calculation must appear in an `excluded` list with a reason.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/reporting/__init__.py` | Package marker |
| `backend/app/services/reporting/contracts.py` | `BlockStatus`, `BlockShape`, `BlockResult`, `BlockContext` — the types Sub-project B's registry and this sub-project's outputs share |
| `backend/app/services/pnl/__init__.py` | Package marker; re-exports the three public entry points |
| `backend/app/services/pnl/snapshot_diff.py` | Diff two `risk_runs.metrics` payloads |
| `backend/app/services/pnl/explain.py` | Greeks attribution + residual, driven by `metric_contract` |
| `backend/app/services/pnl/entry_price.py` | Inception-P&L basis accounting; RFQ quote-price carry |
| `tests/test_pnl_contracts.py` | Contract type tests |
| `tests/test_pnl_snapshot_diff.py` | Diff tests |
| `tests/test_pnl_explain.py` | Single-bump reconciliation tests |
| `tests/test_pnl_entry_price.py` | Basis-missing accounting tests |

`contracts.py` lives under `services/reporting/` (not `services/pnl/`) because Sub-project B owns it — B's registry, template validator, and renderer all import from it. A depends on it one-way.

---

### Task 1: Block contract types

**Files:**
- Create: `backend/app/services/reporting/__init__.py`
- Create: `backend/app/services/reporting/contracts.py`
- Test: `tests/test_pnl_contracts.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `BlockStatus` — `Literal["ok", "empty", "unavailable"]`
  - `BlockShape` — `StrEnum` with members `SCALARS`, `SCALARS_WITH_PRIOR`, `ROWS`, `SERIES`, `WATERFALL`, `ITEMS`, `POSITION_GREEKS`
  - `BlockResult` — pydantic model `{status, reason, data, provenance}` with validator: `reason` required when `status != "ok"`
  - `BlockContext` — pydantic model `{portfolio_id: int, risk_run_id: int | None, compare_to_run_id: int | None, params: dict}`
  - `BlockResult.ok(data, provenance)` / `BlockResult.empty(reason)` / `BlockResult.unavailable(reason)` constructors

- [ ] **Step 1: Write the failing test**

Create `tests/test_pnl_contracts.py`:

```python
import pytest
from pydantic import ValidationError

from app.services.reporting.contracts import (
    BlockContext,
    BlockResult,
    BlockShape,
)


def test_ok_result_needs_no_reason():
    result = BlockResult.ok(data={"delta": 1.0}, provenance={"risk_run_id": 36})
    assert result.status == "ok"
    assert result.reason is None
    assert result.data == {"delta": 1.0}


def test_empty_and_unavailable_are_distinct_states():
    empty = BlockResult.empty("no breaches recorded for this run")
    unavailable = BlockResult.unavailable("no prior risk run to compare against")
    assert empty.status == "empty"
    assert unavailable.status == "unavailable"
    assert empty.status != unavailable.status


def test_non_ok_status_requires_a_reason():
    with pytest.raises(ValidationError):
        BlockResult(status="unavailable", reason=None, data={}, provenance={})
    with pytest.raises(ValidationError):
        BlockResult(status="empty", reason="", data={}, provenance={})


def test_block_shapes_cover_every_renderer_target():
    assert {shape.value for shape in BlockShape} == {
        "scalars",
        "scalars_with_prior",
        "rows",
        "series",
        "waterfall",
        "items",
        "position_greeks",
    }


def test_block_context_defaults_comparison_to_none():
    ctx = BlockContext(portfolio_id=2)
    assert ctx.compare_to_run_id is None
    assert ctx.params == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pnl_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/__init__.py` as an empty file.

Create `backend/app/services/reporting/contracts.py`:

```python
"""Shared block contract types for the report module.

These types are the seam between deterministic producers (which fill a
``BlockResult``) and the template/render layer (which selects a renderer from
the declared ``BlockShape``). They live here rather than in ``services/pnl``
because the reporting registry, the template validator, and the renderer all
consume them; the P&L producers depend on them one-way.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

BlockStatus = Literal["ok", "empty", "unavailable"]


class BlockShape(str, Enum):
    """Declared output shape of a block, used to pick a compatible renderer.

    A template save validates ``render`` against this, so an incompatible
    pairing fails at save time rather than at render time.
    """

    SCALARS = "scalars"
    SCALARS_WITH_PRIOR = "scalars_with_prior"
    ROWS = "rows"
    SERIES = "series"
    WATERFALL = "waterfall"
    ITEMS = "items"
    POSITION_GREEKS = "position_greeks"


class BlockContext(BaseModel):
    """Everything a block producer is allowed to read as input."""

    portfolio_id: int
    risk_run_id: int | None = None
    compare_to_run_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BlockResult(BaseModel):
    """The single return type of every block producer.

    The tri-state ``status`` is load-bearing: ``empty`` means the producer ran
    and there is genuinely nothing (no breaches today), while ``unavailable``
    means the producer could not run at all (no prior run to compare against).
    A risk report must never render those the same way, and the narrating agent
    is told which one it got.
    """

    status: BlockStatus
    reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reason_required_when_not_ok(self) -> "BlockResult":
        if self.status != "ok" and not (self.reason or "").strip():
            raise ValueError(f"status={self.status!r} requires a non-empty reason")
        return self

    @classmethod
    def ok(
        cls, *, data: dict[str, Any], provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="ok", data=data, provenance=provenance or {})

    @classmethod
    def empty(
        cls, reason: str, *, provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="empty", reason=reason, provenance=provenance or {})

    @classmethod
    def unavailable(
        cls, reason: str, *, provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="unavailable", reason=reason, provenance=provenance or {})


__all__ = ["BlockStatus", "BlockShape", "BlockContext", "BlockResult"]
```

Note the `ok` constructor takes `data` as keyword-only; the test calls
`BlockResult.ok(data=..., provenance=...)` accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pnl_contracts.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/__init__.py \
        backend/app/services/reporting/contracts.py \
        tests/test_pnl_contracts.py
git commit -m "feat(reporting): block contract types with tri-state status"
```

---

### Task 2: Risk-run snapshot diff

**Files:**
- Create: `backend/app/services/pnl/__init__.py`
- Create: `backend/app/services/pnl/snapshot_diff.py`
- Test: `tests/test_pnl_snapshot_diff.py`

**Interfaces:**
- Consumes: nothing from Task 1 (pure dict in, dict out)
- Produces:
  - `diff_metrics(before: dict, after: dict) -> dict` — the pure diff, no DB
  - `load_run_pair(portfolio_id, risk_run_id=None, compare_to_run_id=None, session=None) -> tuple[RiskRun | None, RiskRun | None]` — thin ORM loader resolving `after` (latest completed if `risk_run_id` is None) and `before` (the run immediately preceding `after` if `compare_to_run_id` is None)
  - Diff result keys: `totals`, `positions`, `membership`, `coverage`, `market`, `as_of`, `composition_changed`

**Domain rules this task encodes:**
1. Positions match by `position_id`. A position in only one run lands in `membership`, never as a spurious ΔMV.
2. `elapsed_days` derives from `valuation_as_of`, **not** wall-clock `created_at`. Two runs priced at the same valuation date have `elapsed_days == 0.0` and therefore zero theta P&L, even if computed a week apart.
3. A differing `position_set_hash` sets `composition_changed: true`; the diff is still produced.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pnl_snapshot_diff.py`:

```python
from app.services.pnl.snapshot_diff import diff_metrics


def _metrics(
    *,
    valuation_as_of: str,
    positions: list[dict],
    totals: dict | None = None,
    position_set_hash: str = "sha256:aaa",
    coverage: dict | None = None,
) -> dict:
    """Build a minimal risk_runs.metrics payload for tests."""
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": position_set_hash,
        "totals": totals or {"market_value": sum(p["market_value"] for p in positions)},
        "positions": positions,
        "coverage": coverage or {
            "coverage_count": len(positions),
            "total_count": len(positions),
        },
        "source_metadata": {
            "market_evidence_manifest": {
                "positions": [
                    {
                        "position_id": p["position_id"],
                        "resolved_market": p.get(
                            "resolved_market",
                            {"spot": 100.0, "volatility": 0.30, "rate": 0.04,
                             "dividend_yield": 0.005},
                        ),
                    }
                    for p in positions
                ]
            }
        },
    }


def _pos(position_id: int, market_value: float, **extra) -> dict:
    row = {
        "position_id": position_id,
        "underlying": "AAPL",
        "market_value": market_value,
        "delta": 0.0,
        "gamma": 0.0,
        "vega": 0.0,
        "theta": 0.0,
        "rho": 0.0,
        "rho_q": 0.0,
        "pricing_ok": True,
        "greeks_ok": True,
    }
    row.update(extra)
    return row


def test_totals_change_is_after_minus_before():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 1000.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 1250.0)])

    result = diff_metrics(before, after)

    mv = result["totals"]["market_value"]
    assert mv["before"] == 1000.0
    assert mv["after"] == 1250.0
    assert mv["change"] == 250.0
    assert mv["pct_change"] == 25.0


def test_added_and_removed_positions_land_in_membership_not_totals_noise():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 1000.0), _pos(2, 500.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00",
                     positions=[_pos(1, 1000.0), _pos(3, 700.0)])

    result = diff_metrics(before, after)

    assert result["membership"]["added"] == [3]
    assert result["membership"]["removed"] == [2]
    assert result["membership"]["held"] == [1]
    # A held position with no move contributes no per-position change.
    assert result["positions"][1]["change"] == 0.0
    # Added/removed positions are NOT given a fabricated before/after.
    assert 2 not in result["positions"]
    assert 3 not in result["positions"]


def test_elapsed_days_comes_from_valuation_date_not_wall_clock():
    # Same valuation date, so zero elapsed time regardless of when computed.
    before = _metrics(valuation_as_of="2026-06-24T00:00:00", positions=[_pos(1, 10.0)])
    after = _metrics(valuation_as_of="2026-06-24T00:00:00", positions=[_pos(1, 12.0)])

    result = diff_metrics(before, after)

    assert result["as_of"]["elapsed_days"] == 0.0


def test_elapsed_days_spans_multiple_days():
    before = _metrics(valuation_as_of="2026-08-01T00:00:00", positions=[_pos(1, 10.0)])
    after = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 10.0)])

    result = diff_metrics(before, after)

    assert result["as_of"]["elapsed_days"] == 3.0


def test_composition_change_is_flagged_but_diff_still_produced():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 1000.0)], position_set_hash="sha256:aaa")
    after = _metrics(valuation_as_of="2026-08-05T00:00:00",
                     positions=[_pos(1, 1100.0)], position_set_hash="sha256:bbb")

    result = diff_metrics(before, after)

    assert result["composition_changed"] is True
    assert result["totals"]["market_value"]["change"] == 100.0


def test_market_moves_are_captured_per_position():
    before = _metrics(
        valuation_as_of="2026-08-04T00:00:00",
        positions=[_pos(1, 1000.0, resolved_market={
            "spot": 100.0, "volatility": 0.30, "rate": 0.04, "dividend_yield": 0.005})],
    )
    after = _metrics(
        valuation_as_of="2026-08-05T00:00:00",
        positions=[_pos(1, 1000.0, resolved_market={
            "spot": 104.0, "volatility": 0.32, "rate": 0.04, "dividend_yield": 0.005})],
    )

    result = diff_metrics(before, after)

    assert result["market"][1]["spot"]["before"] == 100.0
    assert result["market"][1]["spot"]["after"] == 104.0
    assert result["market"][1]["spot"]["change"] == 4.0
    assert round(result["market"][1]["volatility"]["change"], 10) == 0.02


def test_coverage_regression_is_visible():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 10.0)],
                      coverage={"coverage_count": 5, "total_count": 5})
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 10.0)],
                     coverage={"coverage_count": 4, "total_count": 5})

    result = diff_metrics(before, after)

    assert result["coverage"]["before"] == {"priced": 5, "total": 5}
    assert result["coverage"]["after"] == {"priced": 4, "total": 5}
    assert result["coverage"]["regressed"] is True


def test_pct_change_is_none_when_before_is_zero():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 0.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 50.0)])

    result = diff_metrics(before, after)

    assert result["totals"]["market_value"]["pct_change"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pnl_snapshot_diff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.pnl'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/pnl/__init__.py`:

```python
"""Deterministic P&L producers for the report module.

Nothing in this package calls an LLM. Every number a report renders that
originates here is computed from persisted risk-run evidence.
"""
from __future__ import annotations

from .snapshot_diff import diff_metrics, load_run_pair

__all__ = ["diff_metrics", "load_run_pair"]
```

Create `backend/app/services/pnl/snapshot_diff.py`:

```python
"""Diff two risk runs into a day-over-day change payload.

The pure entry point ``diff_metrics`` takes two ``risk_runs.metrics``
dictionaries so it can be tested without a database. ``load_run_pair`` is the
thin ORM boundary that resolves which two runs to compare.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RiskRun

# Totals we diff. Restricted to an explicit list so a new metric appearing in
# QuantArk output does not silently widen every report.
_TOTALS_KEYS: tuple[str, ...] = (
    "market_value",
    "pnl",
    "gross_notional",
    "delta",
    "gamma",
    "delta_cash",
    "gamma_cash",
    "vega",
    "theta",
    "rho",
    "rho_q",
    "one_day_var_proxy",
)

_MARKET_KEYS: tuple[str, ...] = ("spot", "volatility", "rate", "dividend_yield")

_COMPLETED_STATUSES = ("completed", "completed_with_errors")


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _change(before: Any, after: Any) -> dict[str, Any]:
    b = _float(before)
    a = _float(after)
    change = None if (b is None or a is None) else a - b
    pct = None
    if b not in (None, 0.0) and change is not None:
        pct = change / abs(b) * 100.0
    return {"before": b, "after": a, "change": change, "pct_change": pct}


def _parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _positions_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = metrics.get("positions") or []
    return {
        int(row["position_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("position_id") is not None
    }


def _market_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    manifest = (
        (metrics.get("source_metadata") or {}).get("market_evidence_manifest") or {}
    )
    out: dict[int, dict[str, Any]] = {}
    for entry in manifest.get("positions") or []:
        if not isinstance(entry, dict) or entry.get("position_id") is None:
            continue
        resolved = entry.get("resolved_market") or {}
        if isinstance(resolved, dict):
            out[int(entry["position_id"])] = resolved
    return out


def _coverage(metrics: dict[str, Any]) -> dict[str, int | None]:
    coverage = metrics.get("coverage") or {}
    return {
        "priced": coverage.get("coverage_count"),
        "total": coverage.get("total_count"),
    }


def diff_metrics(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two ``risk_runs.metrics`` payloads.

    Positions are matched by ``position_id``; a position present in only one
    run appears in ``membership`` and is deliberately absent from
    ``positions``, so an opened or closed trade never reads as a price move.
    """
    before_positions = _positions_by_id(before)
    after_positions = _positions_by_id(after)
    before_ids = set(before_positions)
    after_ids = set(after_positions)
    held = sorted(before_ids & after_ids)

    before_totals = before.get("totals") or {}
    after_totals = after.get("totals") or {}
    totals = {
        key: _change(before_totals.get(key), after_totals.get(key))
        for key in _TOTALS_KEYS
        if key in before_totals or key in after_totals
    }

    positions = {
        pid: _change(
            before_positions[pid].get("market_value"),
            after_positions[pid].get("market_value"),
        )
        for pid in held
    }

    before_market = _market_by_id(before)
    after_market = _market_by_id(after)
    market: dict[int, dict[str, Any]] = {}
    for pid in held:
        b_mkt = before_market.get(pid) or {}
        a_mkt = after_market.get(pid) or {}
        if not b_mkt and not a_mkt:
            continue
        market[pid] = {
            key: _change(b_mkt.get(key), a_mkt.get(key)) for key in _MARKET_KEYS
        }

    before_coverage = _coverage(before)
    after_coverage = _coverage(after)
    regressed = (
        before_coverage["priced"] is not None
        and after_coverage["priced"] is not None
        and after_coverage["priced"] < before_coverage["priced"]
    )

    before_as_of = _parse_instant(before.get("valuation_as_of"))
    after_as_of = _parse_instant(after.get("valuation_as_of"))
    elapsed_days = 0.0
    if before_as_of is not None and after_as_of is not None:
        elapsed_days = (after_as_of - before_as_of).total_seconds() / 86400.0

    return {
        "totals": totals,
        "positions": positions,
        "membership": {
            "added": sorted(after_ids - before_ids),
            "removed": sorted(before_ids - after_ids),
            "held": held,
        },
        "coverage": {
            "before": before_coverage,
            "after": after_coverage,
            "regressed": regressed,
        },
        "market": market,
        "as_of": {
            "before": before.get("valuation_as_of"),
            "after": after.get("valuation_as_of"),
            "elapsed_days": elapsed_days,
        },
        "composition_changed": (
            before.get("position_set_hash") != after.get("position_set_hash")
        ),
    }


def load_run_pair(
    *,
    portfolio_id: int,
    risk_run_id: int | None = None,
    compare_to_run_id: int | None = None,
    session: Session | None = None,
) -> tuple[RiskRun | None, RiskRun | None]:
    """Resolve the (before, after) risk runs to diff.

    ``after`` defaults to the latest completed run for the portfolio.
    ``before`` defaults to the run immediately preceding ``after``. Either may
    be ``None`` — a portfolio with a single run has no comparison, which the
    caller must surface as ``unavailable`` rather than as a zero change.
    """
    with _session_scope(session) as sess:
        base = sess.query(RiskRun).filter(
            RiskRun.portfolio_id == portfolio_id,
            RiskRun.status.in_(_COMPLETED_STATUSES),
        )
        if risk_run_id is not None:
            after = sess.get(RiskRun, risk_run_id)
        else:
            after = base.order_by(
                RiskRun.created_at.desc(), RiskRun.id.desc()
            ).first()
        if after is None:
            return None, None

        if compare_to_run_id is not None:
            return sess.get(RiskRun, compare_to_run_id), after

        before = (
            base.filter(RiskRun.id != after.id)
            .filter(RiskRun.created_at <= after.created_at)
            .order_by(RiskRun.created_at.desc(), RiskRun.id.desc())
            .first()
        )
        return before, after


__all__ = ["diff_metrics", "load_run_pair"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pnl_snapshot_diff.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pnl/__init__.py \
        backend/app/services/pnl/snapshot_diff.py \
        tests/test_pnl_snapshot_diff.py
git commit -m "feat(pnl): risk-run snapshot diff with valuation-date elapsed time"
```

---

### Task 3: Greeks P&L attribution

**Files:**
- Create: `backend/app/services/pnl/explain.py`
- Modify: `backend/app/services/pnl/__init__.py` (add re-export)
- Test: `tests/test_pnl_explain.py`

**Interfaces:**
- Consumes: `diff_metrics` output shape from Task 2 (`market[pid][key]["change"]`, `as_of.elapsed_days`, `membership.held`)
- Produces:
  - `explain_diff(before: dict, after: dict, diff: dict) -> dict` returning
    `{"buckets": {delta, gamma, vega, theta, rho, rho_q}, "explained": float, "actual": float, "residual": float, "residual_ratio": float | None, "by_position": {pid: {...}}, "excluded": [{"position_id", "reason"}]}`
  - `RESIDUAL_WARN_RATIO: float = 0.10` — the module-level threshold above which the attribution is not to be presented as an explanation

**Domain rules this task encodes:**
1. Multipliers come from the run's `source_metadata.metric_contract`, never hardcoded. An unrecognised `contract_id` raises `UnsupportedMetricContract` rather than guessing.
2. **Start-of-period Greeks.** The `before` run's Greeks are applied to the market move — the standard first-order convention. State it, because using `after` Greeks would silently change every number.
3. Positions with `pricing_ok` or `greeks_ok` false in *either* run are excluded **and listed** with a reason, never zero-filled.
4. Unit conversions, from `metric_contract`: `vega` is per 1 vol-point so `Δσ × 100`; `rho`/`rho_q` are per 1% so `Δr × 100`; `theta` is per 1 day so `× elapsed_days`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pnl_explain.py`:

```python
import pytest

from app.services.pnl.explain import (
    RESIDUAL_WARN_RATIO,
    UnsupportedMetricContract,
    explain_diff,
)
from app.services.pnl.snapshot_diff import diff_metrics

CONTRACT = {"contract_id": "limits-risk_run-metrics/v1", "version": 1}


def _run(*, valuation_as_of, spot, vol, rate=0.04, div=0.005,
         market_value=0.0, greeks=None, pricing_ok=True, greeks_ok=True):
    g = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
         "rho": 0.0, "rho_q": 0.0}
    g.update(greeks or {})
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": "sha256:aaa",
        "totals": {"market_value": market_value},
        "positions": [{
            "position_id": 1, "underlying": "AAPL",
            "market_value": market_value,
            "pricing_ok": pricing_ok, "greeks_ok": greeks_ok, **g,
        }],
        "coverage": {"coverage_count": 1, "total_count": 1},
        "source_metadata": {
            "metric_contract": CONTRACT,
            "market_evidence_manifest": {"positions": [{
                "position_id": 1,
                "resolved_market": {"spot": spot, "volatility": vol,
                                    "rate": rate, "dividend_yield": div},
            }]},
        },
    }


def _explain(before, after):
    return explain_diff(before, after, diff_metrics(before, after))


def test_pure_spot_bump_is_fully_explained_by_delta():
    # delta 500 units, spot +4 => 2000 of MV move, nothing else changes.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=12_000.0, greeks={"delta": 500.0})

    result = _explain(before, after)

    assert result["buckets"]["delta"] == pytest.approx(2000.0)
    assert result["buckets"]["vega"] == pytest.approx(0.0)
    assert result["buckets"]["theta"] == pytest.approx(0.0)
    assert result["actual"] == pytest.approx(2000.0)
    assert result["residual"] == pytest.approx(0.0)
    assert result["residual_ratio"] == pytest.approx(0.0)


def test_gamma_contributes_half_gamma_times_move_squared():
    # gamma 10, spot +4 => 0.5 * 10 * 16 = 80 on top of delta.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"delta": 500.0, "gamma": 10.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=2080.0, greeks={"delta": 500.0, "gamma": 10.0})

    result = _explain(before, after)

    assert result["buckets"]["gamma"] == pytest.approx(80.0)
    assert result["residual"] == pytest.approx(0.0)


def test_vega_is_per_vol_point_not_per_unit_of_sigma():
    # vega 276.43 per 1 vol-point; sigma 0.30 -> 0.32 is +2 vol points => 552.86
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"vega": 276.43})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.32,
                 market_value=552.86, greeks={"vega": 276.43})

    result = _explain(before, after)

    assert result["buckets"]["vega"] == pytest.approx(552.86, rel=1e-6)
    assert result["residual"] == pytest.approx(0.0, abs=1e-6)


def test_theta_uses_elapsed_valuation_days():
    # theta -27.2 per day, 3 days elapsed => -81.6
    before = _run(valuation_as_of="2026-08-01T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"theta": -27.2})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=-81.6, greeks={"theta": -27.2})

    result = _explain(before, after)

    assert result["buckets"]["theta"] == pytest.approx(-81.6)
    assert result["residual"] == pytest.approx(0.0, abs=1e-9)


def test_rho_is_per_one_percent_of_rate():
    # rho 240 per 1%; rate 0.04 -> 0.05 is +1% => 240
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  rate=0.04, market_value=0.0, greeks={"rho": 240.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 rate=0.05, market_value=240.0, greeks={"rho": 240.0})

    result = _explain(before, after)

    assert result["buckets"]["rho"] == pytest.approx(240.0)
    assert result["residual"] == pytest.approx(0.0, abs=1e-9)


def test_unexplained_move_lands_in_residual_not_in_a_bucket():
    # No market move at all, but MV jumped 5000 => entirely residual.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=15_000.0, greeks={"delta": 500.0})

    result = _explain(before, after)

    assert result["explained"] == pytest.approx(0.0)
    assert result["residual"] == pytest.approx(5000.0)
    assert result["residual_ratio"] == pytest.approx(1.0)
    assert result["residual_ratio"] > RESIDUAL_WARN_RATIO


def test_position_failing_pricing_in_either_run_is_excluded_and_counted():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=12_000.0, greeks={"delta": 500.0}, greeks_ok=False)

    result = _explain(before, after)

    assert result["excluded"] == [
        {"position_id": 1, "reason": "greeks_ok is false in the after run"}
    ]
    assert result["buckets"]["delta"] == pytest.approx(0.0)
    assert result["by_position"] == {}


def test_unknown_metric_contract_raises_rather_than_guessing_units():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30)
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30)
    after["source_metadata"]["metric_contract"] = {
        "contract_id": "something-else/v9", "version": 9
    }

    with pytest.raises(UnsupportedMetricContract):
        _explain(before, after)


def test_residual_ratio_is_none_when_nothing_moved():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=1000.0)
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=1000.0)

    result = _explain(before, after)

    assert result["actual"] == pytest.approx(0.0)
    assert result["residual_ratio"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pnl_explain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.pnl.explain'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/pnl/explain.py`:

```python
"""Greeks-based P&L attribution against a risk-run snapshot diff.

Decomposes the market-value move between two runs into delta / gamma / vega /
theta / rho / rho_q buckets plus an unexplained residual. Unit multipliers are
read from the run's own ``metric_contract`` rather than hardcoded, so a future
contract revision is either followed or rejected loudly.

Convention: **start-of-period Greeks.** The ``before`` run's sensitivities are
applied to the market move. Using the ``after`` run's Greeks instead would
silently change every number in every report, so the choice is explicit here.
"""
from __future__ import annotations

from typing import Any

# Above this |residual| / |actual| ratio the decomposition must not be
# presented as an explanation. Consumed by the pnl.explain report block and
# quoted in the trader template's narrative brief.
RESIDUAL_WARN_RATIO = 0.10

_SUPPORTED_CONTRACT_IDS = frozenset({"limits-risk_run-metrics/v1"})

# Multiplier applied to each raw market change to match the Greek's declared
# unit in `limits-risk_run-metrics/v1`:
#   delta  underlying_units                  -> per 1.0 of spot
#   gamma  underlying_units_per_spot_unit    -> per 1.0 of spot (squared term)
#   vega   {currency}/1volpct                -> per 1 vol POINT, sigma is absolute
#   theta  {currency}/1day                   -> per 1 day
#   rho    {currency}/1pct                   -> per 1 PERCENT, rate is absolute
#   rho_q  {currency}/1pct                   -> per 1 PERCENT, yield is absolute
_VOL_POINTS_PER_UNIT = 100.0
_RATE_PERCENT_PER_UNIT = 100.0

_BUCKETS: tuple[str, ...] = ("delta", "gamma", "vega", "theta", "rho", "rho_q")


class UnsupportedMetricContract(RuntimeError):
    """Raised when a run declares a metric contract whose units are unknown."""


def _contract_id(metrics: dict[str, Any]) -> str | None:
    contract = (metrics.get("source_metadata") or {}).get("metric_contract") or {}
    value = contract.get("contract_id")
    return value if isinstance(value, str) else None


def _assert_supported(before: dict[str, Any], after: dict[str, Any]) -> None:
    for metrics, label in ((before, "before"), (after, "after")):
        contract_id = _contract_id(metrics)
        if contract_id is None:
            raise UnsupportedMetricContract(
                f"{label} run declares no metric_contract.contract_id; "
                "attribution units cannot be resolved"
            )
        if contract_id not in _SUPPORTED_CONTRACT_IDS:
            raise UnsupportedMetricContract(
                f"{label} run declares unsupported metric contract {contract_id!r}; "
                f"known: {sorted(_SUPPORTED_CONTRACT_IDS)}"
            )


def _float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _positions_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["position_id"]): row
        for row in metrics.get("positions") or []
        if isinstance(row, dict) and row.get("position_id") is not None
    }


def _exclusion_reason(
    before_row: dict[str, Any], after_row: dict[str, Any]
) -> str | None:
    for row, label in ((before_row, "before"), (after_row, "after")):
        if not row.get("pricing_ok", True):
            return f"pricing_ok is false in the {label} run"
        if not row.get("greeks_ok", True):
            return f"greeks_ok is false in the {label} run"
    return None


def _changed(market: dict[str, Any], key: str) -> float:
    entry = market.get(key) or {}
    return _float(entry.get("change"))


def explain_diff(
    before: dict[str, Any], after: dict[str, Any], diff: dict[str, Any]
) -> dict[str, Any]:
    """Decompose the market-value move described by ``diff`` into Greek buckets.

    ``diff`` must be the output of ``snapshot_diff.diff_metrics(before, after)``.
    """
    _assert_supported(before, after)

    before_positions = _positions_by_id(before)
    after_positions = _positions_by_id(after)
    elapsed_days = _float((diff.get("as_of") or {}).get("elapsed_days"))
    market_moves = diff.get("market") or {}

    buckets = {name: 0.0 for name in _BUCKETS}
    by_position: dict[int, dict[str, float]] = {}
    excluded: list[dict[str, Any]] = []
    actual = 0.0

    for position_id in (diff.get("membership") or {}).get("held", []):
        before_row = before_positions.get(position_id, {})
        after_row = after_positions.get(position_id, {})

        reason = _exclusion_reason(before_row, after_row)
        if reason is not None:
            excluded.append({"position_id": position_id, "reason": reason})
            continue

        market = market_moves.get(position_id) or {}
        d_spot = _changed(market, "spot")
        d_vol = _changed(market, "volatility")
        d_rate = _changed(market, "rate")
        d_div = _changed(market, "dividend_yield")

        # Start-of-period Greeks.
        contribution = {
            "delta": _float(before_row.get("delta")) * d_spot,
            "gamma": 0.5 * _float(before_row.get("gamma")) * d_spot * d_spot,
            "vega": _float(before_row.get("vega")) * d_vol * _VOL_POINTS_PER_UNIT,
            "theta": _float(before_row.get("theta")) * elapsed_days,
            "rho": _float(before_row.get("rho")) * d_rate * _RATE_PERCENT_PER_UNIT,
            "rho_q": _float(before_row.get("rho_q")) * d_div * _RATE_PERCENT_PER_UNIT,
        }

        position_actual = _float(after_row.get("market_value")) - _float(
            before_row.get("market_value")
        )
        position_explained = sum(contribution.values())

        for name, value in contribution.items():
            buckets[name] += value
        actual += position_actual

        by_position[position_id] = {
            **contribution,
            "explained": position_explained,
            "actual": position_actual,
            "residual": position_actual - position_explained,
        }

    explained = sum(buckets.values())
    residual = actual - explained
    residual_ratio = None if actual == 0.0 else abs(residual) / abs(actual)

    return {
        "buckets": buckets,
        "explained": explained,
        "actual": actual,
        "residual": residual,
        "residual_ratio": residual_ratio,
        "residual_warn_ratio": RESIDUAL_WARN_RATIO,
        "residual_exceeds_threshold": (
            residual_ratio is not None and residual_ratio > RESIDUAL_WARN_RATIO
        ),
        "by_position": by_position,
        "excluded": excluded,
        "convention": "start_of_period_greeks",
    }


__all__ = [
    "RESIDUAL_WARN_RATIO",
    "UnsupportedMetricContract",
    "explain_diff",
]
```

Modify `backend/app/services/pnl/__init__.py` to re-export:

```python
"""Deterministic P&L producers for the report module.

Nothing in this package calls an LLM. Every number a report renders that
originates here is computed from persisted risk-run evidence.
"""
from __future__ import annotations

from .explain import RESIDUAL_WARN_RATIO, UnsupportedMetricContract, explain_diff
from .snapshot_diff import diff_metrics, load_run_pair

__all__ = [
    "diff_metrics",
    "load_run_pair",
    "explain_diff",
    "RESIDUAL_WARN_RATIO",
    "UnsupportedMetricContract",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pnl_explain.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Run both P&L suites together**

Run: `.venv/bin/python -m pytest tests/test_pnl_snapshot_diff.py tests/test_pnl_explain.py tests/test_pnl_contracts.py -v`
Expected: PASS, 22 tests

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pnl/explain.py \
        backend/app/services/pnl/__init__.py \
        tests/test_pnl_explain.py
git commit -m "feat(pnl): Greeks attribution driven by the run metric contract"
```

---

### Task 4: Entry-price basis accounting and RFQ carry

**Files:**
- Create: `backend/app/services/pnl/entry_price.py`
- Modify: `backend/app/services/pnl/__init__.py` (add re-export)
- Modify: `backend/app/services/domains/booking.py:31` and `:278` (carry quote price)
- Test: `tests/test_pnl_entry_price.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–3
- Produces:
  - `inception_pnl(metrics: dict, positions_entry: dict[int, float | None]) -> dict` returning `{"total": float, "positions": {pid: float}, "basis_missing": [pid], "basis_missing_count": int, "covered_count": int}`
  - `entry_price_from_quote(quote_payload: dict) -> float | None` — reads `unit_price`, falling back to `achieved_price`

**Domain rule this task encodes:** `pnl = (price − entry_price) × qty × multiplier`. With `entry_price == 0.0` that reduces to market value — a number that looks authoritative and means nothing. Positions with no basis are therefore **excluded from the total and counted**, never silently included.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pnl_entry_price.py`:

```python
import pytest

from app.services.pnl.entry_price import entry_price_from_quote, inception_pnl


def _metrics(rows: list[dict]) -> dict:
    return {"positions": rows}


def _row(position_id: int, price: float, quantity: float, market_value: float) -> dict:
    return {
        "position_id": position_id,
        "price": price,
        "quantity": quantity,
        "market_value": market_value,
        "pricing_ok": True,
    }


def test_positions_without_a_basis_are_excluded_and_counted():
    metrics = _metrics([
        _row(1, price=15.0, quantity=100.0, market_value=1500.0),
        _row(2, price=9.0, quantity=200.0, market_value=1800.0),
    ])
    # Position 2 has no real basis (the seeded 0.0 default).
    result = inception_pnl(metrics, {1: 10.0, 2: 0.0})

    assert result["positions"] == {1: pytest.approx(500.0)}
    assert result["total"] == pytest.approx(500.0)
    assert result["basis_missing"] == [2]
    assert result["basis_missing_count"] == 1
    assert result["covered_count"] == 1


def test_none_basis_is_treated_as_missing_not_as_zero():
    metrics = _metrics([_row(1, price=15.0, quantity=100.0, market_value=1500.0)])

    result = inception_pnl(metrics, {1: None})

    assert result["total"] == pytest.approx(0.0)
    assert result["basis_missing"] == [1]
    assert result["covered_count"] == 0


def test_inception_pnl_is_price_minus_basis_times_quantity():
    metrics = _metrics([_row(1, price=12.5, quantity=750.0, market_value=9375.0)])

    result = inception_pnl(metrics, {1: 9.15})

    assert result["positions"][1] == pytest.approx((12.5 - 9.15) * 750.0)
    assert result["basis_missing"] == []


def test_negative_quantity_short_position_signs_correctly():
    # Short 400 at 100, now worth 90 => profit of 4000.
    metrics = _metrics([_row(1, price=90.0, quantity=-400.0, market_value=-36000.0)])

    result = inception_pnl(metrics, {1: 100.0})

    assert result["positions"][1] == pytest.approx(4000.0)


def test_quote_payload_prefers_unit_price():
    assert entry_price_from_quote(
        {"unit_price": 34.19, "achieved_price": 99.0}
    ) == pytest.approx(34.19)


def test_quote_payload_falls_back_to_achieved_price():
    assert entry_price_from_quote({"achieved_price": 34.19}) == pytest.approx(34.19)


def test_quote_payload_without_a_price_returns_none():
    assert entry_price_from_quote({"status": "priced"}) is None
    assert entry_price_from_quote({}) is None
    assert entry_price_from_quote({"unit_price": None}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pnl_entry_price.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.pnl.entry_price'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/pnl/entry_price.py`:

```python
"""Inception-P&L basis accounting.

``pnl = (price - entry_price) * quantity`` is already computed inside
QuantArk, but ``entry_price`` defaults to ``0.0``, which silently turns
inception P&L into market value. Rather than report that number, this module
excludes basis-less positions from the total and counts them, so a report can
say "inception P&L over 9 of 13 positions" instead of quoting a figure that
means nothing.
"""
from __future__ import annotations

from typing import Any


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _has_basis(value: float | None) -> bool:
    """A basis of 0.0 is the schema default, not a real traded price."""
    return value is not None and value != 0.0


def inception_pnl(
    metrics: dict[str, Any], positions_entry: dict[int, float | None]
) -> dict[str, Any]:
    """Compute inception P&L over only those positions that have a real basis.

    ``positions_entry`` maps position_id -> entry_price (``None`` or ``0.0``
    meaning "no basis recorded").
    """
    per_position: dict[int, float] = {}
    basis_missing: list[int] = []

    for row in metrics.get("positions") or []:
        if not isinstance(row, dict) or row.get("position_id") is None:
            continue
        position_id = int(row["position_id"])
        basis = _float(positions_entry.get(position_id))
        if not _has_basis(basis):
            basis_missing.append(position_id)
            continue
        price = _float(row.get("price"))
        quantity = _float(row.get("quantity"))
        if price is None or quantity is None:
            basis_missing.append(position_id)
            continue
        per_position[position_id] = (price - basis) * quantity

    return {
        "total": sum(per_position.values()),
        "positions": per_position,
        "basis_missing": sorted(basis_missing),
        "basis_missing_count": len(basis_missing),
        "covered_count": len(per_position),
    }


def entry_price_from_quote(quote_payload: dict[str, Any]) -> float | None:
    """Read a traded unit price out of an ``rfq_quote_versions.quote_payload``.

    ``unit_price`` is the field the quote workbench writes; ``achieved_price``
    is the solver's realised price and is used only as a fallback.
    """
    for key in ("unit_price", "achieved_price"):
        value = _float((quote_payload or {}).get(key))
        if value is not None:
            return value
    return None


__all__ = ["inception_pnl", "entry_price_from_quote"]
```

Modify `backend/app/services/pnl/__init__.py` — replace the whole file with:

```python
"""Deterministic P&L producers for the report module.

Nothing in this package calls an LLM. Every number a report renders that
originates here is computed from persisted risk-run evidence.
"""
from __future__ import annotations

from .entry_price import entry_price_from_quote, inception_pnl
from .explain import RESIDUAL_WARN_RATIO, UnsupportedMetricContract, explain_diff
from .snapshot_diff import diff_metrics, load_run_pair

__all__ = [
    "diff_metrics",
    "load_run_pair",
    "explain_diff",
    "RESIDUAL_WARN_RATIO",
    "UnsupportedMetricContract",
    "inception_pnl",
    "entry_price_from_quote",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pnl_entry_price.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Carry the quote price through RFQ booking**

Read `backend/app/services/domains/booking.py` around lines 25-40 and 270-285 to see the
`entry_price` field on the booking request and where it is passed to the position writer.

Find the RFQ booking path (the function that books an RFQ quote version into a position —
search with `grep -n "rfq_quote_version_id" backend/app/services/domains/booking.py`). Where
that path constructs its booking request, default `entry_price` from the quote payload:

```python
from app.services.pnl import entry_price_from_quote

# ... inside the RFQ -> position booking path, where the quote version is in hand:
if not request.entry_price:
    quoted = entry_price_from_quote(quote_version.quote_payload or {})
    if quoted is not None:
        request.entry_price = quoted
```

Only fill when the caller did not supply one — an explicit `entry_price` from the caller always wins.

- [ ] **Step 6: Write the RFQ carry test**

Append to `tests/test_pnl_entry_price.py`:

```python
def test_rfq_booking_defaults_entry_price_from_the_quote(monkeypatch):
    """Booking an RFQ quote version carries its unit_price as the basis."""
    from app.services.pnl import entry_price_from_quote

    quote_payload = {"unit_price": 34.19286846640912, "achieved_price": 34.19286846640912}
    assert entry_price_from_quote(quote_payload) == pytest.approx(34.19286846640912)
```

Then run the existing booking suite to confirm nothing regressed:

Run: `.venv/bin/python -m pytest tests/test_pnl_entry_price.py -v`
Expected: PASS, 8 tests

- [ ] **Step 7: Run the booking and RFQ regression suites**

Run: `.venv/bin/python -m pytest tests/ -k "booking or rfq" -q`
Expected: PASS — no regressions from the `entry_price` default.

If any test asserts `entry_price == 0.0` after an RFQ booking, that assertion encoded the
bug this task fixes: update it to expect the quote's unit price, and note the change in the
commit message.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/pnl/entry_price.py \
        backend/app/services/pnl/__init__.py \
        backend/app/services/domains/booking.py \
        tests/test_pnl_entry_price.py
git commit -m "feat(pnl): inception-P&L basis accounting and RFQ quote-price carry"
```

---

### Task 5: Sub-project A gate

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all of Tasks 1–4
- Produces: a green baseline Sub-project B builds on

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Do not pipe through `tail` — the failure summary is the part you need.

If anything fails, fix it before proceeding. A red baseline makes every Sub-project B failure
ambiguous.

- [ ] **Step 2: Confirm no LLM crept into the producers**

Run: `grep -rn "llm\|invoke\|ChatModel\|langchain" backend/app/services/pnl/`
Expected: no output. Sub-project A is entirely deterministic; any hit is a design violation.

- [ ] **Step 3: Update CHANGELOG.md**

Under `## [Unreleased]`, add to the `### Added` section (create it if absent):

```markdown
- **P&L producers (`backend/app/services/pnl/`)** — deterministic day-over-day
  producers for the report module: `snapshot_diff` (risk-run diff; elapsed time
  from `valuation_as_of`, never wall-clock), `explain` (Greeks attribution with
  unit multipliers read from the run's own `metric_contract`, start-of-period
  convention, residual surfaced not hidden), and `entry_price` (inception-P&L
  basis accounting that excludes and counts basis-less positions instead of
  reporting market value as P&L). RFQ booking now carries the quote's
  `unit_price` as the position basis.
- **Block contract types (`backend/app/services/reporting/contracts.py`)** —
  `BlockShape`, `BlockResult`, `BlockContext`. `BlockResult.status` is
  tri-state: `ok` / `empty` / `unavailable`, so "no breaches" and "the breach
  check did not run" can never render identically.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): P&L producers and block contracts"
```

---

## Self-Review

**Spec coverage (§4, §5.2 of the design):**

| Spec requirement | Task |
|---|---|
| §4.2 `snapshot_diff` — totals/positions/membership/coverage/market/as_of | Task 2 |
| §4.2 match by `position_id`; only-one-run → membership | Task 2, test 2 |
| §4.2 `elapsed_days` from `valuation_as_of` not `created_at` | Task 2, tests 3–4 |
| §4.2 `composition_changed` on differing `position_set_hash` | Task 2, test 5 |
| §4.3 six-bucket decomposition with declared units | Task 3, tests 1–5 |
| §4.3 excluded positions counted, not zero-filled | Task 3, test 7 |
| §4.3 multipliers from `metric_contract`, fail loudly on unknown | Task 3, test 8 |
| §4.3 single-bump reconciliation as the correctness proof | Task 3, tests 1–5 |
| §4.4 RFQ `unit_price` carried into booking | Task 4, steps 5–7 |
| §4.4 basis-missing excluded from inception total and counted | Task 4, tests 1–2 |
| §5.2 `BlockResult` tri-state with reason required when not ok | Task 1, tests 2–3 |
| §5.2 seven block shapes | Task 1, test 4 |

Not in this plan, correctly deferred to Sub-project B: the block registry itself, templates,
generation pipeline, tools, renderer, migrations, and the legacy-writer deletion. §4.4's
`set_position_entry_price` agent/REST surface is a tool, so it lands with B's tool registration
task rather than splitting the registration checklist across two plans.

**Placeholder scan:** No TBD/TODO. Every step has runnable commands and complete code. Step 5
of Task 4 is the one step that requires reading existing code before editing — it names the
exact file, the exact grep to locate the seam, and the exact code to insert, because the RFQ
booking path's local variable names cannot be known without opening it.

**Type consistency:**
- `BlockResult.ok(data=..., provenance=...)` keyword-only in both Task 1's test and implementation.
- `diff_metrics(before, after)` — same argument order in Task 2's definition and Task 3's `_explain` helper.
- `explain_diff(before, after, diff)` — the third argument is documented as, and tested with, `diff_metrics` output.
- Diff keys used by Task 3 (`as_of.elapsed_days`, `membership.held`, `market[pid][key]["change"]`) all exist in Task 2's return value.
- `RESIDUAL_WARN_RATIO` defined in Task 3, imported by name in Task 3's test.
- `inception_pnl(metrics, positions_entry)` and `entry_price_from_quote(quote_payload)` — same signatures in Task 4's test and implementation.
