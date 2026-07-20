# backend/app/services/domains/hedging_strategy.py
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy.orm import Session

from ...models import HedgeBand, Instrument, Position, RiskRun, SessionArtifact, Underlying
from ...schemas import PricingEnvironmentSnapshot
from .. import hedging_greeks, hedging_legs, hedging_solver
from ..hedging_strategy_registry import STRATEGIES, tiers_for

# Hard fallback if no defaults row exists yet.
_BUILTIN_DEFAULTS = {"delta": 500000.0, "gamma": 50000.0, "vega": 10000.0}


def resolve_bands(session: Session, *, underlying_id: int | None) -> dict[str, float]:
    row = (
        session.query(HedgeBand).filter(HedgeBand.underlying_id == underlying_id).one_or_none()
        or session.query(HedgeBand).filter(HedgeBand.underlying_id.is_(None)).one_or_none()
    )
    if row is None:
        return dict(_BUILTIN_DEFAULTS)
    return {"delta": row.delta_cash_band, "gamma": row.gamma_cash_band, "vega": row.vega_band}


def set_bands(
    session: Session, *, underlying_id: int | None,
    bands: dict[str, float], actor: str | None = None,
) -> HedgeBand:
    row = (
        session.query(HedgeBand)
        .filter(HedgeBand.underlying_id.is_(None) if underlying_id is None
                else HedgeBand.underlying_id == underlying_id)
        .one_or_none()
    )
    if row is None:
        row = HedgeBand(underlying_id=underlying_id, currency="CNY")
        session.add(row)
    row.delta_cash_band = float(bands["delta"])
    row.gamma_cash_band = float(bands["gamma"])
    row.vega_band = float(bands["vega"])
    row.updated_by = actor
    row.updated_at = datetime.utcnow()
    return row


def _underlying_id(session: Session, symbol: str) -> int | None:
    row = session.query(Underlying.id).filter(Underlying.symbol == symbol).one_or_none()
    return row[0] if row else None


def solve_hedge(
    session: Session, *, portfolio_id: int, underlying: str, strategy: str,
    legs: list[dict[str, Any]] | None = None, bands: dict[str, float] | None = None,
) -> dict[str, Any]:
    agg = hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)
    if agg["status"] != "ok":
        return {"status": agg["status"], "message": agg.get("message")}
    if agg.get("stale"):
        return {
            "status": "stale_risk_run",
            "risk_run_id": agg.get("risk_run_id"),
            "valuation_as_of": agg.get("valuation_as_of"),
            "risk_generated_at": agg.get("risk_generated_at"),
            "expires_at": agg.get("expires_at"),
            "stale_reasons": agg.get("stale_reasons") or [],
            "message": "Risk evidence is stale; refresh risk before sizing a hedge.",
        }
    target = next((u for u in agg["underlyings"] if u["underlying"] == underlying), None)
    if target is None:
        return {"status": "no_exposure",
                "message": f"No greek exposure to {underlying} in risk run {agg['risk_run_id']}."}
    spot = target["spot"]
    if spot is None:
        return {"status": "no_spot",
                "message": f"Risk run {agg['risk_run_id']} has no spot for {underlying}."}

    uid = _underlying_id(session, underlying)
    if legs is None:
        # Resolve near-ATM options against the run's valuation date (quotes are
        # as-of dated); fall back to now if the run carries no timestamp.
        as_of = None
        valuation_as_of = agg.get("valuation_as_of")
        if valuation_as_of:
            try:
                as_of = datetime.fromisoformat(str(valuation_as_of).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                as_of = None
        legs = hedging_legs.propose(
            session, underlying_id=uid, strategy=strategy, as_of=as_of
        )

    market = target.get("market")
    if target.get("params_ok") and market is not None:
        option_market = PricingEnvironmentSnapshot(
            spot=float(market["spot"]), rate=float(market["rate"]),
            dividend_yield=float(market["dividend_yield"]),
            volatility=float(market["volatility"]))
        option_market_error = None
    else:
        option_market = None
        missing = ", ".join(target.get("missing_params") or []) or \
            "rate/dividend_yield/volatility"
        option_market_error = (
            f"option leg not priced: pricing parameters unavailable for {underlying} "
            f"from the risk run's profile ({missing})")

    priced = hedging_legs.price(session, legs, spot=spot,
                                option_market=option_market,
                                option_market_error=option_market_error)
    usable = [p for p in priced if p["priced_ok"]]
    warnings = [{"contract_code": p["contract_code"], "error": p["price_error"]}
                for p in priced if not p["priced_ok"]]

    resolved_bands = bands or resolve_bands(session, underlying_id=uid)
    solver_legs = [hedging_solver.Leg(key=p["key"], delta=p["delta"],
                                      gamma=p["gamma"], vega=p["vega"]) for p in usable]
    result = hedging_solver.solve(
        targets=target["targets"], legs=solver_legs, bands=resolved_bands,
        tiers=tiers_for(strategy),
    )
    by_key = {p["key"]: p for p in usable}
    out_legs = [{**by_key[k], "quantity": q} for k, q in result.quantities.items()]
    diagnostics = _hard_band_diagnostics(
        bindings=result.binding, targets=target["targets"], bands=resolved_bands,
        residual=result.residual, legs=out_legs)
    proposed_at = datetime.utcnow().isoformat() + "Z"
    output = {
        "status": result.status, "portfolio_id": portfolio_id, "underlying": underlying,
        "strategy": strategy, "risk_run_id": agg["risk_run_id"],
        "pricing_parameter_profile_id": agg.get("pricing_parameter_profile_id"),
        "valuation_as_of": agg.get("valuation_as_of"),
        "risk_generated_at": agg.get("risk_generated_at"),
        "position_set_hash": agg.get("position_set_hash"),
        "proposed_at": proposed_at,
        "expires_at": agg.get("expires_at"),
        "spot": spot,
        "targets": target["targets"], "bands": resolved_bands,
        "legs": out_legs, "residual": result.residual, "in_band": result.in_band,
        "binding": result.binding, "warnings": warnings, "diagnostics": diagnostics,
    }
    output["proposal_hash"] = _canonical_hash(output)
    return output


def _canonical_hash(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _source_payload(
    session: Session,
    *,
    source_artifact_id: int,
    workflow_id: int | None,
) -> tuple[SessionArtifact | None, dict[str, Any] | None, list[str]]:
    from ..deep_agent.artifact_access import raw_artifact_content

    artifact = session.get(SessionArtifact, source_artifact_id)
    reasons: list[str] = []
    if artifact is None:
        return None, None, ["source_artifact_not_found"]
    if workflow_id is not None and artifact.workflow_id != workflow_id:
        reasons.append("source_artifact_workflow_mismatch")
    if artifact.kind != "tool_result" or artifact.tool_name not in {
        "get_hedgeable_underlyings",
        "propose_hedge",
    }:
        reasons.append("source_artifact_not_hedge_evidence")
    try:
        payload = json.loads(raw_artifact_content(artifact))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
        reasons.append("source_artifact_unreadable")
    if not isinstance(payload, dict):
        payload = None
    return artifact, payload, reasons


def _validate_hedge_evidence(
    session: Session,
    *,
    portfolio_id: int,
    underlying: str,
    risk_run_id: int,
    strategy: str,
    legs: list[dict[str, Any]],
    spot: float,
    source_artifact_id: int | None,
    workflow_id: int | None,
    artifact_generated_at: str | None,
    valuation_as_of: str | None,
    risk_generated_at: str | None,
    expires_at: str | None,
) -> list[str]:
    reasons: list[str] = []
    run = session.get(RiskRun, risk_run_id)
    if run is None:
        return ["risk_run_not_found"]
    if run.portfolio_id != portfolio_id:
        reasons.append("risk_run_portfolio_mismatch")
    if run.status not in {"completed", "completed_with_errors"}:
        reasons.append("risk_run_not_usable")
    latest = hedging_greeks.aggregate_by_underlying(
        session, portfolio_id=portfolio_id
    )
    if latest.get("status") != "ok":
        reasons.append("current_risk_unavailable")
    elif int(latest.get("risk_run_id") or 0) != risk_run_id:
        reasons.append("risk_run_superseded")
    reasons.extend(
        reason
        for reason in (latest.get("stale_reasons") or [])
        if reason not in reasons
    )

    if source_artifact_id is None:
        return reasons
    artifact, source, artifact_reasons = _source_payload(
        session,
        source_artifact_id=source_artifact_id,
        workflow_id=workflow_id,
    )
    reasons.extend(reason for reason in artifact_reasons if reason not in reasons)
    if artifact is None or source is None:
        return reasons

    expected_timestamps = {
        "artifact_generated_at": (
            (artifact.payload or {}).get("generated_at"), artifact_generated_at
        ),
        "valuation_as_of": (source.get("valuation_as_of"), valuation_as_of),
        "risk_generated_at": (source.get("risk_generated_at"), risk_generated_at),
        "expires_at": (source.get("expires_at"), expires_at),
    }
    if any(
        supplied is not None and authoritative != supplied
        for authoritative, supplied in expected_timestamps.values()
    ):
        reasons.append("approved_timestamp_mismatch")

    expiry = _parse_time(source.get("expires_at"))
    if expiry is None:
        expiry = _parse_time((artifact.payload or {}).get("generated_at"))
        if expiry is not None:
            from ...config import get_settings

            expiry = expiry + timedelta(
                seconds=int(get_settings().hedge_risk_max_age_seconds)
            )
    if expiry is None or expiry < datetime.utcnow():
        reasons.append("source_artifact_expired")

    source_values: dict[str, Any] = source
    if artifact.tool_name == "get_hedgeable_underlyings":
        source_underlyings = source.get("underlyings")
        source_target = next(
            (
                item
                for item in source_underlyings
                if isinstance(item, dict) and item.get("underlying") == underlying
            ),
            None,
        ) if isinstance(source_underlyings, list) else None
        source_values = {
            **source,
            "underlying": source_target.get("underlying") if source_target else None,
            "spot": source_target.get("spot") if source_target else None,
        }

    expected = {
        "portfolio_id": portfolio_id,
        "underlying": underlying,
        "risk_run_id": risk_run_id,
        "spot": float(spot),
    }
    for key, value in expected.items():
        source_value = source_values.get(key)
        if key == "spot" and source_value is not None:
            try:
                matches = abs(float(source_value) - value) <= 1e-12
            except (TypeError, ValueError):
                matches = False
        else:
            matches = source_value == value
        if not matches:
            reasons.append("approved_payload_mismatch")
            break

    current_fingerprint = hedging_greeks.position_set_hash(
        session, portfolio_id=portfolio_id
    )
    if source.get("position_set_hash") != current_fingerprint:
        reasons.append("portfolio_snapshot_changed")
    if artifact.tool_name == "propose_hedge":
        if source.get("strategy") != strategy or _canonical_hash(
            source.get("legs") or []
        ) != _canonical_hash(legs):
            reasons.append("approved_payload_mismatch")
    return list(dict.fromkeys(reasons))


def _hard_band_diagnostics(
    *,
    bindings: list[dict[str, Any]],
    targets: dict[str, float],
    bands: dict[str, float],
    residual: dict[str, float],
    legs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for binding in bindings:
        greek = binding.get("greek")
        if greek not in {"delta", "gamma", "vega"}:
            continue
        residual_value = float(residual.get(greek, 0.0))
        terms = []
        for leg in legs:
            quantity = int(leg.get("quantity") or 0)
            per_lot = float(leg.get(greek, 0.0) or 0.0)
            terms.append({
                "contract_code": leg.get("contract_code"),
                "quantity": quantity,
                "per_lot": per_lot,
                "contribution": quantity * per_lot,
            })
        out.append({
            "kind": "hard_band_residual",
            "greek": greek,
            "target": float(targets.get(greek, 0.0) or 0.0),
            "band": float(bands.get(greek, 0.0) or 0.0),
            "residual": residual_value,
            "shortfall": float(binding.get("shortfall", 0.0) or 0.0),
            "suggested_band": float(ceil(abs(residual_value))),
            "terms": terms,
        })
    return out


# ---------------------------------------------------------------------------
# Atomic tagged hedge booking
# ---------------------------------------------------------------------------

from .booking import BookingRequest, ProductBookingSpec, book_position as _book_position  # noqa: E402
from .product_builders import build_product as _build_product  # noqa: E402

# One day expressed in years; floor for a parsed expiry so an already-expired or
# same-day contract still books with a strictly positive maturity.
_MIN_MATURITY_YEARS = 1.0 / 365.0
# Fallback maturity when a leg carries no parseable expiry (near-month ≈ 1 qtr).
_DEFAULT_MATURITY_YEARS = 0.25

# QuantArk class + product family per hedge instrument type.
_QUANTARK_CLASS = {
    "future": "Futures",
    "spot": "SpotInstrument",
    "option": "EuropeanVanillaOption",
}
_PRODUCT_FAMILY = {
    "future": "futures",
    "spot": "spot",
    "option": "option",
}
_ENGINE = {
    "future": "DeltaOneEngine",
    "spot": "DeltaOneEngine",
    "option": "BlackScholesEngine",
}

# Sizing-provenance tag for desk-stated legs (no solver involved).
_MANUAL_STRATEGY = "manual"


def _maturity_years(leg: dict[str, Any], *, default: float) -> float:
    """Year-fraction to a leg's ``expiry`` (ISO date), floored at one day.

    ``(expiry - today).days / 365`` gives a calendar-day year-fraction; the
    one-day floor keeps an already-expired or same-day contract bookable with a
    strictly positive maturity (QuantArk rejects maturity <= 0). When the leg
    carries no parseable ``expiry`` the ``default`` is used so the position still
    persists without inventing an economics-sensitive value.
    """
    raw = leg.get("expiry")
    if not raw:
        return default
    try:
        days = (date.fromisoformat(str(raw)) - date.today()).days
    except (TypeError, ValueError):
        return default
    return max(_MIN_MATURITY_YEARS, days / 365.0)


def _leg_terms(leg: dict[str, Any], spot: float) -> dict[str, Any]:
    """Return bookable QuantArk ``terms`` for the leg.

    DeltaOne (future/spot) legs are SYNTHESIZED inside the booking gate
    (prebuilt=False), so they return *raw desk terms*: the builder requires
    ``initial_price`` (S0), threads ``underlying`` from the spec top-level, and
    carries ``contract_code``/``instrument_code``/``exchange`` as ``_otc_``
    persistence-only metadata (the bare keys are absent from the final QuantArk
    kwargs). ``maturity_years`` is derived from the leg ``expiry`` when present
    (else a near-month 0.25-yr fallback for futures).

    Option legs take the validate-and-wrap (prebuilt=True) path in the gate,
    which feeds ``terms`` straight to QuantArk's validator — so raw desk fields
    like ``expiry``/``initial_price`` would be rejected. We instead SYNTHESIZE a
    complete vanilla termsheet here via ``build_product`` and return its
    ``product_kwargs`` (``{contract_multiplier, maturity, strike, option_type}``
    — the multiplier is a NATIVE QuantArk kwarg, not ``_otc_``; ``initial_price``
    is consumed only as the validation spot and is correctly absent). The gate's
    prebuilt revalidation of these kwargs then passes.
    """
    itype = leg["instrument_type"]
    if itype == "future":
        terms: dict[str, Any] = {
            "initial_price": float(spot),
            "contract_multiplier": float(leg.get("multiplier") or 1.0),
            "maturity_years": _maturity_years(leg, default=_DEFAULT_MATURITY_YEARS),
        }
        # Pass contract_code in terms so the builder carries it as _otc_contract_code.
        if leg.get("contract_code"):
            terms["contract_code"] = leg["contract_code"]
        return terms
    if itype == "spot":
        terms = {"initial_price": float(spot)}
        if leg.get("instrument_code"):
            terms["instrument_code"] = leg["instrument_code"]
        if leg.get("exchange"):
            terms["exchange"] = leg["exchange"]
        if leg.get("family") == "stock":
            terms["deltaone_type"] = "STOCK"
        return terms
    # option: synthesize a complete EuropeanVanillaOption termsheet so the gate's
    # prebuilt validate-and-wrap accepts it (raw expiry/initial_price would not).
    built = _build_product(
        "EuropeanVanillaOption",
        {
            "initial_price": float(spot),
            "strike": float(leg.get("strike") or spot),
            "maturity_years": _maturity_years(leg, default=_DEFAULT_MATURITY_YEARS),
            "option_type": hedging_legs.normalize_option_type(leg.get("option_type")),
            "contract_multiplier": float(leg.get("multiplier") or 1.0),
        },
    )
    if not built.ok:
        detail = ", ".join(built.missing) or (built.validation or {}).get("error") or "?"
        raise ValueError(f"Cannot synthesize option hedge leg {leg.get('contract_code')!r}: {detail}")
    return built.product_kwargs


def _instrument_family_for(inst: Instrument) -> str:
    if inst.kind == "listed_option" or inst.option_type is not None:
        if (inst.exchange or "") in {"SSE", "SZSE"}:
            return "etf_option"
        return "index_option" if inst.exchange == "CFFEX" else "commodity_option"
    return "index_future" if inst.exchange == "CFFEX" else "commodity_future"


def _canonical_book_leg(session: Session, leg: dict[str, Any]) -> dict[str, Any]:
    """Return a bookable hedge leg from the instrument master, not request claims."""
    itype = str(leg.get("instrument_type") or "")
    if itype == "spot":
        return dict(leg)

    instrument_id = leg.get("instrument_id")
    if instrument_id is None:
        raise ValueError("hedge legs for listed contracts must include instrument_id")
    inst = session.get(Instrument, int(instrument_id))
    if inst is None:
        raise ValueError(f"hedge instrument {instrument_id} not found")
    if inst.kind not in {"futures", "listed_option"}:
        raise ValueError(f"instrument {instrument_id} is not a listed hedge contract")

    is_option = inst.kind == "listed_option" or inst.option_type is not None
    multiplier = inst.multiplier
    if multiplier is None:
        multiplier = hedging_legs.contract_multiplier(_instrument_family_for(inst), inst.series_root)
    return {
        **leg,
        "instrument_id": inst.id,
        "symbol": inst.symbol,
        "exchange": inst.exchange,
        "contract_code": inst.contract_code,
        "instrument_type": "option" if is_option else "future",
        "option_type": inst.option_type,
        "strike": inst.strike,
        "expiry": inst.expiry.isoformat() if inst.expiry else None,
        "multiplier": multiplier,
        "family": _instrument_family_for(inst),
    }


def book_hedge(
    session: Session,
    *,
    portfolio_id: int,
    underlying: str,
    risk_run_id: int,
    strategy: str,
    legs: list[dict[str, Any]],
    spot: float,
    source_artifact_id: int | None = None,
    workflow_id: int | None = None,
    artifact_generated_at: str | None = None,
    valuation_as_of: str | None = None,
    risk_generated_at: str | None = None,
    expires_at: str | None = None,
    actor: str = "desk_user",
) -> dict[str, Any]:
    """Atomically book each non-zero leg into the portfolio, tagged as a hedge.

    All legs are written inside the caller's session unit-of-work; a raised
    exception rolls back every leg booked so far (the endpoint/tool layer
    commits exactly once after this returns).
    """
    allowed = set(STRATEGIES) | {_MANUAL_STRATEGY}
    if strategy not in allowed:
        raise ValueError(
            f"Unknown hedge strategy {strategy!r}; expected one of {sorted(allowed)}."
        )
    reasons = _validate_hedge_evidence(
        session,
        portfolio_id=portfolio_id,
        underlying=underlying,
        risk_run_id=risk_run_id,
        strategy=strategy,
        legs=legs,
        spot=spot,
        source_artifact_id=source_artifact_id,
        workflow_id=workflow_id,
        artifact_generated_at=artifact_generated_at,
        valuation_as_of=valuation_as_of,
        risk_generated_at=risk_generated_at,
        expires_at=expires_at,
    )
    if reasons:
        return {
            "ok": False,
            "status": "stale_hedge_proposal",
            "portfolio_id": portfolio_id,
            "underlying": underlying,
            "risk_run_id": risk_run_id,
            "source_artifact_id": source_artifact_id,
            "reasons": reasons,
            "message": "Hedge evidence is stale or no longer matches the approved payload.",
        }
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
    for raw_leg in legs:
        qty = int(raw_leg.get("quantity") or 0)
        if qty == 0:
            continue
        leg = _canonical_book_leg(session, raw_leg)
        n += 1
        itype = leg["instrument_type"]
        role = "gamma_vega" if itype == "option" else "delta"
        booked_underlying = leg["symbol"] if itype == "future" else underlying
        spec = ProductBookingSpec(
            asset_class="equity",
            product_family=_PRODUCT_FAMILY[itype],
            quantark_class=_QUANTARK_CLASS[itype],
            underlying=booked_underlying,
            currency="CNY",
            terms=_leg_terms(leg, spot),
        )
        request = BookingRequest(
            portfolio_id=portfolio_id,
            product=spec,
            quantity=float(qty),
            entry_price=0.0,
            status="open",
            position_kind="listed",
            engine_name=_ENGINE[itype],
            source_trade_id=f"HEDGE:{risk_run_id}:{n}",
            source="manual",
            actor=actor,
            source_payload={
                "hedge": {
                    "is_hedge": True,
                    "risk_run_id": risk_run_id,
                    "source_artifact_id": source_artifact_id,
                    "artifact_generated_at": artifact_generated_at,
                    "valuation_as_of": valuation_as_of,
                    "risk_generated_at": risk_generated_at,
                    "expires_at": expires_at,
                    "strategy": strategy,
                    "leg_role": role,
                    "hedged_underlying": underlying,
                    "instrument_id": leg.get("instrument_id"),
                    "exchange": leg.get("exchange"),
                    "contract_code": leg.get("contract_code"),
                    "multiplier": leg.get("multiplier"),
                    "solved_at": datetime.utcnow().isoformat(),
                }
            },
        )
        position = _book_position(session, request)
        position_ids.append(position.id)
    return {
        "status": "booked",
        "portfolio_id": portfolio_id,
        "underlying": underlying,
        "risk_run_id": risk_run_id,
        "source_artifact_id": source_artifact_id,
        "artifact_generated_at": artifact_generated_at,
        "valuation_as_of": valuation_as_of,
        "risk_generated_at": risk_generated_at,
        "expires_at": expires_at,
        "position_ids": position_ids,
    }
