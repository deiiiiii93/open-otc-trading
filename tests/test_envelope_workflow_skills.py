"""Envelope-aware workflow skill catalog tests."""
from __future__ import annotations
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.services.deep_agent.envelopes import Envelope
from app.services.deep_agent.envelope_skills import (
    EnvelopeSkillsMiddleware,
    load_envelope_filtered_skills,
)
from app.services.deep_agent.orchestrator import _build_backend, build_orchestrator
from app.services.deep_agent.personas import all_personas


def _names_for(envelope: Envelope, sources: list[str]) -> set[str]:
    return {
        skill["name"]
        for skill in load_envelope_filtered_skills(
            _build_backend(),
            sources,
            envelope,
        )
    }


def test_pet_page_catalog_excludes_desk_only_workflows() -> None:
    names = _names_for(
        Envelope.PET_PAGE,
        ["/skills/workflows/pricing/", "/skills/workflows/try-solve/"],
    )

    assert "solve-imported-row" in names
    assert "create-request-queue-item" in names
    assert "price-portfolio" not in names
    assert "price-product" not in names


def test_pet_diagnostic_catalog_includes_diagnostic_not_write_workflows() -> None:
    names = _names_for(
        Envelope.PET_DIAGNOSTIC,
        ["/skills/workflows/positions/", "/skills/workflows/risk/"],
    )

    assert "position-diagnosis" in names
    assert "position-inputs" in names
    assert "run-risk" not in names
    assert "create-risk-report" not in names


def test_desk_workflow_catalog_keeps_controlled_write_workflows() -> None:
    names = _names_for(
        Envelope.DESK_WORKFLOW,
        ["/skills/workflows/pricing/", "/skills/workflows/risk/"],
    )

    assert "price-portfolio" in names
    assert "price-product" in names
    assert "run-risk" in names
    assert "create-risk-report" in names


def test_runtime_personas_use_envelope_skill_middleware() -> None:
    backend = _build_backend()
    specs = all_personas(object(), [], skills_backend=backend)

    assert specs
    for spec in specs:
        assert spec.get("skills") == []
        middleware = spec.get("middleware", [])
        assert any(isinstance(item, EnvelopeSkillsMiddleware) for item in middleware)


def test_build_orchestrator_installs_envelope_skill_middleware(monkeypatch) -> None:
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class Graph:
            name = kwargs["name"]

        return Graph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    build_orchestrator(
        model=GenericFakeChatModel(messages=iter([])),
        tools=[],
        checkpointer=object(),
        interrupt_on={},
    )

    # Scoped to the PERSONAS. `general-purpose` is also in this list -- we supply
    # it ourselves so our guards reach it (see orchestrator._general_purpose_
    # subagent) -- but it carries no skill sources, so envelope-scoped skill
    # exposure has nothing to expose there. Named explicitly rather than filtered
    # loosely, so giving it skills later re-arms this assertion.
    personas = [s for s in captured["subagents"] if s["name"] != "general-purpose"]
    assert personas, "expected the persona subagents"
    for spec in personas:
        assert spec.get("skills") == []
        assert any(
            isinstance(item, EnvelopeSkillsMiddleware)
            for item in spec.get("middleware", [])
        )
