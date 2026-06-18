from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as app_main_module
from app.main import create_app
from app.config import Settings
from app import database
from app.models import Portfolio, Position
from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)


def _test_registry() -> ChannelRegistry:
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key=None,
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=False,
        models=(
            ModelDescriptor(
                id="anthropic/claude-sonnet-4-6",
                provider="anthropic",
                label="Claude Sonnet 4.6",
                tags=("tool-use", "reasoning"),
            ),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux,),
        default=("zenmux", "anthropic", "anthropic/claude-sonnet-4-6"),
    )


def _install_test_agent_service(settings: Settings) -> None:
    service = app_main_module.agent_service
    service.settings = settings
    service.registry = _test_registry()
    service.default_model_selection = service.registry.default_selection()
    service.model = None
    service.deep_agent = None
    service.checkpointer = None
    service._owned_deep_agent = None


def make_client(tmp_path: Path) -> TestClient:
    channels_file = tmp_path / "agent_channels.yaml"
    channels_file.write_text(
        """
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: TEST_ZENMUX_KEY
    base_url: https://zenmux.test/api/v1
    anthropic_base_url: https://zenmux.test/api/anthropic
    models:
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Claude Sonnet 4.6
        tags: [tool-use, reasoning]
""",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_channels_file=channels_file,
    )
    _install_test_agent_service(settings)
    return TestClient(
        create_app(settings, agent_service_override=app_main_module.agent_service)
    )


# A minimal valid Snowball term set. Snowball booking now runs QuantArk
# validation, so a bare ``SnowballOption`` (no terms) is rejected with 400.
# These tests only need a live barrier product to attach lifecycle events to.
_SNOWBALL_PRODUCT_KWARGS = {
    "initial_price": 100.0,
    "strike": 100.0,
    "maturity": 1.0,
    "barrier_config": {
        "ko_barrier": 103.0,
        "ko_rate": 0.08,
        "ko_observation_schedule": {
            "records": [
                {
                    "observation_date": "2026-06-30",
                    "barrier": 103.0,
                    "return_rate": 0.08,
                    "is_rate_annualized": True,
                }
            ]
        },
    },
}


def _add_position(client: TestClient, portfolio_id: int, **kwargs) -> dict:
    """Add a position and return the created position dict."""
    if kwargs.get("product_type") == "SnowballOption":
        kwargs.setdefault("product_kwargs", _SNOWBALL_PRODUCT_KWARGS)
        kwargs.setdefault("engine_name", "SnowballQuadEngine")
    resp = client.post(
        f"/api/portfolios/{portfolio_id}/positions",
        json=kwargs,
    )
    assert resp.status_code == 200
    portfolio = resp.json()
    return portfolio["positions"][-1]


def test_create_lifecycle_event_updates_status(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )
    assert position["status"] == "open"

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {"barrier_level": 4500}},
    )
    assert event.status_code == 200
    data = event.json()
    assert data["event_type"] == "knock_in"
    assert data["old_status"] == "open"
    assert data["new_status"] == "knocked_in"

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "knocked_in"


def test_knock_out_closes_position(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client,
        portfolio["id"],
        underlying="CSI500",
        product_type="SnowballOption",
        quantity=1,
        status="knocked_in",
    )

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {"barrier_level": 4500}},
    )
    assert event.status_code == 200
    assert event.json()["new_status"] == "closed"

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "closed"


def test_informational_event_does_not_change_status(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "coupon_observation", "event_data": {"observation_date": "2026-06-01"}},
    )
    assert event.status_code == 200
    data = event.json()
    # API sets new_status = old_status when target_status is None
    assert data["new_status"] == "open"
    assert data["old_status"] == "open"

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "open"


def test_lifecycle_event_rejected_for_view_portfolio(tmp_path: Path):
    client = make_client(tmp_path)

    view = client.post("/api/portfolios", json={"name": "Snowball View", "kind": "view"}).json()
    # Seed a position directly in the DB since view portfolios reject add_position
    with database.SessionLocal() as session:
        pos = Position(
            portfolio_id=view["id"],
            underlying="CSI500",
            product_type="SnowballOption",
            quantity=1,
        )
        session.add(pos)
        session.commit()
        position_id = pos.id

    event = client.post(
        f"/api/portfolios/{view['id']}/positions/{position_id}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )
    assert event.status_code == 400
    assert "container portfolios" in event.json()["detail"]


def test_invalid_event_type_rejected(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "autocall", "event_data": {}},
    )
    assert event.status_code == 400
    assert "Invalid event type" in event.json()["detail"]


def test_list_lifecycle_events_ordered_by_date(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )

    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {}},
    )
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )

    events = client.get(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events"
    )
    assert events.status_code == 200
    data = events.json()
    assert len(data) == 2
    assert data[0]["event_type"] == "knock_out"
    assert data[1]["event_type"] == "knock_in"


def test_list_portfolio_lifecycle_events_returns_events_for_all_positions(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    first = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )
    second = _add_position(
        client, portfolio["id"], underlying="CSI300", product_type="SnowballOption", quantity=1
    )

    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{first['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {}},
    )
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{second['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )

    events = client.get(f"/api/portfolios/{portfolio['id']}/lifecycle-events")

    assert events.status_code == 200
    data = events.json()
    assert [event["position_id"] for event in data] == [first["id"], second["id"]]
    assert [event["event_type"] for event in data] == ["knock_in", "knock_out"]


def test_list_view_portfolio_lifecycle_events_uses_resolved_membership(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client, portfolio["id"], underlying="CSI500", product_type="SnowballOption", quantity=1
    )
    other = _add_position(
        client, portfolio["id"], underlying="CSI300", product_type="SnowballOption", quantity=1
    )
    view = client.post("/api/portfolios", json={"name": "Manual View", "kind": "view"}).json()
    include = client.post(
        f"/api/portfolios/{view['id']}/includes",
        json={"position_ids": [position["id"]]},
    )
    assert include.status_code == 200

    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {}},
    )
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{other['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )

    events = client.get(f"/api/portfolios/{view['id']}/lifecycle-events")

    assert events.status_code == 200
    data = events.json()
    assert [event["position_id"] for event in data] == [position["id"]]
    assert data[0]["event_type"] == "knock_in"


def test_cancel_lifecycle_event_endpoint_marks_event_and_recomputes_status(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = _add_position(
        client,
        portfolio["id"],
        underlying="CSI500",
        product_type="SnowballOption",
        quantity=1,
        status="knocked_in",
    )
    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {"barrier_level": 4500}},
    ).json()

    cancelled = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events/{event['id']}/cancel",
        json={"reason": "wrong observation"},
    )

    assert cancelled.status_code == 200
    data = cancelled.json()
    assert data["cancelled_by"] == "desk_user"
    assert data["cancellation_reason"] == "wrong observation"
    assert data["cancelled_at"] is not None

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "knocked_in"

    events = client.get(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events"
    ).json()
    assert events[0]["id"] == event["id"]
    assert events[0]["cancelled_at"] is not None


def test_patch_position_rejects_view_portfolio(tmp_path: Path):
    client = make_client(tmp_path)

    view = client.post("/api/portfolios", json={"name": "View-1", "kind": "view"}).json()
    # Seed a position directly in the DB since view portfolios reject add_position
    with database.SessionLocal() as session:
        pos = Position(
            portfolio_id=view["id"],
            underlying="CSI500",
            product_type="EuropeanVanillaOption",
            quantity=1,
        )
        session.add(pos)
        session.commit()
        position_id = pos.id

    patched = client.patch(
        f"/api/portfolios/{view['id']}/positions/{position_id}",
        json={"quantity": 2},
    )
    assert patched.status_code == 400
    assert "container portfolios" in patched.json()["detail"]
