# tests/test_hedging_domain.py
from __future__ import annotations

from datetime import date, datetime

from app.models import HedgeMapEntry, Instrument, Portfolio, Position, Underlying
from app.services.domains import hedging as hedging_domain
from app.services.quotes import record_quote


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def _instrument(session, u, code="IC2406", status="live", family="index_future"):
    # Catalog rows are Instrument now: status 'live' became 'active'; last_price
    # is re-seeded via the quotes store, not a column.
    new_status = "active" if status == "live" else status
    row = Instrument(
        symbol=f"{code}.CFFEX", kind="futures", series_root="IC", exchange="CFFEX",
        contract_code=code, parent_id=u.id, status=new_status,
        expiry=date(2026, 6, 21), source="hedge_load",
    )
    session.add(row)
    session.flush()
    record_quote(session, instrument_id=row.id, price=5600.0,
                 as_of=datetime.utcnow(), source="hedge_load")
    return row


def test_mark_creates_entry_with_snapshot_and_active_status(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    created = hedging_domain.mark(session, [inst.id], actor="desk_user")
    session.flush()
    assert len(created) == 1
    entry = session.query(HedgeMapEntry).one()
    assert entry.contract_code == "IC2406"
    assert entry.series_root == "IC"
    assert entry.reconcile_status == "active"
    assert entry.marked_by == "desk_user"


def test_mark_is_idempotent(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert session.query(HedgeMapEntry).count() == 1


def test_mark_expired_instrument_lands_stale(session):
    u = _underlying(session)
    inst = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert session.query(HedgeMapEntry).one().reconcile_status == "stale"


def test_unmark_deletes_by_instrument_id(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    hedging_domain.unmark(session, instrument_ids=[inst.id])
    session.flush()
    assert session.query(HedgeMapEntry).count() == 0


def test_mark_adds_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="desk_user")
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags


def test_mark_expired_instrument_does_not_add_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_by_instrument_id_removes_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert "hedge" in inst.tags

    hedging_domain.unmark(session, instrument_ids=[inst.id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_by_map_entry_id_removes_hedge_tag(session):
    from app.models import HedgeMapEntry

    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    entry_id = session.query(HedgeMapEntry).one().id

    hedging_domain.unmark(session, map_entry_ids=[entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_keeps_hedge_tag_when_another_active_entry_remains(session):
    """An instrument marked as an allowed hedge for two different underlyings
    keeps the tag after being unmarked from only one of them."""
    u1 = _underlying(session, symbol="000905.SH")
    u2 = _underlying(session, symbol="000300.SH")
    inst = _instrument(session, u1, code="IC2406")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    # Second entry for the same instrument under a different underlying.
    from app.models import HedgeMapEntry
    session.add(HedgeMapEntry(
        underlying_id=u2.id, instrument_id=inst.id,
        exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.flush()

    first_entry_id = (
        session.query(HedgeMapEntry.id)
        .filter(HedgeMapEntry.underlying_id == u1.id).scalar()
    )
    hedging_domain.unmark(session, map_entry_ids=[first_entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags


def test_unmark_by_map_entry_id_removes_hedge_tag_for_legacy_null_instrument_entry(session):
    """A legacy HedgeMapEntry with instrument_id=NULL is still real ground
    truth for sync_hedge_tag (matched via exchange/contract_code) — deleting
    it by map_entry_id must resync the matching instrument's tag, not skip
    it just because the row's own instrument_id column is NULL."""
    from app.models import HedgeMapEntry

    u = _underlying(session)
    inst = _instrument(session, u)
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=None,
        exchange=inst.exchange, contract_code=inst.contract_code, reconcile_status="active",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.flush()
    from app.services.instruments import sync_hedge_tag
    sync_hedge_tag(session, inst.id)
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags

    entry_id = session.query(HedgeMapEntry).one().id
    hedging_domain.unmark(session, map_entry_ids=[entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_list_instruments_annotates_allowed(session):
    u = _underlying(session)
    a = _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    hedging_domain.mark(session, [a.id], actor="a")
    session.flush()
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, family="index_future")
    allowed = {r["contract_code"]: r["allowed"] for r in rows}
    assert allowed == {"IC2406": True, "IC2409": False}


def test_list_instruments_filters(session):
    u = _underlying(session)
    _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    rows = hedging_domain.list_instruments(
        session, underlying_id=u.id, family="index_future", search="2409"
    )
    assert [r["contract_code"] for r in rows] == ["IC2409"]


def test_list_instruments_allowed_only_filters_in_sql(session):
    u = _underlying(session)
    a = _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    hedging_domain.mark(session, [a.id], actor="a")
    session.flush()
    rows = hedging_domain.list_instruments(
        session, underlying_id=u.id, family="index_future", allowed_only=True
    )
    assert [r["contract_code"] for r in rows] == ["IC2406"]


def test_underlyings_overview_counts(session):
    u = _underlying(session)
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    a = _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    stale = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [a.id, stale.id], actor="a")
    session.flush()
    overview = hedging_domain.underlyings_overview(session)
    row = next(r for r in overview if r["symbol"] == u.symbol)
    fam = next(f for f in row["families"] if f["family"] == "index_future")
    # Counts reflect the LIVE markable universe: 2 live (IC2406, IC2409),
    # 1 of them marked (IC2406). The expired+marked IC2403 is NOT counted in
    # total/allowed; it surfaces only via stale_count.
    assert fam["total"] == 2
    assert fam["allowed"] == 1
    assert row["stale_count"] == 1
    assert row["unresolvable"] is False


def test_get_map_groups_by_underlying(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    grouped = hedging_domain.get_map(session)
    assert grouped[0]["underlying_id"] == u.id
    assert grouped[0]["entries"][0]["contract_code"] == "IC2406"


def test_get_map_entry_includes_instrument_id(session):
    """Each map entry dict must carry instrument_id for frontend quote lookup."""
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    grouped = hedging_domain.get_map(session)
    entry = grouped[0]["entries"][0]
    assert "instrument_id" in entry
    assert entry["instrument_id"] == inst.id


def test_get_map_group_includes_underlying_symbol(session):
    """Each map group dict must carry underlying_symbol resolved from Instrument.symbol."""
    u = _underlying(session, symbol="000905.SH")
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    grouped = hedging_domain.get_map(session)
    row = next(g for g in grouped if g["underlying_id"] == u.id)
    assert row["underlying_symbol"] == "000905.SH"


def test_get_map_exposure_only_group_includes_underlying_symbol(session):
    """Exposure-only groups (no map entries) also carry underlying_symbol."""
    pf = Portfolio(name="pf_sym", base_currency="CNY")
    session.add(pf)
    u = _underlying(session, symbol="LH2609.DCE")
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.flush()
    grouped = hedging_domain.get_map(session)
    row = next((g for g in grouped if g["underlying_id"] == u.id), None)
    assert row is not None
    assert row["underlying_symbol"] == "LH2609.DCE"


def test_get_map_includes_open_position_count(session):
    """get_map groups include the count of open positions for each underlying."""
    pf = Portfolio(name="pf_cnt", base_currency="CNY")
    session.add(pf)
    u = _underlying(session, symbol="000300.SH")
    session.flush()
    # Two open + one closed position on the same underlying
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="closed",
    ))
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    grouped = hedging_domain.get_map(session)
    row = next(g for g in grouped if g["underlying_id"] == u.id)
    assert row["open_position_count"] == 2


def test_get_map_counts_only_otc_positions(session):
    pf = Portfolio(name="pf_kind", base_currency="CNY")
    session.add(pf)
    otc = _underlying(session, symbol="000905.SH")
    listed = _underlying(session, symbol="IC2606.CFE")
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=otc.id, underlying=otc.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
        position_kind="otc",
    ))
    session.add(Position(
        portfolio_id=pf.id, underlying_id=listed.id, underlying=listed.symbol,
        product_type="Futures", quantity=-1.0, entry_price=0.0, status="open",
        position_kind="listed",
    ))
    session.flush()

    grouped = hedging_domain.get_map(session)

    otc_group = next((g for g in grouped if g["underlying_id"] == otc.id), None)
    listed_group = next((g for g in grouped if g["underlying_id"] == listed.id), None)
    assert otc_group is not None
    assert otc_group["open_position_count"] == 1
    assert listed_group is None


def test_get_map_includes_exposure_only_group(session):
    """Underlyings with open positions but zero map entries appear in get_map."""
    pf = Portfolio(name="pf_exp", base_currency="CNY")
    session.add(pf)
    u = _underlying(session, symbol="LH2609.DCE")
    session.flush()
    # Two separate open positions on this underlying (no map entries)
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.flush()
    # No map entries for this underlying
    grouped = hedging_domain.get_map(session)
    row = next((g for g in grouped if g["underlying_id"] == u.id), None)
    assert row is not None, "Exposure-only group must appear"
    assert row["entries"] == []
    assert row["open_position_count"] == 2


def test_purge_stale_removes_only_stale(session):
    u = _underlying(session)
    live = _instrument(session, u, code="IC2406")
    stale = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [live.id, stale.id], actor="a")
    session.flush()
    removed = hedging_domain.purge_stale(session, underlying_id=u.id)
    session.flush()
    assert removed == 1
    assert {e.contract_code for e in session.query(HedgeMapEntry).all()} == {"IC2406"}


def test_unmark_by_map_entry_id(session):
    u = _underlying(session)
    inst = _instrument(session, u, code="IC2412")
    hedging_domain.mark(session, [inst.id], actor="b")
    session.flush()
    entry = session.query(HedgeMapEntry).one()
    hedging_domain.unmark(session, map_entry_ids=[entry.id])
    session.flush()
    assert session.query(HedgeMapEntry).count() == 0


def _stock(session, symbol="AAPL", status="active"):
    row = Instrument(
        symbol=symbol, kind="stock", exchange=None,
        status=status, source="manual",
    )
    session.add(row)
    session.flush()
    return row


def test_list_instruments_stock_self_hedge(session):
    u = _stock(session)
    record_quote(session, instrument_id=u.id, price=150.0,
                 as_of=datetime.utcnow(), source="manual")
    rows = hedging_domain.list_instruments(session, underlying_id=u.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == u.id
    assert row["family"] == "stock"
    assert row["instrument_type"] == "spot"
    assert row["contract_code"] == "AAPL"
    assert row["allowed"] is True
    assert row["last_price"] == 150.0


def test_list_instruments_stock_filters_by_family(session):
    u = _stock(session)
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, family="index_future")
    assert rows == []
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, family="stock")
    assert len(rows) == 1


def test_list_instruments_stock_status_live_alias(session):
    u = _stock(session)
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, status="live")
    assert len(rows) == 1
    assert rows[0]["status"] == "active"


def test_list_instruments_stock_allowed_only_inactive_excluded(session):
    u = _stock(session, status="draft")
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, allowed_only=True)
    assert rows == []


def test_underlyings_overview_stock(session):
    u = _stock(session)
    pf = Portfolio(name="pf_stock", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
        position_kind="otc",
    ))
    session.flush()
    overview = hedging_domain.underlyings_overview(session)
    row = next(r for r in overview if r["symbol"] == u.symbol)
    assert row["unresolvable"] is False
    assert row["families"] == [{"family": "stock", "total": 1, "allowed": 1}]


def test_get_map_stock_includes_self_entry(session):
    u = _stock(session, symbol="600519.SH")
    pf = Portfolio(name="pf_map_stock", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
        position_kind="otc",
    ))
    session.flush()
    grouped = hedging_domain.get_map(session)
    bucket = next(g for g in grouped if g["underlying_id"] == u.id)
    assert bucket["underlying_symbol"] == "600519.SH"
    assert len(bucket["entries"]) == 1
    entry = bucket["entries"][0]
    assert entry["family"] == "stock"
    assert entry["instrument_type"] == "spot"
    assert entry["instrument_id"] == u.id
    assert entry["reconcile_status"] == "active"


def test_underlyings_overview_last_loaded_at_includes_expired_instruments(session):
    """An underlying whose entire catalog has rolled to 'expired' must still
    report a non-null last_loaded_at (computed over all rows, not live only)."""
    from datetime import datetime

    u = _underlying(session)
    pf = Portfolio(name="pf2", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    # Only an expired instrument — simulates a universe that fully rolled over.
    expired_inst = Instrument(
        symbol="IC2403.CFFEX", kind="futures", series_root="IC", exchange="CFFEX",
        contract_code="IC2403", parent_id=u.id, status="expired",
        expiry=date(2026, 3, 21), source="hedge_load",
        loaded_at=datetime(2026, 6, 1, 8, 0, 0),
    )
    session.add(expired_inst)
    session.flush()
    record_quote(session, instrument_id=expired_inst.id, price=5500.0,
                 as_of=datetime(2026, 6, 1, 8, 0, 0), source="hedge_load")

    overview = hedging_domain.underlyings_overview(session)
    row = next(r for r in overview if r["symbol"] == u.symbol)
    # Must be non-null despite no live instruments.
    assert row["last_loaded_at"] is not None
    assert "2026-06-01" in row["last_loaded_at"]
    # No live instruments → families list is empty (total/allowed counts are live-only).
    assert row["families"] == []
