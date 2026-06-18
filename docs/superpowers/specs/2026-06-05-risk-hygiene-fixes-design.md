# Risk & Portfolio Hygiene Fixes — Design

**Date:** 2026-06-05
**Status:** Approved-in-conversation; awaiting written-spec review
**Origin:** Watch-items from the hedge-booking-graph cycle (spec 2026-06-04) and its
post-instrument-unification audit. Three independent fixes, one plan.

## Problem statements

### Fix 1 — Three interrupt-listed portfolio tools are unbound

`delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio` are in
`INTERRUPT_TOOL_NAMES` (hitl.py:42-45) with risk levels and card labels already
defined, but absent from `DEEP_AGENT_TOOL_NAMES` (agents.py). This is the exact
latent pattern behind the thread-43 `book_hedge` failure: a HITL card can surface
for a tool the runtime cannot execute. Today the gap is pinned by
`test_interrupt_tools_are_bound_deep_agent_tools` (`rest_only` set). Decision:
**bind all three**, with minimal prompt guidance so they are not unguided.

### Fix 2 — Closed positions poison risk-run status

`resolve_positions` (portfolio_membership.py) returns every position regardless of
`status`. Each closed position then flows through `risk_pricing_exclusion`
(quantark.py:1285) into a `pricing_ok=False` metrics row, and
`_risk_status_from_metrics` (risk_engine.py:646) flips the entire run to
`completed_with_errors` on any such row. Consequences, verified live:

- Portfolio 5: latest run #28 has 107 rows, 12 "errors" — **9 are just
  "Closed position excluded from risk"**; 3 genuine pricing problems are drowned out.
- A portfolio with any closed position can never produce a plain `completed` run,
  which is why `get_latest_run` (domains/risk.py:149) was forced to accept
  `completed_with_errors` — and why `get_hedgeable_underlyings`' docstring
  ("latest completed risk run") drifted from reality.

Decision: **filter closed positions out of risk-run resolution entirely** (silent
filter, including when explicit `position_ids` are passed). Align docstrings.

### Fix 3 — `HEDGE:{run}:{n}` collides on re-booking

`book_hedge` (domains/hedging_strategy.py:323) numbers legs `n = 1..k` per call. A
second booking against the same `risk_run_id` mints duplicate `source_trade_id`s
(`HEDGE:21:1` twice). The index is non-unique so nothing crashes — but identity is
silently lost. Decision: **continuation numbering** — start `n` past the max
existing suffix for that run. A DB unique constraint was rejected: `source_trade_id`
is shared with the OTC-import path, where re-import refresh relies on matching ids.

## Design

### Fix 1: bind + guide the portfolio write tools

**Binding** — `agents.py` `DEEP_AGENT_TOOL_NAMES`, next to `list_portfolios` /
`get_portfolio`:

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

**Test** — `tests/test_hitl.py`: delete the `rest_only` pin from
`test_interrupt_tools_are_bound_deep_agent_tools`; the assertion becomes simply
`set(INTERRUPT_TOOL_NAMES) - DEEP_AGENT_TOOL_NAMES == set()`. The invariant is then
self-enforcing for these three (interrupt-listed ⊆ bound).

**Orchestrator** (`prompts/orchestrator.md`) — two edits:

1. Routing bullet (near the hedge-execution bullet, not a matrix row — there is no
   workflow skill for this, matching the existing position-lifecycle pattern):

   > - Portfolio maintenance (delete a portfolio, replace a view's filter rule,
   >   remove positions from a portfolio) → `trader`, direct tool use
   >   (`delete_portfolio` / `set_portfolio_rule` / `remove_positions_from_portfolio`),
   >   each HITL-gated. Removing positions from a **container** physically deletes
   >   the position rows; "close out a position" is lifecycle (`close_position`),
   >   never removal.

2. Persisted-tools list (line ~349) += `delete_portfolio`, `set_portfolio_rule`,
   `remove_positions_from_portfolio`.

**Trader persona** (`prompts/trader.md`) — one paragraph:

> ## Portfolio maintenance
>
> You may delete portfolios (`delete_portfolio`), replace a view's filter rule
> (`set_portfolio_rule`), and remove positions from a portfolio
> (`remove_positions_from_portfolio`). All three are HITL-gated. Sharp edges:
> deleting a **container** cascades its positions; removing positions from a
> **container** physically deletes the rows (a view only un-includes them). When
> the user means "close/settle a trade," use the lifecycle tools
> (`close_position`/`settle_position`) — removal destroys history, lifecycle
> preserves it.

**Not in scope:** a `portfolio-maintenance` workflow SKILL.md. The catalog tests
pin exact skill sets, and the per-tool semantics live in tool docstrings the model
now sees (bound tools expose their Field/docstring text — the lesson from the
book_hedge smoke test). If usage shows the model needs procedure, add the skill in
a follow-up cycle. Routing stays persona-direct, like position lifecycle.

**Persona note:** `trader` is chosen because portfolio maintenance is a desk
operation adjacent to booking/lifecycle, which trader already owns. risk_manager
keeps its read/analysis lens.

### Fix 2: closed positions out of risk runs

**New helper** — `quantark.py`, extracted from `risk_pricing_exclusion`:

```python
def closed_position_exclusion(position: Position) -> str | None:
    """Economically-closed check: status 'closed' or a terminal lifecycle
    state in the source payload. Shared by membership-time filtering
    (risk_engine._resolve_risk_positions) and pricing-time defense below."""
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

`risk_pricing_exclusion` keeps the closed check as **defense-in-depth**: the async
worker re-resolves from stored ids, and a position can close between queue time and
execution. Mapping errors stay error rows — they are genuinely actionable.

**Filter site** — `risk_engine.py` `_resolve_risk_positions`:

```python
def _resolve_risk_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    position_ids: list[int] | None,
) -> list[Position]:
    resolved = resolve_positions(portfolio, session)
    open_positions = [p for p in resolved if closed_position_exclusion(p) is None]
    if position_ids is None:
        return open_positions
    requested_ids = _normalize_position_ids(position_ids)
    by_id = {position.id: position for position in resolved}
    open_ids = {position.id for position in open_positions}
    missing_ids = [pid for pid in requested_ids if pid not in by_id]
    if missing_ids:
        raise ValueError(
            "Position ids are not in portfolio "
            f"{portfolio.id}: {', '.join(str(pid) for pid in missing_ids)}"
        )
    # Closed positions are silently filtered (user decision 2026-06-05);
    # foreign ids still error. All requested closed -> empty run, which
    # completes with empty metrics — honest, if unusual.
    return [by_id[pid] for pid in requested_ids if pid in open_ids]
```

All three risk paths (sync `run_portfolio_risk`, queue-time scoping, async worker
re-resolution) funnel through this one function, so one filter covers them.
`resolve_positions` itself is untouched — the Positions page must keep showing
closed positions in portfolio membership.

**Effects:** closed positions no longer appear in `metrics["positions"]` nor in
`resolved_position_ids`; runs whose only "errors" were closed positions now reach
plain `completed`. `get_latest_run` keeps accepting `completed_with_errors`
(partial pricing failures must not block hedging the priceable underlyings).

**Docstring alignment** (the original watch-item):

- `get_hedgeable_underlyings_tool` (tools/hedging.py): "Per-underlying greek
  exposure + staleness from the latest usable risk run (completed, or
  completed_with_errors — only rows that priced cleanly aggregate)."
- `aggregate_by_underlying` (hedging_greeks.py): same correction in the first line.

### Fix 3: HEDGE id continuation numbering

`domains/hedging_strategy.py` `book_hedge`, before the leg loop:

```python
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

    n = max((_leg_suffix(t) for t in existing), default=0)
```

The loop keeps `n += 1` per non-zero leg, so a second booking against run 21 mints
`HEDGE:21:3`, `HEDGE:21:4`, … The trailing colon in the prefix prevents
`HEDGE:2:%` from matching `HEDGE:21:1`. Race conditions across concurrent sessions
are accepted (single-desk dev tool; same session books inside one unit-of-work).

## Testing

- **Fix 1:** existing invariant test (with `rest_only` pin removed) enforces
  binding; existing exact-set INTERRUPT pin unchanged. Full `test_hitl.py` +
  skills-catalog suites must stay green (no SKILL.md files added/removed, so the
  catalog count pins are untouched).
- **Fix 2** (new tests in the risk-engine test module):
  - closed position in a container → run status `completed`, no metrics row for
    it, `resolved_position_ids` excludes it;
  - explicit `position_ids` containing a closed id → silently dropped, others run;
  - foreign id still raises "not in portfolio";
  - terminal-source-state (敲出) position excluded the same way.
- **Fix 3** (`tests/test_hedging_book.py`): book twice against the same run →
  second booking's ids continue (`:3`, `:4`); ids unique across both bookings.
- Full backend suite; the 6 known env failures (langchain_sandbox ×2, deepseek ×3,
  quickjs ×1) are pre-existing.

## Out of scope

- `portfolio-maintenance` workflow skill (follow-up if usage demands procedure).
- Binding `create_portfolio` / `update_portfolio` / `add_positions_to_portfolio`
  (write tools that are *not* interrupt-listed; binding them would require new HITL
  cards first — flagged as a known asymmetry: the agent can delete but not create
  portfolios. Acceptable: creation is a deliberate UI act; deletion benefits from
  conversational convenience + HITL).
- DB unique constraint on `source_trade_id` (breaks OTC re-import refresh).
- Cleaning dev-DB smoke artifacts (threads 44/45, positions 113/114) — separate
  housekeeping, not a code change.
