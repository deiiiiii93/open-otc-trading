from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Instrument
from app.services.underlyings import ensure_underlying


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_instrument_curve_columns_roundtrip(session: Session) -> None:
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.03}]
    inst.dividend_yield_curve = None
    inst.volatility_curve = [{"tenor": "6M", "value": 0.2}]
    session.flush()
    # Force a genuine DB round-trip on a FRESH instance — a plain (unmapped)
    # Python attribute would survive an identity-map re-fetch and pass falsely.
    session.expunge_all()
    reloaded = session.query(Instrument).filter(Instrument.symbol == "000300.SH").one()
    assert reloaded.rate_curve == [
        {"tenor": "3M", "value": 0.02},
        {"tenor": "1Y", "value": 0.03},
    ]
    assert reloaded.dividend_yield_curve is None
    assert reloaded.volatility_curve == [{"tenor": "6M", "value": 0.2}]
