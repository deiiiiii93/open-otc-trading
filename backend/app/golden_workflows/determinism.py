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
#
# The second group is DERIVED volatility: provenance fingerprints computed over a
# raw payload that legitimately contains the wall-clock fields above, so they can
# never be stable even when every computed number is. Two clean-DB drives differ
# ONLY in `pricing_parameter_row.updated_at` (seed instants ~0.4s apart) and in
# these three hashes taken over it — prices, Greeks and resolved markets are
# byte-identical. Stripping `updated_at` but not a hash OF `updated_at` was simply
# an inconsistency in the original strip set.
#
# Deliberately NOT fixed by making the hashes deterministic: `position_set_hash`
# embeds `updated_at` so a position mutated IN PLACE (same id, same quantity) still
# changes the hash, which is how a stale risk run is detected. Dropping that input
# would trade a production safety invariant for a green test.
#
# This costs no coverage: the substance behind both hashes —
# `source_metadata.market_evidence_manifest` — is still compared field by field
# (minus its own volatile keys), so a REAL change in resolved market evidence still
# fails the gate. `_require_evidence_manifest` keeps that guarantee honest by
# refusing a payload where the manifest is missing.
_VOLATILE_KEYS = {"created_at", "updated_at", "task_id", "run_id", "id",
                  "queued_at", "completed_at", "as_of", "timestamp",
                  "execution_time", "elapsed", "elapsed_ms", "duration",
                  "duration_ms", "runtime", "generated_at",
                  # derived-over-volatile provenance fingerprints
                  "position_set_hash", "market_evidence_hash",
                  "effective_market_evidence_id"}


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


def _require_evidence_manifest(payload: dict, *, kind: str) -> None:
    """Fail if the market-evidence manifest is absent from a payload.

    ``market_evidence_hash`` / ``effective_market_evidence_id`` are stripped as
    derived-over-volatile (see ``_VOLATILE_KEYS``), which is only safe while the
    manifest they fingerprint is itself compared. If a refactor ever drops the
    manifest from the payload, stripping the hash would silently leave market
    evidence UNGUARDED — the gate would pass on any market change at all. This
    keeps that failure loud instead.
    """
    manifest = (payload.get("source_metadata") or {}).get("market_evidence_manifest")
    if not manifest or not manifest.get("positions"):
        raise AssertionError(
            f"{kind}: market_evidence_manifest missing/empty — the evidence hashes "
            "are stripped as volatile, so the manifest is the ONLY thing still "
            "guarding market evidence. Restore it before trusting this gate."
        )


def _require_priced(risk_metrics: dict) -> dict:
    """Risk has no excluded_positions column; a partial run surfaces as per-position
    greeks_ok/pricing_ok=False. Reject any un-priced position."""
    bad = [p.get("position_id") for p in risk_metrics.get("positions", [])
           if not (p.get("greeks_ok") and p.get("pricing_ok"))]
    if bad:
        raise AssertionError(f"risk positions failed pricing/greeks: {bad}")
    _require_evidence_manifest(risk_metrics, kind="risk")
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
    achieved = payload.get("achieved_price")
    # Spot- AND multiplier-invariant grounding ratios (the harvest pins spot=100,
    # multiplier=1; these EQUAL the live ratios at any real spot/multiplier, which is
    # exactly why the manifest grounds on ratios rather than absolute prices).
    strike, barrier, mult = 100.0, 80.0, 1.0
    premium_spot_ratio = (achieved / (spot * mult)
                          if isinstance(achieved, (int, float)) and spot else None)
    return rfq, {
        "achieved_price": achieved,
        "engine": (payload.get("engine_summary") or {}).get("engine_class"),
        "premium_spot_ratio": premium_spot_ratio,
        "barrier_strike_ratio": barrier / strike,
        "strike_spot_ratio": strike / spot,
    }


def _validate_quote(run, payload):
    """RFQ quote completion predicate. quote_rfq persists status pending_approval on
    success (NOT TaskStatus.COMPLETED, so the task-run validator can't be reused) and
    pricing_failed on failure. A pricing failure emits achieved_price=0.0 via
    _quote_price, so a bare non-null check would certify a FAILED quote as truth —
    fail-honest requires the success status + a finite, strictly-positive price + the
    expected engine (Codex code-review [high])."""
    import math
    from app.models import RfqStatus

    status = getattr(run, "status", None)
    if status != RfqStatus.PENDING_APPROVAL.value:
        raise AssertionError(f"quote not priced (status={status!r}); refusing to certify")
    price = payload.get("achieved_price")
    if (not isinstance(price, (int, float)) or isinstance(price, bool)
            or not math.isfinite(price) or price <= 0):
        raise AssertionError(f"quote produced no positive finite achieved_price: {payload!r}")
    engine = payload.get("engine")
    if engine != "BarrierAnalyticalEngine":
        raise AssertionError(f"unexpected quote engine {engine!r}")
    return payload


DETERMINISM_REGISTRY[TRADER_RFQ_ID] = WorkflowDeterminism(
    workflow_id=TRADER_RFQ_ID,
    seed_fn=_seed_trader_rfq,
    drivers={"quote": ProducerDriver(_drive_quote_rfq, _validate_quote)},
)


# --- High-Board Portfolio Review determinism -------------------------------
# high_board is a consume-only oversight persona: it READS a persisted governed
# risk run (get_latest_risk_run), it does NOT dispatch run_batch_pricing (that
# authority is trader/risk_manager only). The determinism driver here computes a
# FRESH risk run over the seeded desk positions purely to HARVEST the truth
# numbers + the seeded RiskRun.metrics blob; the live match reads the seeded run.
HIGH_BOARD_ID = "high-board-portfolio-review-day"


def _seed_high_board(session) -> dict:
    ids = apply_seed(get_workflow_bundle(HIGH_BOARD_ID).fixtures, session)
    session.commit()
    return ids


def _high_board_ids(ids: dict) -> tuple:
    return ids["portfolios"]["desk"], ids["pricing_profiles"]["prof"]


def _adapt_high_board_risk(session, ids):
    return _drive_risk(session, *_high_board_ids(ids))


DETERMINISM_REGISTRY[HIGH_BOARD_ID] = WorkflowDeterminism(
    workflow_id=HIGH_BOARD_ID,
    seed_fn=_seed_high_board,
    drivers={"risk": ProducerDriver(
        _adapt_high_board_risk,
        partial(_validate_task_run, kind="risk", needs="positions", priced=True))},
)


# --- Risk-Limit-Breach determinism -----------------------------------------
# Three producers: breach_risk (book MINUS the hedge — the numbers authored
# into the seeded breach RiskRun/evaluations), fresh_risk (full post-hedge
# book — what the model's step-6 refresh computes), and monitoring (the exact
# refresh-then-reuse path the run_limit_monitoring tool runs live, via the
# shared derive_monitoring_envelope seam). The monitoring producer never calls
# dispatch_limit_monitoring (it needs a real Future); it executes the queued
# task inline.
LIMIT_BREACH_ID = "risk-limit-breach-day"


def _seed_limit_breach(session) -> dict:
    ids = apply_seed(get_workflow_bundle(LIMIT_BREACH_ID).fixtures, session)
    # Stabilize position economic identity BEFORE any risk run: live paths get
    # this from database.init_db()'s product backfill on every tool call, but
    # the harness drives services directly — without it the evidence manifest
    # hashes the pre-stamp identity and reuse can never match.
    from app.services.domains.products import backfill_position_products

    backfill_position_products(session)
    session.commit()
    return ids


def _limit_breach_ids(ids: dict) -> tuple:
    return ids["portfolios"]["desk"], ids["pricing_profiles"]["control"]


def _drive_risk_subset(session, portfolio_id, profile_id, position_ids):
    from app.services.batch_pricing import (
        queue_batch_pricing, _execute_batch_pricing_task,
    )
    run, task = queue_batch_pricing(
        session, portfolio_id=portfolio_id,
        position_ids=position_ids,
        pricing_parameter_profile_id=profile_id)
    _execute_batch_pricing_task(session, task.id, run.id)
    session.refresh(run)
    return run, run.metrics or {}


def _adapt_breach_risk(session, ids):
    pid, prof = _limit_breach_ids(ids)
    subset = [
        ids["positions"]["driver"],
        ids["positions"]["tsla_pos"],
        ids["positions"]["nvda_pos"],
    ]
    return _drive_risk_subset(session, pid, prof, subset)


def _adapt_fresh_risk(session, ids):
    return _drive_risk(session, *_limit_breach_ids(ids))


def _adapt_monitoring(session, ids):
    from app.services.limits.agent_support import derive_monitoring_envelope
    from app.services.limits.contracts import LimitActionContext
    from app.services.limits.monitoring import (
        execute_limit_monitoring_task,
        queue_limit_monitoring,
    )

    pid, _prof = _limit_breach_ids(ids)
    envelope = derive_monitoring_envelope(session, pid)
    run, task = queue_limit_monitoring(
        session,
        portfolio_id=pid,
        trigger="agent",
        context=LimitActionContext(
            actor="determinism", persona=None, mode="auto"
        ),
        source_policy="reuse_only",
        **envelope,
    )
    session.commit()
    execute_limit_monitoring_task(task.id, run.id)
    session.expire_all()
    from app.models import LimitEvaluation, LimitIncident, RiskLimit, RiskLimitVersion

    evaluations = (
        session.query(LimitEvaluation, RiskLimit.key)
        .join(
            RiskLimitVersion,
            RiskLimitVersion.id == LimitEvaluation.limit_version_id,
        )
        .join(RiskLimit, RiskLimit.id == RiskLimitVersion.risk_limit_id)
        .filter(LimitEvaluation.monitoring_run_id == run.id)
        .order_by(RiskLimit.key)
        .all()
    )
    session.refresh(run)
    # Recommendation-2 hardening (final-review.md): drive the seeded incident's
    # open -> recovered transition end-to-end, not just its evaluations reading
    # "ok" — step 7 of the workflow grades this transition and, before this,
    # no offline test exercised it (only the live-only step-7 reachability
    # analysis in the review covered it).
    incident = session.get(LimitIncident, ids["limit_incidents"]["incident"])
    payload = {
        "status": run.status,
        "summary": dict(run.summary or {}),
        "incident_status": incident.status if incident is not None else None,
        "evaluations": [
            {
                "limit_key": key,
                "status": ev.status,
                "observed_value": ev.observed_value,
                "utilization": ev.utilization,
                "headroom": ev.headroom,
                "reason_code": ev.reason_code,
            }
            for ev, key in evaluations
        ],
    }
    return run, payload


def _validate_monitoring(run, payload):
    if payload.get("status") not in ("completed",):
        raise AssertionError(
            f"monitoring producer did not complete cleanly: {payload}"
        )
    evaluations = payload.get("evaluations") or []
    if len(evaluations) != 3:
        raise AssertionError(
            f"expected exactly one evaluation per seeded limit, got {payload}"
        )
    bad = [e for e in evaluations if e.get("status") != "ok"]
    if bad:
        raise AssertionError(f"non-ok evaluations in clean re-monitor: {bad}")
    if payload.get("incident_status") != "recovered":
        raise AssertionError(
            f"seeded incident did not auto-recover on the clean re-monitor: {payload}"
        )
    return _canonical(payload)


# Ordering is load-bearing: "monitoring" derives its envelope from the LATEST
# completed risk run (derive_monitoring_envelope), so it must run AFTER
# "fresh_risk" seeds that run — otherwise it would reuse the seeded breach-side
# run instead and never observe a clean re-monitor. `drive_producers` iterates
# `wd.drivers.items()`, and dict insertion order is preserved/iterated in
# order since Python 3.7, so this dict's literal key order below IS the
# execution order — do not reorder these three entries without re-checking
# that dependency.
DETERMINISM_REGISTRY[LIMIT_BREACH_ID] = WorkflowDeterminism(
    workflow_id=LIMIT_BREACH_ID,
    seed_fn=_seed_limit_breach,
    drivers={
        "breach_risk": ProducerDriver(_adapt_breach_risk,
            partial(_validate_task_run, kind="risk", needs="positions", priced=True)),
        "fresh_risk": ProducerDriver(_adapt_fresh_risk,
            partial(_validate_task_run, kind="risk", needs="positions", priced=True)),
        "monitoring": ProducerDriver(_adapt_monitoring, _validate_monitoring),
    },
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
