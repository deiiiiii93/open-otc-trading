# backend/app/services/hedging_legs.py
from __future__ import annotations

from datetime import date as _date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import Instrument, Position
from ..schemas import PricingEnvironmentSnapshot
from .hedging_strategy_registry import tiers_for
from .hedging_universe import contract_multiplier, resolve_families
from .quotes import latest_quotes
from .risk_engine import compute_position_greeks

# Listed contracts whose option_type is set are options; everything else
# (futures/spot) is a delta-one leg.
_MAX_DATE = _date(9999, 12, 31)
_CALL_VALUES = {"C", "CALL", "购", "认购"}
_PUT_VALUES = {"P", "PUT", "沽", "认沽"}


def normalize_option_type(value: Any) -> str:
    raw = str(value or "CALL").strip().upper()
    if raw in _CALL_VALUES:
        return "CALL"
    if raw in _PUT_VALUES:
        return "PUT"
    return raw


def _is_option(inst: Instrument) -> bool:
    """An Instrument is an option leg iff it carries an option_type (kind
    'listed_option'); otherwise it is a delta-one (futures/spot) leg."""
    return inst.kind == "listed_option" or inst.option_type is not None


def _family_for(inst: Instrument) -> str:
    """Reconstruct the hedge ``family`` label for an Instrument's API/leg shape.

    Instrument has no family column. The family is recoverable from the active
    family specs (series_root → family). We resolve it from the loader's config
    keyed on series_root + kind; the legacy label is purely descriptive (the
    solver keys on greeks, not family).
    """
    if inst.kind == "stock":
        return "stock"
    if inst.option_type is not None:
        return "etf_option" if (inst.exchange or "") in {"SSE", "SZSE"} else (
            "index_option" if inst.exchange == "CFFEX" else "commodity_option"
        )
    return "index_future" if inst.exchange == "CFFEX" else "commodity_future"


def _effective_multiplier(inst: Instrument) -> float | None:
    """Catalog multiplier, falling back to the exchange-standard constant.

    Loaded rows can have ``multiplier=NULL`` (the AKShare feed/loader did not
    populate it); without this fallback ``price`` would use 1.0 and drop the
    contract multiplier entirely (e.g. IC = 200), grossly mis-sizing the hedge.
    """
    if inst.multiplier is not None:
        return inst.multiplier
    return contract_multiplier(_family_for(inst), inst.series_root)


def _active_instruments(session: Session, underlying_id: int) -> list[Instrument]:
    """Active catalog contracts in scope for a registry underlying.

    Old behaviour: HedgeInstrument rows with underlying_id==X AND status=='live',
    further filtered to those (exchange, contract_code) present as an *active* map
    entry for X. The new catalog has no underlying_id column. The exact
    equivalent: resolve the underlying's family specs via ``resolve_families``
    (the loader only ever created rows for those same specs) and select active
    Instrument rows whose series_root is one of those spec roots. We then keep
    only those carried by an active map entry for X — preserving the
    marked-universe gate the old code applied via HedgeMapEntry.

    For stocks, the hedge candidate is the underlying itself (a delta-one spot
    leg) and is allowed by default.
    """
    row = session.get(Instrument, underlying_id)
    if row is None:
        return []
    if row.kind == "stock":
        return [row] if row.status == "active" else []
    specs = resolve_families(row.symbol, row.kind)
    spec_roots = {s.series_root for s in specs}
    if not spec_roots:
        return []
    from ..models import HedgeMapEntry  # local import: avoids cycle at module load

    active_keys = {
        (e.exchange, e.contract_code)
        for e in session.query(HedgeMapEntry.exchange, HedgeMapEntry.contract_code)
        .filter(HedgeMapEntry.underlying_id == underlying_id,
                HedgeMapEntry.reconcile_status == "active")
    }
    if not active_keys:
        return []
    rows = (
        session.query(Instrument)
        .filter(Instrument.status == "active",
                Instrument.kind.in_(("futures", "listed_option")),
                Instrument.series_root.in_(spec_roots))
        .all()
    )
    return [r for r in rows if (r.exchange, r.contract_code) in active_keys]


def _role_needs(strategy: str) -> set[str]:
    return {g for tier in tiers_for(strategy) for g in tier["greeks"]}


def propose(session: Session, *, underlying_id: int, strategy: str,
            as_of: datetime | None = None) -> list[dict[str, Any]]:
    """Default leg set: a future/spot for delta, plus a near-ATM option when the
    strategy constrains gamma/vega. User can swap before solving.

    Near-ATM detection reads the latest market quote (the run's valuation date,
    ``as_of``; defaults to now) — last_price is no longer a catalog column.
    """
    insts = _active_instruments(session, underlying_id)
    needs = _role_needs(strategy)
    legs: list[dict[str, Any]] = []

    futures = [i for i in insts if not _is_option(i)]
    if futures:
        nearest = min(futures, key=lambda i: (i.expiry or _MAX_DATE))
        legs.append(_leg_dict(nearest, role="delta"))

    if needs & {"gamma", "vega"}:
        options = [i for i in insts if _is_option(i)]
        if options:
            quotes = latest_quotes(
                session, [o.id for o in options], as_of=as_of or datetime.utcnow()
            )
            def _ref_price(o: Instrument) -> float:
                q = quotes.get(o.id)
                return q.price if q is not None else 0.0
            atm = min(options, key=lambda i: abs((i.strike or 0.0) - _ref_price(i)))
            legs.append(_leg_dict(atm, role="gamma_vega"))
    return legs


def _leg_dict(inst: Instrument, *, role: str) -> dict[str, Any]:
    if _is_option(inst):
        itype = "option"
    elif inst.kind == "stock":
        itype = "spot"
    else:
        itype = "future"
    return {
        "instrument_id": inst.id, "role": role, "exchange": inst.exchange,
        "contract_code": inst.contract_code or inst.symbol, "instrument_type": itype,
        "option_type": inst.option_type, "strike": inst.strike,
        "expiry": inst.expiry.isoformat() if inst.expiry else None,
        "multiplier": _effective_multiplier(inst), "family": _family_for(inst),
    }


def price(session: Session, legs: list[dict[str, Any]], *, spot: float,
          option_market: PricingEnvironmentSnapshot | None = None,
          option_market_error: str | None = None) -> list[dict[str, Any]]:
    """Per-contract greek contributions: futures/spot closed form; options via BS.

    Option legs price under ``option_market`` (spot+r+q+vol from the run's pricing
    profile). When it is ``None`` the option leg refuses (``priced_ok=False`` with
    ``option_market_error``) rather than falling back to flat defaults. Cash
    conventions match the book rows: delta_cash = δ·S, gamma_cash = γ·S²/100, vega
    raw; each scaled by the contract multiplier.
    """
    out: list[dict[str, Any]] = []
    for leg in legs:
        inst = session.get(Instrument, leg["instrument_id"])
        mult = float(_effective_multiplier(inst) or 1.0)
        if not _is_option(inst):
            d, g, v = mult * spot, 0.0, 0.0
            ok, error = True, None
        elif option_market is None:
            d = g = v = 0.0
            ok = False
            error = option_market_error or "pricing parameters unavailable for option leg"
        else:
            s = float(option_market.spot)
            per = _option_unit_greeks(inst, option_market)
            ok, error = per["ok"], per.get("error")
            d = per["delta"] * mult * s
            g = per["gamma"] * mult * s * s / 100.0
            v = per["vega"] * mult
        out.append({**_leg_dict(inst, role=leg.get("role", "delta")),
                    "key": f"{inst.exchange or ''}:{inst.contract_code or inst.symbol}",
                    "delta": d, "gamma": g, "vega": v,
                    "priced_ok": ok, "price_error": error})
    return out


def _option_unit_greeks(
    inst: Instrument, market: PricingEnvironmentSnapshot
) -> dict[str, Any]:
    """Per-unit BS greeks for a listed option via a transient Position, priced under
    the supplied ``market`` (spot + rate + dividend_yield + volatility)."""
    # EuropeanVanillaOption takes 'maturity' (years to expiry), 'strike',
    # and uppercase 'option_type' (CALL/PUT). It does NOT accept 'expiry',
    # 'initial_price', or lowercase option_type.
    maturity = _years_to_expiry(inst.expiry)
    product_kwargs = {
        "strike": float(inst.strike or 0.0),
        "maturity": maturity,
        "option_type": normalize_option_type(inst.option_type),
    }
    pos = Position(
        underlying=inst.contract_code, product_type="EuropeanVanillaOption",
        product_kwargs=product_kwargs, engine_name="BlackScholesEngine",
        engine_kwargs={}, quantity=1.0, entry_price=0.0,
    )
    return compute_position_greeks(pos, market)


def _years_to_expiry(expiry: _date | None) -> float:
    """Calendar-day fraction of a year from today to expiry.  Floor at 1/365."""
    if expiry is None:
        return 1.0
    today = datetime.utcnow().date()
    days = max((expiry - today).days, 1)
    return days / 365.0
