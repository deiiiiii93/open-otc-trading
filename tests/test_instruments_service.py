# tests/test_instruments_service.py
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _mk(session, **kw):
    from app.models import Instrument
    row = Instrument(**{"status": "active", "kind": "index", **kw})
    session.add(row)
    session.flush()
    return row


def test_resolvable_includes_drafts_excludes_expired():
    """The silent draft-exclusion gotcha dies: resolvable = has AKShare
    mapping AND not expired/retired. Status 'draft' is IN."""
    from app import database
    from app.services.instruments import resolvable_market_data_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="000905.SH", status="active",
            akshare_symbol="000905", akshare_asset_class="index")
        _mk(session, symbol="LH2609.DCE", status="draft", kind="futures",
            akshare_symbol="LH2609", akshare_asset_class="futures")
        _mk(session, symbol="IC2503.CFFEX", status="expired", kind="futures",
            akshare_symbol="IC2503", akshare_asset_class="futures")
        _mk(session, symbol="NOMAP.SH", status="active", akshare_symbol=None)
        # "retired" is the spec'd manual-removal lifecycle state — pin its
        # exclusion so the filter isn't a dead letter.
        _mk(session, symbol="OLD2401.DCE", status="retired", kind="futures",
            akshare_symbol="OLD2401", akshare_asset_class="futures")
        session.commit()
        symbols = {r.symbol for r in resolvable_market_data_instruments(session)}
        assert symbols == {"000905.SH", "LH2609.DCE"}


def test_list_instruments_search_strips_and_matches_case_insensitively():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="LH2609.DCE", kind="futures")
        _mk(session, symbol="000905.SH")
        session.commit()
        # whitespace stripped, lowercase input matches via ilike
        hits = list_instruments(session, search="  lh26 ")
        assert [r.symbol for r in hits] == ["LH2609.DCE"]


def test_listed_option_requires_strike_and_type():
    from app.services.instruments import validate_instrument_terms

    with pytest.raises(ValueError, match="strike"):
        validate_instrument_terms(kind="listed_option", strike=None, option_type="C")
    with pytest.raises(ValueError, match="option_type"):
        validate_instrument_terms(kind="listed_option", strike=4000.0, option_type=None)
    validate_instrument_terms(kind="futures", strike=None, option_type=None)  # ok


def test_list_instruments_filters_kind_and_parent():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        idx = _mk(session, symbol="000905.SH")
        _mk(session, symbol="IC2606.CFFEX", kind="futures", parent_id=idx.id,
            series_root="IC")
        _mk(session, symbol="510500.SH", kind="etf")
        session.commit()
        futs = list_instruments(session, kind="futures")
        assert [r.symbol for r in futs] == ["IC2606.CFFEX"]
        children = list_instruments(session, parent_id=idx.id)
        assert [r.symbol for r in children] == ["IC2606.CFFEX"]


def test_list_instruments_filters_by_tag():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="TAGFILT.SH", tags=["underlying"])
        _mk(session, symbol="NOTAG.SH", tags=[])
        session.commit()

        rows = list_instruments(session, tag="underlying")
        symbols = {r.symbol for r in rows}
        assert "TAGFILT.SH" in symbols
        assert "NOTAG.SH" not in symbols


def test_list_instruments_tag_filter_applies_before_pagination():
    """A tagged row sorted past the unfiltered limit must still be returned —
    regression for filtering-after-SQL-pagination silently dropping rows."""
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        # Symbols sort alphabetically; put the tagged one last so a naive
        # SQL-then-filter implementation with a small limit would miss it.
        for i in range(5):
            _mk(session, symbol=f"AAA{i}.SH", tags=[])
        _mk(session, symbol="ZZZ_TAGGED.SH", tags=["underlying"])
        session.commit()

        rows = list_instruments(session, tag="underlying", limit=2)
        assert [r.symbol for r in rows] == ["ZZZ_TAGGED.SH"]


def test_set_instrument_tags_replaces_and_normalizes():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        row = _mk(session, symbol="SETTAGS.SH")
        session.commit()

        updated = set_instrument_tags(session, row.id, ["Underlying", " underlying ", "Hedge"])
        assert updated.tags == ["underlying", "hedge"]


def test_set_instrument_tags_raises_lookup_error_for_missing_instrument():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        with pytest.raises(LookupError):
            set_instrument_tags(session, 999999, ["underlying"])


def test_set_instrument_tags_rejects_non_string_tag():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        row = _mk(session, symbol="BADTAG.SH")
        session.commit()

        with pytest.raises(ValueError):
            set_instrument_tags(session, row.id, [123])


def test_is_registered_underlying():
    from app import database
    from app.services.underlyings import is_registered_underlying

    with database.SessionLocal() as session:
        _mk(session, symbol="REG.SH", tags=["underlying"])
        _mk(session, symbol="UNREG.SH", tags=[])
        session.commit()

        assert is_registered_underlying(session, "REG.SH") is True
        assert is_registered_underlying(session, "UNREG.SH") is False
        assert is_registered_underlying(session, "DOES_NOT_EXIST.SH") is False


def test_sync_hedge_tag_adds_tag_for_active_map_entry():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2406")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" in inst.tags


def test_sync_hedge_tag_removes_tag_when_no_active_entry_remains():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", tags=["hedge", "underlying"])
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert inst.tags == ["underlying"]


def test_sync_hedge_tag_matches_legacy_entry_with_null_instrument_id():
    """A HedgeMapEntry never backfilled with a durable instrument_id link is
    still real ground truth — reconcile_map/_active_instruments both fall
    back to matching (exchange, contract_code). sync_hedge_tag must too."""
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2406")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=None,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" in inst.tags


def test_sync_hedge_tag_ignores_stale_entry():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2403.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2403")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2403", reconcile_status="stale",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" not in inst.tags


def test_sync_hedge_tag_requires_instrument_itself_active_even_with_active_map_entry():
    """reconcile_status is only refreshed by mark/unmark/reconcile_map — a
    direct Instrument.status edit (e.g. via PATCH) doesn't touch it, so it
    can be stale "active" data. The real MILP eligibility query
    (_active_instruments) always filters Instrument.status == "active" too;
    sync_hedge_tag must not grant "hedge" off a stale-active map entry when
    the instrument itself is no longer active."""
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX",
                   contract_code="IC2406", status="expired")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" not in inst.tags


def test_sync_hedge_tag_active_stock_is_self_hedging():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        stock = _mk(session, symbol="600519.SH", kind="stock", status="active")
        session.commit()

        sync_hedge_tag(session, stock.id)
        session.commit()

        session.refresh(stock)
        assert "hedge" in stock.tags


def test_sync_hedge_tag_inactive_stock_is_not_self_hedging():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        stock = _mk(session, symbol="600519.SH", kind="stock", status="draft", tags=["hedge"])
        session.commit()

        sync_hedge_tag(session, stock.id)
        session.commit()

        session.refresh(stock)
        assert "hedge" not in stock.tags


def test_sync_hedge_tag_preserves_other_tags():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX",
                   contract_code="IC2406", tags=["underlying", "custom"])
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert set(inst.tags) == {"underlying", "custom", "hedge"}
