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
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

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


def _require_complete(run, payload: dict, *, kind: str, needs: str) -> dict:
    """Reject a non-completed or partial producer run BEFORE harvesting its
    payload (Codex code-review [high]). A deterministic *failure* shape (excluded
    positions, zeroed curves, empty results) must not be certified as truth. The
    run's own status/exclusions are the source of truth, not just payload shape."""
    from app.models import TaskStatus

    status = getattr(run, "status", None)
    if status != TaskStatus.COMPLETED.value:
        raise AssertionError(f"{kind} run not completed: status={status!r}")
    excluded = getattr(run, "excluded_positions", None)
    if excluded:
        raise AssertionError(
            f"{kind} excluded positions (live-fetch/partial run masked?): {excluded}")
    if not payload.get(needs):
        raise AssertionError(f"{kind} payload missing/empty {needs!r}: {payload!r}")
    return _canonical(payload)


def _require_priced(risk_metrics: dict) -> dict:
    """Risk has no excluded_positions column; a partial run surfaces as per-position
    greeks_ok/pricing_ok=False. Reject any un-priced position."""
    bad = [p.get("position_id") for p in risk_metrics.get("positions", [])
           if not (p.get("greeks_ok") and p.get("pricing_ok"))]
    if bad:
        raise AssertionError(f"risk positions failed pricing/greeks: {bad}")
    return risk_metrics


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
    from app.golden_workflows.fixtures import ARENA_MARKET_SOURCE
    from app.models import MarketDataProfile

    days = expected_trading_days(start, end)
    series = [{"date": d.strftime("%Y-%m-%d"), "spot": float(spot)} for d in days]
    for u in underlyings:
        session.add(MarketDataProfile(
            name=f"{u} arena backtest history",
            source=ARENA_MARKET_SOURCE,
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


def _drive_risk(session, portfolio_id, profile_id):
    from app.services.batch_pricing import (
        queue_batch_pricing, _execute_batch_pricing_task,
    )
    run, task = queue_batch_pricing(
        session, portfolio_id=portfolio_id,
        pricing_parameter_profile_id=profile_id)
    _execute_batch_pricing_task(session, task.id, run.id)
    session.refresh(run)
    return run, run.metrics or {}


def _drive_landscape(session, portfolio_id, profile_id):
    from app.services.greeks_landscape import (
        queue_greeks_landscape, _execute_greeks_landscape_task,
    )
    run, task = queue_greeks_landscape(
        session, portfolio_id=portfolio_id,
        pricing_parameter_profile_id=profile_id)
    _execute_greeks_landscape_task(session, task.id, run.id)
    session.refresh(run)
    return run, run.results or {}


def _drive_scenario(session, portfolio_id, profile_id):
    from app.services import scenario_test_runner
    run, task = scenario_test_runner.queue_scenario_test(
        session, portfolio_id=portfolio_id,
        scenario_request={"predefined": ["market_crash"]},
        config={},
        pricing_parameter_profile_id=profile_id)
    scenario_test_runner._execute(session, task.id, run.id)
    session.refresh(run)
    return run, run.results or {}


def _drive_backtest(session, portfolio_id, profile_id):
    from app.services import backtest_runner
    run, task = backtest_runner.queue_backtest(
        session, portfolio_id=portfolio_id,
        spec={"start": "2026-03-24", "end": "2026-06-24"},
        config={},
        pricing_parameter_profile_id=profile_id)
    backtest_runner._execute(session, task.id, run.id)
    session.refresh(run)
    return run, run.results or {}


# --- Per-workflow determinism registry -------------------------------------
#
# The harness generalizes beyond the flagship: each workflow registers a seed
# function + a set of producer drivers, each driver carrying its OWN completion
# validator (the flagship task-run producers use the TaskStatus.COMPLETED guard;
# an RFQ quote persists ``pending_approval`` and needs a status/price predicate
# instead). The flagship entry is a behaviour-preserving wrap of the original
# drive_producers — the ``_drive_*`` functions keep their 3-arg signatures so
# direct callers (e.g. the offline-guard test) are unaffected.


@dataclass(frozen=True)
class ProducerDriver:
    # fn(session, ids) -> (run, payload); validate(run, payload) -> canonical payload (or raises)
    fn: Callable[[Any, dict], tuple]
    validate: Callable[[Any, dict], dict]


@dataclass(frozen=True)
class WorkflowDeterminism:
    workflow_id: str
    seed_fn: Callable[[Any], dict]
    drivers: dict  # name -> ProducerDriver


def _flagship_ids(ids: dict) -> tuple:
    return ids["portfolios"]["control"], ids["pricing_profiles"]["prof"]


# (session, ids) adapters over the unchanged 3-arg _drive_* functions.
def _adapt_risk(session, ids):
    return _drive_risk(session, *_flagship_ids(ids))


def _adapt_landscape(session, ids):
    return _drive_landscape(session, *_flagship_ids(ids))


def _adapt_scenario(session, ids):
    return _drive_scenario(session, *_flagship_ids(ids))


def _adapt_backtest(session, ids):
    return _drive_backtest(session, *_flagship_ids(ids))


def _validate_task_run(run, payload, *, kind, needs, priced=False):
    """Flagship producer completion predicate (byte-identical to the old inline
    checks): optional per-position priced check, then status/exclusion/needs."""
    if priced:
        payload = _require_priced(payload)
    return _require_complete(run, payload, kind=kind, needs=needs)


_FLAGSHIP_DETERMINISM = WorkflowDeterminism(
    workflow_id=FLAGSHIP_ID,
    seed_fn=seed_flagship,
    drivers={
        "risk": ProducerDriver(_adapt_risk,
            partial(_validate_task_run, kind="risk", needs="positions", priced=True)),
        "landscape": ProducerDriver(_adapt_landscape,
            partial(_validate_task_run, kind="landscape", needs="portfolio")),
        "scenario": ProducerDriver(_adapt_scenario,
            partial(_validate_task_run, kind="scenario", needs="var_cvar")),
        "backtest": ProducerDriver(_adapt_backtest,
            partial(_validate_task_run, kind="backtest", needs="by_underlying")),
    },
)

DETERMINISM_REGISTRY: dict = {FLAGSHIP_ID: _FLAGSHIP_DETERMINISM}


# --- Trader RFQ→Booking determinism -----------------------------------------
#
# Grounds on the MSFT down-and-in barrier put QUOTE. The live step-2 tool is
# ``quote_rfq``; in PRICE mode it emits ``quote_payload.achieved_price`` (the
# option's model price = the premium). Solve mode is NOT used — its ``solved_value``
# defaults to a solved *strike*, an input here, not a groundable output. The market
# snapshot is pinned (fixed spot + the seeded Arena Trader Profile MSFT rate/div/vol),
# so the price is byte-deterministic offline.

TRADER_RFQ_ID = "trader-rfq-booking-day"
_TRADER_RFQ_SPOT = 100.0
_MSFT_RATE, _MSFT_DIV, _MSFT_VOL = 0.04, 0.01, 0.28


def _seed_trader_rfq(session) -> dict:
    ids = apply_seed(get_workflow_bundle(TRADER_RFQ_ID).fixtures, session)
    session.commit()
    return ids


def _drive_quote_rfq(session, ids, *, spot: float = _TRADER_RFQ_SPOT):
    """Replay the LIVE quote_rfq PRICE path on a deterministic MSFT down-in barrier
    put draft; return (rfq, {'achieved_price', 'engine'})."""
    from app.services import rfq as rfq_svc
    from app.schemas import RFQRequestDraft, RFQQuoteRequest

    draft = RFQRequestDraft.model_validate({
        "client_name": "ARENA Determinism",
        "product_type": "BarrierOption",
        "product_kwargs": {
            "strike": 100, "barrier": 80, "maturity": 1.0,
            "option_type": "PUT", "barrier_type": "DOWN_IN",
        },
        "market": {
            "spot": spot, "rate": _MSFT_RATE, "dividend_yield": _MSFT_DIV,
            "volatility": _MSFT_VOL, "currency": "USD", "underlying": "MSFT",
        },
        "engine_spec": {"engine_name": "BarrierAnalyticalEngine"},
        "quote_mode": "price",
    })
    rfq = rfq_svc.create_rfq_draft(session, draft, channel="arena", actor="arena")
    rfq = rfq_svc.quote_rfq(session, rfq.id, RFQQuoteRequest(quote_mode="price"))
    session.refresh(rfq)
    payload = rfq.quote_payload or {}
    return rfq, {"achieved_price": payload.get("achieved_price"),
                 "engine": (payload.get("engine_summary") or {}).get("engine_class")}


def _validate_quote(run, payload):
    """RFQ quote completion predicate: quote_rfq persists status pending_approval
    (NOT TaskStatus.COMPLETED), so the task-run validator would wrongly reject it.
    Trust the payload iff a numeric achieved_price is present."""
    price = payload.get("achieved_price")
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        raise AssertionError(f"quote produced no numeric achieved_price: {payload!r}")
    return payload


DETERMINISM_REGISTRY[TRADER_RFQ_ID] = WorkflowDeterminism(
    workflow_id=TRADER_RFQ_ID,
    seed_fn=_seed_trader_rfq,
    drivers={"quote": ProducerDriver(_drive_quote_rfq, _validate_quote)},
)


def seed_workflow(session, workflow_id: str) -> dict:
    return DETERMINISM_REGISTRY[workflow_id].seed_fn(session)


def drive_producers(session, ids: dict, *, workflow_id: str = FLAGSHIP_ID) -> dict[str, Any]:
    """Drive a workflow's producers synchronously and return canonical
    (volatile-stripped) payloads. Each driver's OWN validator gates its payload
    before it is trusted. The default ``workflow_id`` keeps every existing caller
    (harvester, flagship determinism tests) working unchanged."""
    wd = DETERMINISM_REGISTRY[workflow_id]
    out: dict[str, Any] = {}
    with _no_async_dispatch():
        for key, drv in wd.drivers.items():
            run, payload = drv.fn(session, ids)
            out[key] = drv.validate(run, payload)
    return out
