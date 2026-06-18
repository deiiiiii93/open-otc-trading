from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import HedgeMapEntry, Instrument, TaskKind, Underlying


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def test_hedge_load_task_kind_value():
    assert TaskKind.HEDGE_LOAD.value == "hedge_instrument_load"


def test_instrument_unique_on_symbol(session):
    """The catalog identity is the canonical Instrument.symbol (unique). Two
    contracts colliding on symbol cannot coexist."""
    session.add(Instrument(
        symbol="IC2406.CFFEX", kind="futures", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", source="hedge_load",
    ))
    session.flush()
    session.add(Instrument(
        symbol="IC2406.CFFEX", kind="futures", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", source="hedge_load",
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_instrument_same_code_different_exchange_allowed(session):
    """The catalog symbol is f"{contract_code}.{exchange}", so the same code on
    two exchanges yields two distinct rows."""
    session.add(Instrument(
        symbol="IC2406.CFFEX", kind="futures", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", source="hedge_load",
    ))
    session.add(Instrument(
        symbol="IC2406.SSE", kind="futures", series_root="IC",
        exchange="SSE", contract_code="IC2406", source="hedge_load",
    ))
    session.flush()  # must NOT raise
    assert session.query(Instrument).filter(
        Instrument.contract_code == "IC2406").count() == 2


def test_hedge_map_entry_unique_on_underlying_contract(session):
    u = _underlying(session)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        expiry=date(2026, 6, 21),
    ))
    session.flush()
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_hedge_map_entry_same_code_different_underlying_allowed(session):
    """The map key includes underlying_id, so the same contract maps per-underlying."""
    a = _underlying(session, symbol="000905.SH")
    b = _underlying(session, symbol="000300.SH")
    session.add(HedgeMapEntry(
        underlying_id=a.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.add(HedgeMapEntry(
        underlying_id=b.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.flush()  # must NOT raise
    assert session.query(HedgeMapEntry).count() == 2
