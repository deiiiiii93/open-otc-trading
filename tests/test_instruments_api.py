"""Tests for the new instruments + quotes API surface (Task 10).

Covers:
  GET  /api/instruments               — list + filters
  GET  /api/instruments/{id}          — get / 404
  PATCH /api/instruments/{id}         — partial update; PATCH-not-PUT contract
  POST  /api/instruments/sync-from-positions
  POST  /api/instruments/{id}/fetch-spot
  GET  /api/market-data/quotes?latest=1
  GET  /api/market-data/quotes?instrument_id=X  (history)
  POST  /api/market-data/quotes       — manual quote, unknown 404
  POST  /api/market-data/quotes/refresh — happy path, one-fail, unresolvable-skip
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as app_main_module
from app import database
from app.config import Settings
from app.main import create_app
from app.models import Instrument, MarketQuote, Portfolio, Position


# ---------------------------------------------------------------------------
# Client factory (same pattern as test_api.py / test_underlying_defaults.py)
# ---------------------------------------------------------------------------

def _make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'inst_api.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    database.configure_database(settings)
    database.init_db()
    return TestClient(create_app(settings))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_instrument(tmp_path, **kwargs) -> int:
    """Insert an Instrument directly (bypasses API) and return its id."""
    with database.SessionLocal() as s:
        inst = Instrument(
            symbol=kwargs.pop("symbol"),
            kind=kwargs.pop("kind", "index"),
            currency=kwargs.pop("currency", "CNY"),
            status=kwargs.pop("status", "active"),
            **kwargs,
        )
        s.add(inst)
        s.commit()
        return inst.id


def _add_position(tmp_path, underlying: str):
    """Insert a minimal open position so sync-from-positions picks it up."""
    with database.SessionLocal() as s:
        portfolio = s.query(Portfolio).filter_by(name="Test").first()
        if portfolio is None:
            portfolio = Portfolio(name="Test", kind="container", base_currency="CNY")
            s.add(portfolio)
            s.flush()
        pos = Position(
            portfolio_id=portfolio.id,
            underlying=underlying,
            product_type="EuropeanVanillaOption",
            quantity=100,
            status="open",
            source_trade_id=f"TRD-{underlying}",
        )
        s.add(pos)
        s.commit()


# ---------------------------------------------------------------------------
# GET /api/instruments — list + kind filter
# ---------------------------------------------------------------------------

class TestListInstruments:
    def test_empty_returns_empty_list(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.get("/api/instruments")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_kind_filter_returns_only_matching_kind(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_instrument(tmp_path, symbol="IC2609.CFFEX", kind="futures", status="active")
        _add_instrument(tmp_path, symbol="000905.SH", kind="index", status="active")
        _add_instrument(tmp_path, symbol="510500.SH", kind="etf", status="active")

        resp = client.get("/api/instruments", params={"kind": "futures"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "IC2609.CFFEX"
        assert data[0]["kind"] == "futures"

    def test_search_filter_returns_matching(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_instrument(tmp_path, symbol="000905.SH", kind="index")
        _add_instrument(tmp_path, symbol="000300.SH", kind="index")

        resp = client.get("/api/instruments", params={"search": "300"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "000300.SH"

    def test_limit_and_offset(self, tmp_path: Path):
        client = _make_client(tmp_path)
        for i in range(5):
            _add_instrument(tmp_path, symbol=f"SYM{i:03d}.SH", kind="stock")

        resp = client.get("/api/instruments", params={"limit": 2, "offset": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_status_filter(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_instrument(tmp_path, symbol="DRAFT.SH", kind="index", status="draft")
        _add_instrument(tmp_path, symbol="ACTIVE.SH", kind="index", status="active")

        resp = client.get("/api/instruments", params={"status": "draft"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "DRAFT.SH"

    def test_response_schema_fields(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            status="active",
            currency="CNY",
            akshare_symbol="000300",
            akshare_asset_class="index",
            rate=0.033,
            dividend_yield=0.012,
            volatility=0.21,
        )
        resp = client.get("/api/instruments")
        assert resp.status_code == 200
        row = resp.json()[0]
        expected_fields = {
            "id", "symbol", "display_name", "kind", "exchange", "currency",
            "status", "source", "akshare_symbol", "akshare_asset_class",
            "contract_code", "series_root", "expiry", "multiplier",
            "strike", "option_type", "parent_id", "loaded_at",
            "rate", "dividend_yield", "volatility", "notes",
            "created_at", "updated_at",
        }
        assert expected_fields <= set(row.keys())
        assert row["rate"] == pytest.approx(0.033)
        assert row["volatility"] == pytest.approx(0.21)


# ---------------------------------------------------------------------------
# GET /api/instruments/{id}
# ---------------------------------------------------------------------------

class TestGetInstrument:
    def test_returns_instrument(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="000905.SH", kind="index", status="draft")
        resp = client.get(f"/api/instruments/{iid}")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "000905.SH"

    def test_404_when_missing(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.get("/api/instruments/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/instruments/{id} — partial update (PATCH-not-PUT contract)
# ---------------------------------------------------------------------------

class TestPatchInstrument:
    def test_patch_applies_only_provided_fields(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            rate=0.033,
            dividend_yield=0.015,
            volatility=0.19,
        )
        # PATCH only rate — volatility must be untouched
        resp = client.patch(f"/api/instruments/{iid}", json={"rate": 0.04})
        assert resp.status_code == 200
        data = resp.json()
        assert data["rate"] == pytest.approx(0.04)
        assert data["volatility"] == pytest.approx(0.19)   # unchanged
        assert data["dividend_yield"] == pytest.approx(0.015)  # unchanged

    def test_patch_display_name_and_notes(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="510500.SH", kind="etf")
        resp = client.patch(
            f"/api/instruments/{iid}",
            json={"display_name": "CSI 500 ETF", "notes": "test note"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "CSI 500 ETF"
        assert data["notes"] == "test note"

    def test_patch_listed_option_without_strike_returns_400(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="50ETF-C-2606-3000", kind="etf")
        # Changing kind to listed_option without also providing strike
        resp = client.patch(
            f"/api/instruments/{iid}",
            json={"kind": "listed_option", "option_type": "C"},
        )
        assert resp.status_code == 400
        assert "strike" in resp.json()["detail"].lower()

    def test_patch_bad_parent_id_returns_400(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="IC2609.CFFEX", kind="futures")
        resp = client.patch(f"/api/instruments/{iid}", json={"parent_id": 99999})
        assert resp.status_code == 400

    def test_patch_with_valid_parent_id(self, tmp_path: Path):
        client = _make_client(tmp_path)
        parent_id = _add_instrument(tmp_path, symbol="000905.SH", kind="index")
        child_id = _add_instrument(tmp_path, symbol="IC2609.CFFEX", kind="futures")
        resp = client.patch(
            f"/api/instruments/{child_id}", json={"parent_id": parent_id}
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == parent_id

    def test_patch_404_when_missing(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.patch("/api/instruments/99999", json={"rate": 0.05})
        assert resp.status_code == 404

    def test_patch_emits_audit_event(self, tmp_path: Path):
        from app.models import AuditEvent

        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="000300.SH", kind="index")
        client.patch(f"/api/instruments/{iid}", json={"rate": 0.035})
        with database.SessionLocal() as s:
            events = s.query(AuditEvent).filter(
                AuditEvent.event_type == "instrument.updated"
            ).all()
        assert len(events) == 1
        assert int(events[0].subject_id) == iid


# ---------------------------------------------------------------------------
# POST /api/instruments/sync-from-positions
# ---------------------------------------------------------------------------

class TestSyncFromPositions:
    def test_creates_draft_instruments_from_positions(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_position(tmp_path, "000300.SH")
        _add_position(tmp_path, "510500.SH")

        resp = client.post("/api/instruments/sync-from-positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 2
        assert data["existing"] == 0
        symbols = {i["symbol"] for i in data["instruments"]}
        assert "000300.SH" in symbols
        assert "510500.SH" in symbols
        # drafts, not active
        for row in data["instruments"]:
            if row["symbol"] in {"000300.SH", "510500.SH"}:
                assert row["status"] == "draft"

    def test_idempotent_second_sync(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_position(tmp_path, "000300.SH")
        client.post("/api/instruments/sync-from-positions")
        resp = client.post("/api/instruments/sync-from-positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["existing"] == 1

    def test_response_contains_instruments_list(self, tmp_path: Path):
        client = _make_client(tmp_path)
        _add_position(tmp_path, "000300.SH")
        resp = client.post("/api/instruments/sync-from-positions")
        assert resp.status_code == 200
        data = resp.json()
        assert "instruments" in data
        assert isinstance(data["instruments"], list)


# ---------------------------------------------------------------------------
# POST /api/instruments/{id}/fetch-spot  (moved from /api/underlyings/{sym}/fetch-spot)
# ---------------------------------------------------------------------------

class TestFetchSpot:
    def test_fetch_spot_emits_market_quote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        client = _make_client(tmp_path)
        iid = _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            akshare_symbol="000300",
            akshare_asset_class="index",
            status="active",
        )

        def fake_snapshot(payload):
            from app.schemas import MarketDataSnapshot

            return MarketDataSnapshot(
                name=f"{payload.symbol} spot fake",
                source="akshare",
                symbol=payload.symbol,
                asset_class=payload.asset_class,
                data={
                    "latest": {"date": "2026-06-05", "close": 4321.0},
                    "spot": 4321.0,
                },
                source_metadata={"source_name": "fake", "fallback": False},
            )

        monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

        resp = client.post(f"/api/instruments/{iid}/fetch-spot")
        assert resp.status_code == 200

        with database.SessionLocal() as s:
            quote = s.query(MarketQuote).one()
            assert quote.price == pytest.approx(4321.0)
            assert quote.source == "akshare"
            assert quote.instrument_id == iid

    def test_fetch_spot_404_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        client = _make_client(tmp_path)
        resp = client.post("/api/instruments/99999/fetch-spot")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/market-data/quotes?latest=1
# ---------------------------------------------------------------------------

class TestQuotesLatest:
    def test_returns_one_per_instrument_with_age_days(self, tmp_path: Path):
        client = _make_client(tmp_path)
        now = datetime.utcnow()

        with database.SessionLocal() as s:
            inst1 = Instrument(symbol="000300.SH", kind="index", currency="CNY", status="active")
            inst2 = Instrument(symbol="000905.SH", kind="index", currency="CNY", status="active")
            s.add_all([inst1, inst2])
            s.flush()
            # Two quotes for inst1 — only latest (closer to now) should surface
            s.add(MarketQuote(
                instrument_id=inst1.id,
                price=4200.0,
                as_of=now - timedelta(days=5),
                source="akshare",
                price_type="close",
            ))
            s.add(MarketQuote(
                instrument_id=inst1.id,
                price=4300.0,
                as_of=now - timedelta(days=1),
                source="akshare",
                price_type="close",
            ))
            s.add(MarketQuote(
                instrument_id=inst2.id,
                price=5800.0,
                as_of=now - timedelta(days=2),
                source="manual",
                price_type="close",
            ))
            s.commit()
            i1_id, i2_id = inst1.id, inst2.id

        resp = client.get("/api/market-data/quotes", params={"latest": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        by_sym = {row["symbol"]: row for row in data}
        assert "000300.SH" in by_sym
        assert "000905.SH" in by_sym
        # latest for 000300 is 4300, not 4200
        assert by_sym["000300.SH"]["price"] == pytest.approx(4300.0)
        assert by_sym["000905.SH"]["price"] == pytest.approx(5800.0)
        # age_days should be ~1 and ~2 respectively
        assert 0 <= by_sym["000300.SH"]["age_days"] <= 2
        assert 1 <= by_sym["000905.SH"]["age_days"] <= 3

    def test_quote_has_expected_fields(self, tmp_path: Path):
        client = _make_client(tmp_path)
        now = datetime.utcnow()
        with database.SessionLocal() as s:
            inst = Instrument(symbol="510500.SH", kind="etf", currency="CNY", status="active")
            s.add(inst)
            s.flush()
            s.add(MarketQuote(
                instrument_id=inst.id,
                price=6100.5,
                as_of=now - timedelta(days=3),
                source="akshare",
                price_type="close",
            ))
            s.commit()

        resp = client.get("/api/market-data/quotes", params={"latest": 1})
        assert resp.status_code == 200
        row = resp.json()[0]
        expected_fields = {
            "id", "instrument_id", "symbol", "kind", "price", "price_type",
            "as_of", "source", "age_days", "market_data_profile_id",
        }
        assert expected_fields <= set(row.keys())
        assert row["symbol"] == "510500.SH"
        assert row["kind"] == "etf"


# ---------------------------------------------------------------------------
# GET /api/market-data/quotes?instrument_id=X  (history)
# ---------------------------------------------------------------------------

class TestQuotesHistory:
    def test_returns_history_for_instrument_newest_first(self, tmp_path: Path):
        client = _make_client(tmp_path)
        now = datetime.utcnow()
        with database.SessionLocal() as s:
            inst = Instrument(symbol="000300.SH", kind="index", currency="CNY", status="active")
            s.add(inst)
            s.flush()
            iid = inst.id
            for i in range(4):
                s.add(MarketQuote(
                    instrument_id=iid,
                    price=4000.0 + i * 100,
                    as_of=now - timedelta(days=4 - i),
                    source="akshare",
                    price_type="close",
                ))
            s.commit()

        resp = client.get("/api/market-data/quotes", params={"instrument_id": iid})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4
        # newest first
        prices = [row["price"] for row in data]
        assert prices == sorted(prices, reverse=True)

    def test_history_limit(self, tmp_path: Path):
        client = _make_client(tmp_path)
        now = datetime.utcnow()
        with database.SessionLocal() as s:
            inst = Instrument(symbol="000905.SH", kind="index", currency="CNY", status="active")
            s.add(inst)
            s.flush()
            iid = inst.id
            for i in range(10):
                s.add(MarketQuote(
                    instrument_id=iid,
                    price=5000.0 + i * 50,
                    as_of=now - timedelta(days=10 - i),
                    source="akshare",
                    price_type="close",
                ))
            s.commit()

        resp = client.get(
            "/api/market-data/quotes",
            params={"instrument_id": iid, "limit": 3},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# POST /api/market-data/quotes — manual quote
# ---------------------------------------------------------------------------

class TestManualQuote:
    def test_creates_manual_quote(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="000300.SH", kind="index")
        payload = {
            "instrument_id": iid,
            "price": 4567.89,
            "as_of": "2026-06-04T10:00:00",
            "price_type": "close",
        }
        resp = client.post("/api/market-data/quotes", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["price"] == pytest.approx(4567.89)
        assert data["source"] == "manual"
        assert data["instrument_id"] == iid

    def test_manual_quote_unknown_instrument_returns_404(self, tmp_path: Path):
        client = _make_client(tmp_path)
        payload = {
            "instrument_id": 99999,
            "price": 4567.89,
            "as_of": "2026-06-04T10:00:00",
        }
        resp = client.post("/api/market-data/quotes", json=payload)
        assert resp.status_code == 404

    def test_manual_quote_emits_audit_event(self, tmp_path: Path):
        from app.models import AuditEvent

        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="000300.SH", kind="index")
        client.post(
            "/api/market-data/quotes",
            json={
                "instrument_id": iid,
                "price": 4100.0,
                "as_of": "2026-06-04T12:00:00",
            },
        )
        with database.SessionLocal() as s:
            events = s.query(AuditEvent).filter(
                AuditEvent.event_type == "market_data.quote.manual"
            ).all()
        assert len(events) == 1


# ---------------------------------------------------------------------------
# POST /api/market-data/quotes/refresh
# ---------------------------------------------------------------------------

class TestQuoteRefresh:
    def _fake_snapshot_factory(self, price_by_symbol: dict[str, float], fail: set[str] | None = None):
        fail = fail or set()

        def fake_snapshot(payload):
            from app.schemas import MarketDataSnapshot

            sym = payload.symbol
            if sym in fail:
                raise RuntimeError(f"Simulated AKShare failure for {sym}")
            price = price_by_symbol.get(sym, 9999.0)
            return MarketDataSnapshot(
                name=f"{sym} fake",
                source="akshare",
                symbol=sym,
                asset_class=payload.asset_class,
                data={"latest": {"date": "2026-06-05", "close": price}, "spot": price},
                source_metadata={"source_name": "fake", "fallback": False},
            )

        return fake_snapshot

    def test_happy_path_fetches_resolvable_instruments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        client = _make_client(tmp_path)
        # Two resolvable instruments (have akshare mappings + non-retired status)
        _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            akshare_symbol="000300",
            akshare_asset_class="index",
            status="active",
        )
        _add_instrument(
            tmp_path,
            symbol="510500.SH",
            kind="etf",
            akshare_symbol="510500",
            akshare_asset_class="etf",
            status="draft",
        )
        # One NOT resolvable (no akshare_symbol)
        _add_instrument(tmp_path, symbol="UNKNOWN.SH", kind="stock", status="active")

        monkeypatch.setattr(
            app_main_module,
            "fetch_akshare_snapshot",
            self._fake_snapshot_factory({"000300": 4300.0, "510500": 6100.0}),
        )

        resp = client.post("/api/market-data/quotes/refresh", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 2
        assert data["failed"] == []
        assert len(data["skipped"]) == 0  # no open positions with non-resolvable instruments

        with database.SessionLocal() as s:
            quotes = s.query(MarketQuote).order_by(MarketQuote.id.asc()).all()
        assert len(quotes) == 2
        prices = sorted(q.price for q in quotes)
        assert prices[0] == pytest.approx(4300.0)
        assert prices[1] == pytest.approx(6100.0)

    def test_one_fail_other_still_fetched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        client = _make_client(tmp_path)
        _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            akshare_symbol="000300",
            akshare_asset_class="index",
            status="active",
        )
        _add_instrument(
            tmp_path,
            symbol="000905.SH",
            kind="index",
            akshare_symbol="000905",
            akshare_asset_class="index",
            status="active",
        )

        monkeypatch.setattr(
            app_main_module,
            "fetch_akshare_snapshot",
            self._fake_snapshot_factory(
                {"000300": 4300.0, "000905": 5800.0},
                fail={"000905"},
            ),
        )

        resp = client.post("/api/market-data/quotes/refresh", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 1
        assert len(data["failed"]) == 1
        assert data["failed"][0]["symbol"] in {"000905", "000905.SH"}
        assert "Simulated AKShare" in data["failed"][0]["error"]

        with database.SessionLocal() as s:
            quotes = s.query(MarketQuote).all()
        assert len(quotes) == 1
        assert quotes[0].price == pytest.approx(4300.0)

    def test_refresh_emits_audit_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from app.models import AuditEvent

        client = _make_client(tmp_path)
        _add_instrument(
            tmp_path,
            symbol="000300.SH",
            kind="index",
            akshare_symbol="000300",
            akshare_asset_class="index",
            status="active",
        )
        monkeypatch.setattr(
            app_main_module,
            "fetch_akshare_snapshot",
            self._fake_snapshot_factory({"000300": 4300.0}),
        )
        client.post("/api/market-data/quotes/refresh", json={})
        with database.SessionLocal() as s:
            events = s.query(AuditEvent).filter(
                AuditEvent.event_type == "market_data.quotes.refreshed"
            ).all()
        assert len(events) == 1
