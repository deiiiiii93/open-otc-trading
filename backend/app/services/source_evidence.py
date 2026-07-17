"""Canonical valuation and market-evidence metadata for persisted source runs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from ..models import (
    AssumptionRow,
    MarketQuote,
    MarketSnapshot,
    PricingParameterProfile,
    PricingParameterRow,
)


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def datetime_iso(value: datetime | None) -> str | None:
    return utc_naive(value).isoformat() if value is not None else None


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def backtest_position_evidence(
    position: Any,
    resolved_engine: Any | None = None,
) -> dict[str, Any]:
    """Hash the mutable economic and resolved-engine inputs used by backtests."""
    engine = resolved_engine or position
    return {
        "position_id": int(position.id),
        "economic_input_hash": canonical_hash(
            {
                "product_id": getattr(position, "product_id", None),
                "product_type": position.product_type,
                "product_kwargs": dict(position.product_kwargs or {}),
                "quantity": _finite_float(position.quantity),
                "entry_price": _finite_float(position.entry_price),
                "currency": position.currency,
            }
        ),
        "resolved_engine_hash": canonical_hash(
            {
                "engine_name": engine.engine_name,
                "engine_kwargs": dict(engine.engine_kwargs or {}),
            }
        ),
    }


def valuation_metadata(
    session: Session,
    *,
    created_at: datetime,
    pricing_parameter_profile_id: int | None,
    explicit_valuation_as_of: datetime | None,
    market_snapshot_id: int | None,
    requested_effective_market_evidence_id: str | None,
) -> dict[str, Any]:
    created = utc_naive(created_at)
    profile_valuation: datetime | None = None
    if pricing_parameter_profile_id is not None:
        profile = session.get(
            PricingParameterProfile,
            pricing_parameter_profile_id,
        )
        if profile is None:
            raise ValueError(
                f"Pricing parameter profile not found: "
                f"{pricing_parameter_profile_id}"
            )
        profile_valuation = utc_naive(profile.valuation_date)
    if market_snapshot_id is not None:
        if session.get(MarketSnapshot, market_snapshot_id) is None:
            raise ValueError(f"Market snapshot not found: {market_snapshot_id}")
    explicit = (
        utc_naive(explicit_valuation_as_of)
        if explicit_valuation_as_of is not None
        else None
    )
    if profile_valuation is not None:
        if explicit is not None and explicit != profile_valuation:
            raise ValueError(
                "valuation_as_of must equal the selected profile valuation_date"
            )
        effective = profile_valuation
        origin = "profile"
    elif explicit is not None:
        effective = explicit
        origin = "explicit"
    else:
        effective = created
        origin = "created_at"
    return {
        "valuation_as_of": effective.isoformat(),
        "effective_valuation_as_of": effective.isoformat(),
        "valuation_origin": origin,
        "profile_valuation_as_of": datetime_iso(profile_valuation),
        "pricing_parameter_profile_id": pricing_parameter_profile_id,
        "market_snapshot_id": market_snapshot_id,
        "requested_effective_market_evidence_id": (
            str(requested_effective_market_evidence_id).strip()
            if requested_effective_market_evidence_id is not None
            else None
        ),
    }


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("market evidence values must be finite numbers")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("market evidence values must be finite numbers") from exc
    if not math.isfinite(result):
        raise ValueError("market evidence values must be finite numbers")
    return result


def _row_payload(session: Session, diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    pricing_row_id = diagnostics.get("pricing_parameter_row_id")
    if pricing_row_id is not None:
        row = session.get(PricingParameterRow, int(pricing_row_id))
        if row is None:
            raise ValueError(f"Pricing parameter row not found: {pricing_row_id}")
        payload["pricing_parameter_row"] = {
            "id": row.id,
            "profile_id": row.profile_id,
            "source_trade_id": row.source_trade_id,
            "symbol": row.symbol,
            "rate": _finite_float(row.rate),
            "dividend_yield": _finite_float(row.dividend_yield),
            "volatility": _finite_float(row.volatility),
            "updated_at": datetime_iso(row.updated_at),
        }
    quote_id = diagnostics.get("market_quote_id")
    if quote_id is not None:
        quote = session.get(MarketQuote, int(quote_id))
        if quote is None:
            raise ValueError(f"Market quote not found: {quote_id}")
        payload["market_quote"] = {
            "id": quote.id,
            "instrument_id": quote.instrument_id,
            "as_of": datetime_iso(quote.as_of),
            "price": _finite_float(quote.price),
            "price_type": quote.price_type,
            "source": quote.source,
        }
    assumption_row_id = diagnostics.get("assumption_row_id")
    if assumption_row_id is not None:
        row = session.get(AssumptionRow, int(assumption_row_id))
        if row is None:
            raise ValueError(f"Assumption row not found: {assumption_row_id}")
        payload["assumption_row"] = {
            "id": row.id,
            "set_id": row.set_id,
            "instrument_id": row.instrument_id,
            "symbol": row.symbol,
            "rate": _finite_float(row.rate),
            "dividend_yield": _finite_float(row.dividend_yield),
            "volatility": _finite_float(row.volatility),
        }
    return payload


def build_market_evidence_manifest(
    session: Session,
    *,
    positions: Iterable[Any],
    position_markets: Mapping[int, Any],
    pricing_diagnostics: Mapping[int, Mapping[str, Any]],
    valuation_as_of: datetime,
    market_snapshot_id: int | None,
) -> dict[str, Any]:
    snapshot_payload: dict[str, Any] | None = None
    if market_snapshot_id is not None:
        snapshot = session.get(MarketSnapshot, market_snapshot_id)
        if snapshot is None:
            raise ValueError(f"Market snapshot not found: {market_snapshot_id}")
        snapshot_payload = {
            "id": snapshot.id,
            "name": snapshot.name,
            "source": snapshot.source,
            "symbol": snapshot.symbol,
            "asset_class": snapshot.asset_class,
            "valuation_date": datetime_iso(snapshot.valuation_date),
            "data": dict(snapshot.data or {}),
            "source_metadata": dict(snapshot.source_metadata or {}),
        }
    rows: list[dict[str, Any]] = []
    missing_evidence: list[str] = []
    for position in sorted(positions, key=lambda value: int(value.id)):
        market = position_markets.get(position.id)
        diagnostics = dict(pricing_diagnostics.get(position.id) or {})
        resolved_market = None
        if market is not None:
            resolved_market = {
                "valuation_date": datetime_iso(market.valuation_date),
                "spot": _finite_float(market.spot),
                "rate": _finite_float(market.rate),
                "dividend_yield": _finite_float(market.dividend_yield),
                "volatility": _finite_float(market.volatility),
                "currency": market.currency,
                "asset_name": market.asset_name,
            }
        position_missing: list[str] = []
        if diagnostics.get("spot_input_source") == "synthetic_default":
            position_missing.append("missing:spot")
        if (
            diagnostics.get("pricing_params_required", True)
            and diagnostics.get("parameter_input_source") == "synthetic_default"
        ):
            position_missing.append("missing:parameters")
        missing_evidence.extend(
            f"position:{position.id}:{reason}" for reason in position_missing
        )
        rows.append(
            {
                "position_id": int(position.id),
                "underlying": str(position.underlying),
                "economic_input_hash": canonical_hash(
                    {
                        "product_id": getattr(position, "product_id", None),
                        "product_type": str(position.product_type),
                        "product_kwargs": dict(position.product_kwargs or {}),
                        "quantity": _finite_float(position.quantity),
                        "entry_price": _finite_float(position.entry_price),
                        "currency": getattr(position, "currency", None),
                        "status": getattr(position, "status", None),
                        "position_kind": getattr(position, "position_kind", None),
                        "source_trade_id": getattr(
                            position, "source_trade_id", None
                        ),
                    }
                ),
                "resolved_engine_hash": (
                    canonical_hash(diagnostics["resolved_engine"])
                    if diagnostics.get("resolved_engine") is not None
                    else None
                ),
                "market_snapshot_id": market_snapshot_id,
                "resolved_market": resolved_market,
                "missing_evidence": position_missing,
                **_row_payload(session, diagnostics),
            }
        )
    return {
        "valuation_as_of": datetime_iso(valuation_as_of),
        "market_snapshot": snapshot_payload,
        "positions": rows,
        "evidence_complete": not missing_evidence,
        "missing_evidence": sorted(missing_evidence),
    }


def finalize_market_metadata(
    metadata: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    namespace: str = "risk-market-evidence/v1",
) -> dict[str, Any]:
    result = dict(metadata)
    copied_manifest = json.loads(canonical_json(manifest))
    evidence_hash = canonical_hash(copied_manifest)
    evidence_id = f"{namespace}:{evidence_hash.removeprefix('sha256:')}"
    requested = result.get("requested_effective_market_evidence_id")
    if requested not in {None, "", evidence_id}:
        raise ValueError(
            "effective_market_evidence_id does not match resolved market evidence"
        )
    result["market_evidence_manifest"] = copied_manifest
    result["market_evidence_hash"] = evidence_hash
    result["effective_market_evidence_id"] = evidence_id
    result["market_evidence_complete"] = bool(
        copied_manifest.get("evidence_complete", True)
    )
    result["missing_market_evidence"] = list(
        copied_manifest.get("missing_evidence") or []
    )
    return result
