"""Pluggable model factory for the desk deep agent.

Consults the channel registry (see ``channel_registry.py``) to resolve a
``(channel, provider, model)`` triple into a concrete LangChain chat model.
Returns ``None`` when the selected channel is unhealthy so AgentService can
render the "agent disabled" stub without raising.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, convert_to_messages
from pydantic import SecretStr

from .channel_registry import ChannelRegistry

try:
    from langchain_deepseek import ChatDeepSeek as _ChatDeepSeek
except ModuleNotFoundError:
    _ChatDeepSeek = None


if _ChatDeepSeek is not None:

    def _extract_deepseek_reasoning_content(message: AIMessage) -> str | None:
        """Return DeepSeek thinking content from known LangChain storage shapes."""
        for source in (message.additional_kwargs, message.response_metadata):
            for key in ("reasoning_content", "reasoning"):
                value = source.get(key)
                if isinstance(value, str):
                    return value

        if isinstance(message.content, list):
            parts: list[str] = []
            for block in message.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in {
                    "reasoning",
                    "reasoning_content",
                    "reasoning_content_delta",
                }:
                    continue
                text = (
                    block.get("reasoning_content")
                    or block.get("reasoning")
                    or block.get("text")
                    or block.get("content")
                )
                if isinstance(text, str):
                    parts.append(text)
            if parts:
                return "".join(parts)

        return None


    class DeepSeekReasoningChat(_ChatDeepSeek):
        """DeepSeek chat model that preserves reasoning_content across tool loops."""

        def _get_request_payload(
            self,
            input_: LanguageModelInput,
            *,
            stop: list[str] | None = None,
            **kwargs: Any,
        ) -> dict:
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            source_messages = convert_to_messages(input_)
            payload_messages = payload.get("messages", [])
            if len(source_messages) == len(payload_messages):
                pairs = zip(source_messages, payload_messages, strict=False)
            else:
                source_ai_messages = (
                    message
                    for message in source_messages
                    if isinstance(message, AIMessage)
                )
                pairs = (
                    (next(source_ai_messages, None), target)
                    for target in payload_messages
                    if target.get("role") == "assistant"
                )
            for source, target in pairs:
                if (
                    not isinstance(source, AIMessage)
                    or target.get("role") != "assistant"
                ):
                    continue
                reasoning_content = _extract_deepseek_reasoning_content(source)
                if reasoning_content is not None:
                    target["reasoning_content"] = reasoning_content
            return payload

else:

    class DeepSeekReasoningChat:  # type: ignore[no-redef]
        """Placeholder used when langchain-deepseek is not installed."""


def default_agent_model_selection(registry: ChannelRegistry) -> dict[str, str]:
    return registry.default_selection()


def resolve_agent_model_selection(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate `selection` against `registry`. Back-fill `channel="zenmux"` for
    legacy `{provider, model}` rows. Raise ValueError on unknown selections."""
    if selection is None:
        return registry.default_selection()

    channel = str(selection.get("channel") or "zenmux")
    provider = str(selection.get("provider", ""))
    model = str(selection.get("model", ""))
    try:
        registry.find_model(channel, provider, model)
    except KeyError as exc:
        raise ValueError(
            f"unsupported agent model selection {channel}:{provider}:{model}"
        ) from exc
    return {"channel": channel, "provider": provider, "model": model}


def build_agent_model(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> BaseChatModel | None:
    if selection is None:
        selection = registry.default_selection()
    channel, model_desc = registry.find_model(
        str(selection["channel"]), str(selection["provider"]), str(selection["model"])
    )
    if not channel.healthy:
        return None  # caller renders "agent disabled"

    # Route by WIRE PROTOCOL, not provider: a model whose provider is "openai"
    # (its ZenMux gateway label) but which emits Anthropic-format tool calls
    # (e.g. minimax) declares protocol="anthropic" and must be dispatched through
    # the Anthropic endpoint, or its tool calls leak into text as unparsed markup.
    if channel.type == "zenmux" and model_desc.wire_protocol == "anthropic":
        from langchain_anthropic import ChatAnthropic
        assert channel.anthropic_base_url is not None  # validated at load
        return ChatAnthropic(
            model_name=model_desc.id,
            api_key=SecretStr(channel.api_key or ""),
            base_url=channel.anthropic_base_url,
            default_headers={"anthropic-version": "2023-06-01"},
            timeout=None,
            stop=None,
    )

    if model_desc.provider == "deepseek":
        if _ChatDeepSeek is None:
            raise RuntimeError(
                "DeepSeek model selected but langchain-deepseek is not installed. "
                "Install project dependencies with `uv sync --extra dev` or run the "
                "backend through the project `.venv`."
            )
        return DeepSeekReasoningChat(
            model=model_desc.id,
            api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
            base_url=channel.base_url,
        )

    from langchain_openai import ChatOpenAI
    # stream_usage=True sends OpenAI's stream_options={"include_usage": true}, so
    # the final streamed chunk carries token usage. Without it, OpenAI-compatible
    # streaming (the arena's path) drops usage entirely and the tracer records
    # zero tokens for every non-Anthropic model. With it, usage_metadata flows into
    # the trace token columns (prompt/completion/total) exactly like ChatAnthropic,
    # giving exact, run-isolated per-match token counts — no external billing API
    # needed. (ChatAnthropic already reports usage natively.)
    return ChatOpenAI(
        model=model_desc.id,
        api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
        base_url=channel.base_url,
        stream_usage=True,
    )


def agent_model_config(registry: ChannelRegistry) -> dict[str, object]:
    active = registry.default_selection()
    enabled = any(ch.healthy for ch in registry.channels)
    channels_payload: list[dict[str, object]] = []
    for ch in registry.channels:
        models_payload: list[dict[str, object]] = []
        for md in ch.models:
            models_payload.append({
                "channel": ch.name,
                "provider": md.provider,
                "model": md.id,
                "label": md.label,
                "description": md.description,
                "tags": list(md.tags),
                "is_default": (
                    ch.name == active["channel"]
                    and md.provider == active["provider"]
                    and md.id == active["model"]
                ),
            })
        channels_payload.append({
            "name": ch.name,
            "label": ch.label,
            "type": ch.type,
            "healthy": ch.healthy,
            "models": models_payload,
        })
    return {
        "enabled": enabled,
        "active": active,
        "channels": channels_payload,
    }


def agent_registry_config(registry: ChannelRegistry) -> dict[str, object]:
    """Maintenance view: full editable fields incl. api_key_env (read from raw
    YAML, since the dataclass keeps only the derived api_key/healthy)."""
    import yaml as _yaml

    from . import channel_registry as _cr

    raw = _yaml.safe_load(_cr._yaml_path().read_text()) or {}
    api_key_env_by_channel: dict[str, str | None] = {}
    for entry in raw.get("channels") or []:
        if isinstance(entry, dict) and entry.get("name"):
            api_key_env_by_channel[entry["name"]] = entry.get("api_key_env")

    # Report the DECLARED default (what is persisted in the YAML), not the
    # resolved registry.default — the loader silently redirects the resolved
    # default away from an unhealthy channel, which would make the UI show a
    # different default than the file holds and let the agent "switch" later
    # when the api_key_env var returns. The declared default is the truth the
    # maintenance UI must edit.
    raw_default = raw.get("default")
    if isinstance(raw_default, dict) and raw_default.get("channel") and raw_default.get("model"):
        ch_name = raw_default["channel"]
        model_id = raw_default["model"]
    else:
        ch_name, _prov, model_id = registry.default
    channels_payload: list[dict[str, object]] = []
    for ch in registry.channels:
        channels_payload.append({
            "name": ch.name,
            "label": ch.label,
            "type": ch.type,
            "base_url": ch.base_url,
            "anthropic_base_url": ch.anthropic_base_url,
            "api_key_env": api_key_env_by_channel.get(ch.name),
            "healthy": ch.healthy,
            "models": [
                {
                    "id": md.id,
                    "provider": md.provider,
                    "label": md.label,
                    "description": md.description,
                    "tags": list(md.tags),
                    "protocol": md.protocol or None,
                }
                for md in ch.models
            ],
        })
    return {
        "default": {"channel": ch_name, "model": model_id},
        "channels": channels_payload,
    }
