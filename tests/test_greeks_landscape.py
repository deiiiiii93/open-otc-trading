from __future__ import annotations

import pytest


def _add_portfolio(session):
    from app.models import Portfolio, Position

    portfolio = Portfolio(name="Landscape", base_currency="USD")
    session.add(portfolio)
    session.flush()
    for underlying, quantity in (("AAPL", 2.0), ("MSFT", -1.0)):
        session.add(
            Position(
                portfolio_id=portfolio.id,
                underlying=underlying,
                product_type="EuropeanVanillaOption",
                product_kwargs={
                    "strike": 100.0,
                    "option_type": "CALL",
                    "maturity": 1.0,
                    "contract_multiplier": 1.0,
                },
                engine_name="BlackScholesEngine",
                quantity=quantity,
                entry_price=8.0,
                currency="USD",
            )
        )
    session.flush()
    return portfolio


def test_queue_landscape_creates_linked_task(session):
    from app.services.greeks_landscape import queue_greeks_landscape

    portfolio = _add_portfolio(session)
    run, task = queue_greeks_landscape(
        session,
        portfolio_id=portfolio.id,
        spot_min_pct=-10,
        spot_max_pct=10,
        spot_nodes=5,
    )

    assert run.status == "queued"
    assert run.config == {"spot_min_pct": -10.0, "spot_max_pct": 10.0, "spot_nodes": 5}
    assert task.kind == "greeks_landscape"
    assert task.greeks_landscape_run_id == run.id


def test_execute_landscape_persists_position_and_group_curves(session):
    from app.models import GreekLandscapeRun, TaskRun
    from app.services.greeks_landscape import _execute_greeks_landscape_task, queue_greeks_landscape

    portfolio = _add_portfolio(session)
    run, task = queue_greeks_landscape(
        session,
        portfolio_id=portfolio.id,
        spot_min_pct=-10,
        spot_max_pct=10,
        spot_nodes=3,
    )
    session.commit()

    _execute_greeks_landscape_task(session, task.id, run.id)

    saved = session.get(GreekLandscapeRun, run.id)
    saved_task = session.get(TaskRun, task.id)
    assert saved.status == "completed", saved.excluded_positions
    assert saved.results["spot_shifts_pct"] == [-10.0, 0.0, 10.0]
    assert len(saved.results["positions"]) == 2
    assert saved.results["positions"][0]["calculation_mode"] == "reprice"
    assert set(saved.results["by_underlying"]) == {"AAPL", "MSFT"}
    assert len(saved.results["portfolio"]["raw"]) == 3
    assert saved.results["portfolio"]["cash_by_currency"]["USD"][1]["delta_cash"] == pytest.approx(
        sum(row["curves"]["cash"][1]["delta_cash"] for row in saved.results["positions"])
    )
    assert saved_task.status == "completed"
    assert saved_task.result_payload == {"greeks_landscape_run_id": run.id}


def test_landscape_request_rejects_grid_without_zero(session):
    from app.services.greeks_landscape import queue_greeks_landscape

    portfolio = _add_portfolio(session)
    with pytest.raises(ValueError, match="include 0"):
        queue_greeks_landscape(
            session,
            portfolio_id=portfolio.id,
            spot_min_pct=1,
            spot_max_pct=10,
            spot_nodes=5,
        )


def test_landscape_api_queues_and_loads_latest(client, session, monkeypatch):
    monkeypatch.setattr("app.main.submit_async_task", lambda *args, **kwargs: None)
    portfolio = _add_portfolio(session)
    session.commit()

    created = client.post(
        "/api/greeks-landscape/runs",
        json={
            "portfolio_id": portfolio.id,
            "spot_min_pct": -20,
            "spot_max_pct": 20,
            "spot_nodes": 9,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["status"] == "queued"
    assert payload["task_id"] is not None
    assert payload["config"]["spot_nodes"] == 9

    latest = client.get(
        f"/api/portfolios/{portfolio.id}/greeks-landscape-runs/latest"
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == payload["id"]


def test_landscape_position_scope_and_partial_failure(session):
    from app.models import GreekLandscapeRun
    from app.services.greeks_landscape import _execute_greeks_landscape_task, queue_greeks_landscape

    portfolio = _add_portfolio(session)
    scoped = portfolio.positions[0]
    scoped.engine_name = "UnsupportedLandscapeEngine"
    run, task = queue_greeks_landscape(
        session,
        portfolio_id=portfolio.id,
        position_ids=[scoped.id],
        spot_nodes=3,
    )
    session.commit()

    _execute_greeks_landscape_task(session, task.id, run.id)

    saved = session.get(GreekLandscapeRun, run.id)
    assert saved.resolved_position_ids == [scoped.id]
    assert saved.status == "completed_with_errors"
    assert saved.results["positions"] == []
    assert saved.excluded_positions[0]["position_id"] == scoped.id


def test_landscape_aggregate_keeps_cash_greeks_separate_by_currency():
    from app.services.greeks_landscape import _aggregate

    positions = [
        {
            "underlying": "AAPL",
            "currency": "USD",
            "curves": {
                "raw": [{"delta": 2.0, "gamma": 0.5}],
                "cash": [{"delta_cash": 200.0, "gamma_cash": 50.0}],
            },
        },
        {
            "underlying": "AAPL",
            "currency": "CNY",
            "curves": {
                "raw": [{"delta": 3.0, "gamma": 0.25}],
                "cash": [{"delta_cash": 300.0, "gamma_cash": 25.0}],
            },
        },
    ]

    results = _aggregate(positions, [0.0])

    assert results["portfolio"]["raw"][0] == {
        "spot_shift_pct": 0.0,
        "delta": 5.0,
        "gamma": 0.75,
    }
    assert results["portfolio"]["cash_by_currency"]["USD"][0]["delta_cash"] == 200.0
    assert results["portfolio"]["cash_by_currency"]["CNY"][0]["gamma_cash"] == 25.0


def test_landscape_agent_tools_queue_submit_and_read(session, monkeypatch):
    from app.models import GreekLandscapeRun
    from app.tools import greeks_landscape as landscape_tools

    portfolio = _add_portfolio(session)
    session.commit()
    submitted = []
    monkeypatch.setattr(
        landscape_tools,
        "submit_async_task",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )

    queued = landscape_tools.run_greeks_landscape_tool.invoke(
        {
            "portfolio_id": portfolio.id,
            "spot_min_pct": -15,
            "spot_max_pct": 20,
            "spot_nodes": 8,
        }
    )

    assert queued["status"] == "queued"
    assert queued["portfolio_id"] == portfolio.id
    assert queued["config"]["spot_nodes"] == 8
    assert len(submitted) == 1
    assert submitted[0][0][1:] == (queued["task_id"], queued["run_id"])

    saved = session.get(GreekLandscapeRun, queued["run_id"])
    saved.status = "completed"
    saved.results = {"spot_shifts_pct": [-15.0, 0.0, 20.0]}
    session.commit()

    by_id = landscape_tools.get_greeks_landscape_run_tool.invoke(
        {"run_id": queued["run_id"]}
    )
    latest = landscape_tools.get_latest_greeks_landscape_run_tool.invoke(
        {"portfolio_id": portfolio.id}
    )
    assert by_id["found"] is True
    assert by_id["results"]["spot_shifts_pct"] == [-15.0, 0.0, 20.0]
    assert "task_id" not in by_id
    assert latest["found"] is True
    assert latest["run_id"] == queued["run_id"]
    assert "task_id" not in latest


def test_landscape_prefers_native_spot_greeks_curve():
    from app.schemas import PricingEnvironmentSnapshot
    from app.services.greeks_landscape import _calculate_spot_greeks_curve

    calls = []

    class NativeEngine:
        def calculate_spot_greeks_curve(self, product, pricing_env, spot_levels):
            calls.append(("native", pricing_env.spot, spot_levels))
            return [
                {
                    "spot": spot,
                    "delta": 1.0,
                    "gamma": 2.0,
                    "calculation_mode": "native",
                }
                for spot in spot_levels
            ]

        def calculate_greeks(self, product, pricing_env):
            raise AssertionError("fallback must not run when native method exists")

    curve = _calculate_spot_greeks_curve(
        NativeEngine(),
        object(),
        PricingEnvironmentSnapshot(spot=100.0),
        [90.0, 100.0, 110.0],
    )

    assert calls == [("native", 100.0, [90.0, 100.0, 110.0])]
    assert [point["calculation_mode"] for point in curve] == ["native"] * 3
