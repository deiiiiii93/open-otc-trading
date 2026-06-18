from __future__ import annotations

from langchain_core.messages import ToolMessage

from app.services.agents import _sse, _extract_tool_error


def test_sse_formats_event_with_event_and_data_lines():
    line = _sse("token", {"text": "hi"})
    assert line == 'event: token\ndata: {"text": "hi"}\n\n'


def test_sse_handles_unicode_without_escaping():
    line = _sse("token", {"text": "你好"})
    assert "你好" in line


def test_extract_tool_error_returns_none_for_clean_output():
    assert _extract_tool_error({}, {"price": 1.0}) is None
    assert _extract_tool_error({"output": "ok"}, "ok") is None


def test_extract_tool_error_picks_up_error_key_in_data():
    msg = _extract_tool_error({"error": "boom"}, None)
    assert msg == "boom"


def test_extract_tool_error_picks_up_tool_message_status_error():
    tool_msg = ToolMessage(content="not allowed", tool_call_id="x", status="error")
    assert _extract_tool_error({}, tool_msg) == "not allowed"


def test_agent_service_constructs_with_registry_param():
    from app.services.agents import AgentService
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    md = ModelDescriptor(id="m", provider="anthropic", label="M")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))

    svc = AgentService(registry=reg)
    assert svc.default_model_selection == {
        "channel": "zenmux", "provider": "anthropic", "model": "m",
    }


def test_agent_service_disabled_when_default_channel_unhealthy():
    from app.services.agents import AgentService
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    md = ModelDescriptor(id="m", provider="anthropic", label="M")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key=None, base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=False,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))

    svc = AgentService(registry=reg)
    assert svc.deep_agent is None
    assert svc.is_enabled() is False


def test_agent_service_rebuild_default_model_picks_up_new_registry(monkeypatch):
    from app.services.agents import AgentService
    from app.services.deep_agent import channel_registry
    from app.services.deep_agent.channel_registry import (
        ChannelDescriptor, ChannelRegistry, ModelDescriptor,
    )

    def make_reg(model_id: str) -> ChannelRegistry:
        md = ModelDescriptor(id=model_id, provider="anthropic", label=model_id)
        cd = ChannelDescriptor(
            name="zenmux", label="Z", type="zenmux",
            api_key="k", base_url="https://x", anthropic_base_url="https://y",
            models=(md,), healthy=True,
        )
        return ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", model_id))

    reg1 = make_reg("model-a")
    svc = AgentService(registry=reg1)
    assert svc.default_model_selection["model"] == "model-a"

    reg2 = make_reg("model-b")
    channel_registry.configure_registry(reg2)
    try:
        svc.rebuild_default_model()
        assert svc.default_model_selection["model"] == "model-b"
    finally:
        channel_registry.configure_registry(None)
