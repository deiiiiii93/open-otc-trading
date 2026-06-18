import pytest
from fastapi.testclient import TestClient


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


def test_thread_exposes_default_report_currency(client):
    from app import database
    from app.models import AgentThread
    with database.SessionLocal() as session:
        session.add(AgentThread(title="T", character="trader"))
        session.commit()
    rows = client.get("/api/chat/threads").json()
    assert rows and all("report_currency" in r for r in rows)
    assert any(r["report_currency"] == "by_position" for r in rows)


def test_patch_thread_report_currency(client):
    from app import database
    from app.models import AgentThread
    with database.SessionLocal() as session:
        t = AgentThread(title="T", character="trader")
        session.add(t)
        session.commit()
        tid = t.id
    resp = client.patch(f"/api/chat/threads/{tid}", json={"report_currency": "USD"})
    assert resp.status_code == 200
    assert resp.json()["report_currency"] == "USD"
    # 404 on missing thread
    assert client.patch("/api/chat/threads/999999", json={"report_currency": "USD"}).status_code == 404
