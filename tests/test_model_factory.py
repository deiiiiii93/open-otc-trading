from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)
from app.services.deep_agent.model_factory import (
    agent_model_config,
    build_agent_model,
    default_agent_model_selection,
    resolve_agent_model_selection,
)


def _registry(*, zenmux_healthy: bool = True, deepseek_healthy: bool = True) -> ChannelRegistry:
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key="zm_fake" if zenmux_healthy else None,
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=zenmux_healthy,
        models=(
            ModelDescriptor(id="anthropic/claude-sonnet-4-6", provider="anthropic", label="Sonnet 4.6"),
            ModelDescriptor(id="openai/gpt-5.4", provider="openai", label="GPT-5.4"),
        ),
    )
    deepseek = ChannelDescriptor(
        name="deepseek",
        label="DeepSeek",
        type="openai_compatible",
        api_key="ds_fake" if deepseek_healthy else None,
        base_url="https://api.deepseek.test",
        anthropic_base_url=None,
        healthy=deepseek_healthy,
        models=(
            ModelDescriptor(id="deepseek-v4-flash", provider="deepseek", label="DeepSeek V4 Flash", tags=("fast",)),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux, deepseek),
        default=("zenmux", "anthropic", "anthropic/claude-sonnet-4-6"),
    )


def test_build_agent_model_returns_chat_anthropic_for_zenmux_anthropic():
    from langchain_anthropic import ChatAnthropic
    model = build_agent_model(_registry())
    assert isinstance(model, ChatAnthropic)


def test_build_agent_model_uses_zenmux_anthropic_base_url():
    model = build_agent_model(_registry())
    base_url_attr = (
        getattr(model, "anthropic_api_url", None)
        or getattr(model, "base_url", None)
    )
    assert "zenmux.test/api/anthropic" in str(base_url_attr)


def test_build_agent_model_returns_chat_openai_for_zenmux_openai():
    from langchain_openai import ChatOpenAI
    model = build_agent_model(
        _registry(),
        selection={"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"},
    )
    assert isinstance(model, ChatOpenAI)


def test_build_agent_model_returns_deepseek_wrapper_for_deepseek_channel():
    from app.services.deep_agent.model_factory import DeepSeekReasoningChat

    model = build_agent_model(
        _registry(),
        selection={"channel": "deepseek", "provider": "deepseek", "model": "deepseek-v4-flash"},
    )
    assert isinstance(model, DeepSeekReasoningChat)
    base_url_attr = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None)
    assert "api.deepseek.test" in str(base_url_attr)


def test_deepseek_wrapper_replays_reasoning_content_after_tool_call():
    model = build_agent_model(
        _registry(),
        selection={
            "channel": "deepseek",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        },
    )

    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [
            HumanMessage(content="How many positions?"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "I should call the tool."},
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "get_positions",
                        "args": {},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content='{"count": 3}', tool_call_id="call_1"),
        ]
    )

    assistant_payload = payload["messages"][1]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["reasoning_content"] == "I should call the tool."
    assert assistant_payload["tool_calls"][0]["id"] == "call_1"


def test_deepseek_wrapper_replays_reasoning_content_from_upgrade_shapes():
    model = build_agent_model(
        _registry(),
        selection={
            "channel": "deepseek",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        },
    )

    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [
            AIMessage(
                content="answer from metadata",
                response_metadata={"reasoning": "metadata thinking"},
            ),
            AIMessage(
                content=[
                    {"type": "reasoning", "text": "block thinking"},
                    {"type": "text", "text": "answer from blocks"},
                ],
            ),
        ]
    )

    assert payload["messages"][0]["reasoning_content"] == "metadata thinking"
    assert payload["messages"][1]["reasoning_content"] == "block thinking"


def test_build_agent_model_returns_none_when_selected_channel_unhealthy():
    reg = _registry(zenmux_healthy=False)
    assert build_agent_model(reg) is None


def test_build_agent_model_raises_on_unknown_selection():
    with pytest.raises(KeyError, match="unknown selection"):
        build_agent_model(
            _registry(),
            selection={"channel": "zenmux", "provider": "anthropic", "model": "does-not-exist"},
        )


def test_default_agent_model_selection_returns_registry_default():
    assert default_agent_model_selection(_registry()) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_returns_default_when_none():
    assert resolve_agent_model_selection(_registry(), None) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_back_fills_legacy_channel():
    # Legacy thread-history rows have only {provider, model}.
    resolved = resolve_agent_model_selection(
        _registry(),
        {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-6"},
    )
    assert resolved == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_raises_on_unknown():
    with pytest.raises(ValueError, match="unsupported"):
        resolve_agent_model_selection(
            _registry(),
            {"channel": "zenmux", "provider": "anthropic", "model": "ghost"},
        )


def test_agent_model_config_returns_nested_catalog():
    cfg = agent_model_config(_registry())
    assert cfg["enabled"] is True
    assert cfg["active"]["channel"] == "zenmux"
    names = [ch["name"] for ch in cfg["channels"]]
    assert names == ["zenmux", "deepseek"]
    zenmux_models = cfg["channels"][0]["models"]
    assert any(m["model"] == "openai/gpt-5.4" for m in zenmux_models)


def test_agent_model_config_marks_disabled_when_no_healthy_channels():
    reg = _registry(zenmux_healthy=False, deepseek_healthy=False)
    cfg = agent_model_config(reg)
    assert cfg["enabled"] is False
