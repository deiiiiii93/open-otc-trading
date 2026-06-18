# tests/test_scenario_test_api.py
"""REST-endpoint tests for /api/scenario-test/*

Mirrors the conftest.py fixtures: requests `client` and `session` from
conftest, which wires a temp-SQLite DB and a TestClient pointing at create_app.
"""
from __future__ import annotations

import pytest

from app.models import Portfolio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_portfolio(session) -> Portfolio:
    pf = Portfolio(name="test_portfolio", base_currency="CNY")
    session.add(pf)
    session.commit()
    return pf


# ---------------------------------------------------------------------------
# GET /api/scenario-test/library
# ---------------------------------------------------------------------------


def test_library_returns_predefined_and_saved_sets(client, session):
    r = client.get("/api/scenario-test/library")
    assert r.status_code == 200
    body = r.json()
    assert "predefined" in body
    assert "saved_sets" in body
    # Check that at least one predefined entry mentions Market Crash
    names = [s["name"] for s in body["predefined"]]
    assert any("Market Crash" in n or "market_crash" in n.lower() for n in names), (
        f"Expected a 'Market Crash' scenario in predefined list, got: {names}"
    )


# ---------------------------------------------------------------------------
# GET /api/scenario-test/sets
# ---------------------------------------------------------------------------


def test_list_sets_returns_a_list(client, session):
    r = client.get("/api/scenario-test/sets")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_sets_crud_roundtrip(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path
    )
    spec = {
        "name": "API Custom",
        "custom": [{
            "name": "API Custom",
            "description": "api made",
            "stresses": [{"param": "spot", "stress_type": "PERCENTAGE", "value": -0.12, "level": "portfolio"}],
        }],
    }
    assert client.post("/api/scenario-test/sets", json=spec).status_code == 200
    listed = client.get("/api/scenario-test/sets")
    assert listed.status_code == 200
    names = {d["name"] for d in listed.json()}
    assert "API_Custom" in names  # _safe_name sanitizes spaces -> underscores
    one = client.get("/api/scenario-test/sets/API_Custom")
    assert one.status_code == 200
    assert one.json()["stresses"][0]["param"] == "spot"
    deleted = client.delete("/api/scenario-test/sets/API_Custom")
    assert deleted.status_code in (200, 204)
    assert client.get("/api/scenario-test/sets/API_Custom").status_code == 404


def test_get_missing_set_404(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path
    )
    assert client.get("/api/scenario-test/sets/does_not_exist").status_code == 404


def test_delete_traversal_guard(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path
    )
    assert client.delete("/api/scenario-test/sets/..%2f..%2fetc%2fpasswd").status_code == 404


# ---------------------------------------------------------------------------
# POST /api/scenario-test/runs
# ---------------------------------------------------------------------------


def test_post_run_returns_queued_status(client, session, monkeypatch):
    """POST /api/scenario-test/runs should create a run with status='queued'.

    Monkeypatches submit_async_task in scenario_test_runner so no real thread
    is spawned.
    """
    import app.services.scenario_test_runner as _runner

    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)

    pf = _make_portfolio(session)

    payload = {
        "portfolio_id": pf.id,
        "predefined": ["market_crash"],
        "config": {"calculate_greeks": False},
    }
    r = client.post("/api/scenario-test/runs", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["portfolio_id"] == pf.id
    assert "id" in body


# ---------------------------------------------------------------------------
# GET /api/scenario-test/runs?portfolio_id=
# ---------------------------------------------------------------------------


def test_list_runs_for_portfolio(client, session, monkeypatch):
    import app.services.scenario_test_runner as _runner

    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)

    pf = _make_portfolio(session)

    # Create a run first
    client.post(
        "/api/scenario-test/runs",
        json={"portfolio_id": pf.id, "predefined": ["market_crash"], "config": {}},
    )

    r = client.get(f"/api/scenario-test/runs?portfolio_id={pf.id}")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert body[0]["portfolio_id"] == pf.id


# ---------------------------------------------------------------------------
# GET /api/scenario-test/runs/{run_id}
# ---------------------------------------------------------------------------


def test_get_run_by_id(client, session, monkeypatch):
    import app.services.scenario_test_runner as _runner

    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)

    pf = _make_portfolio(session)

    create_r = client.post(
        "/api/scenario-test/runs",
        json={"portfolio_id": pf.id, "predefined": ["market_crash"], "config": {}},
    )
    run_id = create_r.json()["id"]

    r = client.get(f"/api/scenario-test/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id


def test_get_run_404_for_missing(client, session):
    r = client.get("/api/scenario-test/runs/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/scenario-test/runs — negative path (bad portfolio_id)
# ---------------------------------------------------------------------------


def test_post_run_404_for_bad_portfolio(client, session, monkeypatch):
    """POST /api/scenario-test/runs with a nonexistent portfolio_id should 404."""
    import app.services.scenario_test_runner as _runner

    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)

    payload = {
        "portfolio_id": 999999,
        "predefined": ["market_crash"],
        "config": {"calculate_greeks": False},
    }
    resp = client.post("/api/scenario-test/runs", json=payload)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# GET /api/scenario-test/runs/{run_id}/artifacts/{name}
# ---------------------------------------------------------------------------


def test_artifact_download_returns_file(client, session, tmp_path):
    """GET /api/scenario-test/runs/{run_id}/artifacts/{name} returns 200 + file bytes."""
    from app.models import ScenarioTestRun, TaskStatus

    # Write a real temp file to serve.
    artifact_file = tmp_path / "report.html"
    artifact_file.write_bytes(b"<html>scenario report</html>")

    pf = _make_portfolio(session)
    run = ScenarioTestRun(
        portfolio_id=pf.id,
        status=TaskStatus.COMPLETED.value,
        scenario_spec={},
        config={},
        results={},
        excluded_positions=[],
        artifacts={
            "report_html_path": str(artifact_file),
            "export_paths": [],
            "notes": [],
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    r = client.get(f"/api/scenario-test/runs/{run.id}/artifacts/report.html")
    assert r.status_code == 200, r.text
    assert r.content == b"<html>scenario report</html>"


def test_artifact_download_404_for_bogus_name(client, session, tmp_path):
    """GET with a name not in the run's recorded paths returns 404."""
    from app.models import ScenarioTestRun, TaskStatus

    artifact_file = tmp_path / "report.html"
    artifact_file.write_bytes(b"data")

    pf = _make_portfolio(session)
    run = ScenarioTestRun(
        portfolio_id=pf.id,
        status=TaskStatus.COMPLETED.value,
        scenario_spec={},
        config={},
        results={},
        excluded_positions=[],
        artifacts={
            "report_html_path": str(artifact_file),
            "export_paths": [],
            "notes": [],
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    r = client.get(f"/api/scenario-test/runs/{run.id}/artifacts/nonexistent.csv")
    assert r.status_code == 404, r.text


def test_artifact_download_404_for_nonexistent_run(client, session):
    """GET for a run that doesn't exist returns 404."""
    r = client.get("/api/scenario-test/runs/99999/artifacts/report.html")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# POST /api/scenario-test/runs — empty scenario sources → 400
# ---------------------------------------------------------------------------


def test_post_run_400_for_empty_scenarios(client, session, monkeypatch):
    """POST /api/scenario-test/runs with no scenarios returns 400 before queuing."""
    import app.services.scenario_test_runner as _runner

    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)

    pf = _make_portfolio(session)
    payload = {
        "portfolio_id": pf.id,
        "predefined": [],
        "custom": [],
        "config": {},
    }
    resp = client.post("/api/scenario-test/runs", json=payload)
    assert resp.status_code == 400, resp.text
    assert "scenario" in resp.json()["detail"].lower()


def test_run_with_scenario_sets_expands_all_scenarios(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    import app.services.scenario_test_runner as _runner
    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)
    from app.models import Portfolio
    pf = Portfolio(name="p_sets", base_currency="CNY"); session.add(pf); session.commit()
    # save a 2-scenario set
    client.post("/api/scenario-test/sets", json={"name": "multi", "custom": [
        {"name": "A", "stresses": [{"param": "spot", "stress_type": "PERCENTAGE", "value": -0.1, "level": "portfolio"}]},
        {"name": "B", "stresses": [{"param": "vol", "stress_type": "PERCENTAGE", "value": 0.2, "level": "portfolio"}]},
    ]})
    r = client.post("/api/scenario-test/runs", json={"portfolio_id": pf.id, "scenario_sets": ["multi"]})
    assert r.status_code == 200
    spec = r.json()["scenario_spec"]
    assert len(spec["custom"]) == 2  # both scenarios expanded inline, none dropped


def test_generate_set_creates_grid(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    payload = {
        "name": "spot_vol_grid",
        "combine_mode": "cross_product",
        "axes": [
            {"param": "spot", "start": -0.2, "stop": 0.2, "step": 0.1, "stress_type": "PERCENTAGE", "level": "portfolio"},
            {"param": "vol", "start": 0.0, "stop": 0.2, "step": 0.1, "stress_type": "PERCENTAGE", "level": "portfolio"},
        ],
    }
    r = client.post("/api/scenario-test/sets/generate", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["num_scenarios"] == 15
    # appears in the full Set list, not the single-custom list
    full = {d["name"]: d for d in client.get("/api/scenario-test/sets/full").json()}
    assert "spot_vol_grid" in full and full["spot_vol_grid"]["has_grid"] is True
    assert "spot_vol_grid" not in {d["name"] for d in client.get("/api/scenario-test/sets").json()}


def test_generate_set_400_on_bad_axis(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    r = client.post("/api/scenario-test/sets/generate",
                    json={"name": "bad", "axes": [{"param": "spot", "start": 0.0, "stop": 0.2, "step": -0.1}]})
    assert r.status_code == 400


def test_get_set_scenarios_lists_members(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    client.post("/api/scenario-test/sets/generate",
                json={"name": "ladder", "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}]})
    r = client.get("/api/scenario-test/sets/ladder/scenarios")
    assert r.status_code == 200
    members = r.json()
    assert len(members) == 3
    assert members[0]["stresses"][0]["param"] == "spot"


def test_generated_set_runs_all_scenarios(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    import app.services.scenario_test_runner as _runner
    monkeypatch.setattr(_runner, "submit_async_task", lambda *a, **kw: None)
    pf = _make_portfolio(session)
    client.post("/api/scenario-test/sets/generate",
                json={"name": "g", "axes": [
                    {"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1},
                    {"param": "vol", "start": 0.0, "stop": 0.1, "step": 0.1}]})
    r = client.post("/api/scenario-test/runs", json={"portfolio_id": pf.id, "scenario_sets": ["g"]})
    assert r.status_code == 200
    assert len(r.json()["scenario_spec"]["custom"]) == 6  # 3 x 2 expanded inline
