from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.workflows import build_desk_workflows_router

SCRIPT = (
    'meta = {"name": "api-wf", "title": "API WF", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("one")\n'
)


def _make_app(session) -> TestClient:
    app = FastAPI()

    def _get_db():
        yield session

    app.include_router(build_desk_workflows_router(get_db=_get_db))
    return TestClient(app)


def test_create_list_get_delete(session):
    client = _make_app(session)
    r = client.post("/api/workflows", json={"script": SCRIPT})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "api-wf"
    assert any(w["slug"] == "api-wf" for w in client.get("/api/workflows").json())
    assert client.get("/api/workflows/api-wf").json()["script"].startswith("meta =")
    assert client.delete("/api/workflows/api-wf").status_code == 200
    assert client.get("/api/workflows/api-wf").status_code == 404


def test_create_invalid_script_422(session):
    client = _make_app(session)
    r = client.post("/api/workflows", json={"script": 'await step("x")\n'})
    assert r.status_code == 422


def test_delete_seed_409(session):
    client = _make_app(session)
    assert client.delete("/api/workflows/risk-manager-control-day").status_code == 409


def test_validate_endpoint(session):
    client = _make_app(session)
    assert client.post("/api/workflows/validate", json={"script": SCRIPT}).json()["ok"] is True
    bad = client.post("/api/workflows/validate", json={"script": "import os\n"})
    assert bad.json()["ok"] is False and bad.json()["error"]
