"""Deterministic seed + producer drive for the flagship arena workflow.

Calls the producer SERVICES directly (no LLM) so the determinism gate and the
fixture harvester run offline. Each producer is driven via its private
``_execute_*`` seam on the caller's session; async dispatch (scenario/backtest)
is suppressed so the drive is fully synchronous and single-session.

Comparison surface is CANONICAL: volatile metadata (created_at, ids, task ids)
is stripped before equality, because freezing ``valuation_date`` does not freeze
a queued run's ``created_at`` (defaults to utcnow). The backtest result is
additionally checked STRICT — ``domains/backtest.py`` swallows per-underlying
market-data failures into an empty "completed" result, which would let the gate
certify a hollow backtest.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.golden_workflows.fixtures import apply_seed
from app.golden_workflows.registry import get_workflow_bundle

FLAGSHIP_ID = "risk-manager-control-day"
FLAGSHIP_UNDERLYINGS = ("AAPL", "TSLA", "NVDA")
BACKTEST_START = "2026-03-24"
BACKTEST_END = "2026-06-24"
FROZEN_SPOT = 100.0

# Volatile keys stripped before equality (Codex plan-review [high]): queued-run
# metadata (created_at/ids) and wall-clock timing fields that vary run-to-run even
# when every computed number is identical.
_VOLATILE_KEYS = {"created_at", "updated_at", "task_id", "run_id", "id",
                  "queued_at", "completed_at", "as_of", "timestamp",
                  "execution_time", "elapsed", "elapsed_ms", "duration",
                  "duration_ms", "runtime", "generated_at"}


def _canonical(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _canonical(v) for k, v in payload.items()
                if k not in _VOLATILE_KEYS}
    if isinstance(payload, list):
        return [_canonical(v) for v in payload]
    return payload


def _strict_backtest(results: dict) -> dict:
    """Reject a hollow backtest (Codex plan-review [high]). The backtest pipeline
    records ``excluded_positions`` and can return an empty result rather than
    raising when market-data prep fails under the offline gate."""
    excluded = results.get("excluded_positions")
    if excluded:
        raise AssertionError(
            f"backtest excluded positions (live-fetch masked?): {excluded}")
    if not results.get("by_underlying"):
        raise AssertionError(f"backtest produced no by_underlying series: {results!r}")
    return _canonical(results)


@contextmanager
def _no_async_dispatch():
    """Suppress submit_async_task in the runners that dispatch, so the private
    _execute we call ourselves is the ONLY execution (no ThreadPool race)."""
    from app.services import scenario_test_runner, backtest_runner
    noop = lambda *_a, **_k: None  # noqa: E731
    saved = (scenario_test_runner.submit_async_task,
             backtest_runner.submit_async_task)
    scenario_test_runner.submit_async_task = noop
    backtest_runner.submit_async_task = noop
    try:
        yield
    finally:
        (scenario_test_runner.submit_async_task,
         backtest_runner.submit_async_task) = saved


def seed_backtest_history(
    session, *, underlyings=FLAGSHIP_UNDERLYINGS,
    start=BACKTEST_START, end=BACKTEST_END, spot=FROZEN_SPOT,
) -> None:
    """Seed a flat ``MarketDataProfile`` per underlying covering EVERY expected SSE
    trading day in the window, so ``ensure_spot_history`` finds full coverage and
    never fetches akshare (offline + deterministic). Tagged source='arena_seed'
    for isolation/purge. Covering the SSE-expected days sidesteps the US-stock
    gap-detection refetch (issue #7): have_dates ⊇ expected ⇒ no gap ⇒ no fetch."""
    from app.services.backtest_market_history import expected_trading_days
    from app.services.underlyings import akshare_symbol, akshare_asset_class
    from app.models import MarketDataProfile

    days = expected_trading_days(start, end)
    series = [{"date": d.strftime("%Y-%m-%d"), "spot": float(spot)} for d in days]
    for u in underlyings:
        session.add(MarketDataProfile(
            name=f"{u} arena backtest history",
            source="arena_seed",
            symbol=akshare_symbol(u),
            asset_class=akshare_asset_class(u),
            start_date=series[0]["date"],
            end_date=series[-1]["date"],
            adjust="qfq",
            data={"series": series},
            source_metadata={"backtest_history": True, "arena_seed": True},
        ))
    session.flush()


def seed_flagship(session) -> dict[str, dict[str, int]]:
    """Seed the flagship fixtures + backtest history into ``session``; return
    alias→id maps."""
    ids = apply_seed(get_workflow_bundle(FLAGSHIP_ID).fixtures, session)
    seed_backtest_history(session)
    session.commit()
    return ids


def _drive_risk(session, portfolio_id, profile_id) -> dict:
    from app.services.batch_pricing import (
        queue_batch_pricing, _execute_batch_pricing_task,
    )
    run, task = queue_batch_pricing(
        session, portfolio_id=portfolio_id,
        pricing_parameter_profile_id=profile_id)
    _execute_batch_pricing_task(session, task.id, run.id)
    session.refresh(run)
    return run.metrics or {}


def _drive_landscape(session, portfolio_id, profile_id) -> dict:
    from app.services.greeks_landscape import (
        queue_greeks_landscape, _execute_greeks_landscape_task,
    )
    run, task = queue_greeks_landscape(
        session, portfolio_id=portfolio_id,
        pricing_parameter_profile_id=profile_id)
    _execute_greeks_landscape_task(session, task.id, run.id)
    session.refresh(run)
    return run.results or {}


def _drive_scenario(session, portfolio_id, profile_id) -> dict:
    from app.services import scenario_test_runner
    run, task = scenario_test_runner.queue_scenario_test(
        session, portfolio_id=portfolio_id,
        scenario_request={"predefined": ["market_crash"]},
        config={},
        pricing_parameter_profile_id=profile_id)
    scenario_test_runner._execute(session, task.id, run.id)
    session.refresh(run)
    return run.results or {}


def _drive_backtest(session, portfolio_id, profile_id) -> dict:
    from app.services import backtest_runner
    run, task = backtest_runner.queue_backtest(
        session, portfolio_id=portfolio_id,
        spec={"start": "2026-03-24", "end": "2026-06-24"},
        config={},
        pricing_parameter_profile_id=profile_id)
    backtest_runner._execute(session, task.id, run.id)
    session.refresh(run)
    return run.results or {}


def drive_producers(session, ids: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Drive all four flagship producers synchronously and return canonical
    (volatile-stripped) payloads keyed risk/landscape/scenario/backtest."""
    portfolio_id = ids["portfolios"]["control"]
    profile_id = ids["pricing_profiles"]["prof"]
    with _no_async_dispatch():
        return {
            "risk": _canonical(_drive_risk(session, portfolio_id, profile_id)),
            "landscape": _canonical(_drive_landscape(session, portfolio_id, profile_id)),
            "scenario": _canonical(_drive_scenario(session, portfolio_id, profile_id)),
            "backtest": _strict_backtest(_drive_backtest(session, portfolio_id, profile_id)),
        }
