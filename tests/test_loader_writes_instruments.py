# tests/test_loader_writes_instruments.py
"""Task 5: the contract loader writes Instrument rows + emits quotes.

These tests pin the new behaviour of ``_upsert_catalog``/``_expire_missing``:
the catalog is the unified ``instruments`` table and prices flow through the
``market_quotes`` observation store (never a ``last_price`` column).
"""
from __future__ import annotations

from datetime import date

from app.models import Instrument, MarketQuote, Underlying
from app.services.hedging_loader import _expire_missing, _upsert_catalog
from app.services.hedging_universe import EnumeratedContract


def test_upsert_catalog_merges_into_existing_instrument(session):
    """The LH2609 case: a contract row arriving for a symbol that already
    exists (position-sourced) MERGES terms onto it — one identity."""
    existing = Instrument(
        symbol="LH2609.DCE", kind="futures", status="draft", source="position"
    )
    session.add(existing)
    session.flush()
    contracts = [
        EnumeratedContract(
            family="commodity_future",
            series_root="LH",
            exchange="DCE",
            contract_code="LH2609",
            instrument_type="future",
            multiplier=16.0,
            last_price=13905.0,
            akshare_symbol="LH2609",
        )
    ]
    _upsert_catalog(session, contracts, existing.id)
    session.commit()

    rows = (
        session.query(Instrument)
        .filter(Instrument.symbol == "LH2609.DCE")
        .all()
    )
    assert len(rows) == 1  # merged, not duplicated
    assert rows[0].multiplier == 16.0  # terms updated from feed
    assert rows[0].status == "active"  # feed terms are authoritative
    assert rows[0].kind == "futures"
    q = session.query(MarketQuote).one()
    assert q.source == "hedge_load" and q.price == 13905.0
    assert q.instrument_id == rows[0].id


def test_upsert_catalog_merges_across_exchange_suffix(session):
    """The IC2606.CFE case: a registry row carries the akshare-suffix style while
    the feed reports exchange CFFEX. The exchange-agnostic prefix match MERGES
    onto the existing row (keeping symbol IC2606.CFE) — never a IC2606.CFFEX twin.
    """
    existing = Instrument(
        symbol="IC2606.CFE", kind="futures", status="active", source="positions"
    )
    session.add(existing)
    session.flush()
    existing_id = existing.id
    contracts = [
        EnumeratedContract(
            family="index_future",
            series_root="IC",
            exchange="CFFEX",
            contract_code="IC2606",
            instrument_type="future",
            multiplier=200.0,
            last_price=6398.2,
            akshare_symbol="IC2606",
        )
    ]
    _upsert_catalog(session, contracts, existing_id)
    session.commit()

    rows = session.query(Instrument).filter(Instrument.contract_code == "IC2606").all()
    assert len(rows) == 1  # merged, not duplicated
    assert rows[0].id == existing_id
    assert rows[0].symbol == "IC2606.CFE"  # registry style kept, no .CFFEX twin
    assert rows[0].multiplier == 200.0  # terms updated from feed
    assert rows[0].source == "positions"  # curated/source untouched on merge
    assert (
        session.query(Instrument).filter(Instrument.symbol == "IC2606.CFFEX").count()
        == 0
    )
    q = session.query(MarketQuote).filter(MarketQuote.instrument_id == existing_id).one()
    assert q.price == 6398.2 and q.source == "hedge_load"


def test_upsert_catalog_inserts_index_future_with_parent(session):
    """A non-colliding index future INSERTs a fresh Instrument whose parent_id
    is the registry underlying row (its contractual underlier)."""
    u = Underlying(symbol="000905.SH", kind="index", currency="CNY")
    session.add(u)
    session.flush()
    contracts = [
        EnumeratedContract(
            family="index_future",
            series_root="IC",
            exchange="CFFEX",
            contract_code="IC2406",
            instrument_type="future",
            expiry=date(2026, 6, 21),
            multiplier=200.0,
            last_price=5600.0,
            akshare_symbol="IC2406",
        )
    ]
    _upsert_catalog(session, contracts, u.id)
    session.commit()

    row = (
        session.query(Instrument)
        .filter(Instrument.symbol == "IC2406.CFFEX")
        .one()
    )
    assert row.kind == "futures"
    assert row.source == "hedge_load"
    assert row.status == "active"
    assert row.parent_id == u.id  # index future's underlier is the registry row
    assert row.series_root == "IC"
    assert row.multiplier == 200.0
    q = session.query(MarketQuote).filter(MarketQuote.instrument_id == row.id).one()
    assert q.price == 5600.0 and q.source == "hedge_load"


def test_upsert_catalog_inserts_listed_option_kind(session):
    """An option contract INSERTs with kind='listed_option' (option_type set)."""
    u = Underlying(symbol="000300.SH", kind="index", currency="CNY")
    session.add(u)
    session.flush()
    contracts = [
        EnumeratedContract(
            family="etf_option",
            series_root="510300",
            exchange="SSE",
            contract_code="510300C2606M04300",
            instrument_type="option",
            option_type="C",
            strike=4.3,
            expiry=date(2026, 6, 24),
            multiplier=10000.0,
            last_price=0.12,
        )
    ]
    _upsert_catalog(session, contracts, u.id)
    session.commit()

    row = (
        session.query(Instrument)
        .filter(Instrument.symbol == "510300C2606M04300.SSE")
        .one()
    )
    assert row.kind == "listed_option"
    assert row.option_type == "C"
    assert row.parent_id == u.id  # ETF/index option underlier is the registry row


def test_expire_missing_flags_unseen_of_same_series(session):
    """A previously-loaded instrument of the same series/kind not in ``seen``
    is flagged status='expired'; the seen one stays active."""
    u = Underlying(symbol="000905.SH", kind="index", currency="CNY")
    session.add(u)
    session.flush()
    contracts = [
        EnumeratedContract(
            family="index_future", series_root="IC", exchange="CFFEX",
            contract_code="IC2406", instrument_type="future", last_price=5600.0,
        ),
        EnumeratedContract(
            family="index_future", series_root="IC", exchange="CFFEX",
            contract_code="IC2409", instrument_type="future", last_price=5650.0,
        ),
    ]
    _upsert_catalog(session, contracts, u.id)
    session.flush()
    _expire_missing(
        session, "index_future", {("CFFEX", "IC2406")}, series_root="IC"
    )
    session.flush()

    by_code = {
        r.contract_code: r.status
        for r in session.query(Instrument).filter(Instrument.kind == "futures").all()
    }
    assert by_code == {"IC2406": "active", "IC2409": "expired"}
