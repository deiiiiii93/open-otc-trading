"""The guard exists in AUTO only, in all four stacks (spec §1 truth table).

Mirrors test_audit_registration.py, and additionally pins ABSENCE under
interactive and under headless YOLO (every arena run).
"""
from __future__ import annotations
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from unittest.mock import MagicMock

import pytest

from app.services.agents import resolve_execution_mode

GUARD = "ToolGuardMiddleware"


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def _orchestrator(yolo_mode, allow_reply_options):
    from app.services.deep_agent.orchestrator import _agent_middleware

    return _names(_agent_middleware(False, model=GenericFakeChatModel(messages=iter([])), backend=object(), tools=[],
                                    yolo_mode=yolo_mode,
                                    allow_reply_options=allow_reply_options))


def _personas(yolo_mode, allow_reply_options):
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(model=None, tools=[], skills_backend=object(),
                         yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)
    assert specs
    return [(spec["name"], spec["middleware"]) for spec in specs]


def _general_purpose(yolo_mode, allow_reply_options):
    from app.services.deep_agent.orchestrator import _general_purpose_subagent

    return _names(_general_purpose_subagent(
        [], yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)["middleware"])


def _async(monkeypatch, yolo_mode, allow_reply_options):
    import deepagents

    import app.services.async_agents.agent as agent_mod

    captured = {}
    monkeypatch.setattr(deepagents, "create_deep_agent",
                        lambda **kw: captured.update(kw) or object())
    agent_mod.build_async_agent(model=MagicMock(), tools=[], checkpointer=None, task_id=1,
                                yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)
    return _names(captured["middleware"])


@pytest.mark.parametrize("mode, legacy_yolo, expected", [
    ("interactive", False, False),
    ("auto", False, True),
    ("yolo", False, False),       # headless — every arena run
    (None, True, True),           # legacy caller: resolves to auto
    (None, False, False),
])
def test_truth_table_in_every_stack(monkeypatch, mode, legacy_yolo, expected):
    _mode, clear_hitl, allow = resolve_execution_mode(mode, legacy_yolo)
    assert (GUARD in _orchestrator(clear_hitl, allow)) is expected
    for name, middleware in _personas(clear_hitl, allow):
        assert (GUARD in _names(middleware)) is expected, name
    assert (GUARD in _general_purpose(clear_hitl, allow)) is expected
    assert (GUARD in _async(monkeypatch, clear_hitl, allow)) is expected


def test_each_stack_labels_its_persona():
    from app.services.deep_agent.orchestrator import _agent_middleware, _general_purpose_subagent

    guard = next(m for m in _agent_middleware(False, model=GenericFakeChatModel(messages=iter([])), backend=object(), tools=[],
                                              yolo_mode=True, allow_reply_options=True)
                 if type(m).__name__ == GUARD)
    assert guard.persona == "orchestrator"
    for name, middleware in _personas(True, True):
        assert next(m for m in middleware if type(m).__name__ == GUARD).persona == name
    gp = _general_purpose_subagent([], yolo_mode=True, allow_reply_options=True)["middleware"]
    assert next(m for m in gp if type(m).__name__ == GUARD).persona == "general-purpose"
