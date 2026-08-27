"""Pluggable model factory for the desk deep agent.

Consults the channel registry (see ``channel_registry.py``) to resolve a
``(channel, provider, model)`` triple into a concrete LangChain chat model.
Returns ``None`` when the selected channel is unhealthy so AgentService can
render the "agent disabled" stub without raising.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, convert_to_messages
from pydantic import SecretStr

from app.config import get_settings

from . import reasoning_capabilities
from .channel_registry import ChannelRegistry

logger = logging.getLogger(__name__)

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


# Every effort token any routable model is known to accept, ordered weakest →
# strongest. This is the OUTER BOUND used only when a model's own ladder is
# unknown; the authoritative per-model answer comes from
# `reasoning_capabilities` (a vendored models.dev snapshot), because **there is
# no universal ladder** — most models take low/medium/high, the GPT-5.6 family
# takes none…max, GLM-5.2 takes only high/max, and MiniMax/MiMo/Qwen3.7 have no
# ladder at all (reasoning is a bare on/off toggle).
#
# ZenMux's own API cannot answer this: `GET /api/v1/models` reports only
# `capabilities.reasoning: true|false`, with no effort enumeration.
#
# Validation exists at all because an unrecognised value is forwarded to the
# provider and typically IGNORED, so a misspelling reads as "effort had no
# effect" rather than "effort was never applied". A loud rejection lets the
# caller retry — the same reason PositionLifecycleReferenceInput sets
# extra="forbid". Deliberately NOT gated on the desk registry's `reasoning` tag:
# that tag is hand-maintained and only 13 of 32 models carry it (openai/gpt-5.5,
# a reasoning model, does not), so gating on it would falsely block real models.
VALID_REASONING_EFFORTS = (
    "none", "minimal", "low", "medium", "high", "xhigh", "max",
)


def normalize_reasoning_effort(value: object) -> str | None:
    """Canonicalize a requested reasoning effort, or None when unset.

    Empty/None means "unset" — the caller sends no `reasoning_effort` at all and
    the vendor default applies, which is what every turn did before this existed.

    The STRING ``"none"`` is a different thing and is preserved: it explicitly
    asks the model to skip reasoning, which is a request that gets sent, not an
    absence of one. Collapsing it to unset would silently turn "no thinking"
    into "vendor default thinking".
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in VALID_REASONING_EFFORTS:
        raise ValueError(
            f"unsupported reasoning_effort {text!r}; "
            f"expected one of {list(VALID_REASONING_EFFORTS)}"
        )
    return text


def effort_rejection(
    registry: ChannelRegistry, channel: str, provider: str, model: str, effort: str
) -> str | None:
    """Why `effort` cannot be honoured on this route, or None if it can.

    The SINGLE seam for that question, so every caller agrees — used by
    `resolve_agent_model_selection`, `queue_arena_run` and both UI ladder builders.

    **Protocol is not a blocker; it is a different mechanism.** An earlier version
    refused every anthropic-routed model on the belief that the protocol "budgets
    thinking in tokens rather than an effort level". Measured, that is wrong: the
    Anthropic Messages API takes **`output_config.effort`** — a named level
    (low/medium/high/xhigh/max) — and langchain's ChatAnthropic exposes it. That
    blanket refusal wrongly denied effort to 7 of 8 anthropic-routed models,
    including the four that are not Claude at all (`z-ai/glm-5.2`,
    `minimax/minimax-m3`, `qwen/qwen3.7-max`, `meituan/longcat-2.0`).

    So the only question left is whether the MODEL accepts the level, which the
    measured ladder answers for both protocols. `claude-haiku-4.5` is the one real
    "no effort" case — it rejects every level, because it does not reason.
    """
    try:
        _channel, model_desc = registry.find_model(channel, provider, model)
    except KeyError:
        return None  # unknown selection; the caller's own validation reports it
    # Resolve to the PINNED id before asking: the ladder is measured per route,
    # and a selection may name the bare id while the registry pins an upstream.
    return reasoning_capabilities.rejection_reason(channel, model_desc.wire_id, effort)


def resolve_agent_model_selection(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate `selection` against `registry`. Back-fill `channel="zenmux"` for
    legacy `{provider, model}` rows. Raise ValueError on unknown selections.

    An optional `reasoning_effort` key rides along with the triple. It is
    **omitted entirely when unset**, never emitted as an explicit ``None``: the
    resolved dict is compared by equality against
    ``AgentService.default_model_selection`` to decide whether the prebuilt
    orchestrator can be reused, so a fourth key present on every turn would stop
    that reuse for every default turn. Omitted-when-unset also means an
    effort-pinned selection is *by construction* unequal to the default, which
    correctly forces a per-turn model build that can apply the effort.
    """
    if selection is None:
        return registry.default_selection()

    channel = str(selection.get("channel") or "zenmux")
    provider = str(selection.get("provider", ""))
    model = str(selection.get("model", ""))
    try:
        _, model_desc = registry.find_model(channel, provider, model)
    except KeyError as exc:
        raise ValueError(
            f"unsupported agent model selection {channel}:{provider}:{model}"
        ) from exc
    resolved = {"channel": channel, "provider": provider, "model": model}

    effort = normalize_reasoning_effort(selection.get("reasoning_effort"))
    if effort is not None:
        # Refuse rather than drop: a forwarded-and-ignored effort reports itself as
        # applied, and a silently-failing instrument is worse than none.
        reason = effort_rejection(registry, channel, provider, model, effort)
        if reason is not None:
            raise ValueError(f"unsupported reasoning_effort: {reason}")
        resolved["reasoning_effort"] = effort

    # Output budget rides the same carrier as effort — the model-selection dict —
    # and is OMITTED when unset for the same load-bearing reason: the resolved
    # dict is compared by equality against AgentService.default_model_selection
    # to decide whether the prebuilt orchestrator can be reused, so a key present
    # on every turn (even as None) would silently end that reuse.
    budget = normalize_max_output_tokens(selection.get("max_output_tokens"))
    if budget is not None:
        resolved["max_output_tokens"] = budget
    return resolved


def normalize_max_output_tokens(raw: object) -> int | None:
    """Validate an output-token budget; None means unset (use the default).

    Refuses rather than clamps. A budget is the independent variable of a budget
    study, so a value silently coerced into something else would report a regime
    that never ran — the same reason an unrecognised reasoning effort is refused
    instead of forwarded.
    """
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"max_output_tokens must be an integer, got {raw!r}")
    if value <= 0:
        # 0 is the DB sentinel for "unpinned" and never a real budget; a
        # negative one is nonsense. Both would otherwise reach the provider.
        raise ValueError(
            f"max_output_tokens must be a positive integer, got {value}"
        )
    return value


def _reasoning_effort_override() -> str | None:
    """Process-wide fallback `reasoning_effort`, from the environment.

    UNSET (the default) reproduces the original behaviour exactly — no
    `reasoning_effort` is sent and the vendor's own default applies, which is what
    every arena board from run #8 onward used.

    This is the LOWEST-precedence source: an explicit per-turn / per-run effort on
    the model selection wins over it, mirroring the gateway bridge's
    explicit-arg → settings → process-env → default ladder. Its remaining use is
    sweeping a whole process (a controlled A/B) without touching each caller.
    """
    value = (os.getenv("OPEN_OTC_MODEL_REASONING_EFFORT") or "").strip().lower()
    if not value:
        return None
    # Validate the spelling here too. Unvalidated, a typo was forwarded verbatim to
    # the provider, which ignores it — so a mistyped sweep looked like "effort had
    # no effect on this model" and would have been read as a finding.
    return normalize_reasoning_effort(value)


def _max_output_tokens_for(selection: Mapping[str, str] | None) -> int | None:
    """A budget pinned on the selection, else None (caller applies its default).

    Explicit-selection-first, exactly like _effort_for. This is what makes an
    output budget a per-ARM setting rather than a process-wide one: the arena
    runs two budgets inside a single run, so a process env var could not express
    the board at all — and, being env-only, it silently survived a resume at the
    wrong value.
    """
    return normalize_max_output_tokens((selection or {}).get("max_output_tokens"))


def _effort_for(
    selection: Mapping[str, str] | None,
    channel_name: str,
    model_id: str,
) -> str | None:
    """Resolve the effort to send: explicit selection first, then the env sweep.

    An EXPLICIT effort is already validated against this model by
    `resolve_agent_model_selection`, which refuses rather than drops. The env sweep
    has no such boundary — it is process-wide and blunt — so it is filtered here
    against the model's own ladder and **skipped with a warning** for a model that
    has none. Sending it anyway was the original bug: a toggle-only model
    (Qwen3.7, MiMo) and a non-reasoning one (kimi-k2.7-code) both received
    `reasoning_effort=high`, which the provider ignores, so an A/B sweep would have
    silently included models that never varied.
    """
    explicit = normalize_reasoning_effort((selection or {}).get("reasoning_effort"))
    if explicit is not None:
        return explicit

    swept = _reasoning_effort_override()
    if swept is None:
        return None
    reason = reasoning_capabilities.rejection_reason(channel_name, model_id, swept)
    if reason is not None:
        logger.warning(
            "OPEN_OTC_MODEL_REASONING_EFFORT=%s not applied to %s — %s",
            swept, model_id, reason,
        )
        return None
    return swept


def _zenmux_responses_chat(**kwargs: Any) -> BaseChatModel:
    """ChatOpenAI on the Responses API, with a ZenMux payload workaround.

    ZenMux silently DROPS the entire ``tools`` array when an input item is exactly
    ``{"type": "message", "content": "<string>"}`` — the shape langchain-openai
    emits. The model then answers in prose with no error and no tool call, which is
    indistinguishable from a model that chose not to use its tools. Measured
    2026-08-21: 0/4 tool calls in that shape versus 4/4 once the string is promoted
    to a content-parts list. Every other input shape already works, including
    ``type: "message"`` WITH content parts, so the fix is narrow and additive.

    This is a workaround for a third-party bug at the hottest seam in the system.
    If ZenMux fixes it the normalizer becomes a no-op, not a breakage — promoting a
    string to its equivalent parts list is semantically identical either way.
    """
    from langchain_openai import ChatOpenAI

    class _ZenmuxResponsesChat(ChatOpenAI):  # type: ignore[misc]
        def _get_request_payload(
            self, input_: LanguageModelInput, **kw: Any
        ) -> dict[str, Any]:
            payload = super()._get_request_payload(input_, **kw)
            items = payload.get("input")
            if isinstance(items, list):
                for item in items:
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "message"
                        and isinstance(item.get("content"), str)
                    ):
                        kind = (
                            "output_text"
                            if item.get("role") == "assistant"
                            else "input_text"
                        )
                        item["content"] = [{"type": kind, "text": item["content"]}]
            return payload

    return _ZenmuxResponsesChat(**kwargs)


def _forced_zenmux_protocol() -> str:
    """Process-wide protocol override for the protocol A/B study.

    Env-only and deliberately un-persisted, exactly like the output budget was
    before migration 0059 — WITH THE SAME CAVEAT: a resume that omits it finishes
    the run on a DIFFERENT protocol than it started, and nothing in the stored data
    would reveal that. If the study finds a real protocol effect, protocol must be
    promoted to part of the contestant key (as effort and budget already were)
    rather than left here.
    """
    from .channel_registry import canonical_protocol

    return canonical_protocol(os.getenv("OPEN_OTC_ZENMUX_FORCE_PROTOCOL"))


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

    # Route by PROTOCOL, never by provider. `provider` names the ZenMux UPSTREAM
    # (whose metal serves the request); the wire format is an independent axis,
    # and a model may need one that its vendor's own API would not imply — e.g.
    # minimax emits Anthropic-format tool calls that the OpenAI-compatible
    # endpoint leaves unparsed, so it declares protocol: anthropic.
    protocol = model_desc.protocol
    if channel.type == "zenmux":
        forced = _forced_zenmux_protocol()
        if forced and forced != protocol:
            logger.warning(
                "OPEN_OTC_ZENMUX_FORCE_PROTOCOL=%s overrides %s for %s",
                forced, protocol, model_desc.id,
            )
            protocol = forced

    if channel.type == "zenmux" and protocol == "openai_responses":
        # Effort rides `reasoning_effort`, which langchain maps to the Responses
        # API's `reasoning: {effort: ...}` — verified against the wire payload.
        # No max_output_tokens: same reasoning as the chat-completions branch
        # below, let the provider apply the model's own ceiling.
        return _zenmux_responses_chat(
            model=model_desc.wire_id,
            api_key=SecretStr(channel.api_key or ""),
            base_url=channel.base_url,
            use_responses_api=True,
            **({"reasoning_effort": _effort_for(
                selection, channel.name, model_desc.id)}
               if _effort_for(selection, channel.name, model_desc.id) else {}),
        )

    if channel.type == "zenmux" and protocol == "anthropic":
        # Effort on this protocol is `output_config.effort` — a NAMED level, not the
        # OpenAI `reasoning_effort` field and not the older `thinking.budget_tokens`
        # budget. Sent through `output_config` rather than ChatAnthropic's `effort=`
        # shorthand because that shorthand is typed to Claude's own ladder
        # (low..max), while the gateway accepts `none`/`minimal` for the non-Claude
        # models routed here (glm-5.2, minimax-m3, longcat-2.0) — measured.
        anth_effort = _effort_for(selection, channel.name, model_desc.id)
        from langchain_anthropic import ChatAnthropic
        assert channel.anthropic_base_url is not None  # validated at load
        # max_tokens is EXPLICIT because langchain-anthropic otherwise applies
        # `_FALLBACK_MAX_OUTPUT_TOKENS` (4096) whenever it has no profile for the
        # model id — and it has none for ANY id routed here, `anthropic/…` ones
        # included, because the ZenMux vendor prefix defeats its lookup. That cap
        # silenced reasoning models mid-thought: a truncated turn emits a lone
        # `reasoning` block with no text and no tool call, so the turn produces
        # NOTHING while the span still reports success. Arena run #114 lost 5 turns
        # that way (glm-5.3 truncated on 6-14% of calls per workflow; the
        # OpenAI-protocol baseline truncated 0 times in 349), and the harness's
        # infra-blank gate could not see it — it corroborates blankness with step
        # ERRORS, and there are none. The OpenAI branch below deliberately sends no
        # max_tokens at all, so it already gets the provider default; this keeps
        # the two protocols from handicapping each other.
        max_out = (
            _max_output_tokens_for(selection)
            or int(get_settings().agent_max_output_tokens)
        )
        return ChatAnthropic(
            model_name=model_desc.wire_id,
            api_key=SecretStr(channel.api_key or ""),
            base_url=channel.anthropic_base_url,
            default_headers={"anthropic-version": "2023-06-01"},
            timeout=None,
            stop=None,
            max_tokens=max_out,
            **({"output_config": {"effort": anth_effort}} if anth_effort else {}),
    )

    effort = _effort_for(selection, channel.name, model_desc.id)
    extra = {"reasoning_effort": effort} if effort else {}

    # `channel.type` guard is load-bearing since provider was redefined to mean
    # the UPSTREAM: the zenmux row `deepseek/deepseek-v4-flash` now also carries
    # provider="deepseek", and without this it would be built with ChatDeepSeek
    # against the ZenMux base_url — a client for the wrong API, silently.
    # Here `provider` is the SDK label, which only an openai_compatible channel
    # has: a vendor's own API has exactly one upstream, so there is nothing to
    # pin and the field is free to select the client.
    if channel.type == "openai_compatible" and model_desc.provider == "deepseek":
        if _ChatDeepSeek is None:
            raise RuntimeError(
                "DeepSeek model selected but langchain-deepseek is not installed. "
                "Install project dependencies with `uv sync --extra dev` or run the "
                "backend through the project `.venv`."
            )
        return DeepSeekReasoningChat(
            model=model_desc.wire_id,
            api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
            base_url=channel.base_url,
            **extra,
        )

    from langchain_openai import ChatOpenAI
    # stream_usage=True sends OpenAI's stream_options={"include_usage": true}, so
    # the final streamed chunk carries token usage. Without it, OpenAI-compatible
    # streaming (the arena's path) drops usage entirely and the tracer records
    # zero tokens for every non-Anthropic model. With it, usage_metadata flows into
    # the trace token columns (prompt/completion/total) exactly like ChatAnthropic,
    # giving exact, run-isolated per-match token counts — no external billing API
    # needed. (ChatAnthropic already reports usage natively.)
    # UNSET by default, and that is the correct default: sending no max_tokens
    # lets the provider apply the model's own ceiling, which is what "let every
    # model score at its best" means here. Pinning a number would CAP models whose
    # native ceiling is higher — the Anthropic-path bug in reverse. It exists as an
    # opt-in solely so a budget can be varied deliberately (the effort-vs-budget
    # study), never as a production default.
    # A budget PINNED on the selection applies to both protocols. Without this
    # branch a budget arm would be silently anthropic-only, so an OpenAI-protocol
    # contestant would run at its provider default under both arms and report a
    # null result as if the budget had been tested.
    openai_max_out = (
        _max_output_tokens_for(selection)
        or get_settings().agent_openai_max_output_tokens
    )
    if openai_max_out:
        extra["max_tokens"] = int(openai_max_out)

    return ChatOpenAI(
        model=model_desc.wire_id,
        api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
        base_url=channel.base_url,
        stream_usage=True,
        **extra,
    )


def agent_model_config(registry: ChannelRegistry) -> dict[str, object]:
    active = registry.default_selection()
    enabled = any(ch.healthy for ch in registry.channels)
    channels_payload: list[dict[str, object]] = []
    for ch in registry.channels:
        models_payload: list[dict[str, object]] = []
        for md in ch.models:
            # Per-model effort ladder, mirroring EXACTLY what the server accepts,
            # or the UI either offers a level that fails at send time or hides one
            # that works. Both protocols go through the same measured data — an
            # anthropic-routed model carries effort via output_config.effort and is
            # NOT excluded (only claude-haiku-4.5 genuinely accepts no level).
            #   measured           → the probed ladder (the only data we gate on)
            #   unmeasured/unknown → the outer bound, matching the permissive
            #                        server fallback; narrowing to an unverified
            #                        models.dev ladder would hide working levels.
            # Keyed on the PINNED id: a ladder is a property of the route
            # (channel + upstream + model), and a pinned route is a different
            # key from its unpinned twin. `effort_support` falls back to the
            # bare id when the pinned one has not been probed.
            support = reasoning_capabilities.effort_support(ch.name, md.wire_id)
            efforts = (
                list(support.efforts) if support is not None and support.measured
                else list(VALID_REASONING_EFFORTS)
            )
            models_payload.append({
                "channel": ch.name,
                "provider": md.provider,
                "model": md.id,
                "label": md.label,
                "description": md.description,
                "tags": list(md.tags),
                "reasoning_efforts": efforts,
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
                    # The three routing axes, as the YAML now declares them.
                    "id": md.id,              # bare model id
                    "provider": md.provider,  # ZenMux upstream (SDK label off-gateway)
                    "protocol": md.protocol or None,
                    # Derived, read-only: what actually goes on the wire. Shown so
                    # the console can display the pin without the editor having to
                    # recompose it — and so a legacy row reveals its real route.
                    "dispatch_id": md.wire_id,
                    "label": md.label,
                    "description": md.description,
                    "tags": list(md.tags),
                }
                for md in ch.models
            ],
        })
    return {
        "default": {"channel": ch_name, "model": model_id},
        "channels": channels_payload,
    }
