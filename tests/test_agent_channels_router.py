import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.routers.agent_channels import build_agent_channels_router
from app.services.deep_agent import channel_registry as cr


class _FakeAgent:
    def __init__(self):
        self.rebuilt = 0

    def rebuild_default_model(self):
        self.rebuilt += 1


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(cr._REPO_ROOT / "config" / "agent_channels.yaml", dst)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(dst))
    cr.configure_registry(None)
    agent = _FakeAgent()
    app = FastAPI()
    app.include_router(build_agent_channels_router(agent, settings=Settings()))
    yield TestClient(app), agent, dst
    cr.configure_registry(None)


def test_get_registry_lists_editable_fields(client):
    c, _agent, _ = client
    r = c.get("/api/agent/registry")
    assert r.status_code == 200
    body = r.json()
    zen = next(ch for ch in body["channels"] if ch["name"] == "zenmux")
    assert zen["api_key_env"] == "ZENMUX_API_KEY"


def test_add_model_then_rebuilds(client):
    c, agent, _ = client
    r = c.post(
        "/api/agent/channels/zenmux/models",
        json={"id": "openai/gpt-6.0", "provider": "openai", "label": "GPT-6.0", "tags": ["tool-use"]},
    )
    assert r.status_code == 200, r.text
    assert agent.rebuilt >= 1


def test_update_model_with_slash_id_route(client):
    c, _agent, _ = client
    r = c.put(
        "/api/agent/channels/zenmux/models/anthropic/claude-sonnet-4.6",
        json={"id": "anthropic/claude-sonnet-4.6", "provider": "anthropic", "label": "Renamed"},
    )
    assert r.status_code == 200, r.text


def test_invalid_model_returns_422(client):
    c, _agent, _ = client
    r = c.post(
        "/api/agent/channels/zenmux/models",
        json={"id": "x/y", "provider": "deepseek", "label": "bad"},
    )
    assert r.status_code == 422


def test_delete_default_channel_returns_409(client):
    c, _agent, _ = client
    r = c.delete("/api/agent/channels/zenmux")
    assert r.status_code == 409


def test_write_gate_403_when_flag_off(tmp_path, monkeypatch):
    dst = tmp_path / "agent_channels.yaml"
    shutil.copy(cr._REPO_ROOT / "config" / "agent_channels.yaml", dst)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(dst))
    cr.configure_registry(None)
    app = FastAPI()
    settings = Settings(feature_model_write_api=False)
    app.include_router(build_agent_channels_router(_FakeAgent(), settings=settings))
    c = TestClient(app)
    r = c.post("/api/agent/channels/zenmux/models",
               json={"id": "a/b", "provider": "openai", "label": "x"})
    assert r.status_code == 403
    cr.configure_registry(None)
