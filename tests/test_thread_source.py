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


def _make_thread(client, title: str, source: str) -> int:
    r = client.post(
        "/api/chat/threads",
        json={"title": title, "character": "trader", "source": source},
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_list_threads_scoped_to_source_excludes_other_sources(client):
    _make_thread(client, "desk one", "desk")
    _make_thread(client, "builder one", "workflow_builder")

    listed = client.get("/api/chat/threads", params={"source": "desk"})

    assert listed.status_code == 200
    assert [t["title"] for t in listed.json()] == ["desk one"]


def test_list_threads_scoped_to_desk_excludes_arena_threads(client):
    """An arena board's threads must never load into the desk thread list.

    Arena runs mint one thread per match, so an unscoped list grows without
    bound with board history — the payload that exhausted the connection pool.
    """
    _make_thread(client, "desk one", "desk")
    for n in range(3):
        _make_thread(client, f"arena match {n}", "arena")

    listed = client.get("/api/chat/threads", params={"source": "desk"})

    assert listed.status_code == 200
    assert [t["source"] for t in listed.json()] == ["desk"]


def test_list_threads_without_source_still_returns_every_public_thread(client):
    """Back-compat: the scoping is opt-in, so an unscoped caller is unchanged."""
    _make_thread(client, "desk one", "desk")
    _make_thread(client, "builder one", "workflow_builder")

    listed = client.get("/api/chat/threads")

    assert listed.status_code == 200
    assert sorted(t["source"] for t in listed.json()) == ["desk", "workflow_builder"]


def test_single_thread_lookup_is_not_source_scoped(client):
    """Scoping belongs to the list only — a thread stays reachable by id.

    `public_thread_query` also backs `_get_thread_or_404`, so the list's
    filter must not leak into per-thread resolution.
    """
    builder_id = _make_thread(client, "builder one", "workflow_builder")

    renamed = client.patch(
        f"/api/chat/threads/{builder_id}", json={"title": "renamed"}
    )

    assert renamed.status_code == 200
    assert renamed.json()["source"] == "workflow_builder"
