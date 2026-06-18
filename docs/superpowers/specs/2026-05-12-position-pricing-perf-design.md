# Position pricing performance — adaptive grid + parallel inner loop

**Date:** 2026-05-12
**Owner:** open-otc-trading backend
**Status:** Approved (design)

## Motivation

Task #4 (`position_pricing`, run #14) priced 130 SnowballOption positions for portfolio 6
in **79 min 5 s** wall-clock, single-threaded, at `SnowballQuadEngine(grid_points=1001)`.
Mean per-pricing time was ≈ 36.5 s. Two structural causes:

1. `price_portfolio_positions` runs a serial `for position in positions:` loop
   (`backend/app/services/position_pricer.py:139`). With `async_task_workers=1` and no
   inner parallelism in the pricer, exactly one CPU core does real work for the entire run.
2. Every position carries `engine_kwargs.params_kwargs.grid_points = 1001` baked in by
   `position_adapter.py:879`. Snowball QUAD cost scales as
   *O(N_grid · log N_grid · N_obs_steps)*, so 1001 is paid even when a coarser grid would
   produce a numerically usable price.

Goal: cut task #4's wall-clock from 79 min to roughly tens of seconds while preserving
correctness, and surface per-position progress in the UI.

## Scope

In scope:

- Inner-loop parallelism in `price_portfolio_positions`.
- Adaptive grid escalation `[201, 501, 1001]` for snowball QUAD pricings whose
  `engine_kwargs.params_kwargs.grid_points` is unset; gate on `usable_model_value`.
- Greeks (`compute_position_greeks`) reuse the grid the price chose ("price as a probe").
- Per-position progress reporting via a callback wired into `update_task_progress`.
- Importer change: stop baking `grid_points: 1001` into new positions.
- One-shot alembic migration that strips the default `1001` from existing positions only.

Out of scope (deliberate YAGNI):

- Greeks-driven grid escalation (greeks reuse the price's chosen grid only).
- Cross-position caching for shared `(underlying, valuation_date)` — separate optimization.
- Throttling progress commits (≤ 130 commits/run on SQLite WAL is trivial).
- Replaying historical `position_valuation_runs.overrides` snapshots — they are records,
  not config.

## Design decisions (resolved)

| Decision | Choice | Rationale |
|---|---|---|
| Stop rule for adaptive grid | `usable_model_value(market_value, gross_notional)` only | Cheapest gate; catches NaN/Inf and implausibly large prices. Accepted that grid=201 will be the de-facto default for well-behaved snowballs. |
| Parallelism model | `concurrent.futures.ThreadPoolExecutor` | SnowballQuadEngine is FFT-heavy (NumPy releases the GIL). Shared imports, no pickling. SQLAlchemy session stays on the main thread. |
| Worker count source | Reuse `Settings.risk_parallel_workers` | Same shape as risk runs (CPU-bound NumPy work). Default `min(8, cpu_count())`. |
| Existing positions | Migrate: strip `grid_points: 1001` from `positions.engine_kwargs` only where it equals the old default | New default applies to existing data so task #4 benefits immediately. Hand-tuned values (≠1001) preserved. |
| Greeks adaptivity | Reuse the grid the price chose | "Price as a probe": the price has been gated by `usable_model_value` at that grid, so greeks at the same grid are self-consistent. Avoids a second adaptive chain. |
| Progress message | `"Priced k/N positions"` | Confirmed wording for the `task_runs.message` field. |
| Migration scope | Only `positions.engine_kwargs`; leave historical `position_valuation_runs.overrides` and `position_valuation_results.market_inputs` untouched | Historical fields are records of what was done, not config to replay. |
| Breadcrumb naming | `result_payload.grid_points_used` (always set); `result_payload.attempted_grids` (set only when chain length > 1) | Minimal payload growth on the fast path. |

## Architecture

### Files touched

```
backend/app/services/position_pricer.py
  + _resolve_grid_chain(engine_kwargs) -> list[int]
  + _engine_kwargs_with_grid(engine_kwargs, grid) -> dict
  ~ _price_position(...)              # adaptive chain; reuse winning grid for greeks
  ~ price_portfolio_positions(...)    # ThreadPoolExecutor + progress_callback

backend/app/services/risk_engine.py
  ~ compute_position_greeks(position, market, *, engine_kwargs=None)

backend/app/services/position_adapter.py
  ~ default_engine_kwargs (line 879)  # drop {"grid_points": 1001}

backend/app/services/quantark.py
  (no changes — engine_kwargs override already flows through price_product)

backend/alembic/versions/0009_strip_default_quad_grid_points.py
  + one-shot data migration

tests/
  + test_position_pricer_grid.py            # pure helpers
  + test_position_pricer_adaptive.py        # rigged engine; chain behavior
  + test_migrations.py (or extend existing) # alembic upgrade/downgrade
  ~ test_position_import_pricing.py         # parallel + progress integration
```

### Data flow

```
execute_position_pricing_task
  └── mark_task_running(total=0)
  └── price_portfolio_positions(progress_callback=cb)
        ├── resolve positions, market_inputs, pricing_rows
        ├── cb(0, N)                            # tell caller the real total
        ├── ThreadPoolExecutor(max_workers=W)
        │     for each position: submit _price_position(...)
        │     as_completed:
        │         results[position.id] = future.result()  # or _safe_result on raise
        │         done += 1
        │         cb(done, N)                   # main-thread session write inside cb
        ├── for position in positions (submission order):
        │         build PositionValuationResult, totals, session.add
        ├── run.summary = totals; session.flush
  └── update_task_progress(...) + mark_task_finished
```

### _price_position adaptive flow

```
... (existing market-input resolution unchanged) ...

grid_chain = _resolve_grid_chain(resolved_engine_kwargs)
last_priced = None
attempt_grid = None
priced_data  = None

for grid in grid_chain:
    attempt_kwargs = _engine_kwargs_with_grid(resolved_engine_kwargs, grid)
    priced = price_product(position.product_type, position.product_kwargs or {},
                           market, resolved_engine_name, attempt_kwargs)
    if not priced.ok:
        last_priced = priced
        continue
    unit_price = float(priced.data.get("price", 0.0))
    market_value = unit_price * float(position.quantity)
    gross_notional = gross_notional_for_position(position, market)
    if usable_model_value(market_value, gross_notional):
        attempt_grid = grid
        priced_data  = priced
        break
    last_priced = priced

if priced_data is None:
    return _failed_from(last_priced, attempted_grids=grid_chain, ...)  # "implausible" or engine error

# Build result_payload as today, with breadcrumbs:
result_payload["grid_points_used"] = attempt_grid
if len(grid_chain) > 1:
    result_payload["attempted_grids"] = grid_chain

if compute_greeks:
    greeks = compute_position_greeks(position, market, engine_kwargs=attempt_kwargs)
    ...
```

### Helper contracts

```python
def _resolve_grid_chain(engine_kwargs: dict | None) -> list[int]:
    """Return the grid escalation sequence.

    - If engine_kwargs['params_kwargs']['grid_points'] is set, returns
      [int(that_value)] — single attempt, no escalation.
    - Otherwise returns [201, 501, 1001] — adaptive chain.
    """

def _engine_kwargs_with_grid(engine_kwargs: dict | None, grid: int) -> dict:
    """Return a new engine_kwargs dict with params_kwargs.grid_points set to `grid`.

    - Does not mutate the input.
    - `params_type` is preserved verbatim if present, omitted if absent (the helper
      does not invent a params_type).
    - Any other keys in `params_kwargs` are preserved.
    - Any other top-level keys in `engine_kwargs` are preserved.
    """
```

### Threading model

- `_price_position` is a pure compute function. It calls `spot_fetcher`, `price_product`,
  and (optionally) `compute_position_greeks`. None of these touch the SQLAlchemy session.
- `symbol_spot_cache: dict[str, ...]` is read/written without a lock. Concurrent misses for
  the same symbol cause an idempotent duplicate `spot_fetcher` call (rare; harmless). In
  task #4's actual mode (`pricing_parameter_profile_id` set), the cache is never used.
- All ORM writes (`session.add`, totals, run.summary, update_task_progress) execute on the
  main thread inside `as_completed`'s loop.
- Worker count: `worker_count = max(1, min(get_settings().risk_parallel_workers, len(positions)))`.
- Single-position runs and `risk_parallel_workers <= 1` still go through the executor;
  the overhead is negligible and the code stays single-path.

### Importer default

`position_adapter.py:879`:

```python
# before
return {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
# after
return {"params_type": "quad_params"}
```

New positions imported after this change carry no explicit grid; the pricer's chain
applies. Hand-tuned positions (those whose import path set a non-1001 grid) continue to be
respected by `_resolve_grid_chain`.

### Migration 0009_strip_default_quad_grid_points

Driver-agnostic (works on SQLite without JSONB operators).

```python
def upgrade():
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, engine_kwargs FROM positions")).fetchall()
    updates = []
    for pid, ek in rows:
        kw = json.loads(ek) if isinstance(ek, str) else (ek or {})
        if kw.get("params_type") != "quad_params":
            continue
        pk = kw.get("params_kwargs") or {}
        if pk.get("grid_points") != 1001:
            continue
        pk.pop("grid_points", None)
        if pk:
            kw["params_kwargs"] = pk
        else:
            kw.pop("params_kwargs", None)
        updates.append({"id": pid, "engine_kwargs": json.dumps(kw)})
    for u in updates:
        bind.execute(text("UPDATE positions SET engine_kwargs = :engine_kwargs WHERE id = :id"), u)

def downgrade():
    # Inverse: add grid_points: 1001 back to any quad_params row missing it.
    # Idempotent w.r.t. rows that already have a grid_points value.
```

Rows with `grid_points` ≠ 1001 are untouched (preserves any hand-tuned values).

## Error handling

| Scenario | Behavior |
|---|---|
| Engine returns `ok=False` at grid N | Treat as soft fail; try next grid. Preserve the last error to surface if no grid succeeds. |
| All grids fail `usable_model_value` | Return the existing "implausible market value" error using the *last* attempt's payload, with `result_payload.attempted_grids = [201, 501, 1001]` for diagnostics. |
| `_price_position` raises an exception | A private helper `_safe_result(future, position)` calls `future.result()` inside `try`; on exception it `logger.exception(...)` and returns the same failed-dict shape as `_failed(position, f"Unexpected pricing error: {exc}", "pricing", engine_name=...)` produced today at `position_pricer.py:154-166`. Other positions continue; progress still advances. |
| `progress_callback` raises | Bubble up. Pricing of subsequent positions stops; `execute_position_pricing_task`'s outer `try/except` marks the task FAILED. |
| Single-position runs | Same executor path; no special-case. |

## Testing strategy

### Layer 1: Pure helpers (`tests/test_position_pricer_grid.py`)

- `_resolve_grid_chain(None)` → `[201, 501, 1001]`.
- `_resolve_grid_chain({})` → `[201, 501, 1001]`.
- `_resolve_grid_chain({"params_kwargs": {"grid_points": 501}})` → `[501]`.
- `_engine_kwargs_with_grid({"params_type": "quad_params"}, 201)` returns a new dict with `params_kwargs.grid_points == 201`; input is not mutated.
- `_engine_kwargs_with_grid({"params_kwargs": {"grid_points": 1001, "other": "x"}}, 201)` preserves `other`.

### Layer 2: Behavioral with rigged engine (`tests/test_position_pricer_adaptive.py`)

Monkeypatch `price_product` to a stub keyed on the `grid_points` in the passed engine_kwargs.

- **Case A — fast path**: stub returns a usable price at 201. Assert engine called once with grid=201; `result_payload.grid_points_used == 201`; `attempted_grids` absent.
- **Case B — escalate once**: stub returns infinity at 201, usable at 501. Assert two calls (201 then 501); `grid_points_used == 501`; `attempted_grids == [201, 501, 1001]`.
- **Case C — escalate to top**: stub returns infinity at 201, infinity at 501, usable at 1001. Assert three calls; `grid_points_used == 1001`.
- **Case D — all fail**: stub returns infinity at all grids. Assert returned dict has `ok=False`, `error_type="pricing"`, and `result_payload.attempted_grids == [201, 501, 1001]`.
- **Case E — explicit grid honored**: position has `engine_kwargs.params_kwargs.grid_points = 501`. Stub returns infinity at 501. Assert single call at 501, NO escalation to 1001, failed result.
- **Case F — greeks reuse winning grid**: stub returns usable at 501 (not 201). Monkeypatch `compute_position_greeks` to record `engine_kwargs`. Assert it was called with `engine_kwargs.params_kwargs.grid_points == 501`.

### Layer 3: Parallel + progress integration (extend `tests/test_position_import_pricing.py`)

- Build a small portfolio (4 snowball positions) using existing fixtures. Run `price_portfolio_positions(..., progress_callback=cb)` with `risk_parallel_workers=4` (override via `configure_settings` for the test).
- Assert all 4 result rows present in `position_valuation_results`; `run.summary["priced"] == 4`.
- Assert `cb` was invoked with `(0, 4), …, (4, 4)` exactly N+1 times.
- Inject one position that raises in pricing; assert the failed result is recorded with the exception message as `error`, other 3 price normally, callback still reaches `(4, 4)`.

### Layer 4: Migration (`tests/test_migrations.py` or new)

- Seed a position with `engine_kwargs={"params_type":"quad_params","params_kwargs":{"grid_points":1001}}`. Apply upgrade. Assert `engine_kwargs == {"params_type":"quad_params"}`.
- Seed a position with `engine_kwargs={"params_type":"quad_params","params_kwargs":{"grid_points":501}}`. Apply upgrade. Assert unchanged.
- Seed a position with `engine_kwargs={"params_type":"mc_params","params_kwargs":{...}}`. Apply upgrade. Assert unchanged.
- Apply downgrade after upgrade on the 1001 row. Assert it returns to the original shape.

## Expected impact

| Scenario | Before | After (estimated) |
|---|---|---|
| Task #4 — 130 snowballs, no greeks | 79 min | 30–90 s |
| Same portfolio with greeks ON | ~5–8 h | 3–8 min |
| UI progress during pricing | Stuck at `0/1` | Live `k/130` counter |

Per-position worst case (rare): 3 attempts (201 + 501 + 1001) ≈ sum of all three grid
times ≈ ≤ 2× single-grid 1001 cost. Bounded and rare.

## Open items

None — all design questions resolved during brainstorming. Spec is ready for the planning
phase via `writing-plans`.
