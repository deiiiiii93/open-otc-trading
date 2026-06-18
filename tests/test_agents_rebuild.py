"""rebuild_orchestrator: narrow graph rebuild, no registry/dotenv re-read."""
from __future__ import annotations

import app.services.agents as agents_module
from app.services.agents import AgentService


def _bare_service(model: object | None) -> AgentService:
    service = object.__new__(AgentService)
    service.model = model
    service.tools = ["tool-sentinel"]
    service.checkpointer = "checkpointer-sentinel"
    service.deep_agent = "old-graph"
    service._owned_deep_agent = "old-graph"

    class _Settings:
        agent_code_interpreter_enabled = False

    service.settings = _Settings()
    return service


def test_rebuild_swaps_graph_and_keeps_model_and_checkpointer(monkeypatch) -> None:
    captured: dict = {}

    def fake_build_orchestrator(**kwargs):
        captured.update(kwargs)
        return "new-graph"

    monkeypatch.setattr(agents_module, "build_orchestrator", fake_build_orchestrator)
    service = _bare_service(model="model-sentinel")

    assert service.rebuild_orchestrator() is True
    assert service.deep_agent == "new-graph"
    assert service._owned_deep_agent == "new-graph"
    assert captured["model"] == "model-sentinel"
    assert captured["checkpointer"] == "checkpointer-sentinel"
    assert captured["tools"] == ["tool-sentinel"]


def test_rebuild_is_noop_when_agent_disabled(monkeypatch) -> None:
    def explode(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("build_orchestrator must not run when model is None")

    monkeypatch.setattr(agents_module, "build_orchestrator", explode)
    service = _bare_service(model=None)

    assert service.rebuild_orchestrator() is False
    assert service.deep_agent == "old-graph"
    assert service._owned_deep_agent == "old-graph"
