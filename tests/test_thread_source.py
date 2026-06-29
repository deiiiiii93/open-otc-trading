"""Thread `source` tagging — isolates builder threads from desk threads."""
import pytest
from fastapi.testclient import TestClient


def test_create_thread_service_defaults_to_desk(session):
    from app.services.agents import AgentService

    svc = AgentService()
    thread = svc.create_thread(session, "t", "trader")
    session.commit()
    assert thread.source == "desk"


def test_create_thread_service_honors_source(session):
    from app.services.agents import AgentService

    svc = AgentService()
    thread = svc.create_thread(session, "t", "trader", source="workflow_builder")
    session.commit()
    assert thread.source == "workflow_builder"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    from app.main import create_app

    return TestClient(create_app())


def test_create_thread_endpoint_defaults_to_desk(client):
    r = client.post("/api/chat/threads", json={"title": "t", "character": "trader"})
    assert r.status_code == 200
    assert r.json()["source"] == "desk"


def test_create_thread_endpoint_persists_builder_source(client):
    r = client.post(
        "/api/chat/threads",
        json={"title": "b", "character": "risk_manager", "source": "workflow_builder"},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "workflow_builder"
