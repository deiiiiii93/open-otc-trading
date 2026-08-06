# Report Module — Sub-project B1: Block Registry & Producers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the block registry and every block producer, so that any declared report section can be resolved to a `BlockResult` deterministically, with no template, pipeline, or UI yet.

**Architecture:** A decorator-based registry maps a block `key` to a producer function and its declared `BlockShape`. Producers read persisted evidence (`risk_runs.metrics`, limits tables, RFQ tables, `agent_action_audits`) and Sub-project A's P&L modules. Every producer returns the tri-state `BlockResult` from `services/reporting/contracts.py` — never raises for missing data.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.2
**Depends on:** Plan A (`docs/superpowers/plans/2026-08-06-report-module-a-pnl-producers.md`) — all 5 tasks complete and green.

## Global Constraints

- **Numbers never come from an LLM.** No LLM call appears anywhere in this plan.
- **Producers never raise for missing data.** A missing prior run, an empty table, or an absent producer returns `BlockResult.unavailable(reason)` or `.empty(reason)`. Raising is reserved for programming errors.
- **`empty` vs `unavailable` is load-bearing.** `empty` = the producer ran, nothing to report. `unavailable` = the producer could not run. Never conflate them.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe pytest through `tail`.
- **`_session_scope` pattern:** DB-touching helpers take `session: Session | None = None`, matching `backend/app/services/domains/risk.py:29-36`.
- **QuantArk is pinned at `quantark==0.3.0`.** Never `pip install -e`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/reporting/registry.py` | `@report_block` decorator, `BlockSpec`, `BlockRegistry`, `resolve_block`, `list_blocks` |
| `backend/app/services/reporting/blocks/__init__.py` | Imports every block module so registration side-effects fire |
| `backend/app/services/reporting/blocks/risk.py` | `risk.totals`, `risk.exposure_by_underlying`, `risk.greeks_by_bucket`, `risk.exposure_diff`, `coverage.evidence` |
| `backend/app/services/reporting/blocks/pnl.py` | `pnl.daily`, `pnl.explain`, `pnl.by_position`, `pnl.inception` |
| `backend/app/services/reporting/blocks/limits.py` | `limits.utilization`, `limits.breaches`, `limits.incidents`, `scenario.latest_grid` |
| `backend/app/services/reporting/blocks/desk.py` | `rfq.open_pipeline`, `rfq.pending_approvals`, `positions.changes`, `positions.barrier_proximity`, `audit.write_actions_summary` |
| `tests/test_reporting_registry.py` | Registry mechanics |
| `tests/test_reporting_blocks_risk.py` | Risk + coverage producers |
| `tests/test_reporting_blocks_pnl.py` | P&L producers |
| `tests/test_reporting_blocks_limits.py` | Limits + scenario producers |
| `tests/test_reporting_blocks_desk.py` | RFQ + positions + audit producers |

Blocks are split by **data domain, not by shape**, so a file changes when its upstream tables change.

---

### Task 1: Block registry core

**Files:**
- Create: `backend/app/services/reporting/registry.py`
- Create: `backend/app/services/reporting/blocks/__init__.py`
- Test: `tests/test_reporting_registry.py`

**Interfaces:**
- Consumes: `BlockShape`, `BlockResult`, `BlockContext` from `app.services.reporting.contracts` (Plan A Task 1)
- Produces:
  - `@report_block(key, title, shape, requires=(), domain="", description="")` — registering decorator
  - `BlockSpec` — frozen dataclass `{key, title, shape, requires, domain, description, produce}`
  - `list_blocks() -> list[BlockSpec]` — catalog, sorted by key
  - `get_block(key) -> BlockSpec | None`
  - `resolve_block(key, ctx) -> BlockResult` — looks up and invokes; unknown key or missing required param returns `unavailable`, never raises
  - `UnknownBlockError` — raised only by `require_block(key)`, used by the template validator in B2

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_registry.py`:

```python
import pytest

from app.services.reporting.contracts import BlockContext, BlockResult, BlockShape
from app.services.reporting.registry import (
    UnknownBlockError,
    get_block,
    list_blocks,
    report_block,
    require_block,
    resolve_block,
)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Each test gets a clean registry so fixtures cannot leak between tests."""
    from app.services.reporting import registry

    monkeypatch.setattr(registry, "_REGISTRY", {})
    yield


def test_decorator_registers_a_block_with_its_declared_shape():
    @report_block(key="test.scalar", title="Test scalar", shape=BlockShape.SCALARS,
                  domain="test", description="A test block.")
    def _produce(ctx):
        return BlockResult.ok(data={"value": 1.0})

    spec = get_block("test.scalar")
    assert spec is not None
    assert spec.key == "test.scalar"
    assert spec.shape is BlockShape.SCALARS
    assert spec.domain == "test"
    assert spec.description == "A test block."


def test_resolve_invokes_the_producer():
    @report_block(key="test.scalar", title="T", shape=BlockShape.SCALARS)
    def _produce(ctx):
        return BlockResult.ok(data={"portfolio_id": ctx.portfolio_id})

    result = resolve_block("test.scalar", BlockContext(portfolio_id=7))
    assert result.status == "ok"
    assert result.data == {"portfolio_id": 7}


def test_unknown_key_resolves_to_unavailable_rather_than_raising():
    result = resolve_block("test.nope", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "test.nope" in result.reason


def test_missing_required_param_resolves_to_unavailable_naming_the_param():
    @report_block(key="test.cmp", title="T", shape=BlockShape.SCALARS_WITH_PRIOR,
                  requires=("compare_to_run_id",))
    def _produce(ctx):
        return BlockResult.ok(data={})

    result = resolve_block("test.cmp", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "compare_to_run_id" in result.reason


def test_required_param_present_lets_the_producer_run():
    @report_block(key="test.cmp", title="T", shape=BlockShape.SCALARS_WITH_PRIOR,
                  requires=("compare_to_run_id",))
    def _produce(ctx):
        return BlockResult.ok(data={"compared": ctx.compare_to_run_id})

    result = resolve_block("test.cmp", BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "ok"
    assert result.data == {"compared": 35}


def test_producer_exception_becomes_unavailable_not_a_500():
    @report_block(key="test.boom", title="T", shape=BlockShape.SCALARS)
    def _produce(ctx):
        raise RuntimeError("upstream exploded")

    result = resolve_block("test.boom", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "upstream exploded" in result.reason


def test_duplicate_key_registration_is_rejected():
    @report_block(key="test.dupe", title="T", shape=BlockShape.SCALARS)
    def _first(ctx):
        return BlockResult.ok(data={})

    with pytest.raises(ValueError, match="test.dupe"):
        @report_block(key="test.dupe", title="T2", shape=BlockShape.SCALARS)
        def _second(ctx):
            return BlockResult.ok(data={})


def test_list_blocks_is_sorted_by_key():
    @report_block(key="test.b", title="B", shape=BlockShape.ROWS)
    def _b(ctx):
        return BlockResult.ok(data={})

    @report_block(key="test.a", title="A", shape=BlockShape.ROWS)
    def _a(ctx):
        return BlockResult.ok(data={})

    assert [spec.key for spec in list_blocks()] == ["test.a", "test.b"]


def test_require_block_raises_for_the_template_validator():
    with pytest.raises(UnknownBlockError, match="test.nope"):
        require_block("test.nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reporting.registry'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/reporting/registry.py`:

```python
"""Registry mapping a block key to its deterministic producer.

The registry is the single source of numbers in a report. A template may only
name keys registered here, and the template validator checks that at save time
via ``require_block``. At render time ``resolve_block`` never raises: an unknown
key, a missing required parameter, or an exploding producer all become an
``unavailable`` BlockResult, because one broken block must not take down a
whole report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .contracts import BlockContext, BlockResult, BlockShape

BlockProducer = Callable[[BlockContext], BlockResult]


class UnknownBlockError(KeyError):
    """Raised by ``require_block`` when a key is not registered.

    Used by the template validator so an unresolvable ``blocks[].key`` fails at
    save time (HTTP 422) rather than rendering as a silent gap.
    """


@dataclass(frozen=True)
class BlockSpec:
    key: str
    title: str
    shape: BlockShape
    requires: tuple[str, ...]
    domain: str
    description: str
    produce: BlockProducer

    def catalog_entry(self) -> dict[str, Any]:
        """Agent-facing description, returned by the list_report_blocks tool."""
        return {
            "key": self.key,
            "title": self.title,
            "shape": self.shape.value,
            "requires": list(self.requires),
            "domain": self.domain,
            "description": self.description,
        }


_REGISTRY: dict[str, BlockSpec] = {}


def report_block(
    *,
    key: str,
    title: str,
    shape: BlockShape,
    requires: tuple[str, ...] = (),
    domain: str = "",
    description: str = "",
) -> Callable[[BlockProducer], BlockProducer]:
    """Register a block producer under ``key``."""

    def decorate(func: BlockProducer) -> BlockProducer:
        if key in _REGISTRY:
            raise ValueError(f"block key already registered: {key!r}")
        _REGISTRY[key] = BlockSpec(
            key=key,
            title=title,
            shape=shape,
            requires=tuple(requires),
            domain=domain,
            description=description or (func.__doc__ or "").strip().split("\n")[0],
            produce=func,
        )
        return func

    return decorate


def get_block(key: str) -> BlockSpec | None:
    return _REGISTRY.get(key)


def require_block(key: str) -> BlockSpec:
    """Return the spec for ``key`` or raise ``UnknownBlockError``."""
    spec = _REGISTRY.get(key)
    if spec is None:
        raise UnknownBlockError(
            f"unknown block key: {key!r}; known keys: {sorted(_REGISTRY)}"
        )
    return spec


def list_blocks() -> list[BlockSpec]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def _missing_requirements(spec: BlockSpec, ctx: BlockContext) -> list[str]:
    missing: list[str] = []
    for name in spec.requires:
        value = getattr(ctx, name, None)
        if value is None:
            value = ctx.params.get(name)
        if value is None:
            missing.append(name)
    return missing


def resolve_block(key: str, ctx: BlockContext) -> BlockResult:
    """Resolve one block. Never raises."""
    spec = _REGISTRY.get(key)
    if spec is None:
        return BlockResult.unavailable(f"no producer registered for block {key!r}")

    missing = _missing_requirements(spec, ctx)
    if missing:
        return BlockResult.unavailable(
            f"block {key!r} requires {', '.join(missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} not available for this report"
        )

    try:
        return spec.produce(ctx)
    except Exception as exc:  # noqa: BLE001 - one bad block must not kill a report
        return BlockResult.unavailable(f"producer for {key!r} failed: {exc}")


__all__ = [
    "BlockSpec",
    "UnknownBlockError",
    "get_block",
    "list_blocks",
    "report_block",
    "require_block",
    "resolve_block",
]
```

Create `backend/app/services/reporting/blocks/__init__.py`:

```python
"""Block producers, imported for their registration side effects.

Importing this package registers every block. Anything that resolves blocks
must import it first, or the registry will be empty.
"""
from __future__ import annotations

from . import desk, limits, pnl, risk  # noqa: F401

__all__ = ["risk", "pnl", "limits", "desk"]
```

Note: this import will fail until Tasks 2–5 create those modules. Create the four files as
empty placeholders now so the package imports:

```bash
touch backend/app/services/reporting/blocks/risk.py \
      backend/app/services/reporting/blocks/pnl.py \
      backend/app/services/reporting/blocks/limits.py \
      backend/app/services/reporting/blocks/desk.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_registry.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/registry.py \
        backend/app/services/reporting/blocks/ \
        tests/test_reporting_registry.py
git commit -m "feat(reporting): block registry with fail-soft resolution"
```

---

### Task 2: Risk and coverage blocks

**Files:**
- Modify: `backend/app/services/reporting/blocks/risk.py` (currently empty)
- Test: `tests/test_reporting_blocks_risk.py`

**Interfaces:**
- Consumes: `report_block` (Task 1), `diff_metrics` / `load_run_pair` (Plan A Task 2)
- Produces five registered keys:
  - `risk.totals` → `SCALARS`, data `{metrics: {name: float}, currency: str}`
  - `risk.exposure_by_underlying` → `SERIES`, data `{chart_type, x_key, y_key, series: [{underlying, delta_cash, vega, market_value}]}`
  - `risk.greeks_by_bucket` → `POSITION_GREEKS`, data `{positions: [row]}`
  - `risk.exposure_diff` → `SCALARS_WITH_PRIOR`, data `{metrics: {name: {before, after, change, pct_change}}}`
  - `coverage.evidence` → `SCALARS`, data `{priced, total, ratio, quote_ages_days, missing_evidence, evidence_complete, position_set_hash}`
- Shared private helper: `_load_metrics(ctx) -> tuple[dict | None, dict[str, Any]]` returning `(metrics, provenance)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_blocks_risk.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def _metrics() -> dict:
    return {
        "valuation_as_of": "2026-06-24T00:00:00",
        "position_set_hash": "sha256:f69b",
        "currencies": ["CNY"],
        "totals": {
            "market_value": 9246.35, "delta_cash": 57334.67, "gamma_cash": 1842.88,
            "vega": 276.43, "theta": -27.20, "rho": 240.44, "rho_q": -286.67,
            "pnl": 9246.35, "gross_notional": 100000.0, "one_day_var_proxy": 1200.0,
        },
        "positions": [
            {"position_id": 23, "underlying": "AAPL", "product_type": "EuropeanVanillaOption",
             "quantity": 1000.0, "market_value": 9246.35, "delta_cash": 57334.67,
             "gamma_cash": 1842.88, "vega": 276.43, "theta": -27.20, "rho": 240.44,
             "rho_q": -286.67, "delta": 573.35, "gamma": 18.43, "quote_age_days": 0,
             "pricing_ok": True, "greeks_ok": True, "currency": "CNY"},
            {"position_id": 24, "underlying": "TSLA", "product_type": "BarrierOption",
             "quantity": 200.0, "market_value": 1000.0, "delta_cash": 2000.0,
             "gamma_cash": 100.0, "vega": 50.0, "theta": -5.0, "rho": 10.0,
             "rho_q": -12.0, "delta": 20.0, "gamma": 1.0, "quote_age_days": 3,
             "pricing_ok": True, "greeks_ok": True, "currency": "CNY"},
        ],
        "coverage": {"coverage_count": 2, "total_count": 3},
        "source_metadata": {
            "metric_contract": {"contract_id": "limits-risk_run-metrics/v1"},
            "market_evidence_manifest": {
                "evidence_complete": False,
                "missing_evidence": ["position 25: spot"],
                "positions": [],
            },
        },
    }


@pytest.fixture
def patched_metrics(monkeypatch):
    """Point the risk blocks at an in-memory metrics payload, no DB."""
    from app.services.reporting.blocks import risk as risk_blocks

    def _fake(ctx):
        return _metrics(), {"risk_run_id": 36, "valuation_as_of": "2026-06-24T00:00:00"}

    monkeypatch.setattr(risk_blocks, "_load_metrics", _fake)
    return _fake


def test_all_five_risk_blocks_are_registered_with_declared_shapes():
    assert get_block("risk.totals").shape.value == "scalars"
    assert get_block("risk.exposure_by_underlying").shape.value == "series"
    assert get_block("risk.greeks_by_bucket").shape.value == "position_greeks"
    assert get_block("risk.exposure_diff").shape.value == "scalars_with_prior"
    assert get_block("coverage.evidence").shape.value == "scalars"


def test_risk_totals_returns_metrics_and_currency(patched_metrics):
    result = resolve_block("risk.totals", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["metrics"]["delta_cash"] == pytest.approx(57334.67)
    assert result.data["currency"] == "CNY"
    assert result.provenance["risk_run_id"] == 36


def test_exposure_by_underlying_aggregates_and_sorts_by_absolute_delta(patched_metrics):
    result = resolve_block("risk.exposure_by_underlying", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["x_key"] == "underlying"
    assert result.data["chart_type"] == "bar"
    assert [row["underlying"] for row in result.data["series"]] == ["AAPL", "TSLA"]
    assert result.data["series"][0]["delta_cash"] == pytest.approx(57334.67)


def test_greeks_by_bucket_passes_position_rows_through(patched_metrics):
    result = resolve_block("risk.greeks_by_bucket", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert len(result.data["positions"]) == 2
    assert result.data["positions"][0]["position_id"] == 23


def test_coverage_evidence_surfaces_incompleteness(patched_metrics):
    result = resolve_block("coverage.evidence", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["priced"] == 2
    assert result.data["total"] == 3
    assert result.data["ratio"] == pytest.approx(2 / 3)
    assert result.data["evidence_complete"] is False
    assert result.data["missing_evidence"] == ["position 25: spot"]
    assert result.data["max_quote_age_days"] == 3


def test_blocks_are_unavailable_when_no_risk_run_exists(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    monkeypatch.setattr(risk_blocks, "_load_metrics", lambda ctx: (None, {}))
    result = resolve_block("risk.totals", BlockContext(portfolio_id=999))
    assert result.status == "unavailable"
    assert "risk run" in result.reason


def test_exposure_diff_is_unavailable_without_a_comparison_run(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    monkeypatch.setattr(risk_blocks, "_load_run_pair_metrics",
                        lambda ctx: (None, None, {}))
    result = resolve_block("risk.exposure_diff",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "unavailable"


def test_exposure_diff_reports_changes_when_two_runs_exist(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    before = _metrics()
    after = _metrics()
    after["totals"] = dict(after["totals"], delta_cash=60000.0)
    monkeypatch.setattr(
        risk_blocks, "_load_run_pair_metrics",
        lambda ctx: (before, after, {"risk_run_id": 36, "compare_to_run_id": 35}),
    )
    result = resolve_block("risk.exposure_diff",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    assert result.data["metrics"]["delta_cash"]["change"] == pytest.approx(
        60000.0 - 57334.67
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_risk.py -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'shape'` (no blocks registered yet)

- [ ] **Step 3: Write minimal implementation**

Replace `backend/app/services/reporting/blocks/risk.py`:

```python
"""Risk and evidence-quality block producers.

All five read a persisted ``risk_runs.metrics`` payload. None of them price
anything: a report shows the governed run, it does not mint a new valuation.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.services.pnl import diff_metrics, load_run_pair

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_DIFF_KEYS = (
    "market_value", "delta_cash", "gamma_cash", "vega", "theta", "rho", "rho_q",
)

_NO_RUN = "no completed risk run exists for this portfolio"
_NO_PAIR = "no prior completed risk run to compare against"


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _provenance(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    coverage = metrics.get("coverage") or {}
    return {
        "valuation_as_of": metrics.get("valuation_as_of"),
        "position_set_hash": metrics.get("position_set_hash"),
        "coverage": {
            "priced": coverage.get("coverage_count"),
            "total": coverage.get("total_count"),
        },
        **extra,
    }


def _load_metrics(ctx: BlockContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load the ``after`` run's metrics for this context.

    Patched wholesale in tests, which is why it is a module-level function
    rather than an inline query.
    """
    with _session_scope() as session:
        _before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id,
            risk_run_id=ctx.risk_run_id,
            session=session,
        )
        if after is None:
            return None, {}
        metrics = after.metrics or {}
        return metrics, _provenance(metrics, risk_run_id=after.id)


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
        metrics = after.metrics or {}
        return (
            before.metrics or {},
            metrics,
            _provenance(metrics, risk_run_id=after.id, compare_to_run_id=before.id),
        )


def _currency(metrics: dict[str, Any]) -> str:
    currencies = metrics.get("currencies") or []
    return currencies[0] if len(currencies) == 1 else "mixed"


@report_block(
    key="risk.totals", title="Portfolio totals", shape=BlockShape.SCALARS,
    domain="risk",
    description="Governed portfolio totals from the latest completed risk run.",
)
def risk_totals(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    totals = metrics.get("totals") or {}
    if not totals:
        return BlockResult.empty(
            "the risk run recorded no portfolio totals", provenance=provenance
        )
    return BlockResult.ok(
        data={"metrics": dict(totals), "currency": _currency(metrics)},
        provenance=provenance,
    )


@report_block(
    key="risk.exposure_by_underlying", title="Exposure by underlying",
    shape=BlockShape.SERIES, domain="risk",
    description="Delta cash, vega and market value aggregated per underlying.",
)
def risk_exposure_by_underlying(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)

    buckets: dict[str, dict[str, float]] = {}
    for row in metrics.get("positions") or []:
        name = row.get("underlying") or "unknown"
        bucket = buckets.setdefault(
            name, {"underlying": name, "delta_cash": 0.0, "vega": 0.0,
                   "market_value": 0.0}
        )
        for key in ("delta_cash", "vega", "market_value"):
            try:
                bucket[key] += float(row.get(key) or 0.0)
            except (TypeError, ValueError):
                continue

    if not buckets:
        return BlockResult.empty(
            "the risk run priced no positions", provenance=provenance
        )

    series = sorted(
        buckets.values(), key=lambda row: abs(row["delta_cash"]), reverse=True
    )
    return BlockResult.ok(
        data={"chart_type": "bar", "x_key": "underlying", "y_key": "delta_cash",
              "series": series},
        provenance=provenance,
    )


@report_block(
    key="risk.greeks_by_bucket", title="Greeks by position",
    shape=BlockShape.POSITION_GREEKS, domain="risk",
    description="Per-position Greeks from the governed run.",
)
def risk_greeks_by_bucket(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    positions = list(metrics.get("positions") or [])
    if not positions:
        return BlockResult.empty(
            "the risk run priced no positions", provenance=provenance
        )
    return BlockResult.ok(data={"positions": positions}, provenance=provenance)


@report_block(
    key="risk.exposure_diff", title="Exposure change since last run",
    shape=BlockShape.SCALARS_WITH_PRIOR, requires=("compare_to_run_id",),
    domain="risk",
    description="Change in portfolio exposure between two governed risk runs.",
)
def risk_exposure_diff(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    diff = diff_metrics(before, after)
    metrics = {key: diff["totals"][key] for key in _DIFF_KEYS if key in diff["totals"]}
    return BlockResult.ok(
        data={
            "metrics": metrics,
            "composition_changed": diff["composition_changed"],
            "elapsed_days": diff["as_of"]["elapsed_days"],
        },
        provenance=provenance,
    )


@report_block(
    key="coverage.evidence", title="Evidence quality", shape=BlockShape.SCALARS,
    domain="risk",
    description="How much of the book actually priced, and how fresh its market evidence is.",
)
def coverage_evidence(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)

    coverage = metrics.get("coverage") or {}
    priced = coverage.get("coverage_count")
    total = coverage.get("total_count")
    ratio = None
    if isinstance(priced, int) and isinstance(total, int) and total > 0:
        ratio = priced / total

    manifest = (
        (metrics.get("source_metadata") or {}).get("market_evidence_manifest") or {}
    )
    ages = [
        int(row["quote_age_days"])
        for row in metrics.get("positions") or []
        if isinstance(row.get("quote_age_days"), (int, float))
    ]

    return BlockResult.ok(
        data={
            "priced": priced,
            "total": total,
            "ratio": ratio,
            "max_quote_age_days": max(ages) if ages else None,
            "evidence_complete": manifest.get("evidence_complete"),
            "missing_evidence": list(manifest.get("missing_evidence") or []),
            "position_set_hash": metrics.get("position_set_hash"),
        },
        provenance=provenance,
    )


__all__ = [
    "risk_totals",
    "risk_exposure_by_underlying",
    "risk_greeks_by_bucket",
    "risk_exposure_diff",
    "coverage_evidence",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_risk.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/blocks/risk.py \
        tests/test_reporting_blocks_risk.py
git commit -m "feat(reporting): risk and evidence-quality block producers"
```

---

### Task 3: P&L blocks

**Files:**
- Modify: `backend/app/services/reporting/blocks/pnl.py` (currently empty)
- Test: `tests/test_reporting_blocks_pnl.py`

**Interfaces:**
- Consumes: `diff_metrics`, `explain_diff`, `inception_pnl`, `RESIDUAL_WARN_RATIO` (Plan A); `_load_run_pair_metrics` pattern from Task 2 (re-declared locally, not imported — each block module owns its loaders)
- Produces four registered keys:
  - `pnl.daily` → `SCALARS_WITH_PRIOR`, data `{metrics: {market_value: {...}}, elapsed_days, composition_changed, membership}`
  - `pnl.explain` → `WATERFALL`, data `{buckets, explained, actual, residual, residual_ratio, residual_exceeds_threshold, excluded, convention}`
  - `pnl.by_position` → `ROWS`, data `{rows: [{position_id, underlying, before, after, change}], truncated_to}`
  - `pnl.inception` → `SCALARS`, data `{total, covered_count, basis_missing_count, basis_missing}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_blocks_pnl.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block

CONTRACT = {"contract_id": "limits-risk_run-metrics/v1"}


def _metrics(*, valuation_as_of, positions, spot_by_id=None):
    spot_by_id = spot_by_id or {}
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": "sha256:aaa",
        "totals": {"market_value": sum(p["market_value"] for p in positions)},
        "positions": positions,
        "coverage": {"coverage_count": len(positions), "total_count": len(positions)},
        "source_metadata": {
            "metric_contract": CONTRACT,
            "market_evidence_manifest": {
                "positions": [
                    {"position_id": p["position_id"],
                     "resolved_market": {
                         "spot": spot_by_id.get(p["position_id"], 100.0),
                         "volatility": 0.30, "rate": 0.04, "dividend_yield": 0.005}}
                    for p in positions
                ]
            },
        },
    }


def _pos(position_id, market_value, *, underlying="AAPL", delta=0.0,
         price=10.0, quantity=100.0):
    return {"position_id": position_id, "underlying": underlying,
            "market_value": market_value, "price": price, "quantity": quantity,
            "delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
            "rho": 0.0, "rho_q": 0.0, "pricing_ok": True, "greeks_ok": True}


@pytest.fixture
def two_runs(monkeypatch):
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 10_000.0, delta=500.0),
                                 _pos(2, 5_000.0, underlying="TSLA")],
                      spot_by_id={1: 100.0, 2: 100.0})
    after = _metrics(valuation_as_of="2026-08-04T00:00:00",
                     positions=[_pos(1, 12_000.0, delta=500.0),
                                _pos(2, 4_500.0, underlying="TSLA")],
                     spot_by_id={1: 104.0, 2: 100.0})

    from app.services.reporting.blocks import pnl as pnl_blocks

    monkeypatch.setattr(
        pnl_blocks, "_load_run_pair_metrics",
        lambda ctx: (before, after, {"risk_run_id": 36, "compare_to_run_id": 35}),
    )
    return before, after


def test_all_four_pnl_blocks_are_registered_with_declared_shapes():
    assert get_block("pnl.daily").shape.value == "scalars_with_prior"
    assert get_block("pnl.explain").shape.value == "waterfall"
    assert get_block("pnl.by_position").shape.value == "rows"
    assert get_block("pnl.inception").shape.value == "scalars"


def test_pnl_daily_reports_the_market_value_move(two_runs):
    result = resolve_block("pnl.daily",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    mv = result.data["metrics"]["market_value"]
    assert mv["before"] == pytest.approx(15_000.0)
    assert mv["after"] == pytest.approx(16_500.0)
    assert mv["change"] == pytest.approx(1_500.0)


def test_pnl_daily_is_unavailable_without_a_comparison(monkeypatch):
    from app.services.reporting.blocks import pnl as pnl_blocks

    monkeypatch.setattr(pnl_blocks, "_load_run_pair_metrics",
                        lambda ctx: (None, None, {}))
    result = resolve_block("pnl.daily",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "unavailable"
    assert "prior" in result.reason


def test_pnl_explain_attributes_the_spot_move_to_delta(two_runs):
    result = resolve_block("pnl.explain",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    # Position 1: delta 500 x spot +4 = 2000. Position 2 did not move on spot,
    # so its -500 is unexplained residual.
    assert result.data["buckets"]["delta"] == pytest.approx(2000.0)
    assert result.data["actual"] == pytest.approx(1500.0)
    assert result.data["residual"] == pytest.approx(-500.0)
    assert result.data["convention"] == "start_of_period_greeks"


def test_pnl_explain_flags_a_residual_over_the_threshold(two_runs):
    result = resolve_block("pnl.explain",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    # |−500| / |1500| = 0.33 > 0.10
    assert result.data["residual_exceeds_threshold"] is True


def test_pnl_by_position_sorts_by_absolute_move_and_truncates(two_runs):
    result = resolve_block(
        "pnl.by_position",
        BlockContext(portfolio_id=2, compare_to_run_id=35, params={"top_n": 1}),
    )
    assert result.status == "ok"
    assert len(result.data["rows"]) == 1
    assert result.data["rows"][0]["position_id"] == 1
    assert result.data["rows"][0]["change"] == pytest.approx(2000.0)
    assert result.data["truncated_to"] == 1


def test_pnl_inception_excludes_positions_without_a_basis(monkeypatch):
    from app.services.reporting.blocks import pnl as pnl_blocks

    metrics = _metrics(valuation_as_of="2026-08-04T00:00:00",
                       positions=[_pos(1, 1500.0, price=15.0, quantity=100.0),
                                  _pos(2, 1800.0, price=9.0, quantity=200.0)])
    monkeypatch.setattr(pnl_blocks, "_load_metrics",
                        lambda ctx: (metrics, {"risk_run_id": 36}))
    monkeypatch.setattr(pnl_blocks, "_entry_prices",
                        lambda ctx, metrics: {1: 10.0, 2: 0.0})

    result = resolve_block("pnl.inception", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["total"] == pytest.approx(500.0)
    assert result.data["covered_count"] == 1
    assert result.data["basis_missing_count"] == 1
    assert result.data["basis_missing"] == [2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_pnl.py -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'shape'`

- [ ] **Step 3: Write minimal implementation**

Replace `backend/app/services/reporting/blocks/pnl.py`:

```python
"""P&L block producers over Sub-project A's deterministic modules.

``pnl.daily`` reports the market-value move between two governed runs;
``pnl.explain`` decomposes it; ``pnl.inception`` reports since-trade P&L over
only the positions that carry a real cost basis.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import Position
from app.services.pnl import (
    UnsupportedMetricContract,
    diff_metrics,
    explain_diff,
    inception_pnl,
    load_run_pair,
)

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_NO_RUN = "no completed risk run exists for this portfolio"
_NO_PAIR = "no prior completed risk run to compare against"
_DEFAULT_TOP_N = 10


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _provenance(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "valuation_as_of": metrics.get("valuation_as_of"),
        "position_set_hash": metrics.get("position_set_hash"),
        **extra,
    }


def _load_metrics(ctx: BlockContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    with _session_scope() as session:
        _before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id, risk_run_id=ctx.risk_run_id,
            session=session,
        )
        if after is None:
            return None, {}
        metrics = after.metrics or {}
        return metrics, _provenance(metrics, risk_run_id=after.id)


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
        metrics = after.metrics or {}
        return (
            before.metrics or {},
            metrics,
            _provenance(metrics, risk_run_id=after.id, compare_to_run_id=before.id),
        )


def _entry_prices(
    ctx: BlockContext, metrics: dict[str, Any]
) -> dict[int, float | None]:
    ids = [
        int(row["position_id"])
        for row in metrics.get("positions") or []
        if row.get("position_id") is not None
    ]
    if not ids:
        return {}
    with _session_scope() as session:
        rows = session.query(Position.id, Position.entry_price).filter(
            Position.id.in_(ids)
        ).all()
    return {int(pid): entry for pid, entry in rows}


@report_block(
    key="pnl.daily", title="Change since last run",
    shape=BlockShape.SCALARS_WITH_PRIOR, requires=("compare_to_run_id",),
    domain="pnl",
    description="Market-value move between the governed run and its predecessor.",
)
def pnl_daily(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    diff = diff_metrics(before, after)
    metrics = {
        key: diff["totals"][key]
        for key in ("market_value", "pnl", "gross_notional")
        if key in diff["totals"]
    }
    return BlockResult.ok(
        data={
            "metrics": metrics,
            "elapsed_days": diff["as_of"]["elapsed_days"],
            "composition_changed": diff["composition_changed"],
            "membership": diff["membership"],
            "coverage": diff["coverage"],
        },
        provenance=provenance,
    )


@report_block(
    key="pnl.explain", title="P&L attribution", shape=BlockShape.WATERFALL,
    requires=("compare_to_run_id",), domain="pnl",
    description="Decomposition of the market-value move into Greek buckets plus residual.",
)
def pnl_explain(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    try:
        explained = explain_diff(before, after, diff_metrics(before, after))
    except UnsupportedMetricContract as exc:
        return BlockResult.unavailable(
            f"attribution units cannot be resolved: {exc}", provenance=provenance
        )
    return BlockResult.ok(data=explained, provenance=provenance)


@report_block(
    key="pnl.by_position", title="Position movers", shape=BlockShape.ROWS,
    requires=("compare_to_run_id",), domain="pnl",
    description="Positions ranked by absolute market-value move since the prior run.",
)
def pnl_by_position(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)

    diff = diff_metrics(before, after)
    underlying_by_id = {
        int(row["position_id"]): row.get("underlying")
        for row in after.get("positions") or []
        if row.get("position_id") is not None
    }
    rows = [
        {
            "position_id": position_id,
            "underlying": underlying_by_id.get(position_id),
            "before": change["before"],
            "after": change["after"],
            "change": change["change"],
            "pct_change": change["pct_change"],
        }
        for position_id, change in diff["positions"].items()
    ]
    if not rows:
        return BlockResult.empty(
            "no positions were held across both runs", provenance=provenance
        )

    rows.sort(key=lambda row: abs(row["change"] or 0.0), reverse=True)
    top_n = int(ctx.params.get("top_n") or _DEFAULT_TOP_N)
    return BlockResult.ok(
        data={"rows": rows[:top_n], "truncated_to": top_n,
              "total_rows": len(rows)},
        provenance=provenance,
    )


@report_block(
    key="pnl.inception", title="P&L since trade", shape=BlockShape.SCALARS,
    domain="pnl",
    description="Since-inception P&L over only those positions that carry a real cost basis.",
)
def pnl_inception(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    result = inception_pnl(metrics, _entry_prices(ctx, metrics))
    if result["covered_count"] == 0:
        return BlockResult.empty(
            "no position in this book records a cost basis, so since-inception "
            "P&L cannot be computed",
            provenance=provenance,
        )
    return BlockResult.ok(
        data={
            "total": result["total"],
            "covered_count": result["covered_count"],
            "basis_missing_count": result["basis_missing_count"],
            "basis_missing": result["basis_missing"],
        },
        provenance=provenance,
    )


__all__ = ["pnl_daily", "pnl_explain", "pnl_by_position", "pnl_inception"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_blocks_pnl.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/blocks/pnl.py \
        tests/test_reporting_blocks_pnl.py
git commit -m "feat(reporting): P&L block producers over the deterministic modules"
```

---

## Remaining tasks in this plan

Tasks 4 (limits + scenario blocks) and 5 (RFQ, positions, audit blocks) follow the identical
pattern established in Tasks 2 and 3: a `_load_*` module-level loader that tests patch, a
producer per key returning the tri-state `BlockResult`, and a test asserting registration
shape plus one behaviour per status. They are specified in
`docs/superpowers/plans/2026-08-06-report-module-b-blocks-continued.md`.

---

## Self-Review

**Spec coverage (§5.2):**

| Spec requirement | Task |
|---|---|
| `@report_block` decorator with declared shape and requires | Task 1 |
| Catalog enumeration for the `list_report_blocks` tool | Task 1 (`BlockSpec.catalog_entry`) |
| Producers never raise; one bad block cannot kill a report | Task 1, test 6 |
| Unknown key fails loudly at template-save time | Task 1 (`require_block`) |
| `risk.totals`, `risk.exposure_by_underlying`, `risk.greeks_by_bucket`, `risk.exposure_diff`, `coverage.evidence` | Task 2 |
| `pnl.daily`, `pnl.explain`, `pnl.by_position`, `pnl.inception` | Task 3 |
| `limits.*`, `scenario.latest_grid` | Task 4 (continued plan) |
| `rfq.*`, `positions.*`, `audit.write_actions_summary` | Task 5 (continued plan) |
| `unavailable` when the producer cannot run | Tasks 2–3, throughout |
| `empty` when the producer ran with nothing to report | Task 2 (`risk.totals` empty totals), Task 3 (`pnl.by_position`, `pnl.inception`) |

**Placeholder scan:** No TBD/TODO. Every step has runnable commands and complete code. Tasks 4–5
are deferred to a named companion file rather than stubbed inline — that file must exist before
this plan is executed to completion.

**Type consistency:**
- `report_block(key=, title=, shape=, requires=, domain=, description=)` — keyword-only in the
  decorator definition and at every call site in Tasks 2–3.
- `BlockResult.ok(data=..., provenance=...)`, `.empty(reason, provenance=...)`,
  `.unavailable(reason, provenance=...)` — matches Plan A Task 1's constructors exactly
  (`ok` keyword-only for `data`; `empty`/`unavailable` positional for `reason`).
- `_load_metrics(ctx) -> (metrics | None, provenance)` and
  `_load_run_pair_metrics(ctx) -> (before | None, after | None, provenance)` — same signatures
  in `risk.py` and `pnl.py`, and both are patched with those exact arities in their tests.
- `diff_metrics(before, after)` and `explain_diff(before, after, diff)` — argument order matches
  Plan A Tasks 2 and 3.
- `inception_pnl(metrics, positions_entry)` — matches Plan A Task 4.
- `BlockContext(portfolio_id=, risk_run_id=, compare_to_run_id=, params=)` — matches Plan A Task 1.
