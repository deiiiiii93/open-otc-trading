# tests/test_hedging_loader.py
from __future__ import annotations

from datetime import date, datetime

from app.models import HedgeMapEntry, Instrument, MarketQuote, Portfolio, Position, TaskRun, TaskStatus, Underlying
from app.services.hedging_universe import EnumeratedContract, FamilySpec
from app.services import hedging_loader
from app.services.quotes import latest_quote


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def _contract(code="IC2406", strike=None, option_type=None):
    return EnumeratedContract(
        family="index_future", series_root="IC", exchange="CFFEX",
        contract_code=code, instrument_type="future",
        option_type=option_type, strike=strike, expiry=date(2026, 6, 21),
        multiplier=200.0, last_price=5600.0, akshare_symbol=code,
    )


def test_upsert_inserts_then_updates(session):
    u = _underlying(session)
    seen = hedging_loader._upsert_catalog(session, [_contract()], u.id)
    session.flush()
    assert seen == {("CFFEX", "IC2406")}
    # Catalog rows are Instrument now; status 'live' became 'active' and the
    # price flows into the quotes store (latest_quote), not a last_price column.
    row = session.query(Instrument).filter(Instrument.kind == "futures").one()
    assert row.status == "active"
    q = latest_quote(session, row.id, as_of=datetime.utcnow())
    assert q is not None and q.price == 5600.0

    # second load with a fresh full snapshot updates in place (no duplicate)
    updated = EnumeratedContract(
        family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", instrument_type="future",
        expiry=date(2026, 6, 21), multiplier=200.0, last_price=5700.0,
    )
    hedging_loader._upsert_catalog(session, [updated], u.id)
    session.flush()
    assert session.query(Instrument).filter(Instrument.kind == "futures").count() == 1
    refreshed = session.query(Instrument).filter(Instrument.kind == "futures").one()
    # A fresh observation is recorded; latest resolves to the newest price.
    assert latest_quote(session, refreshed.id, as_of=datetime.utcnow()).price == 5700.0
    assert refreshed.expiry == date(2026, 6, 21) and refreshed.multiplier == 200.0


def test_expire_missing_only_flags_unseen(session):
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2406"), _contract("IC2409")], u.id)
    session.flush()
    hedging_loader._expire_missing(session, "index_future", {("CFFEX", "IC2406")}, series_root="IC")
    session.flush()
    by_code = {
        r.contract_code: r.status
        for r in session.query(Instrument).filter(Instrument.kind == "futures").all()
    }
    assert by_code == {"IC2406": "active", "IC2409": "expired"}


def test_expire_missing_scopes_to_series_root(session):
    u = _underlying(session, "000300.SH")
    hedging_loader._upsert_catalog(
        session,
        [
            EnumeratedContract(
                family="etf_option", series_root="510300", exchange="SSE",
                contract_code="510300C2606M04300", instrument_type="option",
                option_type="C",
            ),
            EnumeratedContract(
                family="etf_option", series_root="159919", exchange="SZSE",
                contract_code="9007001", instrument_type="option",
                option_type="C",
            ),
        ],
        u.id,
    )
    session.flush()

    hedging_loader._expire_missing(
        session,
        "etf_option",
        set(),
        series_root="159919",
    )
    session.flush()

    by_code = {
        r.contract_code: r.status
        for r in session.query(Instrument).filter(Instrument.kind == "listed_option").all()
    }
    assert by_code == {"510300C2606M04300": "active", "9007001": "expired"}


def test_reconcile_map_sets_active_and_stale(session):
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], u.id)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="stale",
    ))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2403",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active",
    ))
    session.flush()
    hedging_loader.reconcile_map(session)
    session.flush()
    by_code = {e.contract_code: e.reconcile_status for e in session.query(HedgeMapEntry).all()}
    assert by_code == {"IC2406": "active", "IC2403": "stale"}


def _open_position(session, underlying):
    pf = Portfolio(name=f"pf-{underlying.symbol}", base_currency="CNY")
    session.add(pf)
    session.flush()
    pos = Position(
        portfolio_id=pf.id, underlying_id=underlying.id, underlying=underlying.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    )
    session.add(pos)
    session.flush()
    return pos


def test_list_in_scope_includes_open_positions_and_mapped(session):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    orphan = _underlying(session, "000300.SH")
    session.add(HedgeMapEntry(
        underlying_id=orphan.id, exchange="CFFEX", contract_code="IF2406",
        family="index_future", series_root="IF", instrument_type="future",
    ))
    session.flush()
    symbols = {u.symbol for u in hedging_loader.list_in_scope_underlyings(session)}
    assert symbols == {"000905.SH", "000300.SH"}


def test_execute_load_populates_catalog_and_reconciles(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)

    def fake_future(series_root):
        return [EnumeratedContract(
            family="index_future", series_root=series_root, exchange="CFFEX",
            contract_code=f"{series_root}2406", instrument_type="future",
            last_price=5600.0,
        )]

    monkeypatch.setitem(hedging_loader_enumerators(), "cffex_future", fake_future)
    # ETF option enumerator returns nothing (still counts as success)
    monkeypatch.setitem(hedging_loader_enumerators(), "etf_option", lambda s: [])

    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))

    session.expire_all()
    codes = {
        r.contract_code
        for r in session.query(Instrument).filter(Instrument.source == "hedge_load").all()
    }
    assert "IC2406" in codes
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.COMPLETED.value
    assert done.progress_current == done.progress_total > 0
    assert done.result_payload["families"]


def test_concurrent_load_is_rejected(session):
    hedging_loader.queue_hedge_load(session)
    session.commit()
    try:
        hedging_loader.queue_hedge_load(session)
        raise AssertionError("expected HedgeLoadInProgress")
    except hedging_loader.HedgeLoadInProgress as exc:
        assert exc.task_id is not None


def test_family_error_does_not_expire_existing(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], held.id)
    session.commit()

    def boom(series_root):
        raise RuntimeError("akshare down")

    monkeypatch.setitem(hedging_loader_enumerators(), "cffex_future", boom)
    monkeypatch.setitem(hedging_loader_enumerators(), "etf_option", lambda s: [])
    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))

    session.expire_all()
    row = session.query(Instrument).filter_by(contract_code="IC2406").one()
    assert row.status == "active"  # NOT expired despite the failed fetch
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.COMPLETED_WITH_ERRORS.value
    assert done.result_payload["errors"]


def test_unexpected_error_marks_task_failed(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    monkeypatch.setitem(hedging_loader_enumerators(), "cffex_future", lambda s: [])
    monkeypatch.setitem(hedging_loader_enumerators(), "etf_option", lambda s: [])
    # An unexpected error after the per-family loop must mark the task FAILED,
    # not leave it stuck RUNNING.
    monkeypatch.setattr(
        hedging_loader, "reconcile_map",
        lambda _session: (_ for _ in ()).throw(RuntimeError("db blip")),
    )
    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))
    session.expire_all()
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.FAILED.value
    assert done.error and "db blip" in done.error


def test_missing_enumerator_records_error_and_does_not_expire(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], held.id)
    session.commit()
    # Config drift: a resolved family points at an enumerator key that is not
    # registered. The loader must record an error and NOT expire existing rows.
    monkeypatch.setattr(
        hedging_loader, "resolve_families",
        lambda symbol, asset_class=None: [
            FamilySpec("index_future", "IC", "nonexistent_key")
        ],
    )
    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))
    session.expire_all()
    row = session.query(Instrument).filter_by(contract_code="IC2406").one()
    assert row.status == "active"  # missing enumerator must NOT expire
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.COMPLETED_WITH_ERRORS.value
    assert any("nonexistent_key" in e["error"] for e in done.result_payload["errors"])


# --- test helpers ---
def hedging_loader_enumerators():
    from app.services import hedging_universe
    return hedging_universe.ENUMERATORS


def _factory(session):
    """Return a callable yielding the SAME session the test inspects."""
    class _S:
        def __call__(self):
            return session
    # prevent execute_hedge_load_task from closing the test's session
    session.close = lambda: None  # type: ignore[assignment]
    return _S()
