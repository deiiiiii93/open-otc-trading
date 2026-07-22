from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models import MarketDataProfile, Position, Product, Underlying
from .currency_codes import normalize_currency
from .fx import parse_fx_pair_symbol
from .import_schema import is_knocked_out


MANUAL_INPUT_FIELDS = ("rate", "dividend_yield", "volatility")
VISIBLE_STATUSES = {"active", "draft"}


@dataclass(frozen=True)
class UnderlyingSyncResult:
    rows: list[Underlying]
    created: int
    existing: int


def normalize_underlying_symbol(symbol: str | None) -> str:
    return (symbol or "").strip()


def akshare_symbol(symbol: str) -> str:
    code = normalize_underlying_symbol(symbol)
    pair = parse_fx_pair_symbol(code)
    if pair:
        return f"{pair[0]}/{pair[1]}"
    if "." in code:
        return code.split(".", 1)[0]
    return code


def akshare_asset_class(symbol: str) -> str:
    if parse_fx_pair_symbol(symbol):
        return "fx_rate"
    code, _, suffix = normalize_underlying_symbol(symbol).partition(".")
    if suffix in {"DCE", "SHF", "CZC", "INE", "CFFEX", "GFEX"}:
        return "futures"
    if suffix == "SGE" or code.upper() in {"AU9999", "AU9995", "AU100G", "AG9999"}:
        return "sge_spot"
    if suffix == "CSI" or code in {
        "000016",
        "000300",
        "000852",
        "000905",
        "931059",
    }:
        return "index"
    if code.startswith(("15", "16", "18", "51", "56", "58")):
        return "etf"
    return "stock"


def infer_exchange(symbol: str) -> str | None:
    if parse_fx_pair_symbol(symbol):
        return "CFETS"
    code = normalize_underlying_symbol(symbol)
    if "." not in code:
        return None
    return code.rsplit(".", 1)[1] or None


def infer_market(symbol: str) -> str | None:
    if parse_fx_pair_symbol(symbol):
        return "FX"
    exchange = infer_exchange(symbol)
    if exchange in {"SH", "SZ", "CSI"}:
        return "CN"
    if exchange in {"DCE", "SHF", "CZC", "INE", "CFFEX", "GFEX", "SGE"}:
        return "CN"
    return exchange


_CSI_INDEX_CODES = {
    "000016",
    "000300",
    "000852",
    "000905",
    "931059",
}


def infer_currency(symbol: str) -> str:
    pair = parse_fx_pair_symbol(symbol)
    if pair:
        return pair[1]
    market = infer_market(symbol)
    if market == "CN":
        return "CNY"
    if market is not None:
        return "USD"
    code = normalize_underlying_symbol(symbol)
    # Bare A-share / CSI identifiers default to CNY; everything else defaults to USD
    # so US underlyings without an exchange suffix still get a reasonable currency.
    if code.isdigit() and len(code) == 6:
        return "CNY"
    if code.startswith("CSI") or code in _CSI_INDEX_CODES:
        return "CNY"
    return "USD"


def resolve_underlying_currency(symbol: str, explicit: str | None = None) -> str:
    """Return the explicitly supplied currency, or infer it from the underlying symbol."""
    if explicit:
        return normalize_currency(explicit)
    return infer_currency(symbol)


def ensure_underlying(
    session: Session,
    symbol: str,
    *,
    source: str = "manual",
    status: str = "draft",
    activate: bool = False,
) -> Underlying:
    cleaned = normalize_underlying_symbol(symbol)
    if not cleaned:
        raise ValueError("underlying symbol must not be empty")
    row = (
        session.query(Underlying)
        .filter(Underlying.symbol == cleaned)
        .one_or_none()
    )
    if row is None:
        row = Underlying(
            symbol=cleaned,
            display_name=cleaned,
            asset_class=akshare_asset_class(cleaned),
            market=infer_market(cleaned),
            exchange=infer_exchange(cleaned),
            currency=infer_currency(cleaned),
            akshare_symbol=akshare_symbol(cleaned),
            akshare_asset_class=akshare_asset_class(cleaned),
            status="active" if activate else status,
            source=source,
        )
        session.add(row)
        session.flush()
        return row
    changed = False
    if activate and row.status != "active":
        row.status = "active"
        changed = True
    for key, value in _derived_metadata(cleaned).items():
        if getattr(row, key) in {None, ""} and value not in {None, ""}:
            setattr(row, key, value)
            changed = True
    if changed:
        session.flush()
    return row


def _derived_metadata(symbol: str) -> dict[str, Any]:
    asset_class = akshare_asset_class(symbol)
    return {
        "display_name": symbol,
        "asset_class": asset_class,
        "market": infer_market(symbol),
        "exchange": infer_exchange(symbol),
        "currency": infer_currency(symbol),
        "akshare_symbol": akshare_symbol(symbol),
        "akshare_asset_class": asset_class,
    }


def list_underlyings(session: Session, *, include_inactive: bool = True) -> list[Underlying]:
    query = session.query(Underlying)
    if not include_inactive:
        query = query.filter(Underlying.status.in_(VISIBLE_STATUSES))
    return query.order_by(Underlying.symbol.asc()).all()


def update_underlying(session: Session, symbol: str, fields: dict[str, Any]) -> Underlying:
    row = ensure_underlying(session, symbol, source="manual")
    for key, value in fields.items():
        if key == "symbol":
            continue
        if not hasattr(row, key):
            continue
        setattr(row, key, value)
    if not row.akshare_symbol:
        row.akshare_symbol = akshare_symbol(row.symbol)
    if not row.akshare_asset_class:
        row.akshare_asset_class = akshare_asset_class(row.symbol)
    session.flush()
    return row


def is_registered_underlying(session: Session, symbol: str) -> bool:
    """True when the symbol resolves to an Instrument tagged "underlying" —
    the gate book_position/book_hedge check before booking."""
    cleaned = normalize_underlying_symbol(symbol)
    if not cleaned:
        return False
    row = session.query(Underlying).filter(Underlying.symbol == cleaned).one_or_none()
    if row is None:
        return False
    return "underlying" in (row.tags or [])


def link_position_underlying(
    session: Session,
    position: Position,
    *,
    source: str = "position",
) -> Underlying | None:
    symbol = normalize_underlying_symbol(position.underlying)
    if not symbol:
        return None
    row = ensure_underlying(session, symbol, source=source)
    position.underlying_id = row.id
    return row


def link_product_underlying(
    session: Session,
    product: Product,
    *,
    source: str = "product",
) -> Underlying | None:
    symbol = normalize_underlying_symbol(product.underlying)
    if not symbol:
        return None
    row = ensure_underlying(session, symbol, source=source)
    product.underlying_id = row.id
    return row


def link_market_data_profile_underlying(
    session: Session,
    profile: MarketDataProfile,
    *,
    source: str = "market_data",
) -> Underlying | None:
    symbol = normalize_underlying_symbol(profile.symbol)
    if not symbol:
        return None
    row = ensure_underlying(session, symbol, source=source)
    profile.underlying_id = row.id
    return row


def open_position_underlying_symbols(session: Session) -> list[str]:
    rows = (
        session.query(Position.underlying, Position.source_payload, Position.status)
        .filter(Position.underlying.isnot(None))
        .filter(Position.status == "open")
        .filter(Position.position_kind == "otc")
        .all()
    )
    underlyings: set[str] = set()
    for underlying, payload, status in rows:
        if status == "closed":
            continue
        state = payload.get("trade_state") if isinstance(payload, dict) else None
        if is_knocked_out(state or ""):
            continue
        cleaned = normalize_underlying_symbol(underlying)
        if cleaned:
            underlyings.add(cleaned)
    return sorted(underlyings)


def open_otc_positions(session: Session) -> list[Position]:
    """Open OTC positions in the same scope as open_position_underlying_symbols,
    but returning the Position rows (product eager-loaded for term extraction)."""
    from sqlalchemy.orm import selectinload

    rows = (
        session.query(Position)
        .options(selectinload(Position.product))
        .filter(Position.underlying.isnot(None))
        .filter(Position.status == "open")
        .filter(Position.position_kind == "otc")
        .all()
    )
    live: list[Position] = []
    for pos in rows:
        payload = pos.source_payload if isinstance(pos.source_payload, dict) else {}
        state = payload.get("trade_state")
        if is_knocked_out(state or ""):
            continue
        live.append(pos)
    return live


def sync_underlyings_from_positions(session: Session) -> UnderlyingSyncResult:
    symbols = open_position_underlying_symbols(session)
    existing = {
        row.symbol: row
        for row in session.query(Underlying).filter(Underlying.symbol.in_(symbols)).all()
    }
    rows: list[Underlying] = []
    created = 0
    for symbol in symbols:
        if symbol in existing:
            rows.append(existing[symbol])
            continue
        rows.append(ensure_underlying(session, symbol, source="position", status="draft"))
        created += 1
    session.flush()
    return UnderlyingSyncResult(rows=list_underlyings(session), created=created, existing=len(symbols) - created)


def active_market_data_underlyings(session: Session) -> list[Underlying]:
    """Deprecated name kept for callers; semantics are now 'resolvable'
    (drafts included). See instruments.resolvable_market_data_instruments."""
    from .instruments import resolvable_market_data_instruments

    rows = resolvable_market_data_instruments(session)
    if rows:
        return rows
    sync_underlyings_from_positions(session)
    return resolvable_market_data_instruments(session)


def latest_akshare_close_by_symbol(
    session: Session, symbols: list[str]
) -> dict[str, dict | None]:
    cleaned = [normalize_underlying_symbol(u) for u in symbols if normalize_underlying_symbol(u)]
    if not cleaned:
        return {}
    result: dict[str, dict | None] = {u: None for u in cleaned}
    rows = (
        session.query(MarketDataProfile)
        .filter(MarketDataProfile.symbol.in_(cleaned))
        .order_by(MarketDataProfile.symbol.asc(), desc(MarketDataProfile.valuation_date))
        .all()
    )
    seen: set[str] = set()
    for profile in rows:
        if profile.symbol in seen:
            continue
        seen.add(profile.symbol)
        data = profile.data or {}
        metadata = profile.source_metadata or {}
        spot = data.get("spot")
        if spot is None:
            result[profile.symbol] = None
            continue
        result[profile.symbol] = {
            "spot": float(spot),
            "fetched_at": profile.valuation_date,
            "fallback": bool(metadata.get("fallback")),
            "market_data_profile_id": profile.id,
        }
    return result
