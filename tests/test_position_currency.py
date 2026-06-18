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


def test_models_expose_currency_columns():
    from app.models import AgentThread, FxRate, Position

    assert "currency" in Position.__table__.columns
    assert "report_currency" in AgentThread.__table__.columns
    assert {"base_currency", "quote_currency", "rate", "as_of_date"} <= set(
        FxRate.__table__.columns.keys()
    )


def test_fx_rate_roundtrip():
    from datetime import datetime

    from app import database
    from app.models import FxRate

    with database.SessionLocal() as session:
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency="CNY",
                rate=7.2,
                as_of_date=datetime(2026, 6, 2),
                source="manual",
            )
        )
        session.commit()
        row = session.query(FxRate).one()
        assert row.rate == 7.2
        assert row.base_currency == "USD"
        assert row.source == "manual"  # server/Python default wired through


def test_migration_backfills_position_currency_from_product(tmp_path, monkeypatch):
    """Backfill priority: product.currency -> 'CNY'."""
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from app.config import Settings

    db = f"sqlite:///{tmp_path}/m.db"
    # Monkeypatch get_settings so alembic env.py picks up the test DB URL.
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: Settings(database_url=db),
    )

    engine = sa.create_engine(db)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db)
    command.upgrade(cfg, "0019_underlying_master_data")
    now = "2026-06-02 00:00:00"
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO products "
            "(id, asset_class, product_family, underlying, currency, term_hash, created_at, updated_at) "
            f"VALUES (1, 'equity', 'vanilla', 'AAPL', 'USD', 'h1', '{now}', '{now}')"
        ))
        conn.execute(sa.text(
            "INSERT INTO portfolios "
            "(id, name, base_currency, kind, manual_include_ids, manual_exclude_ids, "
            "source_portfolio_ids, tags, created_at, updated_at) "
            f"VALUES (1, 'P', 'USD', 'container', '[]', '[]', '[]', '[]', '{now}', '{now}')"
        ))
        conn.execute(sa.text(
            "INSERT INTO positions (id, portfolio_id, product_id, underlying, product_type, "
            "product_kwargs, engine_name, engine_kwargs, quantity, entry_price, status, "
            "mapping_status, source_payload, created_at, updated_at) "
            f"VALUES (1, 1, 1, 'AAPL', 'vanilla', '{{}}', 'BlackScholesEngine', '{{}}', "
            f"1, 0, 'open', 'manual', '{{}}', '{now}', '{now}')"
        ))
    command.upgrade(cfg, "0020_currency_convention")
    with engine.begin() as conn:
        ccy = conn.execute(sa.text("SELECT currency FROM positions WHERE id = 1")).scalar()
        assert ccy == "USD"  # inherited from product
        assert conn.execute(sa.text("SELECT COUNT(*) FROM fx_rates")).scalar() == 0


def test_booking_sets_currency_from_product_and_warns_on_mismatch(caplog):
    import logging
    from types import SimpleNamespace
    from app.services.domains import booking as booking_svc

    product = SimpleNamespace(currency="USD")
    position = SimpleNamespace(
        currency=None, product=product,
        underlying_record=SimpleNamespace(currency="CNY"),
    )
    with caplog.at_level(logging.WARNING):
        booking_svc.set_position_currency(position)
    assert position.currency == "USD"
    assert any("same currency for non-quanto" in r.message for r in caplog.records)


def test_booking_currency_no_warning_when_match(caplog):
    import logging
    from types import SimpleNamespace
    from app.services.domains import booking as booking_svc

    position = SimpleNamespace(
        currency=None, product=SimpleNamespace(currency="USD"),
        underlying_record=SimpleNamespace(currency="USD"),
    )
    with caplog.at_level(logging.WARNING):
        booking_svc.set_position_currency(position)
    assert position.currency == "USD"
    assert not any("non-quanto" in r.message for r in caplog.records)


def test_source_currency_prefers_position_column():
    from types import SimpleNamespace
    from app.services.position_pricer import _source_currency

    # New behavior: explicit column wins over the source-payload scrape.
    pos = SimpleNamespace(currency="USD", source_payload={"row": {"Notional Unit": "CNY"}})
    assert _source_currency(pos) == "USD"

    # Fallback: no column -> legacy nested source-payload scrape.
    legacy = SimpleNamespace(currency=None, source_payload={"row": {"Notional Unit": "EUR"}})
    assert _source_currency(legacy) == "EUR"

    # Default when neither is present.
    bare = SimpleNamespace(currency=None, source_payload={})
    assert _source_currency(bare) == "CNY"


def test_position_out_serializes_currency():
    from datetime import datetime
    from types import SimpleNamespace

    from app.schemas import PositionOut

    now = datetime(2026, 6, 4)
    position = SimpleNamespace(
        id=1, portfolio_id=2, product_id=None, underlying_id=None,
        underlying="000300.SH", product_type="SnowballOption",
        product_kwargs={}, product=None, engine_name="SnowballQuadEngine",
        engine_kwargs={}, quantity=1.0, entry_price=0.0, status="open",
        source_trade_id=None, source_row=None, mapping_status="supported",
        mapping_error=None, source_payload=None, rfq_id=None,
        rfq_quote_version_id=None, trade_effective_date=None,
        currency="USD", created_at=now, updated_at=now,
    )
    out = PositionOut.model_validate(position, from_attributes=True)
    assert out.currency == "USD"


def test_product_spec_layers_default_to_cny():
    """Product-currency defaults must agree with the Position model / booking
    builders (CNY) — the USD spec defaults mislabeled whole books."""
    from app.schemas import ProductSpecIn
    from app.services.domains.products import (
        ProductSpec,
        product_spec_from_executable_terms,
        product_spec_from_position_payload,
    )

    assert ProductSpec(
        asset_class="equity",
        product_family="option",
        quantark_class="EuropeanVanillaOption",
        underlying="000300.SH",
    ).currency == "CNY"
    assert ProductSpecIn().currency == "CNY"
    assert product_spec_from_position_payload({"underlying": "000300.SH"}).currency == "CNY"
    assert product_spec_from_executable_terms({"underlying": "000300.SH"}).currency == "CNY"
    # Explicit values still win at every layer.
    assert product_spec_from_position_payload(
        {"underlying": "000300.SH", "currency": "USD"}
    ).currency == "USD"
