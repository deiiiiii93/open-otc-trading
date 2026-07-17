from __future__ import annotations

from datetime import datetime

import pytest


def _database(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/limit-fx.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def test_fx_evidence_pins_exact_direct_inverse_and_identity_rows(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import FxRate
    from app.services.fx import fx_rate_as_of, fx_rate_evidence_as_of

    with database.SessionLocal() as session:
        old = FxRate(
            base_currency="USD",
            quote_currency="CNY",
            rate=7.0,
            as_of_date=datetime(2026, 7, 15),
            source="manual",
        )
        pinned = FxRate(
            base_currency="USD",
            quote_currency="CNY",
            rate=7.2,
            as_of_date=datetime(2026, 7, 16),
            source="close",
        )
        future = FxRate(
            base_currency="USD",
            quote_currency="CNY",
            rate=7.5,
            as_of_date=datetime(2026, 7, 18),
            source="future",
        )
        session.add_all([old, pinned, future])
        session.commit()

        direct = fx_rate_evidence_as_of(
            session,
            "USD",
            "CNY",
            datetime(2026, 7, 17),
        )
        inverse = fx_rate_evidence_as_of(
            session,
            "CNY",
            "USD",
            datetime(2026, 7, 17),
        )
        identity = fx_rate_evidence_as_of(
            session,
            "USD",
            "USD",
            datetime(2026, 7, 17),
        )

        assert direct.fx_rate_id == pinned.id
        assert direct.rate == pytest.approx(7.2)
        assert direct.as_of == datetime(2026, 7, 16)
        assert direct.is_inverse is False
        assert inverse.fx_rate_id == pinned.id
        assert inverse.rate == pytest.approx(1 / 7.2)
        assert inverse.is_inverse is True
        assert identity.fx_rate_id is None
        assert identity.rate == 1.0
        assert identity.is_inverse is False
        assert fx_rate_as_of(
            session,
            "USD",
            "CNY",
            datetime(2026, 7, 17),
        ) == pytest.approx(7.2)


def test_mixed_currency_risk_observation_pins_fx_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import FxRate
    from app.services.limits.sources import ObservationScope, adapt_risk_run
    from test_limit_sources import _risk_run

    with database.SessionLocal() as session:
        fx = FxRate(
            base_currency="CNY",
            quote_currency="USD",
            rate=0.14,
            as_of_date=datetime(2026, 7, 16, 15, 0),
            source="close",
        )
        session.add(fx)
        session.commit()
        observation = adapt_risk_run(
            session,
            _risk_run(),
            metric_kind="rho_q",
            aggregation="net",
            unit="USD/1pct",
            currency="USD",
            scope=ObservationScope("portfolio"),
            valuation_as_of=datetime(2026, 7, 17, 9, 0),
            bump_convention="per_1pct",
        )

        assert observation.values == pytest.approx((9.8, 10.0))
        assert observation.evidence["fx_rates"] == [
            {
                "base_currency": "CNY",
                "quote_currency": "USD",
                "fx_rate_id": fx.id,
                "is_inverse": False,
                "rate": 0.14,
                "as_of": "2026-07-16T15:00:00",
                "source": "close",
            }
        ]


def test_missing_fx_is_unknown(tmp_path, monkeypatch) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.sources import ObservationScope, adapt_risk_run
    from test_limit_sources import _risk_run

    with database.SessionLocal() as session:
        observation = adapt_risk_run(
            session,
            _risk_run(),
            metric_kind="rho_q",
            aggregation="net",
            unit="USD/1pct",
            currency="USD",
            scope=ObservationScope("portfolio"),
            valuation_as_of=datetime(2026, 7, 17, 9, 0),
            bump_convention="per_1pct",
        )

    assert observation.values is None
    assert observation.reason_code == "missing_fx"
    assert observation.evidence["missing_fx"] == ["CNY->USD"]


@pytest.mark.parametrize("rate", [0.0, -7.2, float("inf")])
def test_non_positive_or_non_finite_fx_rows_are_never_used(
    tmp_path,
    monkeypatch,
    rate: float,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import FxRate
    from app.services.fx import fx_rate_evidence_as_of

    with database.SessionLocal() as session:
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency="CNY",
                rate=rate,
                as_of_date=datetime(2026, 7, 16),
                source="bad",
            )
        )
        session.commit()
        assert (
            fx_rate_evidence_as_of(
                session, "USD", "CNY", datetime(2026, 7, 17)
            )
            is None
        )
        assert (
            fx_rate_evidence_as_of(
                session, "CNY", "USD", datetime(2026, 7, 17)
            )
            is None
        )


@pytest.mark.parametrize(
    ("base", "quote", "stored_base", "stored_quote", "stored_rate", "expected"),
    [
        ("CNY", "USD", "CNY", "USD", 0.14, -14.0),
        ("CNY", "USD", "USD", "CNY", 7.0, -100.0 / 7.0),
    ],
)
def test_scenario_currency_conversion_uses_pinned_source_evidence(
    tmp_path,
    monkeypatch,
    base: str,
    quote: str,
    stored_base: str,
    stored_quote: str,
    stored_rate: float,
    expected: float,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import FxRate, ScenarioTestRun
    from app.services.limits.sources import adapt_scenario_test_run
    from app.services.source_evidence import source_metric_contract

    set_hash = "sha256:" + ("e" * 64)
    with database.SessionLocal() as session:
        session.add(
            FxRate(
                base_currency=stored_base,
                quote_currency=stored_quote,
                rate=stored_rate,
                as_of_date=datetime(2026, 7, 16),
                source="close",
            )
        )
        session.commit()
        run = ScenarioTestRun(
            id=81,
            portfolio_id=1,
            status="completed",
            scenario_spec={},
            config={},
            results={
                "source_metadata": {
                    "methodology": {
                        "method": "scenario_distribution",
                        "confidence": 0.95,
                        "horizon": "scenario_set",
                        "scaling": "none",
                    },
                    "source_currencies": [base],
                    "scenario_set_hash": set_hash,
                    "scenario_names": ["down"],
                    "valuation_as_of": "2026-07-17T09:00:00",
                    "metric_contract": source_metric_contract("scenario_test"),
                },
                "scenarios": [{"name": "down", "pnl": -100.0}],
            },
            excluded_positions=[],
            resolved_position_ids=[1],
            created_at=datetime(2026, 7, 17, 9, 0),
        )
        observation = adapt_scenario_test_run(
            run,
            metric_kind="stress_pnl",
            methodology={
                "selection": "named",
                "scenario_set_hash": set_hash,
                "scenario_name": "down",
            },
            unit=f"{quote}",
            currency=quote,
            session=session,
        )

        assert observation.values == pytest.approx((expected,))
        assert observation.evidence["source_currency"] == base
        assert observation.evidence["fx_rates"][0]["fx_rate_id"] is not None
