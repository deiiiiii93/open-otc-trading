"""Market data domain service.

Pure-Python facade over the existing ``app.services.market_data`` AKShare
adapter and the ``MarketDataProfile`` ORM model. Returns Pydantic snapshot
models and ORM rows; never JSON. Session-aware so tool wrappers and CLI
commands can share transactions when needed.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy.orm import Session

from app import database
from app.models import MarketDataProfile
from app.schemas import AkshareSnapshotRequest, MarketDataSnapshot
from app.services.market_data import fetch_akshare_snapshot as _fetch_akshare_snapshot


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """Yield a session; market_data is read-only so no commit is needed."""
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _coerce_date_string(value: date | datetime | str) -> str:
    """Normalize a date-like input into the YYYY-MM-DD string the request expects."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fetch_snapshot(
    *,
    symbol: str,
    asset_class: str = "index",
    start_date: date | datetime | str,
    end_date: date | datetime | str,
    use_proxy: bool = False,
    name: str | None = None,
    adjust: str = "qfq",
    session: Session | None = None,
) -> MarketDataSnapshot:
    """Fetch a market snapshot from AKShare for the given window.

    Accepts ``date``/``datetime``/``str`` for the window bounds. Returns the
    ``MarketDataSnapshot`` Pydantic model unchanged from the underlying
    adapter so callers can inspect ``source_metadata.fallback`` etc. without
    parsing dicts. The ``session`` parameter is accepted for parity with the
    rest of the domain layer; the AKShare path does not currently touch the
    database.
    """
    request = AkshareSnapshotRequest(
        symbol=symbol,
        asset_class=asset_class,  # type: ignore[arg-type]
        start_date=_coerce_date_string(start_date),
        end_date=_coerce_date_string(end_date),
        name=name,
        adjust=adjust,
        use_proxy=use_proxy,
    )
    return _fetch_akshare_snapshot(request)


def list_profiles(*, session: Session | None = None) -> list[MarketDataProfile]:
    """Return all stored market data profiles, oldest first by id."""
    with _session_scope(session) as sess:
        return (
            sess.query(MarketDataProfile)
            .order_by(MarketDataProfile.id)
            .all()
        )


def get_profile(
    *, profile_id: int, session: Session | None = None
) -> MarketDataProfile | None:
    """Return one ``MarketDataProfile`` by id, or ``None`` if not found."""
    with _session_scope(session) as sess:
        return sess.get(MarketDataProfile, profile_id)


__all__ = [
    "fetch_snapshot",
    "list_profiles",
    "get_profile",
]
