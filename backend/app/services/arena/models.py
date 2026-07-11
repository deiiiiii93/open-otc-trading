"""Arena model registry.

Defines candidate models for the LLM arena and provides lookup helpers.

Slugs are filesystem/URL-safe: lowercase, dashes only, no slashes or dots.
Zenmux names use the vendor/model-name convention used by Zenmux routing.

At module load the registry validates uniqueness of both slugs and zenmux_names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArenaModel:
    """Immutable descriptor for a candidate model in the arena.

    Attributes:
        slug: Filesystem/URL-safe identifier (lowercase, dashes only).
        zenmux_name: The model name as Zenmux routes it (e.g. "openai/gpt-5.5").
        display_name: Human-readable label shown in the UI.
        default_config: Fallback inference params (temperature, max_tokens, …).
        provider: Desk-channel dispatch label ("anthropic" or "openai"). Empty
            means "derive from the zenmux_name vendor prefix" — correct only when
            that prefix IS the dispatch label (anthropic/openai). Third-party
            vendors routed through Zenmux's OpenAI-compatible gateway (GLM, Kimi,
            MiniMax, MiMo, DeepSeek, Qwen, …) carry their real vendor in the
            zenmux_name but MUST set provider="openai" so the selection matches
            the channel-registry entry in config/agent_channels.yaml.
    """

    slug: str
    zenmux_name: str
    display_name: str
    default_config: dict[str, Any]
    provider: str = ""


def _index_models(
    models: list[ArenaModel],
) -> tuple[dict[str, ArenaModel], dict[str, str]]:
    """Build lookup maps from a list of ArenaModel instances.

    Returns:
        (by_slug, canonical_map) where canonical_map[key] = slug for both slug
        and zenmux_name keys.

    Raises:
        ValueError: if any two models share a slug or a zenmux_name.
    """
    by_slug: dict[str, ArenaModel] = {}
    canonical_map: dict[str, str] = {}

    for m in models:
        if m.slug in by_slug:
            raise ValueError(
                f"Duplicate slug in model registry: '{m.slug}' — "
                "each model must have a unique slug."
            )
        if m.zenmux_name in canonical_map:
            raise ValueError(
                f"Duplicate zenmux_name in model registry: '{m.zenmux_name}' — "
                "each model must have a unique zenmux_name."
            )
        by_slug[m.slug] = m
        canonical_map[m.slug] = m.slug
        canonical_map[m.zenmux_name] = m.slug

    return by_slug, canonical_map


# ---------------------------------------------------------------------------
# Seed registry — candidate models
#
# Anthropic/OpenAI models let the dispatch label fall out of the zenmux_name
# vendor prefix (provider=""). Third-party vendors route through Zenmux's
# OpenAI-compatible gateway, so they pin provider="openai" explicitly and need a
# matching entry in config/agent_channels.yaml to be dispatchable in a live run.
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {"temperature": 0, "max_tokens": 4096}

CANDIDATE_MODELS: list[ArenaModel] = [
    ArenaModel(
        slug="gpt-5-5",
        zenmux_name="openai/gpt-5.5",
        display_name="GPT-5.5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-6-terra",
        zenmux_name="openai/gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-6-luna",
        zenmux_name="openai/gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-opus-4-8",
        zenmux_name="anthropic/claude-opus-4.8",
        display_name="Claude Opus 4.8",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-sonnet-4-6",
        zenmux_name="anthropic/claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-sonnet-5",
        zenmux_name="anthropic/claude-sonnet-5",
        display_name="Claude Sonnet 5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-2-5-pro",
        zenmux_name="google/gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        default_config=_DEFAULT_CONFIG,
        provider="openai",  # routed via Zenmux's OpenAI-compatible gateway
    ),
    ArenaModel(
        slug="gemini-3-1-pro",
        zenmux_name="google/gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro",
        default_config=_DEFAULT_CONFIG,
        provider="openai",  # routed via Zenmux's OpenAI-compatible gateway
    ),
    # --- Zenmux-routed third-party vendors (OpenAI-compatible gateway) ---
    ArenaModel(
        slug="glm-5-2",
        zenmux_name="z-ai/glm-5.2",
        display_name="GLM 5.2",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="kimi-2-7",
        zenmux_name="moonshotai/kimi-k2.7-code",
        display_name="Kimi 2.7",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        # provider stays "openai" (the ZenMux gateway routing label), but minimax
        # emits Anthropic-format tool calls, so config/agent_channels.yaml pins
        # protocol: anthropic to dispatch it via the Anthropic endpoint. Without
        # that, its tool calls leak into text as <invoke …> markup and never run.
        slug="minimax-m3",
        zenmux_name="minimax/minimax-m3",
        display_name="MiniMax M3",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="mimo-2-5-pro",
        zenmux_name="xiaomi/mimo-v2.5-pro",
        display_name="MiMo V2.5 Pro",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="deepseek-v4-pro",
        zenmux_name="deepseek/deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        # provider stays "openai", but qwen's tool-call id arrives empty through the
        # OpenAI-compatible gateway in the full agent flow, breaking subagent (task)
        # dispatch; config/agent_channels.yaml pins protocol: anthropic (server-side
        # toolu_ ids) to fix it. See minimax-m3 above for the protocol mechanism.
        slug="qwen-3-7-max",
        zenmux_name="qwen/qwen3.7-max",
        display_name="Qwen 3.7 Max",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    # --- Run #9 candidate field: flash-tier models (all Zenmux OpenAI-compat) ---
    ArenaModel(
        slug="doubao-seed-evolving",
        zenmux_name="bytedance/doubao-seed-evolving",
        display_name="Doubao Seed Evolving",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        # Sibling Doubao route used to obtain a functional result when the
        # doubao-seed-evolving route is infrastructure-censored (Run #9).
        slug="doubao-seed-2-1-turbo",
        zenmux_name="bytedance/doubao-seed-2.1-turbo",
        display_name="Doubao Seed 2.1 Turbo",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="qwen-3-7-plus",
        zenmux_name="qwen/qwen3.7-plus",
        display_name="Qwen 3.7 Plus",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="step-3-7-flash",
        zenmux_name="stepfun/step-3.7-flash",
        display_name="Step 3.7 Flash",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="gemini-3-5-flash",
        zenmux_name="google/gemini-3.5-flash",
        display_name="Gemini 3.5 Flash",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="gpt-5-5-instant",
        zenmux_name="openai/chat-latest",
        display_name="GPT-5.5 Instant",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="deepseek-v4-flash",
        zenmux_name="deepseek/deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="mimo-2-5",
        zenmux_name="xiaomi/mimo-v2.5",
        display_name="MiMo V2.5",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="hunyuan-3-preview",
        zenmux_name="tencent/hy3-preview",
        display_name="Hunyuan 3 Preview",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        # provider stays "openai" (the ZenMux gateway routing label), but longcat
        # emits tool calls as <longcat_tool_call> markup the OpenAI-compatible
        # gateway leaves unparsed (they leak into text → zero tools → arena floor),
        # so config/agent_channels.yaml pins protocol: anthropic to dispatch it via
        # the Anthropic endpoint. See minimax-m3 / qwen-3-7-max above.
        slug="longcat-2-0",
        zenmux_name="meituan/longcat-2.0",
        display_name="LongCat 2.0",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
    ArenaModel(
        slug="grok-4-5",
        zenmux_name="x-ai/grok-4.5",
        display_name="Grok 4.5",
        default_config=_DEFAULT_CONFIG,
        provider="openai",
    ),
]

# Build module-level maps (validates uniqueness at import time)
_BY_SLUG, _CANONICAL_MAP = _index_models(CANDIDATE_MODELS)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def canonical_model_id(s: str) -> str:
    """Resolve a slug OR a zenmux_name to the canonical slug.

    Args:
        s: Either a slug (e.g. "gpt-5-5") or a zenmux_name
           (e.g. "openai/gpt-5.5").

    Returns:
        The canonical slug string.

    Raises:
        KeyError: if *s* does not match any registered slug or zenmux_name.
    """
    try:
        return _CANONICAL_MAP[s]
    except KeyError:
        raise KeyError(
            f"Unknown model id '{s}'. "
            f"Known slugs/names: {sorted(_CANONICAL_MAP)}"
        ) from None


def get_model(s: str) -> ArenaModel:
    """Return the ArenaModel for a slug or zenmux_name.

    Raises:
        KeyError: if *s* is not found.
    """
    return _BY_SLUG[canonical_model_id(s)]


def arena_model_to_selection(model: ArenaModel) -> dict[str, str]:
    """Map an ArenaModel's zenmux_name to a desk model_selection dict.

    "openai/gpt-5.5" -> {"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.5"}

    The desk channel registry (config/agent_channels.yaml) keys Zenmux models by
    their FULL id (e.g. ``openai/gpt-5.5``), so ``model`` carries the whole
    zenmux_name. ``provider`` is the desk dispatch label: an explicit
    ``model.provider`` when set (third-party vendors routed through Zenmux's
    OpenAI-compatible gateway pin "openai"), otherwise the vendor prefix of the
    zenmux_name (correct when that prefix is itself "anthropic"/"openai").
    Returning a stripped model id, or a provider that disagrees with the YAML
    entry, would fail ``resolve_agent_model_selection`` before any turn is driven.

    Raises:
        ValueError: if zenmux_name does not contain a '<vendor>/<model>' slash.
    """
    name = model.zenmux_name
    if "/" not in name:
        raise ValueError(
            f"zenmux_name '{name}' must be '<vendor>/<model>' (e.g. 'openai/gpt-5.5')."
        )
    provider = model.provider or name.split("/", 1)[0]
    return {"channel": "zenmux", "provider": provider, "model": name}


def validate_model_ids(ids: list[str]) -> list[str]:
    """Canonicalize a list of model identifiers (slugs or zenmux_names).

    Each element may be a slug or a zenmux_name; all are resolved to slugs.
    Duplicates are preserved (caller decides whether to deduplicate).

    Returns:
        A list of canonical slugs in the same order as *ids*.

    Raises:
        ValueError: listing all unknown ids if any are not found in the registry.
    """
    unknown = [s for s in ids if s not in _CANONICAL_MAP]
    if unknown:
        raise ValueError(
            f"Unknown model id(s): {unknown}. "
            f"Known: {sorted(k for k in _CANONICAL_MAP if '/' not in k)}"
        )
    return [canonical_model_id(s) for s in ids]
