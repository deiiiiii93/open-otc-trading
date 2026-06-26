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


def _make_thread(client) -> int:
    return client.post(
        "/api/chat/threads", json={"title": "t", "character": "risk_manager"}
    ).json()["id"]


def test_run_unknown_slug_404(client):
    tid = _make_thread(client)
    r = client.post(f"/api/chat/threads/{tid}/workflows/nope/run", json={})
    assert r.status_code == 404


def test_real_factories_resolve(client):
    """Guard against broken imports inside the un-monkeypatched factories."""
    import app.main as main_mod

    settle = main_mod._desk_workflow_settle_factory()
    assert callable(settle)
    settle()  # no tasks queued -> returns immediately
    drive = main_mod._desk_workflow_drive_factory(object())
    assert callable(drive)


def test_run_streams_workflow_events(client, monkeypatch):
    import app.main as main_mod

    async def fake_drive(thread_id, prompt, mode):
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    monkeypatch.setattr(
        main_mod, "_desk_workflow_drive_factory", lambda svc, character="auto": fake_drive
    )
    monkeypatch.setattr(main_mod, "_desk_workflow_settle_factory", lambda: (lambda: None))

    tid = _make_thread(client)
    body = client.post(
        f"/api/chat/threads/{tid}/workflows/risk-manager-control-day/run",
        json={"mode": "yolo"},
    )
    assert body.status_code == 200
    text = body.text
    assert "event: workflow.start" in text
    assert "event: workflow.complete" in text
    assert text.count("event: workflow.step.start") == 7
