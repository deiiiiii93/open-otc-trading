from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed(session, base, quote, rate, day):
    from app.models import FxRate
    session.add(FxRate(base_currency=base, quote_currency=quote, rate=rate,
                       as_of_date=datetime(2026, 6, day), source="manual"))


def test_latest_on_or_before_valuation_date():
    from app import database
    from app.services.fx import fx_rate_as_of

    with database.SessionLocal() as session:
        _seed(session, "USD", "CNY", 7.0, 1)
        _seed(session, "USD", "CNY", 7.2, 3)
        _seed(session, "USD", "CNY", 7.5, 10)  # after valuation -> ignored
        session.commit()
        assert fx_rate_as_of(session, "USD", "CNY", datetime(2026, 6, 5)) == 7.2


def test_identity_and_inverse_and_missing():
    from app import database
    from app.services.fx import fx_rate_as_of

    with database.SessionLocal() as session:
        _seed(session, "USD", "CNY", 8.0, 1)
        session.commit()
        assert fx_rate_as_of(session, "USD", "USD", datetime(2026, 6, 5)) == 1.0
        assert fx_rate_as_of(session, "CNY", "USD", datetime(2026, 6, 5)) == pytest.approx(1 / 8.0)
        assert fx_rate_as_of(session, "JPY", "USD", datetime(2026, 6, 5)) is None
