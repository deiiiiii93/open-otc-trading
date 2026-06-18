# Risk & Portfolio Hygiene Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind the three interrupt-listed portfolio write tools to the desk agent (with routing/persona guidance), filter economically-closed positions out of risk-run resolution, and make `HEDGE:{run}:{n}` ids continue past existing legs instead of colliding.

**Architecture:** Three independent fixes from `docs/superpowers/specs/2026-06-05-risk-hygiene-fixes-design.md`. Fix 1 closes the bound-vs-interrupt-listed gap pinned by `test_interrupt_tools_are_bound_deep_agent_tools`. Fix 2 extracts a `closed_position_exclusion` helper from `risk_pricing_exclusion` (quantark.py) and applies it in `_resolve_risk_positions` (risk_engine.py) — the single chokepoint all three risk paths (sync, queue-time scoping, async worker) funnel through. Fix 3 scans existing `HEDGE:{run}:%` ids inside the booking session and starts numbering past the max suffix.

**Tech Stack:** Python/SQLAlchemy backend, pytest, LangGraph deep-agent prompt files (markdown).

**Environment gotchas (read first):**
- Work from the worktree root: `/Users/fuxinyao/open-otc-trading/.claude/worktrees/risk-hygiene-fixes`.
- ALWAYS prefix pytest with `PYTHONPATH=$PWD/backend` — the venv's `.pth` resolves `app` imports to the MAIN checkout otherwise, and you'd be testing the wrong tree.
- 6 backend test failures are pre-existing environment issues, NOT yours: 2× `test_run_python*` (langchain_sandbox missing), 3× `test_model_factory*deepseek*`, 1× quickjs (langchain_quickjs missing).

---

## File Structure

| File | Change |
|---|---|
| `tests/test_hitl.py` | Tighten invariant test: remove the `rest_only` pin (Task 1) |
| `backend/app/services/agents.py` | Bind 3 portfolio write tools in `DEEP_AGENT_TOOL_NAMES` (Task 1) |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | Routing bullet + persisted-tools list (Task 2) |
| `backend/app/services/deep_agent/prompts/trader.md` | Portfolio-maintenance paragraph (Task 2) |
| `tests/test_risk_engine.py` | 3 new closed-position-filter tests (Task 3) |
| `backend/app/services/quantark.py` | Extract `closed_position_exclusion` (Task 3) |
| `backend/app/services/risk_engine.py` | Filter closed positions in `_resolve_risk_positions` (Task 3) |
| `backend/app/tools/hedging.py` | Docstring alignment (Task 4) |
| `backend/app/services/hedging_greeks.py` | Docstring alignment (Task 4) |
| `tests/test_hedging_book.py` | Continuation-numbering test (Task 5) |
| `backend/app/services/domains/hedging_strategy.py` | HEDGE id continuation (Task 5) |

---

### Task 1: Bind the three portfolio write tools

The HITL cards, risk levels, and labels for `delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio` already exist in `backend/app/services/deep_agent/hitl.py` — only the binding is missing. TDD via the existing invariant test: tightening it IS the failing test.

**Files:**
- Modify: `tests/test_hitl.py:38-60`
- Modify: `backend/app/services/agents.py` (DEEP_AGENT_TOOL_NAMES, near `"list_portfolios"`)

- [ ] **Step 1: Tighten the invariant test (this is the failing test)**

In `tests/test_hitl.py`, replace the body of `test_interrupt_tools_are_bound_deep_agent_tools`. Old text:

```python
def test_interrupt_tools_are_bound_deep_agent_tools():
    """A HITL card must never surface for a tool the runtime cannot execute.

    The interrupt middleware matches tool-CALL names, but execution requires
    the tool in select_deep_agent_tools' allowlist — a gap here means the user
    approves a card and then gets "book_hedge is not a valid tool" (found live
    in thread 44's smoke replay).
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    # Pre-existing, deliberate gap: portfolio CRUD writes are REST-only today.
    # Pinned so the gap is visible and cannot silently grow.
    rest_only = {
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
    }
    unbound = set(INTERRUPT_TOOL_NAMES) - DEEP_AGENT_TOOL_NAMES - rest_only
    assert unbound == set(), (
        f"Interrupt-listed tools not bound to the deep agent: {sorted(unbound)}"
    )
    # If a rest_only tool gets bound, shrink the pin instead of leaving it stale.
    assert rest_only & DEEP_AGENT_TOOL_NAMES == set()
```

New text:

```python
def test_interrupt_tools_are_bound_deep_agent_tools():
    """A HITL card must never surface for a tool the runtime cannot execute.

    The interrupt middleware matches tool-CALL names, but execution requires
    the tool in select_deep_agent_tools' allowlist — a gap here means the user
    approves a card and then gets "book_hedge is not a valid tool" (found live
    in thread 44's smoke replay). No exceptions: every interrupt-listed tool
    must be bound (the former portfolio-CRUD rest_only pin was closed
    2026-06-05 by binding the three tools).
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    unbound = set(INTERRUPT_TOOL_NAMES) - DEEP_AGENT_TOOL_NAMES
    assert unbound == set(), (
        f"Interrupt-listed tools not bound to the deep agent: {sorted(unbound)}"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py::test_interrupt_tools_are_bound_deep_agent_tools -q`
Expected: FAIL with `Interrupt-listed tools not bound to the deep agent: ['delete_portfolio', 'remove_positions_from_portfolio', 'set_portfolio_rule']`

- [ ] **Step 3: Bind the tools**

In `backend/app/services/agents.py` inside `DEEP_AGENT_TOOL_NAMES`, replace:

```python
        "list_portfolios",
        "get_portfolio",
```

with:

```python
        "list_portfolios",
        "get_portfolio",
        # Portfolio maintenance writes (HITL-gated; delete/remove are
        # "irreversible" so even YOLO mode keeps the card). Bound so the
        # cards in INTERRUPT_TOOL_NAMES are always executable.
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py -q`
Expected: ALL PASS (the whole module — the exact-set INTERRUPT pin and YOLO tests must also stay green; they don't change because the interrupt list itself is untouched).

- [ ] **Step 5: Run the coupled suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py tests/test_capability_assignments.py tests/test_personas.py tests/test_async_agents_unit.py -q`
Expected: ALL PASS (capability test already pins `delete_portfolio` as DOMAIN_WRITE; async agents derive from the same allowlist).

- [ ] **Step 6: Commit**

```bash
git add tests/test_hitl.py backend/app/services/agents.py
git commit -m "feat(agent): bind portfolio maintenance writes (delete/set-rule/remove) — closes the rest_only HITL gap"
```

---

### Task 2: Orchestrator routing + trader persona guidance

Prose-only task (no TDD — verified by the prompt-contract suites). Three edits.

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md` (routing bullets ~line 60; persisted list ~line 349)
- Modify: `backend/app/services/deep_agent/prompts/trader.md` (end of "Routing from skills")

- [ ] **Step 1: Add the routing bullet to orchestrator.md**

Find the hedge-execution bullet ending with:

```markdown
  bookings are NEVER quote-first and never `book-position` — only `book_hedge`
  carries the hedge tag.
```

Append immediately after it (new bullet, before the "Reporting, report artifacts" bullet):

```markdown
- Portfolio maintenance (delete a portfolio, replace a view's filter rule,
  remove positions from a portfolio) → `trader`, direct tool use
  (`delete_portfolio` / `set_portfolio_rule` / `remove_positions_from_portfolio`),
  each HITL-gated. Removing positions from a **container** physically deletes
  the position rows; "close out a position" is lifecycle (`close_position`),
  never removal.
```

- [ ] **Step 2: Extend the persisted-tools list in orchestrator.md**

In the line starting `NEVER request more than one persisted/HITL-gated tool call`, replace:

```markdown
`book_position`, `book_hedge`, `set_hedge_bands`, `import_otc_positions`, plus `run_python` when `writes_artifacts=true`.
```

with:

```markdown
`book_position`, `book_hedge`, `set_hedge_bands`, `import_otc_positions`, `delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio`, plus `run_python` when `writes_artifacts=true`.
```

- [ ] **Step 3: Add the trader persona paragraph**

In `backend/app/services/deep_agent/prompts/trader.md`, after the final paragraph (which ends `carries the hedge tag onto the Hedging page.`), append:

```markdown

For portfolio maintenance you may delete portfolios (`delete_portfolio`),
replace a view's filter rule (`set_portfolio_rule`), and remove positions from
a portfolio (`remove_positions_from_portfolio`). All three are HITL-gated.
Sharp edges: deleting a **container** cascades its positions; removing
positions from a **container** physically deletes the rows (a view only
un-includes them). When the user means "close/settle a trade," use the
lifecycle tools (`close_position`/`settle_position`) — removal destroys
history, lifecycle preserves it.
```

- [ ] **Step 4: Run the prompt-contract suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_routing_contracts_phase3.py tests/test_workflow_skills_phase3.py tests/test_personas.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py -q`
Expected: ALL PASS (edits only add prose; contract tests assert substrings that are untouched; no SKILL.md files added so catalog count pins hold).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md backend/app/services/deep_agent/prompts/trader.md
git commit -m "docs(agent): route portfolio maintenance to trader; teach remove-vs-close distinction; extend persisted-tools list"
```

---

### Task 3: Filter closed positions out of risk-run resolution

**Files:**
- Test: `tests/test_risk_engine.py` (append 3 tests at end of file)
- Modify: `backend/app/services/quantark.py:1285-1297` (`risk_pricing_exclusion`)
- Modify: `backend/app/services/risk_engine.py:25-32` (import) and `:613-630` (`_resolve_risk_positions`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_engine.py` (modeled on `test_risk_run_can_scope_to_position_ids` — same settings/monkeypatch harness; `fake_calculate_portfolio_risk` reports every position it receives as cleanly priced, so any `completed_with_errors` status or extra row can only come from resolution, not pricing):

```python
def _risk_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _capture_calculate(monkeypatch):
    from app.services import risk_engine

    captured: dict[str, list[int]] = {}

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        captured["position_ids"] = [position.id for position in portfolio.positions]
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "pricing_ok": True,
                    "greeks_ok": True,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        risk_engine,
        "calculate_portfolio_risk",
        fake_calculate_portfolio_risk,
    )
    return captured


def test_risk_run_excludes_closed_positions(tmp_path, monkeypatch):
    """A closed position must not enter the run at all — no metrics row, not in
    resolved_position_ids, and (crucially) it cannot poison run status: a run
    whose only 'problem' is a closed position completes plainly."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        open_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        closed_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="closed",
        )
        session.add_all([open_pos, closed_pos])
        session.flush()

        run = run_portfolio_risk(session, portfolio_id=portfolio.id, method="summary")

        assert captured["position_ids"] == [open_pos.id]
        assert run.resolved_position_ids == [open_pos.id]
        assert run.status == "completed"


def test_risk_run_silently_drops_closed_explicit_ids(tmp_path, monkeypatch):
    """Explicit position_ids naming a closed position: the closed id is
    silently filtered (user decision 2026-06-05), the rest run; foreign ids
    still raise (covered by test_risk_run_rejects_position_ids_outside_portfolio)."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        open_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        closed_pos = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0},
            quantity=2.0,
            status="closed",
        )
        session.add_all([open_pos, closed_pos])
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            method="summary",
            position_ids=[open_pos.id, closed_pos.id],
        )

        assert captured["position_ids"] == [open_pos.id]
        assert run.resolved_position_ids == [open_pos.id]


def test_risk_run_excludes_terminal_source_state(tmp_path, monkeypatch):
    """status='open' but source row says 敲出 (knocked out): economically
    closed, excluded the same way (closed_position_exclusion covers both)."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        live = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        knocked_out = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="SnowballOption",
            product_kwargs={},
            quantity=1.0,
            status="open",
            source_payload={"row": {"交易状态": "敲出"}},
        )
        session.add_all([live, knocked_out])
        session.flush()

        run = run_portfolio_risk(session, portfolio_id=portfolio.id, method="summary")

        assert captured["position_ids"] == [live.id]
        assert run.resolved_position_ids == [live.id]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_risk_engine.py -q -k "closed or terminal_source"`
Expected: 3 FAILED — each `captured["position_ids"]` assertion shows the closed/terminal id still present.

- [ ] **Step 3: Extract `closed_position_exclusion` in quantark.py**

Replace (at `backend/app/services/quantark.py:1285`):

```python
def risk_pricing_exclusion(position: Position) -> str | None:
    mapping_status = str(getattr(position, "mapping_status", "manual") or "manual")
    if mapping_status in {"unsupported", "error"}:
        return (
            getattr(position, "mapping_error", None)
            or f"Position mapping status is {mapping_status}"
        )
    if str(getattr(position, "status", "open") or "open") == "closed":
        return "Closed position excluded from risk"
    source_state = _source_trade_state_for_position(position)
    if source_state in {"敲出", "结算", "平仓"}:
        return f"Terminal lifecycle state excluded from risk: {source_state}"
    return None
```

with:

```python
def closed_position_exclusion(position: Position) -> str | None:
    """Economically-closed check: status 'closed' or a terminal lifecycle state
    in the source payload. Shared by membership-time filtering
    (risk_engine._resolve_risk_positions) and the pricing-time defense in
    risk_pricing_exclusion below (a position can close between queue time and
    async worker execution)."""
    if str(getattr(position, "status", "open") or "open") == "closed":
        return "Closed position excluded from risk"
    source_state = _source_trade_state_for_position(position)
    if source_state in {"敲出", "结算", "平仓"}:
        return f"Terminal lifecycle state excluded from risk: {source_state}"
    return None


def risk_pricing_exclusion(position: Position) -> str | None:
    mapping_status = str(getattr(position, "mapping_status", "manual") or "manual")
    if mapping_status in {"unsupported", "error"}:
        return (
            getattr(position, "mapping_error", None)
            or f"Position mapping status is {mapping_status}"
        )
    return closed_position_exclusion(position)
```

- [ ] **Step 4: Import and filter in risk_engine.py**

Edit the import block (`backend/app/services/risk_engine.py:25-32`), replace:

```python
    market_snapshot_for_position,
    risk_pricing_exclusion,
```

with:

```python
    closed_position_exclusion,
    market_snapshot_for_position,
    risk_pricing_exclusion,
```

Then replace `_resolve_risk_positions` (currently at `:613`):

```python
def _resolve_risk_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    position_ids: list[int] | None,
) -> list[Position]:
    resolved = resolve_positions(portfolio, session)
    if position_ids is None:
        return resolved
    requested_ids = _normalize_position_ids(position_ids)
    by_id = {position.id: position for position in resolved}
    missing_ids = [position_id for position_id in requested_ids if position_id not in by_id]
    if missing_ids:
        raise ValueError(
            "Position ids are not in portfolio "
            f"{portfolio.id}: {', '.join(str(position_id) for position_id in missing_ids)}"
        )
    return [by_id[position_id] for position_id in requested_ids]
```

with:

```python
def _resolve_risk_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    position_ids: list[int] | None,
) -> list[Position]:
    resolved = resolve_positions(portfolio, session)
    # Economically-closed positions never enter a risk run: no metrics row,
    # not in resolved_position_ids, cannot poison run status. Membership
    # display (resolve_positions itself) still includes them.
    open_positions = [p for p in resolved if closed_position_exclusion(p) is None]
    if position_ids is None:
        return open_positions
    requested_ids = _normalize_position_ids(position_ids)
    by_id = {position.id: position for position in resolved}
    open_ids = {position.id for position in open_positions}
    missing_ids = [position_id for position_id in requested_ids if position_id not in by_id]
    if missing_ids:
        raise ValueError(
            "Position ids are not in portfolio "
            f"{portfolio.id}: {', '.join(str(position_id) for position_id in missing_ids)}"
        )
    # Closed positions are silently filtered (user decision 2026-06-05);
    # foreign ids still error above. All-requested-closed yields an empty,
    # plainly-completed run with empty metrics — honest, if unusual.
    return [by_id[position_id] for position_id in requested_ids if position_id in open_ids]
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_risk_engine.py -q`
Expected: ALL PASS (including the pre-existing scope/reject tests — foreign ids still raise).

- [ ] **Step 6: Run the coupled risk suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_risk_engine.py tests/test_services_domains_risk.py tests/test_tools_risk.py tests/test_cli_risk.py tests/test_risk_row_spot.py tests/test_position_currency_drives_risk.py tests/test_hedging_greeks.py -q`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_risk_engine.py backend/app/services/quantark.py backend/app/services/risk_engine.py
git commit -m "fix(risk): closed/terminal positions never enter risk runs — stop poisoning run status with completed_with_errors"
```

---

### Task 4: Docstring alignment (`get_hedgeable_underlyings`)

The watch-item that started this: docstrings say "latest completed risk run" but `get_latest_run` (deliberately, still) accepts `completed_with_errors`.

**Files:**
- Modify: `backend/app/tools/hedging.py:71-73`
- Modify: `backend/app/services/hedging_greeks.py:14-15`

- [ ] **Step 1: Fix the tool docstring**

In `backend/app/tools/hedging.py`, replace:

```python
def get_hedgeable_underlyings_tool(portfolio_id: int) -> dict[str, Any]:
    """Per-underlying greek exposure + staleness from the latest completed risk run."""
```

with:

```python
def get_hedgeable_underlyings_tool(portfolio_id: int) -> dict[str, Any]:
    """Per-underlying greek exposure + staleness from the latest usable risk run
    (completed, or completed_with_errors — only rows that priced cleanly aggregate)."""
```

- [ ] **Step 2: Fix the service docstring**

In `backend/app/services/hedging_greeks.py`, replace:

```python
def aggregate_by_underlying(session: Session, *, portfolio_id: int) -> dict[str, Any]:
    """Per-underlying {delta_cash, gamma_cash, vega} from the latest completed RiskRun.
```

with:

```python
def aggregate_by_underlying(session: Session, *, portfolio_id: int) -> dict[str, Any]:
    """Per-underlying {delta_cash, gamma_cash, vega} from the latest usable RiskRun
    (completed or completed_with_errors; only greeks_ok rows aggregate).
```

- [ ] **Step 3: Run the hedging suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hedging_greeks.py tests/test_hedging_tools.py tests/test_hitl.py -q`
Expected: ALL PASS (docstring text is not pinned by any test; test_hitl re-run guards against accidental tool-name drift).

- [ ] **Step 4: Commit**

```bash
git add backend/app/tools/hedging.py backend/app/services/hedging_greeks.py
git commit -m "docs(hedging): get_hedgeable_underlyings honestly documents completed_with_errors acceptance"
```

---

### Task 5: HEDGE source_trade_id continuation numbering

**Files:**
- Test: `tests/test_hedging_book.py` (append)
- Modify: `backend/app/services/domains/hedging_strategy.py:299-301` (top of `book_hedge` body, after the strategy validation)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hedging_book.py`:

```python
def test_book_hedge_continues_leg_numbering_per_run(session):
    """A second booking against the same risk_run_id must not re-mint
    HEDGE:{run}:1 — numbering continues past the max existing suffix so
    source_trade_id stays unique without a DB constraint (which would break
    OTC re-import refresh)."""
    pf = Portfolio(name="book_seq", base_currency="CNY")
    session.add(pf); session.flush()
    leg = {"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
           "instrument_type": "future", "multiplier": 200.0, "quantity": -2}

    first = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=21, strategy="manual", legs=[leg, dict(leg)],
                          spot=5600.0)
    session.flush()
    second = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                           risk_run_id=21, strategy="manual", legs=[dict(leg)],
                           spot=5600.0)
    session.flush()

    first_ids = [session.get(Position, pid).source_trade_id
                 for pid in first["position_ids"]]
    second_ids = [session.get(Position, pid).source_trade_id
                  for pid in second["position_ids"]]
    assert first_ids == ["HEDGE:21:1", "HEDGE:21:2"]
    assert second_ids == ["HEDGE:21:3"]
    # A different run keeps its own namespace (prefix match must not bleed
    # across runs sharing a string prefix, e.g. HEDGE:2: vs HEDGE:21:).
    other = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=2, strategy="manual", legs=[dict(leg)],
                          spot=5600.0)
    session.flush()
    other_ids = [session.get(Position, pid).source_trade_id
                 for pid in other["position_ids"]]
    assert other_ids == ["HEDGE:2:1"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hedging_book.py::test_book_hedge_continues_leg_numbering_per_run -q`
Expected: FAIL — `second_ids == ["HEDGE:21:1"]` (duplicate of the first booking's first leg).

- [ ] **Step 3: Implement continuation numbering**

In `backend/app/services/domains/hedging_strategy.py`, inside `book_hedge`, replace:

```python
    position_ids: list[int] = []
    n = 0
```

with:

```python
    position_ids: list[int] = []
    # Continue numbering past existing legs for this run so a second booking
    # against the same risk_run_id cannot re-mint HEDGE:{run}:1 (the index is
    # non-unique by design — the OTC import path shares source_trade_id).
    # Trailing colon keeps the namespace per-run: 'HEDGE:2:%' must not match
    # 'HEDGE:21:1'.
    prefix = f"HEDGE:{risk_run_id}:"
    existing = [
        tid
        for (tid,) in session.query(Position.source_trade_id)
        .filter(Position.source_trade_id.like(prefix + "%"))
        .all()
    ]

    def _leg_suffix(trade_id: str) -> int:
        try:
            return int(trade_id.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return 0

    n = max((_leg_suffix(tid) for tid in existing), default=0)
```

`Position` is NOT currently imported in this file (booking goes through
`BookingRequest`, never the model). Add it — replace line 10:

```python
from ...models import HedgeBand, Underlying
```

with:

```python
from ...models import HedgeBand, Position, Underlying
```

(`Underlying` stays as-is — the `Underlying`→`Instrument` vocabulary cleanup is a
separate, out-of-scope item.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hedging_book.py -q`
Expected: ALL PASS (existing single-booking tests still mint `:1, :2, ...` because their runs have no prior legs).

- [ ] **Step 5: Run the hedging domain suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hedging_book.py tests/test_hedging_domain.py tests/test_hedging_strategy_api.py tests/test_hedging_tools.py -q`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_hedging_book.py backend/app/services/domains/hedging_strategy.py
git commit -m "fix(hedging): HEDGE:{run}:{n} ids continue past existing legs — re-booking a run no longer mints duplicates"
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Run the full backend suite**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/ -q 2>&1 | tail -15`
Expected: only the 6 known pre-existing env failures (2× run_python/langchain_sandbox, 3× model_factory deepseek, 1× quickjs). Anything else failing is a regression from this plan — stop and investigate before proceeding.

- [ ] **Step 2: Verify the worktree is clean and history is coherent**

Run: `git status --short && git log --oneline main..HEAD 2>/dev/null || git log --oneline -8`
Expected: no unstaged changes; commits from Tasks 1-5 plus the spec/plan docs.
