"""Write-facade tests for app.services.domains.pricing_profiles."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import (
    AuditEvent,
    FxRate,
    Instrument,
    Portfolio,
    Position,
    PositionValuationRun,
    PricingParameterProfile,
    PricingParameterRow,
)
from app.services.domains import pricing_profiles as svc
from app.services.domains._errors import DomainWriteError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed_position(trade_id: str = "T-1", symbol: str = "000905.SH") -> tuple[int, int]:
    """Create portfolio + instrument + position; return (position_id, instrument_id)."""
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Book", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        session.add(instrument)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying=symbol,
            underlying_id=instrument.id,
            product_type="vanilla_option",
            quantity=1.0,
            source_trade_id=trade_id,
        )
        session.add(position)
        session.commit()
        return position.id, instrument.id


def test_create_profile_persists_agent_source_and_rows():
    _, instrument_id = _seed_position("T-1", "000905.SH")

    profile = svc.create_profile(
        rows=[
            {"symbol": "000905.SH", "source_trade_id": "T-1", "rate": 0.037,
             "dividend_yield": 0.013, "volatility": 0.31},
            {"symbol": "USO.NEW", "volatility": 0.42},
        ],
        valuation_date=datetime(2026, 6, 5),
    )

    assert profile.source_type == "agent"
    assert profile.status == "completed"
    assert profile.name == "Agent Pricing Parameters 2026-06-05"
    assert profile.summary["row_count"] == 2
    assert len(profile.rows) == 2
    by_symbol = {row.symbol: row for row in profile.rows}
    # Trade-keyed row resolves the booked position's instrument.
    assert by_symbol["000905.SH"].instrument_id == instrument_id
    assert by_symbol["000905.SH"].rate == 0.037
    # Unknown symbol gets a draft instrument via ensure_instrument.
    assert by_symbol["USO.NEW"].instrument_id is not None
    assert by_symbol["USO.NEW"].source_trade_id == ""
    with database.SessionLocal() as session:
        created = session.query(Instrument).filter_by(symbol="USO.NEW").one()
        assert created.status == "draft"
        audit = session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.created"
        ).one()
        assert audit.subject_id == str(profile.id)
        assert audit.actor == "agent"


def test_create_profile_validation_refusals():
    with pytest.raises(DomainWriteError) as no_rows:
        svc.create_profile(rows=[])
    assert no_rows.value.error == "no_rows"

    with pytest.raises(DomainWriteError) as empty:
        svc.create_profile(rows=[{"symbol": "000905.SH"}])
    assert empty.value.error == "empty_row"
    assert empty.value.detail == {"row_indexes": [0]}

    with pytest.raises(DomainWriteError) as blank:
        svc.create_profile(rows=[{"symbol": "  ", "rate": 0.03}])
    assert blank.value.error == "blank_symbol"

    with pytest.raises(DomainWriteError) as dupes:
        svc.create_profile(
            rows=[
                {"symbol": "000905.SH", "rate": 0.03},
                {"symbol": "000905.sh ", "volatility": 0.2},
            ]
        )
    assert dupes.value.error == "duplicate_rows"
    assert dupes.value.detail == {"pairs": [["", "000905.sh"]]}
    # Nothing persisted on refusal.
    with database.SessionLocal() as session:
        assert session.query(PricingParameterProfile).count() == 0


def _create_simple_profile(**overrides) -> int:
    profile = svc.create_profile(
        rows=[{"symbol": "000905.SH", "rate": 0.03, "dividend_yield": 0.01,
               "volatility": 0.2}],
        **overrides,
    )
    return profile.id


def _retag_archived(profile_id: int) -> None:
    with database.SessionLocal() as session:
        session.get(PricingParameterProfile, profile_id).source_type = (
            "default_underlying_archived"
        )
        session.commit()


def test_update_profile_renames_and_redates():
    profile_id = _create_simple_profile()

    updated = svc.update_profile(
        profile_id=profile_id,
        name="Vol bump scenario",
        valuation_date=datetime(2026, 6, 6),
    )

    assert updated.name == "Vol bump scenario"
    assert updated.valuation_date == datetime(2026, 6, 6)
    with database.SessionLocal() as session:
        assert session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.updated"
        ).count() == 1


def test_update_profile_refusals():
    profile_id = _create_simple_profile()

    with pytest.raises(DomainWriteError) as no_fields:
        svc.update_profile(profile_id=profile_id)
    assert no_fields.value.error == "no_fields"

    with pytest.raises(DomainWriteError) as blank:
        svc.update_profile(profile_id=profile_id, name="   ")
    assert blank.value.error == "blank_name"

    with pytest.raises(DomainWriteError) as missing:
        svc.update_profile(profile_id=99999, name="x")
    assert missing.value.error == "profile_not_found"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.update_profile(profile_id=profile_id, name="x")
    assert archived.value.error == "profile_archived"


def test_upsert_rows_updates_matches_and_inserts_new():
    profile_id = _create_simple_profile()

    profile, counts = svc.upsert_rows(
        profile_id=profile_id,
        rows=[
            # Matches existing ("", "000905.SH") row: vol overwritten, r/q kept.
            {"symbol": "000905.SH", "volatility": 0.35},
            # New underlying-level row.
            {"symbol": "000852.SH", "rate": 0.028, "dividend_yield": 0.02,
             "volatility": 0.27},
        ],
    )

    assert counts == {"updated": 1, "inserted": 1}
    by_symbol = {row.symbol: row for row in profile.rows}
    assert by_symbol["000905.SH"].volatility == 0.35
    assert by_symbol["000905.SH"].rate == 0.03  # untouched
    assert by_symbol["000852.SH"].volatility == 0.27
    assert profile.summary["row_count"] == 2


def test_upsert_rows_guards():
    profile_id = _create_simple_profile()
    with pytest.raises(DomainWriteError) as empty:
        svc.upsert_rows(profile_id=profile_id, rows=[{"symbol": "000905.SH"}])
    assert empty.value.error == "empty_row"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.upsert_rows(profile_id=profile_id,
                        rows=[{"symbol": "000905.SH", "rate": 0.01}])
    assert archived.value.error == "profile_archived"


def test_delete_rows_removes_owned_rows_only():
    profile_id = _create_simple_profile()
    other_id = _create_simple_profile(name="Other")
    with database.SessionLocal() as session:
        own_row = session.query(PricingParameterRow).filter_by(
            profile_id=profile_id
        ).one()
        foreign_row = session.query(PricingParameterRow).filter_by(
            profile_id=other_id
        ).one()

    with pytest.raises(DomainWriteError) as not_owned:
        svc.delete_rows(profile_id=profile_id, row_ids=[own_row.id, foreign_row.id])
    assert not_owned.value.error == "rows_not_in_profile"
    assert not_owned.value.detail == {"row_ids": [foreign_row.id]}

    profile, deleted = svc.delete_rows(profile_id=profile_id, row_ids=[own_row.id])
    assert deleted == 1
    assert profile.rows == []
    assert profile.summary["row_count"] == 0


def test_delete_rows_refuses_empty_and_archived():
    profile_id = _create_simple_profile()
    with pytest.raises(DomainWriteError) as empty:
        svc.delete_rows(profile_id=profile_id, row_ids=[])
    assert empty.value.error == "no_rows"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.delete_rows(profile_id=profile_id, row_ids=[1])
    assert archived.value.error == "profile_archived"


def test_delete_profile_refused_when_referenced_by_runs():
    profile_id = _create_simple_profile()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="RunBook", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        session.add(
            PositionValuationRun(
                portfolio_id=portfolio.id,
                pricing_parameter_profile_id=profile_id,
                valuation_date=datetime(2026, 6, 5),
                status="completed",
            )
        )
        session.commit()
        run_id = session.query(PositionValuationRun.id).scalar()

    with pytest.raises(DomainWriteError) as referenced:
        svc.delete_profile(profile_id=profile_id)
    assert referenced.value.error == "profile_referenced_by_runs"
    assert referenced.value.detail["position_valuation_run_ids"] == [run_id]
    with database.SessionLocal() as session:
        assert session.get(PricingParameterProfile, profile_id) is not None


def test_delete_profile_cascades_rows_when_unreferenced():
    profile_id = _create_simple_profile()

    result = svc.delete_profile(profile_id=profile_id)

    assert result["deleted_profile_id"] == profile_id
    assert result["deleted_row_count"] == 1
    with database.SessionLocal() as session:
        assert session.get(PricingParameterProfile, profile_id) is None
        assert session.query(PricingParameterRow).filter_by(
            profile_id=profile_id
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.deleted"
        ).count() == 1


def test_delete_profile_refuses_archived():
    profile_id = _create_simple_profile()
    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.delete_profile(profile_id=profile_id)
    assert archived.value.error == "profile_archived"


def test_delete_profile_refused_when_referenced_by_fx_rate():
    profile_id = _create_simple_profile()
    with database.SessionLocal() as session:
        fx = FxRate(
            base_currency="USD",
            quote_currency="CNY",
            rate=7.25,
            as_of_date=datetime(2026, 6, 5),
            pricing_parameter_profile_id=profile_id,
        )
        session.add(fx)
        session.commit()
        fx_id = fx.id

    with pytest.raises(DomainWriteError) as referenced:
        svc.delete_profile(profile_id=profile_id)
    assert referenced.value.error == "profile_referenced_by_runs"
    assert referenced.value.detail["fx_rate_ids"] == [fx_id]
    with database.SessionLocal() as session:
        assert session.get(PricingParameterProfile, profile_id) is not None


def test_create_profile_refuses_nonsense_values():
    with pytest.raises(DomainWriteError) as zero_vol:
        svc.create_profile(rows=[{"symbol": "000905.SH", "volatility": 0.0}])
    assert zero_vol.value.error == "invalid_value"
    assert zero_vol.value.detail == {
        "rows": [{"row_index": 0, "field": "volatility", "reason": "must_be_positive"}]
    }

    with pytest.raises(DomainWriteError) as neg_vol:
        svc.create_profile(rows=[{"symbol": "000905.SH", "volatility": -0.5}])
    assert neg_vol.value.error == "invalid_value"

    with pytest.raises(DomainWriteError) as inf_rate:
        svc.create_profile(rows=[{"symbol": "000905.SH", "rate": float("inf")}])
    assert inf_rate.value.error == "invalid_value"
    assert inf_rate.value.detail == {
        "rows": [{"row_index": 0, "field": "rate", "reason": "not_finite"}]
    }
    with database.SessionLocal() as session:
        assert session.query(PricingParameterProfile).count() == 0


def test_value_guard_keeps_deliberate_looseness():
    """Sign/zero only by user decision (2026-06-06 spec): near-zero vol and
    negative rates are LEGITIMATE — do not tighten without a spec change."""
    profile = svc.create_profile(
        rows=[{"symbol": "931059.CSI", "volatility": 0.0009, "rate": -0.02,
               "dividend_yield": 0.0}],
    )
    row = profile.rows[0]
    assert row.volatility == 0.0009
    assert row.rate == -0.02


def test_upsert_rows_refuses_nonsense_values():
    profile_id = _create_simple_profile()
    with pytest.raises(DomainWriteError) as nan_q:
        svc.upsert_rows(
            profile_id=profile_id,
            rows=[{"symbol": "000905.SH", "dividend_yield": float("nan")}],
        )
    assert nan_q.value.error == "invalid_value"
    assert nan_q.value.detail == {
        "rows": [{"row_index": 0, "field": "dividend_yield", "reason": "not_finite"}]
    }
