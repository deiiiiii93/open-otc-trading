# Scenario Test Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Scenario Test feature that drives QuantArk's `stresstest` engine end-to-end — predefined + custom + saved scenarios, async persisted runs, structured results + HTML report + exports — reachable from the agent (tool + skill) and a dedicated frontend page.

**Architecture:** Approach 1 (real `EquityPortfolio` bridge). A new bridge assembles a genuine QuantArk `EquityPortfolio` from DB positions + a pricing parameter profile (reusing the risk path's `_pricing_position_context` resolver), then `StressTestEngine.run_static_scenarios` reprices it under each `Scenario`. Runs are queued + persisted like batch-pricing (`scenario_test_runs` + `TaskKind.SCENARIO_TEST`). Results shaped via `ResultAggregator`, exported via `ResultExporter`/`ReportGenerator`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Alembic, Pydantic, pytest; QuantArk packages (`portfolio`, `priceenv`, `asset`, `stresstest`); React + TypeScript + Vitest frontend.

---

## Conventions & Setup

- **Worktree:** all work happens in `/Users/fuxinyao/open-otc-trading/.claude/worktrees/scenario-test` (branch `worktree-scenario-test`). Run every command from there.
- **Backend tests:** `python -m pytest tests/<file> -v` from the worktree root. `pyproject.toml` sets `pythonpath = ["backend"]`, so `from app...` resolves to the worktree's `backend/app`. **Do NOT use `python -c` to import `app`** — the venv `.pth` resolves it to the *main* checkout, not the worktree (project-memory gotcha).
- **Frontend tests:** `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`.
- **Commit after every task** with the message shown in the task's final step.
- **Naming:** everything uses the `scenario_test` namespace to avoid colliding with the legacy spot×vol grid (`RiskRun.scenario_cells`).
- **Spec:** `docs/superpowers/specs/2026-06-08-scenario-test-design.md`.

---

## File Structure

**Create:**
- `backend/alembic/versions/0026_scenario_test_runs.py` — migration: `scenario_test_runs` table + `task_runs.scenario_test_run_id`.
- `backend/app/services/scenario_test_bridge.py` — DB positions + per-position markets → `EquityPortfolio` (the heart).
- `backend/app/services/domains/scenario_catalog.py` — `ScenarioLibrary`/`ScenarioBuilder`/`ScenarioStorage` wrappers + spec resolution.
- `backend/app/services/domains/scenario_test.py` — pipeline: build → resolve → engine → aggregate → shape → export.
- `backend/app/services/scenario_test_runner.py` — queue + async execute (mirrors `batch_pricing`).
- `backend/app/tools/scenario_test.py` — agent `@tool` wrappers.
- `backend/app/skills/workflows/risk/run-scenario-test/SKILL.md` — workflow skill.
- `backend/app/skills/references/risk/scenario-test.md` — scenario taxonomy reference.
- `frontend/src/routes/ScenarioTest.tsx`, `ScenarioTest.css`, `ScenarioTest.test.tsx`, `ScenarioTest.live.tsx` — page.
- `tests/test_scenario_test_bridge.py`, `tests/test_scenario_catalog.py`, `tests/test_scenario_test_engine.py`, `tests/test_scenario_test_runner.py`, `tests/test_scenario_test_api.py`, `tests/test_scenario_test_tools.py` — tests.

**Modify:**
- `backend/app/models.py` — `TaskKind.SCENARIO_TEST`, `ScenarioTestRun` model, `TaskRun.scenario_test_run_id` + relationship.
- `backend/app/schemas.py` — `ScenarioStressSpec`, `ScenarioSpec`, `ScenarioTestRunRequest`, `ScenarioTestRunOut`, `ScenarioLibraryOut`.
- `backend/app/main.py` — `/api/scenario-test/*` endpoints.
- `backend/app/tools/__init__.py` — register tools in `QUANT_AGENT_TOOLS`.
- `backend/app/services/agents.py` — add tool names to `DEEP_AGENT_TOOL_NAMES`.
- `backend/app/config.py` — `scenario_sets_dir` + `scenario_test_output_dir` settings.
- `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py`, `tests/test_remaining_workflow_skills_phase3.py`, `tests/test_reference_docs.py`, `tests/test_routing_table.py` — catalog/count/routing updates.
- `frontend/src/main.tsx` + `frontend/src/components/Sidebar.tsx` — route + nav entry.
- `frontend/src/api/*` + `frontend/src/types.ts` — API client + types.

---

## Phase 1 — Data Model & Migration

### Task 1: `ScenarioTestRun` model + `TaskKind.SCENARIO_TEST` + `TaskRun` FK

**Files:**
- Modify: `backend/app/models.py`
- Test: `tests/test_scenario_test_bridge.py` (temporary model smoke test, kept)

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_test_bridge.py`:

```python
from app import database
from app.models import ScenarioTestRun, TaskKind, TaskRun


def test_scenario_test_kind_exists():
    assert TaskKind.SCENARIO_TEST.value == "scenario_test"


def test_scenario_test_run_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'t.sqlite'}")
    database.reset_engine_for_tests() if hasattr(database, "reset_engine_for_tests") else None
    database.init_db()
    with database.SessionLocal() as session:
        from app.models import Portfolio, PricingParameterProfile
        pf = Portfolio(name="P1", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run = ScenarioTestRun(
            portfolio_id=pf.id,
            pricing_parameter_profile_id=None,
            status="queued",
            scenario_spec={"predefined": ["market_crash"]},
            config={"calculate_greeks": True},
            results={},
            excluded_positions=[],
            artifacts={},
            resolved_position_ids=[],
        )
        session.add(run)
        session.flush()
        task = TaskRun(kind=TaskKind.SCENARIO_TEST.value, status="queued",
                       portfolio_id=pf.id, scenario_test_run_id=run.id)
        session.add(task)
        session.flush()
        assert task.scenario_test_run.id == run.id
        assert run.task_runs[0].id == task.id
```

> If `database` has no `reset_engine_for_tests`, delete that line — `tests/conftest.py` already isolates the DB. Check `tests/conftest.py` for the standard DB fixture and prefer it (e.g. a `session` fixture) over manual setup if one exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_bridge.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScenarioTestRun'` / `AttributeError: SCENARIO_TEST`.

- [ ] **Step 3: Add `TaskKind.SCENARIO_TEST`**

In `backend/app/models.py`, in `class TaskKind(str, Enum)` (currently only `BATCH_PRICING = "batch_pricing"`), add:

```python
class TaskKind(str, Enum):
    BATCH_PRICING = "batch_pricing"
    SCENARIO_TEST = "scenario_test"
```

(Keep any other existing members — only add the new line.)

- [ ] **Step 4: Add the `ScenarioTestRun` model**

In `backend/app/models.py`, after the `RiskRun` class, add:

```python
class ScenarioTestRun(Base):
    __tablename__ = "scenario_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True
    )
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    scenario_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped["Portfolio"] = relationship()
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="scenario_test_run")
```

- [ ] **Step 5: Add the `TaskRun.scenario_test_run_id` FK + relationship**

In `backend/app/models.py`, inside `class TaskRun`, after the `risk_run_id` column add:

```python
    scenario_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_test_runs.id"), index=True, nullable=True
    )
```

and after the `risk_run` relationship add:

```python
    scenario_test_run: Mapped["ScenarioTestRun | None"] = relationship(
        back_populates="task_runs"
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_bridge.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py tests/test_scenario_test_bridge.py
git commit -m "feat(scenario-test): ScenarioTestRun model + SCENARIO_TEST task kind"
```

---

### Task 2: Alembic migration `0026_scenario_test_runs`

**Files:**
- Create: `backend/alembic/versions/0026_scenario_test_runs.py`

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/0026_scenario_test_runs.py`:

```python
"""scenario test runs

Revision ID: 0026_scenario_test_runs
Revises: 0025_position_kind
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0026_scenario_test_runs"
down_revision = "0025_position_kind"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_table("scenario_test_runs"):
        op.create_table(
            "scenario_test_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column(
                "pricing_parameter_profile_id", sa.Integer(),
                sa.ForeignKey("pricing_parameter_profiles.id"), nullable=True,
            ),
            sa.Column("resolved_position_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("scenario_spec", sa.JSON(), nullable=True),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("results", sa.JSON(), nullable=True),
            sa.Column("excluded_positions", sa.JSON(), nullable=True),
            sa.Column("artifacts", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_scenario_test_runs_portfolio_id", "scenario_test_runs", ["portfolio_id"])
        op.create_index(
            "ix_scenario_test_runs_pricing_parameter_profile_id",
            "scenario_test_runs", ["pricing_parameter_profile_id"],
        )

    if "scenario_test_run_id" not in _columns("task_runs"):
        with op.batch_alter_table("task_runs") as batch:
            batch.add_column(sa.Column("scenario_test_run_id", sa.Integer(), nullable=True))
        op.create_index(
            "ix_task_runs_scenario_test_run_id", "task_runs", ["scenario_test_run_id"]
        )


def downgrade() -> None:
    if "scenario_test_run_id" in _columns("task_runs"):
        op.drop_index("ix_task_runs_scenario_test_run_id", table_name="task_runs")
        with op.batch_alter_table("task_runs") as batch:
            batch.drop_column("scenario_test_run_id")
    if _has_table("scenario_test_runs"):
        op.drop_table("scenario_test_runs")
```

> **Migration discipline (project memory):** this migration uses only `sa`/`op` Core ops — never ORM models or services. Do not add a FK constraint on `task_runs.scenario_test_run_id` via batch on SQLite if it complains; the index + nullable column is sufficient for the app (the ORM declares the relationship).

- [ ] **Step 2: Apply the migration against a scratch DB**

Run:
```bash
DATABASE_URL="sqlite:////tmp/scenario_mig.sqlite" python -m alembic upgrade head
DATABASE_URL="sqlite:////tmp/scenario_mig.sqlite" python -m alembic downgrade -1
DATABASE_URL="sqlite:////tmp/scenario_mig.sqlite" python -m alembic upgrade head
rm -f /tmp/scenario_mig.sqlite
```
Expected: each command exits 0; `alembic heads` now reports `0026_scenario_test_runs (head)`.

> Do NOT run this against `data/open_otc.sqlite3` (the live DB). Use a scratch path as shown. Live-DB migration happens later, on the user's say-so.

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/0026_scenario_test_runs.py
git commit -m "feat(scenario-test): alembic 0026 scenario_test_runs + task_runs FK"
```

---

## Phase 2 — Bridge & Catalog

### Task 3: `scenario_test_bridge.build_equity_portfolio`

**Files:**
- Create: `backend/app/services/scenario_test_bridge.py`
- Test: `tests/test_scenario_test_bridge.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scenario_test_bridge.py`:

```python
from types import SimpleNamespace

from app.schemas import PricingEnvironmentSnapshot
from app.services import scenario_test_bridge


def _pos(pid, underlying, qty, mapping_status="manual"):
    # Minimal Position-like object the bridge needs. Real positions come from DB.
    return SimpleNamespace(
        id=pid, underlying=underlying, quantity=qty,
        mapping_status=mapping_status, mapping_error=None, status="open",
        product_type="european_vanilla", product_kwargs={"initial_price": 100.0},
        engine_name="BlackScholesEngine", engine_kwargs={},
    )


def test_excludes_unsupported_positions(monkeypatch):
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position",
                        lambda p, m: SimpleNamespace(name="prod"))
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace(name="engine"))
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env",
                        lambda m: SimpleNamespace(name="env"))

    positions = [_pos(1, "AAPL", 10), _pos(2, "AAPL", 5, mapping_status="unsupported")]
    markets = {1: PricingEnvironmentSnapshot(spot=100.0),
               2: PricingEnvironmentSnapshot(spot=100.0)}

    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="test")

    assert len(portfolio) == 1                       # only position 1 added
    assert "AAPL" in portfolio.pricing_environments  # one env per underlying
    assert excluded == [{"position_id": 2, "reason": "Position mapping status is unsupported"}]


def test_zero_quantity_excluded(monkeypatch):
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position",
                        lambda p, m: SimpleNamespace())
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace())
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env",
                        lambda m: SimpleNamespace())
    positions = [_pos(3, "TSLA", 0)]
    markets = {3: PricingEnvironmentSnapshot(spot=200.0)}
    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="t")
    assert len(portfolio) == 0
    assert excluded[0]["reason"] == "Position quantity is zero"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_bridge.py -k "excludes or zero" -v`
Expected: FAIL — `ModuleNotFoundError: app.services.scenario_test_bridge`.

- [ ] **Step 3: Implement the bridge**

Create `backend/app/services/scenario_test_bridge.py`:

```python
"""Bridge: DB positions + per-position market snapshots -> QuantArk EquityPortfolio.

The single new abstraction for the scenario-test feature. Pure: it takes already
resolved per-position market snapshots (from risk_engine._pricing_position_context)
and assembles a QuantArk EquityPortfolio. No DB / profile logic lives here.
"""
from __future__ import annotations

from typing import Any

from app.schemas import PricingEnvironmentSnapshot
from app.services import quantark


def build_equity_portfolio(
    positions: list[Any],
    position_markets: dict[int, PricingEnvironmentSnapshot],
    *,
    portfolio_name: str,
) -> tuple[Any, list[dict]]:
    """Assemble an EquityPortfolio. Returns (portfolio, excluded).

    `excluded` is [{"position_id": id, "reason": str}] for positions dropped by the
    same policy risk runs use (`risk_pricing_exclusion`), plus zero-quantity guards.
    Pricing environments are keyed by underlying (one per underlying — the correct
    stress baseline); the first position seen for an underlying seeds its env.
    """
    quantark.ensure_quantark_path()
    from portfolio import EquityPortfolio  # imported after path is ensured

    excluded: list[dict] = []
    pricing_environments: dict[str, Any] = {}
    buildable: list[tuple[Any, PricingEnvironmentSnapshot, str]] = []

    for position in positions:
        reason = quantark.risk_pricing_exclusion(position)
        if reason:
            excluded.append({"position_id": position.id, "reason": reason})
            continue
        if float(getattr(position, "quantity", 0) or 0) == 0.0:
            excluded.append({"position_id": position.id, "reason": "Position quantity is zero"})
            continue
        market = position_markets.get(position.id)
        if market is None:
            excluded.append({"position_id": position.id, "reason": "No market snapshot resolved"})
            continue
        underlying = str(position.underlying)
        if underlying not in pricing_environments:
            pricing_environments[underlying] = quantark.build_pricing_env(market)
        buildable.append((position, market, underlying))

    portfolio = EquityPortfolio(
        portfolio_name=portfolio_name,
        pricing_environments=pricing_environments,
    )
    for position, market, underlying in buildable:
        product = quantark.build_product_for_position(position, market)
        engine = quantark.build_engine_for_position(position, market)
        portfolio.add_position(
            product=product,
            quantity=float(position.quantity),
            entry_price=0.0,  # unused for stress P&L (value = market_value, not entry)
            underlying=underlying,
            engine=engine,
        )
    return portfolio, excluded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_bridge.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scenario_test_bridge.py tests/test_scenario_test_bridge.py
git commit -m "feat(scenario-test): EquityPortfolio bridge with risk-parity exclusions"
```

> **Learning-mode contribution (a):** the exclusion + zero-quantity policy and the entry_price choice above are the reference impl. During execution, offer the user the chance to author the `for position in positions:` exclusion loop themselves — it encodes the desk's "what counts as priceable" judgment.

---

### Task 4: `scenario_catalog` — predefined + custom builder + resolve

**Files:**
- Create: `backend/app/services/domains/scenario_catalog.py`
- Modify: `backend/app/config.py` (add `scenario_sets_dir`, `scenario_test_output_dir`)
- Test: `tests/test_scenario_catalog.py`

- [ ] **Step 1: Add config settings**

In `backend/app/config.py`, in the `Settings` model, add two fields next to the other path settings (e.g. near `quantark_path`):

```python
    scenario_sets_dir: str = "data/scenario_sets"
    scenario_test_output_dir: str = "outputs/scenario_test"
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_scenario_catalog.py`:

```python
import pytest

from app.services.domains import scenario_catalog


def test_list_predefined_includes_market_crash():
    names = {s["name"] for s in scenario_catalog.list_predefined()}
    assert "Market Crash" in names
    assert any("1987" in n or "Black Monday" in n for n in names)


def test_build_custom_spot_and_vol():
    spec = {
        "name": "My Shock",
        "stresses": [
            {"param": "spot", "stress_type": "PERCENTAGE", "value": -0.2, "level": "portfolio"},
            {"param": "vol", "stress_type": "ABSOLUTE", "value": 0.05, "level": "portfolio"},
        ],
    }
    scenario = scenario_catalog.build_custom(spec)
    assert scenario.name == "My Shock"
    assert len(scenario.stresses) == 2


def test_build_custom_rejects_unknown_param():
    spec = {"name": "bad", "stresses": [{"param": "spread", "value": 0.01}]}
    with pytest.raises(ValueError, match="param"):
        scenario_catalog.build_custom(spec)


def test_resolve_scenarios_predefined_plus_custom():
    request = {
        "predefined": ["market_crash"],
        "custom": [{"name": "C1", "stresses": [{"param": "spot", "value": -0.1}]}],
    }
    scenarios = scenario_catalog.resolve_scenarios(request)
    assert len(scenarios) == 2
    assert {s.name for s in scenarios} >= {"C1"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: scenario_catalog`.

- [ ] **Step 4: Implement the catalog (predefined + custom + resolve)**

Create `backend/app/services/domains/scenario_catalog.py`:

```python
"""Scenario authoring: thin wrappers over QuantArk's stresstest scenario layer."""
from __future__ import annotations

from typing import Any

from app.services import quantark

# v1 equity stress params -> ScenarioBuilder method name.
_PARAM_TO_METHOD = {
    "spot": "spot_stress",
    "vol": "vol_stress",
    "rate": "rate_stress",
    "dividend": "div_yield_stress",
}

# Predefined library keys -> ScenarioLibrary factory method name.
_PREDEFINED = {
    "market_crash": "market_crash",
    "market_rally": "market_rally",
    "vol_spike": "vol_spike",
    "vol_crush": "vol_crush",
    "rate_hike": "rate_hike",
    "rate_cut": "rate_cut",
    "severe_downturn": "severe_downturn",
    "inflation_shock": "inflation_shock",
    "black_monday_1987": "black_monday_1987",
    "financial_crisis_2008": "financial_crisis_2008",
    "covid_crash_2020": "covid_crash_2020",
}


def _imports():
    quantark.ensure_quantark_path()
    from stresstest.scenario.scenario_builder import ScenarioBuilder
    from stresstest.scenario.scenario_library import ScenarioLibrary
    from stresstest.stress.stress_types import StressType, StressLevel
    return ScenarioBuilder, ScenarioLibrary, StressType, StressLevel


def list_predefined() -> list[dict]:
    """Expose the curated predefined set with both a stable `key` (used by the
    API/UI) and the human `name` from the QuantArk Scenario."""
    _, ScenarioLibrary, _, _ = _imports()
    out: list[dict] = []
    for key, method in _PREDEFINED.items():
        scenario = getattr(ScenarioLibrary, method)()
        out.append({
            "key": key,
            "name": scenario.name,
            "description": getattr(scenario, "description", ""),
            "num_stresses": len(scenario.stresses),
            "metadata": getattr(scenario, "metadata", {}),
        })
    return out


def build_custom(spec: dict[str, Any]) -> Any:
    """Build a Scenario from a validated spec. Raises ValueError on bad input."""
    ScenarioBuilder, _, StressType, _ = _imports()
    builder = ScenarioBuilder().name(spec["name"])
    if spec.get("description"):
        builder = builder.description(spec["description"])
    stresses = spec.get("stresses") or []
    if not stresses:
        raise ValueError("A custom scenario needs at least one stress")
    for st in stresses:
        param = str(st.get("param", "")).lower()
        if param not in _PARAM_TO_METHOD:
            raise ValueError(
                f"Unsupported stress param {param!r}; v1 supports {sorted(_PARAM_TO_METHOD)}"
            )
        stress_type = StressType[str(st.get("stress_type", "PERCENTAGE")).upper()]
        value = float(st["value"])
        level = str(st.get("level", "portfolio")).lower()
        method = getattr(builder, _PARAM_TO_METHOD[param])
        kwargs: dict[str, Any] = {"stress_type": stress_type}
        if level == "underlying" and st.get("target"):
            kwargs["underlying"] = str(st["target"])
        elif level == "position" and st.get("target") is not None:
            kwargs["position_id"] = str(st["target"])
        builder = method(value, **kwargs)
    return builder.build()


def resolve_scenarios(request: dict[str, Any]) -> list[Any]:
    """Unify {predefined names, custom specs, saved set name} -> list[Scenario]."""
    _, ScenarioLibrary, _, _ = _imports()
    out: list[Any] = []
    for key in request.get("predefined", []) or []:
        method = _PREDEFINED.get(str(key).lower())
        if method is None:
            raise ValueError(f"Unknown predefined scenario {key!r}")
        out.append(getattr(ScenarioLibrary, method)())
    for spec in request.get("custom", []) or []:
        out.append(build_custom(spec))
    set_name = request.get("scenario_set")
    if set_name:
        out.extend(load_set(set_name))
    if not out:
        raise ValueError("No scenarios resolved from request")
    return out
```

> **Learning-mode contribution (b):** `build_custom` (validation strictness, default stress type/level, level→target mapping) is the natural place to hand the user the keyboard. Reference impl above is complete so the plan stands alone.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_catalog.py -k "predefined or custom or resolve" -v`
Expected: PASS. (The `load_set`-dependent path isn't exercised yet; `test_resolve_scenarios_predefined_plus_custom` doesn't pass `scenario_set`.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py backend/app/config.py tests/test_scenario_catalog.py
git commit -m "feat(scenario-test): scenario catalog (predefined + custom builder + resolve)"
```

---

### Task 5: `scenario_catalog` — saved sets (save/load/list)

**Files:**
- Modify: `backend/app/services/domains/scenario_catalog.py`
- Test: `tests/test_scenario_catalog.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scenario_catalog.py`:

```python
def test_save_and_load_set(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_catalog, "_sets_dir", lambda: tmp_path)
    s = scenario_catalog.build_custom(
        {"name": "S1", "stresses": [{"param": "spot", "value": -0.15}]})
    path = scenario_catalog.save_set("my_set", [s])
    assert path.endswith(".yaml")
    assert "my_set" in scenario_catalog.list_sets()
    loaded = scenario_catalog.load_set("my_set")
    assert loaded[0].name == "S1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_catalog.py -k save_and_load -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_sets_dir'`.

- [ ] **Step 3: Implement saved sets**

Append to `backend/app/services/domains/scenario_catalog.py`:

```python
import re
from pathlib import Path

from app.config import get_settings


def _sets_dir() -> Path:
    path = Path(get_settings().scenario_sets_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip())
    if not safe:
        raise ValueError("Scenario set name is empty after sanitization")
    return safe


def save_set(name: str, scenarios: list[Any]) -> str:
    quantark.ensure_quantark_path()
    from stresstest.scenario.scenario_storage import ScenarioStorage
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    ScenarioStorage.save_scenarios(scenarios, str(target))
    return str(target)


def load_set(name: str) -> list[Any]:
    quantark.ensure_quantark_path()
    from stresstest.scenario.scenario_storage import ScenarioStorage
    target = _sets_dir() / f"{_safe_name(name)}.yaml"
    if not target.exists():
        raise ValueError(f"Scenario set not found: {name}")
    return ScenarioStorage.load_scenarios(str(target))


def list_sets() -> list[str]:
    return sorted(p.stem for p in _sets_dir().glob("*.yaml"))
```

> Confirm `ScenarioStorage.save_scenarios(scenarios, filepath)` / `load_scenarios(filepath)` signatures against `stresstest/scenario/scenario_storage.py` (they take a list + path). If `save_scenarios` is a `@staticmethod` it is called as shown.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_catalog.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_catalog.py tests/test_scenario_catalog.py
git commit -m "feat(scenario-test): saved scenario sets via ScenarioStorage"
```

---

## Phase 3 — Engine Driver, Exports & Runner

### Task 6: `domains/scenario_test` — the pipeline + results shaping

**Files:**
- Create: `backend/app/services/domains/scenario_test.py`
- Test: `tests/test_scenario_test_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_test_engine.py`:

```python
from types import SimpleNamespace

from app.services.domains import scenario_test


class _FakeScenarioResult:
    def __init__(self, name, pnl):
        self.scenario = SimpleNamespace(name=name)
        self.portfolio_value = 1000.0 + pnl
        self.portfolio_pnl = pnl
        self.portfolio_pnl_pct = pnl / 10.0
        self.greeks = {"delta": 1.0}
        self.underlying_results = {"AAPL": {"num_positions": 1, "total_value": 1.0, "greeks": None}}
        self.position_results = []
        self.execution_time = 0.01


class _FakeResults:
    baseline_value = 1000.0
    baseline_greeks = {"delta": 2.0}
    total_execution_time = 0.5
    def __init__(self):
        self.scenario_results = [_FakeScenarioResult("Crash", -200.0),
                                 _FakeScenarioResult("Rally", 150.0)]
    def get_worst_scenario(self):
        return min(self.scenario_results, key=lambda r: r.portfolio_pnl)
    def get_best_scenario(self):
        return max(self.scenario_results, key=lambda r: r.portfolio_pnl)


def test_shape_results_picks_worst_best_and_varcvar(monkeypatch):
    monkeypatch.setattr(scenario_test, "_result_aggregator", lambda: SimpleNamespace(
        get_risk_summary=lambda r: {"avg_pnl": -25.0, "max_drawdown_pct": -20.0},
        calculate_var_cvar=lambda r, confidence_level=0.95: {"var": -200.0, "cvar": -200.0},
    ))
    shaped = scenario_test.shape_results(_FakeResults())
    assert shaped["worst_scenario"] == "Crash"
    assert shaped["best_scenario"] == "Rally"
    assert shaped["var_cvar"]["var"] == -200.0
    assert len(shaped["scenarios"]) == 2
    assert shaped["scenarios"][0]["name"] == "Crash"
    assert shaped["baseline_value"] == 1000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: scenario_test`.

- [ ] **Step 3: Implement the pipeline + shaping**

Create `backend/app/services/domains/scenario_test.py`:

```python
"""Scenario-test pipeline: build EquityPortfolio, run StressTestEngine, shape results."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services import quantark, scenario_test_bridge
from app.services.domains import scenario_catalog


def _result_aggregator():
    quantark.ensure_quantark_path()
    from stresstest.results.result_aggregator import ResultAggregator
    return ResultAggregator


def shape_results(results: Any) -> dict[str, Any]:
    """Project a QuantArk StressTestResults into a JSON-serializable dict."""
    aggregator = _result_aggregator()
    worst = results.get_worst_scenario()
    best = results.get_best_scenario()
    scenarios = [
        {
            "name": r.scenario.name,
            "portfolio_value": float(r.portfolio_value),
            "pnl": float(r.portfolio_pnl),
            "pnl_pct": float(r.portfolio_pnl_pct),
            "greeks": r.greeks,
            "underlying_results": r.underlying_results,
            "position_results": r.position_results,
            "execution_time": float(r.execution_time),
        }
        for r in results.scenario_results
    ]
    try:
        risk_summary = aggregator.get_risk_summary(results)
    except Exception as exc:  # pragma: no cover - defensive
        risk_summary = {"error": str(exc)}
    try:
        var_cvar = aggregator.calculate_var_cvar(results, confidence_level=0.95)
        var_cvar["confidence"] = 0.95
    except Exception as exc:  # pragma: no cover - defensive
        var_cvar = {"error": str(exc)}
    return {
        "baseline_value": float(results.baseline_value),
        "baseline_greeks": results.baseline_greeks,
        "scenarios": scenarios,
        "worst_scenario": worst.scenario.name if worst else None,
        "best_scenario": best.scenario.name if best else None,
        "risk_summary": risk_summary,
        "var_cvar": var_cvar,
        "num_scenarios": len(scenarios),
        "execution_time": float(getattr(results, "total_execution_time", 0.0)),
    }


def run_pipeline(
    session: Session,
    *,
    positions: list[Any],
    pricing_parameter_profile_id: int | None,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    portfolio_name: str,
    valuation_date: datetime | None = None,
) -> tuple[str, dict[str, Any], list[dict]]:
    """Returns (status, results_dict, excluded). status in {completed, empty}."""
    from app.services.risk_engine import _pricing_position_context  # reuse risk resolver

    valuation_date = valuation_date or datetime.utcnow()
    position_markets, _failures, _diag = _pricing_position_context(
        session, positions,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        valuation_date=valuation_date,
    )
    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, position_markets, portfolio_name=portfolio_name)

    if len(portfolio) == 0:
        return "empty", {"message": "No includable positions to stress", "scenarios": []}, excluded

    scenarios = scenario_catalog.resolve_scenarios(scenario_request)

    quantark.ensure_quantark_path()
    from stresstest import StressTestEngine, StressTestConfig
    engine_config = StressTestConfig(
        calculate_greeks=bool(config.get("calculate_greeks", True)),
        greeks_method=str(config.get("greeks_method", "numerical")),
        export_formats=list(config.get("export_formats", ["json"])),
        save_detailed_results=bool(config.get("save_detailed_results", True)),
        output_dir=str(config.get("output_dir", "outputs/scenario_test")),
    )
    engine = StressTestEngine(engine_config)
    results = engine.run_static_scenarios(portfolio, scenarios)
    return "completed", shape_results(results), excluded, results  # type: ignore[return-value]
```

> Note `run_pipeline` returns a 4-tuple on the completed path (it also yields the raw `results` for export in Task 7). Adjust the type hint to `tuple[str, dict, list[dict], Any | None]` and return `(status, dict, excluded, None)` on the `empty` path. Fix the empty-path return to `return "empty", {...}, excluded, None`.

- [ ] **Step 4: Fix the empty-path return arity**

Edit the `empty` return to match the 4-tuple:

```python
    if len(portfolio) == 0:
        return "empty", {"message": "No includable positions to stress", "scenarios": []}, excluded, None
```

and the final line:

```python
    return "completed", shape_results(results), excluded, results
```

Update the signature's return annotation to `tuple[str, dict[str, Any], list[dict], Any | None]`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_engine.py -v`
Expected: PASS (`shape_results` test; `run_pipeline` is covered in Task 8).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/scenario_test.py tests/test_scenario_test_engine.py
git commit -m "feat(scenario-test): engine-driver pipeline + results shaping"
```

> **Learning-mode contribution (c):** `shape_results` (the VaR/CVaR + worst/best + risk-summary projection) is contribution point (c). Offer it to the user; reference impl above keeps the plan complete.

---

### Task 7: Exports + HTML report (graceful)

**Files:**
- Modify: `backend/app/services/domains/scenario_test.py` (append `write_artifacts`)
- Test: `tests/test_scenario_test_engine.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scenario_test_engine.py`:

```python
def test_write_artifacts_is_graceful_when_libs_missing(tmp_path, monkeypatch):
    # Force exporter import to fail -> artifacts records a note, never raises.
    def _boom():
        raise ImportError("no pyarrow")
    monkeypatch.setattr(scenario_test, "_result_exporter", _boom)
    monkeypatch.setattr(scenario_test, "_report_generator", _boom)
    artifacts = scenario_test.write_artifacts(
        results=object(), run_id=7, formats=["parquet"], base_dir=str(tmp_path))
    assert "notes" in artifacts
    assert artifacts["export_paths"] == []
    assert artifacts["report_html_path"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_engine.py -k write_artifacts -v`
Expected: FAIL — `AttributeError: ... 'write_artifacts'`.

- [ ] **Step 3: Implement `write_artifacts`**

Append to `backend/app/services/domains/scenario_test.py`:

```python
import os


def _result_exporter():
    quantark.ensure_quantark_path()
    from stresstest.results.result_exporter import ResultExporter
    return ResultExporter


def _report_generator():
    quantark.ensure_quantark_path()
    from stresstest.report import ReportGenerator
    return ReportGenerator


def write_artifacts(*, results: Any, run_id: int, formats: list[str], base_dir: str) -> dict:
    """Write exports + HTML report. Never raises: missing libs become a note."""
    out_dir = os.path.join(base_dir, str(run_id))
    os.makedirs(out_dir, exist_ok=True)
    artifacts: dict[str, Any] = {"export_paths": [], "report_html_path": None, "notes": []}

    try:
        exporter = _result_exporter()
        exporter.export(results, out_dir, formats=formats, base_name=f"scenario_test_{run_id}")
        for fmt in formats:
            path = os.path.join(out_dir, f"scenario_test_{run_id}.{fmt}")
            if os.path.exists(path):
                artifacts["export_paths"].append(path)
    except Exception as exc:
        artifacts["notes"].append(f"export skipped: {exc}")

    try:
        report_path = os.path.join(out_dir, "report.html")
        self_gen = _report_generator()()
        self_gen.generate_report(results, report_path, title=f"Scenario Test #{run_id}")
        if os.path.exists(report_path):
            artifacts["report_html_path"] = report_path
    except Exception as exc:
        artifacts["notes"].append(f"report skipped: {exc}")

    return artifacts
```

> Verify `ResultExporter.export(results, output_dir, formats=..., base_name=...)` and `ReportGenerator().generate_report(results, path, title=...)` signatures against `stresstest/results/result_exporter.py` and `stresstest/report/report_generator.py`; adjust kwargs if they differ. The try/except keeps the run resilient regardless.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_engine.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/scenario_test.py tests/test_scenario_test_engine.py
git commit -m "feat(scenario-test): graceful exports + HTML report artifacts"
```

---

### Task 8: `scenario_test_runner` — queue + async execute

**Files:**
- Create: `backend/app/services/scenario_test_runner.py`
- Test: `tests/test_scenario_test_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_test_runner.py`:

```python
from app import database
from app.models import Portfolio, ScenarioTestRun, TaskRun, TaskKind
from app.services import scenario_test_runner


def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'r.sqlite'}")
    database.init_db()
    return database.SessionLocal()


def test_queue_creates_run_and_task(tmp_path, monkeypatch):
    with _session(tmp_path, monkeypatch) as session:
        pf = Portfolio(name="PF", base_currency="USD", kind="container")
        session.add(pf); session.flush()
        # Don't actually dispatch the async worker in this unit test.
        monkeypatch.setattr(scenario_test_runner, "submit_async_task", lambda *a, **k: None)
        run, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=pf.id,
            pricing_parameter_profile_id=None,
            scenario_request={"predefined": ["market_crash"]},
            config={"calculate_greeks": False},
        )
        assert run.status == "queued"
        assert task.kind == TaskKind.SCENARIO_TEST.value
        assert task.scenario_test_run_id == run.id
        assert run.scenario_spec == {"predefined": ["market_crash"]}


def test_execute_marks_empty_when_no_positions(tmp_path, monkeypatch):
    with _session(tmp_path, monkeypatch) as session:
        pf = Portfolio(name="PF2", base_currency="USD", kind="container")
        session.add(pf); session.flush()
        run = ScenarioTestRun(portfolio_id=pf.id, status="queued",
                              scenario_spec={"predefined": ["market_crash"]},
                              config={}, results={}, excluded_positions=[], artifacts={})
        session.add(run); session.flush()
        task = TaskRun(kind=TaskKind.SCENARIO_TEST.value, status="queued",
                       portfolio_id=pf.id, scenario_test_run_id=run.id)
        session.add(task); session.flush()
        scenario_test_runner._execute(session, task.id, run.id)
        session.refresh(run)
        assert run.status == "empty"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: scenario_test_runner`.

- [ ] **Step 3: Implement the runner**

Create `backend/app/services/scenario_test_runner.py`:

```python
"""Queue + async execution for scenario test runs. Mirrors batch_pricing."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.config import get_settings
from app.models import (
    Portfolio, ScenarioTestRun, TaskKind, TaskRun, TaskStatus,
)
from app.services.audit import record_audit
from app.services.domains import positions as positions_svc
from app.services.domains import scenario_test as scenario_test_svc
from app.services.task_runner import submit_async_task


def queue_scenario_test(
    session: Session,
    *,
    portfolio_id: int,
    pricing_parameter_profile_id: int | None,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    position_ids: list[int] | None = None,
) -> tuple[ScenarioTestRun, TaskRun]:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    run = ScenarioTestRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        status=TaskStatus.QUEUED.value,
        scenario_spec=scenario_request,
        config=config,
        results={},
        excluded_positions=[],
        artifacts={},
        resolved_position_ids=position_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.SCENARIO_TEST.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        scenario_test_run_id=run.id,
        message="Queued scenario test run",
    )
    session.add(task)
    session.flush()
    record_audit(session, event_type="scenario_test.queued", actor="desk_user",
                 subject_type="portfolio", subject_id=portfolio_id,
                 payload={"run_id": run.id, "scenarios": scenario_request})
    session.commit()
    submit_async_task(execute_scenario_test_task, task.id, run.id)
    return run, task


def execute_scenario_test_task(task_id: int, run_id: int,
                               session_factory: sessionmaker | None = None) -> None:
    database.init_db()
    session = (session_factory or database.SessionLocal)()
    try:
        _execute(session, task_id, run_id)
    finally:
        session.close()


def _execute(session: Session, task_id: int, run_id: int) -> None:
    from app.services.task_runner import mark_task_finished
    run = session.get(ScenarioTestRun, run_id)
    task = session.get(TaskRun, task_id)
    if run is None or task is None:
        return
    try:
        run.status = TaskStatus.RUNNING.value
        task.status = TaskStatus.RUNNING.value
        task.started_at = datetime.utcnow()
        session.commit()

        portfolio = session.get(Portfolio, run.portfolio_id)
        all_positions = positions_svc.list_filtered(
            portfolio_id=run.portfolio_id, session=session)
        if run.resolved_position_ids:
            wanted = set(run.resolved_position_ids)
            positions = [p for p in all_positions if p.id in wanted]
        else:
            positions = list(all_positions)
        run.resolved_position_ids = [p.id for p in positions]

        status, results_dict, excluded, raw = scenario_test_svc.run_pipeline(
            session,
            positions=positions,
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            scenario_request=run.scenario_spec,
            config=run.config,
            portfolio_name=f"{portfolio.name if portfolio else 'portfolio'}-scenario",
        )
        run.results = results_dict
        run.excluded_positions = excluded
        if status == "completed" and raw is not None:
            settings = get_settings()
            run.artifacts = scenario_test_svc.write_artifacts(
                results=raw, run_id=run.id,
                formats=run.config.get("export_formats", ["json"]),
                base_dir=settings.scenario_test_output_dir)
        run.status = status
        session.commit()
        mark_task_finished(session, task_id, status=TaskStatus.COMPLETED.value)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - persist failure, never crash the worker
        session.rollback()
        run = session.get(ScenarioTestRun, run_id)
        if run is not None:
            run.status = TaskStatus.FAILED.value
            run.results = {"error": str(exc)}
        mark_task_finished(session, task_id, status=TaskStatus.FAILED.value, error=str(exc))
        session.commit()
```

> Verify `mark_task_finished(session, task_id, status=..., error=...)` and `positions_svc.list_filtered(portfolio_id=..., session=...)` signatures (both are used by `risk`/`batch_pricing` — match their call sites). `record_audit` signature matches `risk_svc.run`'s usage.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_runner.py -v`
Expected: PASS. (`test_execute_marks_empty_when_no_positions` exercises the empty path: no positions → pipeline returns `"empty"`.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scenario_test_runner.py tests/test_scenario_test_runner.py
git commit -m "feat(scenario-test): async queue + executor (mirrors batch_pricing)"
```

---

## Phase 4 — Schemas & REST

### Task 9: Pydantic schemas

**Files:**
- Modify: `backend/app/schemas.py`

- [ ] **Step 1: Add schemas**

In `backend/app/schemas.py` (near the existing `ScenarioRunRequest`/`ScenarioCell` block), add:

```python
class ScenarioStressSpec(BaseModel):
    param: str = Field(description="spot | vol | rate | dividend")
    stress_type: str = "PERCENTAGE"  # ABSOLUTE | PERCENTAGE | VALUE
    value: float
    level: str = "portfolio"          # portfolio | underlying | position
    target: str | int | None = None


class ScenarioSpec(BaseModel):
    name: str
    description: str | None = None
    stresses: list[ScenarioStressSpec] = Field(default_factory=list)


class ScenarioTestConfig(BaseModel):
    calculate_greeks: bool = True
    greeks_method: str = "numerical"
    export_formats: list[str] = Field(default_factory=lambda: ["json"])
    save_detailed_results: bool = True


class ScenarioTestRunRequest(BaseModel):
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    predefined: list[str] = Field(default_factory=list)
    custom: list[ScenarioSpec] = Field(default_factory=list)
    scenario_set: str | None = None
    config: ScenarioTestConfig = Field(default_factory=ScenarioTestConfig)


class ScenarioTestRunOut(BaseModel):
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None
    status: str
    scenario_spec: dict | None = None
    config: dict | None = None
    results: dict | None = None
    excluded_positions: list | None = None
    artifacts: dict | None = None
    resolved_position_ids: list[int] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScenarioLibraryOut(BaseModel):
    predefined: list[dict]
    saved_sets: list[str]
```

> If `ConfigDict` / `datetime` aren't already imported at the top of `schemas.py`, they are (the file defines many `from_attributes` models). Reuse the existing imports.

- [ ] **Step 2: Verify import**

Run: `python -m pytest tests/test_scenario_catalog.py -q` (smoke — confirms `schemas` still imports cleanly).
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(scenario-test): request/response schemas"
```

---

### Task 10: REST endpoints

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_scenario_test_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_test_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app
from app import database


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'api.sqlite'}")
    database.init_db()
    return TestClient(create_app())


def test_library_endpoint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/api/scenario-test/library")
    assert resp.status_code == 200
    body = resp.json()
    assert "predefined" in body and "saved_sets" in body
    assert any(p["name"] == "Market Crash" for p in body["predefined"])


def test_queue_run_returns_run(tmp_path, monkeypatch):
    import app.services.scenario_test_runner as runner
    monkeypatch.setattr(runner, "submit_async_task", lambda *a, **k: None)
    client = _client(tmp_path, monkeypatch)
    with database.SessionLocal() as s:
        from app.models import Portfolio
        pf = Portfolio(name="ApiPF", base_currency="USD", kind="container")
        s.add(pf); s.commit(); pid = pf.id
    resp = client.post("/api/scenario-test/runs", json={
        "portfolio_id": pid, "predefined": ["market_crash"],
        "config": {"calculate_greeks": False},
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_api.py -v`
Expected: FAIL — 404 on `/api/scenario-test/library`.

- [ ] **Step 3: Add endpoints in `main.py`**

Find where routes are registered in `backend/app/main.py` (look for the existing `@app.post("/api/risk/scenarios")` block) and add nearby:

```python
    from .schemas import (
        ScenarioLibraryOut, ScenarioTestRunRequest, ScenarioTestRunOut,
    )
    from .services.domains import scenario_catalog
    from .services import scenario_test_runner

    @app.get("/api/scenario-test/library", response_model=ScenarioLibraryOut)
    def scenario_test_library() -> ScenarioLibraryOut:
        return ScenarioLibraryOut(
            predefined=scenario_catalog.list_predefined(),
            saved_sets=scenario_catalog.list_sets(),
        )

    @app.get("/api/scenario-test/sets")
    def scenario_test_sets() -> dict:
        return {"saved_sets": scenario_catalog.list_sets()}

    @app.post("/api/scenario-test/sets")
    def save_scenario_test_set(payload: dict) -> dict:
        name = payload["name"]
        scenarios = [scenario_catalog.build_custom(s) for s in payload.get("custom", [])]
        path = scenario_catalog.save_set(name, scenarios)
        return {"name": name, "path": path}

    @app.post("/api/scenario-test/runs", response_model=ScenarioTestRunOut)
    def create_scenario_test_run(
        payload: ScenarioTestRunRequest, session: Session = Depends(get_db)
    ) -> ScenarioTestRunOut:
        request = {
            "predefined": payload.predefined,
            "custom": [c.model_dump() for c in payload.custom],
            "scenario_set": payload.scenario_set,
        }
        run, _task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=payload.portfolio_id,
            pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
            scenario_request=request,
            config=payload.config.model_dump(),
            position_ids=payload.position_ids,
        )
        session.refresh(run)
        return ScenarioTestRunOut.model_validate(run)

    @app.get("/api/scenario-test/runs")
    def list_scenario_test_runs(
        portfolio_id: int, session: Session = Depends(get_db)
    ) -> list[ScenarioTestRunOut]:
        from .models import ScenarioTestRun
        rows = (
            session.query(ScenarioTestRun)
            .filter(ScenarioTestRun.portfolio_id == portfolio_id)
            .order_by(ScenarioTestRun.created_at.desc(), ScenarioTestRun.id.desc())
            .all()
        )
        return [ScenarioTestRunOut.model_validate(r) for r in rows]

    @app.get("/api/scenario-test/runs/{run_id}", response_model=ScenarioTestRunOut)
    def get_scenario_test_run(run_id: int, session: Session = Depends(get_db)) -> ScenarioTestRunOut:
        from .models import ScenarioTestRun
        run = session.get(ScenarioTestRun, run_id)
        if run is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Scenario test run not found")
        return ScenarioTestRunOut.model_validate(run)
```

> Match the existing patterns in `main.py`: `get_db` dependency, `Session`, and where imports live (top-of-function imports are used elsewhere, e.g. the `/api/risk/scenarios` handler imports `run_portfolio_scenarios` inline — follow that). If routes are defined at module top-level rather than inside a `create_app()` function, place these at the same level as `/api/risk/scenarios`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_scenario_test_api.py
git commit -m "feat(scenario-test): REST endpoints (library, sets, runs)"
```

---

## Phase 5 — Agent Tools

### Task 11: `tools/scenario_test.py` + registration

**Files:**
- Create: `backend/app/tools/scenario_test.py`
- Modify: `backend/app/tools/__init__.py`, `backend/app/services/agents.py`
- Test: `tests/test_scenario_test_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_test_tools.py`:

```python
from app.tools import QUANT_AGENT_TOOLS
from app.services.agents import DEEP_AGENT_TOOL_NAMES


def test_tools_registered():
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert {"list_scenario_library", "run_scenario_test",
            "get_scenario_test_run", "save_scenario_set"} <= names


def test_tools_in_deep_agent_allowlist():
    assert {"list_scenario_library", "run_scenario_test",
            "get_scenario_test_run", "save_scenario_set"} <= DEEP_AGENT_TOOL_NAMES


def test_list_library_tool_runs():
    from app.tools.scenario_test import list_scenario_library_tool
    out = list_scenario_library_tool.invoke({})
    assert "predefined" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scenario_test_tools.py -v`
Expected: FAIL — import error / names missing.

- [ ] **Step 3: Implement the tools**

Create `backend/app/tools/scenario_test.py`:

```python
"""@tool wrappers for the scenario-test domain. Thin LLM adapters."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import scenario_catalog
from app.services import scenario_test_runner
from app.models import ScenarioTestRun


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunScenarioTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    predefined: list[str] = Field(default_factory=list)
    custom: list[dict] = Field(default_factory=list)
    scenario_set: str | None = None
    config: dict = Field(default_factory=dict)


class GetScenarioTestRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int


class SaveScenarioSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    custom: list[dict]


def _estimate_run_seconds(tool_input: Any) -> float:
    if not isinstance(tool_input, dict):
        return 0.0
    n_scen = len(tool_input.get("predefined", []) or []) + len(tool_input.get("custom", []) or [])
    return max(1, n_scen) * 2.0


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_scenario_library", args_schema=_Empty)
def list_scenario_library_tool() -> dict[str, Any]:
    """List predefined stress scenarios and saved scenario sets available for a scenario test."""
    return {"predefined": scenario_catalog.list_predefined(),
            "saved_sets": scenario_catalog.list_sets()}


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_estimate_run_seconds)
@tool("run_scenario_test", args_schema=RunScenarioTestInput)
def run_scenario_test_tool(
    portfolio_id: int,
    pricing_parameter_profile_id: int | None = None,
    position_ids: list[int] | None = None,
    predefined: list[str] | None = None,
    custom: list[dict] | None = None,
    scenario_set: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Queue an async, persisted scenario (stress) test for a portfolio against a
    pricing parameter profile. Scenarios come from predefined names, custom specs,
    or a saved set. Returns the queued run id; read it later with get_scenario_test_run."""
    database.init_db()
    with database.SessionLocal() as session:
        run, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=portfolio_id,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            scenario_request={"predefined": predefined or [], "custom": custom or [],
                              "scenario_set": scenario_set},
            config=config or {},
            position_ids=position_ids,
        )
        return {"run_id": run.id, "task_id": task.id, "status": run.status}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_scenario_test_run", args_schema=GetScenarioTestRunInput)
def get_scenario_test_run_tool(run_id: int) -> dict[str, Any]:
    """Fetch a scenario test run's status, results, excluded positions, and artifacts."""
    database.init_db()
    with database.SessionLocal() as session:
        run = session.get(ScenarioTestRun, run_id)
        if run is None:
            return {"error": f"Scenario test run not found: {run_id}"}
        return {"id": run.id, "status": run.status, "results": run.results,
                "excluded_positions": run.excluded_positions, "artifacts": run.artifacts}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_scenario_set", args_schema=SaveScenarioSetInput)
def save_scenario_set_tool(name: str, custom: list[dict]) -> dict[str, Any]:
    """Save a reusable named set of custom scenarios."""
    scenarios = [scenario_catalog.build_custom(s) for s in custom]
    path = scenario_catalog.save_set(name, scenarios)
    return {"name": name, "path": path}
```

> Confirm `capability_gated(group=..., cost_estimator=...)` signature against `tools/risk.py` (it wraps `run_batch_pricing_tool` identically). Confirm `ToolGroup.DOMAIN_READ/DOMAIN_WRITE` exist.

- [ ] **Step 4: Register in `tools/__init__.py`**

In `backend/app/tools/__init__.py`, add an import and extend `QUANT_AGENT_TOOLS`:

```python
from .scenario_test import (
    get_scenario_test_run_tool,
    list_scenario_library_tool,
    run_scenario_test_tool,
    save_scenario_set_tool,
)
```

and add these four names into the `QUANT_AGENT_TOOLS = [ ... ]` list (next to `run_batch_pricing_tool`).

- [ ] **Step 5: Add to `DEEP_AGENT_TOOL_NAMES`**

In `backend/app/services/agents.py`, add to the `DEEP_AGENT_TOOL_NAMES` frozenset:

```python
        "list_scenario_library",
        "run_scenario_test",
        "get_scenario_test_run",
        "save_scenario_set",
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_scenario_test_tools.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tools/scenario_test.py backend/app/tools/__init__.py backend/app/services/agents.py tests/test_scenario_test_tools.py
git commit -m "feat(scenario-test): agent tools + registration + deep-agent allowlist"
```

---

## Phase 6 — Skill, Reference Doc & Catalog Tests

### Task 12: SKILL.md + reference doc

**Files:**
- Create: `backend/app/skills/workflows/risk/run-scenario-test/SKILL.md`
- Create: `backend/app/skills/references/risk/scenario-test.md`

- [ ] **Step 1: Write the SKILL.md**

Create `backend/app/skills/workflows/risk/run-scenario-test/SKILL.md` with exactly the frontmatter + body from the spec §10.1 (copy it verbatim from `docs/superpowers/specs/2026-06-08-scenario-test-design.md`). Key frontmatter: `name: run-scenario-test`, `domain: risk`, `workflow_type: compound`, `write_actions: true`, `confirmation_required: true`, a `routing:` block, and `required_context: [portfolio_id, pricing_parameter_profile_id]`.

- [ ] **Step 2: Write the reference doc**

Create `backend/app/skills/references/risk/scenario-test.md`. It MUST start with YAML frontmatter (other reference docs do — `test_reference_docs.py` checks for it):

```markdown
---
title: Scenario Test Taxonomy
description: Predefined, historical, and custom stress scenarios for portfolio scenario tests.
---

# Scenario Test Taxonomy

## Predefined scenarios
- `market_crash` — spot −20%, vol +50%
- `market_rally` — spot +15%, vol −30%
- `vol_spike` / `vol_crush` — vol +80% / vol −40%
- `rate_hike` / `rate_cut` — ±200bps
- `severe_downturn` — spot −35%, vol +100%, rate −100bps
- `inflation_shock` — rate up, equity down

## Historical scenarios
- `black_monday_1987` — −22.6% equity
- `financial_crisis_2008` — −40% equity, +120% vol
- `covid_crash_2020` — −34% equity, +200% vol

## Custom scenarios
A custom scenario is `{name, stresses: [{param, stress_type, value, level, target}]}`.
- `param`: `spot` | `vol` | `rate` | `dividend`
- `stress_type`: `ABSOLUTE` | `PERCENTAGE` | `VALUE`
- `level`: `portfolio` | `underlying` (target = symbol) | `position` (target = id)

## Output
Per-scenario P&L and %, worst/best scenario, 95% VaR/CVaR, per-underlying breakdown,
greeks deltas, excluded positions, and report/export artifact links.
```

- [ ] **Step 3: Verify skills still load (will reveal catalog test breakage next)**

Run: `python -m pytest tests/test_skills_catalog.py -v`
Expected: **FAIL** — the `/workflows/risk/` exact-set assertion now misses `run-scenario-test`. This is expected and fixed in Task 13.

- [ ] **Step 4: Commit**

```bash
git add backend/app/skills/workflows/risk/run-scenario-test/SKILL.md backend/app/skills/references/risk/scenario-test.md
git commit -m "feat(scenario-test): run-scenario-test workflow skill + reference doc"
```

---

### Task 13: Update the six catalog/routing tests

**Files:**
- Modify: `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py`, `tests/test_remaining_workflow_skills_phase3.py`, `tests/test_reference_docs.py`, `tests/test_routing_table.py`

- [ ] **Step 1: Run all six to see current failures**

Run:
```bash
python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py \
  tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py \
  tests/test_reference_docs.py tests/test_routing_table.py -v
```
Expected: failures pointing at the exact assertions to update (exact-sets, counts, routing triples, reference-doc list).

- [ ] **Step 2: `test_skills_catalog.py` — add to the risk set**

Change the `/workflows/risk/` expected set (currently `{"run-risk", "read-risk-result", "create-risk-report"}`) to include `"run-scenario-test"`. Search the file for every other place the full workflow set or a workflow **count** is asserted and update those too (grep within the file for `run-risk` and for numeric counts of workflow skills).

- [ ] **Step 3: `test_skills_catalog_v2.py` — same treatment**

Add `"run-scenario-test"` to the risk-domain exact-set and bump any total workflow-skill count assertion by 1.

- [ ] **Step 4: `test_workflow_skills_phase3.py` + `test_remaining_workflow_skills_phase3.py`**

Add `run-scenario-test` to whatever per-domain set / total count these assert (grep each file for `run-risk` and for an integer total of skills; add 1 to counts, add the name to sets).

- [ ] **Step 5: `test_reference_docs.py` — register the new reference doc**

Add `references/risk/scenario-test.md` to the expected reference-doc list/count, and ensure the frontmatter check passes (the doc has frontmatter from Task 12).

- [ ] **Step 6: `test_routing_table.py` — add the routing triple**

This file pins `OLD_TABLE_ROWS` (routing triples of `(request, persona, skill)`). Add the row for `run-scenario-test` matching the SKILL.md `routing:` block (`request: "Stress test or scenario analysis of a portfolio"`, `persona: trader`, skill `run-scenario-test`). Match the exact tuple shape used by the other rows in the file.

- [ ] **Step 7: Run all six to verify they pass**

Run:
```bash
python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py \
  tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py \
  tests/test_reference_docs.py tests/test_routing_table.py -v
```
Expected: PASS (all six).

- [ ] **Step 8: Commit**

```bash
git add tests/test_skills_catalog.py tests/test_skills_catalog_v2.py \
  tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py \
  tests/test_reference_docs.py tests/test_routing_table.py
git commit -m "test(scenario-test): register run-scenario-test in skill catalog + routing"
```

---

## Phase 7 — Frontend

### Task 14: API client + types

**Files:**
- Modify: `frontend/src/types.ts`, and the API client module (find it: `frontend/src/api/`)

- [ ] **Step 1: Inspect the API client pattern**

Read `frontend/src/api/` (e.g. how `Risk.live.tsx` fetches `/api/risk/...`). Mirror that exact pattern (fetch wrapper, error handling, naming).

- [ ] **Step 2: Add types**

In `frontend/src/types.ts` add:

```typescript
export type ScenarioStress = {
  param: 'spot' | 'vol' | 'rate' | 'dividend';
  stress_type: 'ABSOLUTE' | 'PERCENTAGE' | 'VALUE';
  value: number;
  level: 'portfolio' | 'underlying' | 'position';
  target?: string | number | null;
};

export type ScenarioSpec = { name: string; description?: string; stresses: ScenarioStress[] };

export type ScenarioTestRun = {
  id: number;
  portfolio_id: number;
  pricing_parameter_profile_id: number | null;
  status: string;
  results: Record<string, unknown> | null;
  excluded_positions: Array<{ position_id: number; reason: string }> | null;
  artifacts: { report_html_path?: string | null; export_paths?: string[]; notes?: string[] } | null;
  created_at: string;
};

export type ScenarioLibrary = {
  predefined: Array<{ key: string; name: string; description: string; num_stresses: number }>;
  saved_sets: string[];
};
```

- [ ] **Step 3: Add API functions**

In the API client module add (mirroring the existing fetch helpers):

```typescript
export async function fetchScenarioLibrary(): Promise<ScenarioLibrary> {
  return apiGet('/api/scenario-test/library');
}
export async function createScenarioTestRun(body: unknown): Promise<ScenarioTestRun> {
  return apiPost('/api/scenario-test/runs', body);
}
export async function listScenarioTestRuns(portfolioId: number): Promise<ScenarioTestRun[]> {
  return apiGet(`/api/scenario-test/runs?portfolio_id=${portfolioId}`);
}
export async function getScenarioTestRun(id: number): Promise<ScenarioTestRun> {
  return apiGet(`/api/scenario-test/runs/${id}`);
}
```

> Use whatever the existing helpers are named (`apiGet`/`apiPost` are placeholders — match the real ones in the client).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/
git commit -m "feat(scenario-test): frontend types + API client"
```

---

### Task 15: `ScenarioTest.tsx` page

**Files:**
- Create: `frontend/src/routes/ScenarioTest.tsx`, `ScenarioTest.css`, `ScenarioTest.test.tsx`
- (Optional) Create: `frontend/src/routes/ScenarioTest.live.tsx`

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/routes/ScenarioTest.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ScenarioTest } from './ScenarioTest';

vi.mock('../api', async (orig) => ({
  ...(await orig<typeof import('../api')>()),
  fetchScenarioLibrary: vi.fn().mockResolvedValue({
    predefined: [{ key: 'market_crash', name: 'Market Crash', description: '', num_stresses: 2 }],
    saved_sets: [],
  }),
  listScenarioTestRuns: vi.fn().mockResolvedValue([]),
}));

describe('ScenarioTest', () => {
  it('renders the scenario picker heading', async () => {
    render(<ScenarioTest />);
    expect(await screen.findByText(/Scenario Test/i)).toBeInTheDocument();
    expect(await screen.findByText(/Market Crash/i)).toBeInTheDocument();
  });
});
```

> Adjust the mock path/shape to the real API module location (from Task 14). Mirror `Risk.test.tsx` for the render + mock pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: FAIL — cannot find `./ScenarioTest`.

- [ ] **Step 3: Implement the page**

Create `frontend/src/routes/ScenarioTest.tsx` with three zones (scenario picker incl. custom builder, run config, results + history). Mirror `Risk.tsx` and `HedgeStrategy.tsx` for layout, `PortfolioPicker`, profile picker, and token usage. Minimum to pass + be useful:

```tsx
import { useEffect, useState } from 'react';
import './ScenarioTest.css';
import { PortfolioPicker } from '../components/PortfolioPicker';
import {
  fetchScenarioLibrary, createScenarioTestRun, listScenarioTestRuns,
} from '../api';
import type { ScenarioLibrary, ScenarioTestRun, ScenarioStress } from '../types';

export function ScenarioTest() {
  const [library, setLibrary] = useState<ScenarioLibrary | null>(null);
  const [portfolioId, setPortfolioId] = useState<number | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState<{ name: string; stresses: ScenarioStress[] }[]>([]);
  const [runs, setRuns] = useState<ScenarioTestRun[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => { fetchScenarioLibrary().then(setLibrary); }, []);
  useEffect(() => { if (portfolioId) listScenarioTestRuns(portfolioId).then(setRuns); }, [portfolioId]);

  const toggle = (name: string) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(name) ? next.delete(name) : next.add(name);
    return next;
  });

  const run = async () => {
    if (!portfolioId) return;
    setBusy(true);
    try {
      // `selected` holds library keys (e.g. "market_crash"), bound from p.key below.
      await createScenarioTestRun({
        portfolio_id: portfolioId,
        pricing_parameter_profile_id: profileId,
        predefined: Array.from(selected),
        custom,
        config: { calculate_greeks: true, greeks_method: 'numerical', export_formats: ['json', 'csv'] },
      });
      setRuns(await listScenarioTestRuns(portfolioId));
    } finally { setBusy(false); }
  };

  return (
    <section className="st">
      <h1>Scenario Test</h1>
      <div className="st__config">
        <PortfolioPicker value={portfolioId} onChange={setPortfolioId} />
        {/* profile picker — mirror Risk.tsx */}
      </div>
      <div className="st__scenarios">
        <h2>Scenarios</h2>
        {library?.predefined.map((p) => (
          <label key={p.key}>
            <input type="checkbox" checked={selected.has(p.key)} onChange={() => toggle(p.key)} />
            {p.name}
          </label>
        ))}
        {/* custom builder: add rows of {param, stress_type, value, level, target} into `custom` */}
      </div>
      <button disabled={!portfolioId || busy} onClick={run}>Run scenario test</button>
      <div className="st__results">
        <h2>Runs</h2>
        {runs.map((r) => (
          <div key={r.id} className="st__run">
            #{r.id} · {r.status}
            {r.results?.worst_scenario ? ` · worst: ${String(r.results.worst_scenario)}` : ''}
          </div>
        ))}
      </div>
    </section>
  );
}
```

The checkbox binds to `p.key` (the stable library key like `market_crash`) and shows
`p.name` — `list_predefined()` already returns both (Task 4), so no display→key mapping
is needed in the component.

Create a minimal `ScenarioTest.css` (mirror `Risk.css` class conventions).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ScenarioTest.tsx frontend/src/routes/ScenarioTest.css frontend/src/routes/ScenarioTest.test.tsx
git commit -m "feat(scenario-test): Scenario Test page"
```

---

### Task 16: Router + Sidebar registration

**Files:**
- Modify: `frontend/src/main.tsx` (route), `frontend/src/components/Sidebar.tsx` (nav entry)

- [ ] **Step 1: Add the route**

In `frontend/src/main.tsx`, find where `Risk` / `HedgeStrategy` routes are registered and add a `/scenario-test` route rendering `<ScenarioTest />` (import it). Match the exact router API in use (e.g. `createBrowserRouter` children or `<Route>` elements).

- [ ] **Step 2: Add the Sidebar nav entry**

In `frontend/src/components/Sidebar.tsx`, add a nav item linking to `/scenario-test` labeled "Scenario Test", placed next to "Risk". Match the existing nav-item shape (icon/label/path).

- [ ] **Step 3: Run the frontend test suite for regressions**

Run: `cd frontend && npx vitest run src/components/Sidebar.test.tsx src/routes/ScenarioTest.test.tsx`
Expected: PASS. If `Sidebar.test.tsx` asserts an exact nav list, update it to include "Scenario Test".

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.tsx frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx
git commit -m "feat(scenario-test): route + sidebar nav entry"
```

---

## Final Verification

- [ ] **Backend suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (no regressions; new scenario-test tests green).

- [ ] **Frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **End-to-end smoke (manual, optional)**

Start the app, open **Scenario Test**, pick a portfolio + profile, select `market_crash` + `covid_crash_2020`, run, and confirm a run appears with worst/best + a downloadable report. (Use the `verify`/`run` skill if helpful.)

- [ ] **Update project memory**

Add a memory entry for the scenario-test feature (mirrors the hedging/batch-pricing entries): the `EquityPortfolio` bridge, the six-file skill coupling that was touched, and the `greeks_method="numerical"` default rationale.

---

## Self-Review Notes (author)

- **Spec coverage:** every spec section maps to a task — data model (T1–2), bridge (T3), catalog incl. saved sets (T4–5), engine driver + shaping (T6), exports/report (T7), async runner (T8), schemas (T9), REST (T10), tools (T11), skill + reference (T12) + six-file coupling (T13), frontend (T14–16). Error-handling cases (empty/excluded, missing libs, missing profile coverage) are covered by T3/T6/T7/T8 tests and the `_pricing_position_context` reuse.
- **Type consistency:** `build_equity_portfolio(positions, position_markets, *, portfolio_name) -> (portfolio, excluded)` is used identically in T3 and T8; `run_pipeline(...) -> (status, dict, excluded, raw)` matches T6/T8; `shape_results`/`write_artifacts` signatures match across T6/T7/T8; tool names match across T11 and the catalog/allowlist tests.
- **Known verify-against-source points flagged inline:** `ScenarioStorage` / `ResultExporter` / `ReportGenerator` kwargs, `mark_task_finished` / `positions_svc.list_filtered` / `record_audit` signatures, the frontend router/API-client/Sidebar exact shapes. These are existing-code signatures the executor confirms when touching each file.
