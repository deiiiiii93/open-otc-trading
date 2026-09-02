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
        zenmux_name: The model as Zenmux routes it, INCLUDING the upstream pin:
            "<owner>/<model>:<upstream>", e.g.
            "deepseek/deepseek-v4-flash:deepseek". The pin is part of the route,
            not decoration — ZenMux otherwise picks an upstream per request and
            names none of them in the response (2026-08-25).
        display_name: Human-readable label shown in the UI.
        default_config: Fallback inference params (temperature, max_tokens, …).
        provider: Legacy override for the desk selection's `provider` key, used
            only for an UNPINNED zenmux_name. When the name carries a `:upstream`
            pin the upstream is derived from it instead, so the two cannot drift.
            Since 2026-08-25 that key means the ZenMux UPSTREAM PROVIDER, not the
            old gateway routing label; the registry ignores it when resolving a
            selection, because a model id is already unique per channel.
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
# Every zenmux_name carries its `:upstream` pin, and the desk selection's
# `provider` is DERIVED from it — so no entry declares a provider of its own.
# Each still needs a matching entry in config/agent_channels.yaml (same bare id,
# same upstream) to be dispatchable in a live run; this list does not sync.
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {"temperature": 0, "max_tokens": 4096}

CANDIDATE_MODELS: list[ArenaModel] = [
    ArenaModel(
        slug="gpt-5-5",
        zenmux_name="openai/gpt-5.5:openai",
        display_name="GPT-5.5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-6-terra",
        zenmux_name="openai/gpt-5.6-terra:openai",
        display_name="GPT-5.6 Terra",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-6-luna",
        zenmux_name="openai/gpt-5.6-luna:openai",
        display_name="GPT-5.6 Luna",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-6-sol",
        zenmux_name="openai/gpt-5.6-sol:openai",
        display_name="GPT-5.6 Sol",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-opus-4-8",
        zenmux_name="anthropic/claude-opus-4.8:anthropic",
        display_name="Claude Opus 4.8",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-sonnet-4-6",
        zenmux_name="anthropic/claude-sonnet-4.6:anthropic",
        display_name="Claude Sonnet 4.6",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="claude-sonnet-5",
        zenmux_name="anthropic/claude-sonnet-5:anthropic",
        display_name="Claude Sonnet 5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-2-5-pro",
        zenmux_name="google/gemini-2.5-pro:google-vertex",
        display_name="Gemini 2.5 Pro",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-3-1-pro",
        zenmux_name="google/gemini-3.1-pro-preview:google-vertex",
        display_name="Gemini 3.1 Pro",
        default_config=_DEFAULT_CONFIG,
    ),
    # --- Zenmux-routed third-party vendors (OpenAI-compatible gateway) ---
    ArenaModel(
        slug="glm-5-2",
        zenmux_name="z-ai/glm-5.2:bigmodel",
        display_name="GLM 5.2",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="glm-5-3",
        zenmux_name="z-ai/glm-5.3:bigmodel",
        display_name="GLM 5.3",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="kimi-2-7",
        zenmux_name="moonshotai/kimi-k2.7-code:moonshotai",
        display_name="Kimi 2.7",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # provider stays "openai" (the ZenMux gateway routing label), but minimax
        # emits Anthropic-format tool calls, so config/agent_channels.yaml pins
        # protocol: anthropic to dispatch it via the Anthropic endpoint. Without
        # that, its tool calls leak into text as <invoke …> markup and never run.
        slug="minimax-m3",
        zenmux_name="minimax/minimax-m3:minimax",
        display_name="MiniMax M3",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="mimo-2-5-pro",
        zenmux_name="xiaomi/mimo-v2.5-pro:xiaomi",
        display_name="MiMo V2.5 Pro",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="deepseek-v4-pro",
        zenmux_name="deepseek/deepseek-v4-pro:deepseek",
        display_name="DeepSeek V4 Pro",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # provider stays "openai", but qwen's tool-call id arrives empty through the
        # OpenAI-compatible gateway in the full agent flow, breaking subagent (task)
        # dispatch; config/agent_channels.yaml pins protocol: anthropic (server-side
        # toolu_ ids) to fix it. See minimax-m3 above for the protocol mechanism.
        slug="qwen-3-7-max",
        zenmux_name="qwen/qwen3.7-max:alibaba",
        display_name="Qwen 3.7 Max",
        default_config=_DEFAULT_CONFIG,
    ),
    # --- Run #9 candidate field: flash-tier models (all Zenmux OpenAI-compat) ---
    ArenaModel(
        slug="doubao-seed-evolving",
        zenmux_name="bytedance/doubao-seed-evolving:volcengine",
        display_name="Doubao Seed Evolving",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # Sibling Doubao route used to obtain a functional result when the
        # doubao-seed-evolving route is infrastructure-censored (Run #9).
        slug="doubao-seed-2-1-turbo",
        zenmux_name="bytedance/doubao-seed-2.1-turbo:volcengine",
        display_name="Doubao Seed 2.1 Turbo",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # Frontier-tier Doubao sibling of the two flash routes above — added to
        # backfill the Run #20 (flagship) and Run #33 (trader-rfq) boards. Its
        # tool calls parse cleanly on the OpenAI-compatible gateway (live-probed:
        # non-empty call ids, no vendor markup), so unlike minimax-m3 /
        # qwen-3-7-max / longcat-2-0 it needs no protocol: anthropic pin.
        slug="doubao-seed-2-1-pro",
        zenmux_name="bytedance/doubao-seed-2.1-pro:volcengine",
        display_name="Doubao Seed 2.1 Pro",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="qwen-3-7-plus",
        zenmux_name="qwen/qwen3.7-plus:alibaba",
        display_name="Qwen 3.7 Plus",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="step-3-7-flash",
        zenmux_name="stepfun/step-3.7-flash:stepfun",
        display_name="Step 3.7 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-3-5-flash",
        zenmux_name="google/gemini-3.5-flash:google-vertex",
        display_name="Gemini 3.5 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-3-6-flash",
        zenmux_name="google/gemini-3.6-flash:google-vertex",
        display_name="Gemini 3.6 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-3-7-flash",
        zenmux_name="google/gemini-3.7-flash:google-vertex",
        display_name="Gemini 3.7 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gemini-3-8-flash",
        zenmux_name="google/gemini-3.8-flash:google-vertex",
        display_name="Gemini 3.8 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="gpt-5-5-instant",
        zenmux_name="openai/chat-latest:openai",
        display_name="GPT-5.5 Instant",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # DISTINCT CONTESTANT from the historical `deepseek-v4-flash` slug,
        # which ranked matches run on an UNPINNED id — i.e. whichever of the
        # five upstreams ZenMux happened to pick that request. That is not a
        # nuance: the same slug holds 93.6 (run #112, landed on :deepseek)
        # and 7.7 (runs #121/#122/#125, landed on :alibaba). Averaging those
        # would report a model that does not exist, so the pinned route
        # ranks under its own key and starts a fresh history.
        slug="deepseek-v4-flash-ds",
        # PROVIDER-PINNED. ZenMux serves this id from several upstreams and picks
        # per request, and the response body names none of them — so an unpinned
        # contestant is a routing lottery that cannot even be attributed after the
        # fact. Measured 2026-08-25: the `:alibaba` upstream emits empty-string
        # id/name in streaming tool-call continuation deltas, which langchain
        # merges over the real ones, so `task()` dispatches nothing and the match
        # scores the 7.7 floor (runs #121/#122/#125) — while run #112 scored 93.6
        # on the same unpinned id four days earlier. The slug is unchanged, so
        # matches before 2026-08-25 rank under this key having run on EITHER
        # upstream; treat them as a different regime.
        zenmux_name="deepseek/deepseek-v4-flash:deepseek",
        display_name="DeepSeek V4 Flash (:deepseek)",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # DeepSeek's experimental multimodal flash, onboarded 2026-08-28 as a
        # contestant on the confirmation-desk-day VISION board.
        #
        # A SEPARATE MODEL from deepseek-v4-flash-ds above, not an arm of it: the
        # gateway declares input_modalities ["image","text"] here and ["text"]
        # there. Its scores are therefore not comparable to that slug's history,
        # and the sibling must never be entered on a vision workflow -- the
        # `requires: [vision]` launch gate rejects it, by design.
        slug="deepseek-v4-flash-vision",
        # PROVIDER-PINNED, same reasoning as the sibling above: two upstreams
        # serve this id (`deepseek`, `tencent-cloud`) and an unpinned contestant
        # is a routing lottery the response body cannot even attribute after the
        # fact. Both upstreams probed clean on the streaming wire 2026-08-28
        # (113 / 131 tool-call continuation deltas, null identifiers, zero empty
        # strings) against a control that fragmented 104 -- so the instrument was
        # demonstrably not blind. Pinned to the model owner's own metal.
        zenmux_name="deepseek/deepseek-v4-flash-vision-exp:deepseek",
        display_name="DeepSeek V4 Flash Vision (exp)",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="mimo-2-5",
        zenmux_name="xiaomi/mimo-v2.5:xiaomi",
        display_name="MiMo V2.5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="hunyuan-3",
        zenmux_name="tencent/hy3:tencent-cloud",
        display_name="Hunyuan 3",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # provider stays "openai" (the ZenMux gateway routing label), but longcat
        # emits tool calls as <longcat_tool_call> markup the OpenAI-compatible
        # gateway leaves unparsed (they leak into text → zero tools → arena floor),
        # so config/agent_channels.yaml pins protocol: anthropic to dispatch it via
        # the Anthropic endpoint. See minimax-m3 / qwen-3-7-max above.
        slug="longcat-2-0",
        zenmux_name="meituan/longcat-2.0:longcat",
        display_name="LongCat 2.0",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="grok-4-5",
        zenmux_name="x-ai/grok-4.5:x-ai",
        display_name="Grok 4.5",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        slug="grok-4-6",
        zenmux_name="x-ai/grok-4.6:x-ai",
        display_name="Grok 4.6",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # Qwen's 27B tier, published 2026-08-21. Dispatched over the ANTHROPIC
        # wire format (pinned in agent_channels.yaml) like every other qwen route,
        # and that pin is MEASURED here rather than inherited from the sibling:
        # streaming the raw SSE deltas on 2026-08-25 showed the :alibaba upstream
        # sending id="" and name="" on 640 of 641 tool-call CONTINUATION deltas,
        # which langchain merges over the real id — so task() would dispatch
        # nothing and every match would score the 7.7 prohibition floor, exactly
        # as deepseek-v4-flash did on runs #121/#122/#125.
        # The defect belongs to the UPSTREAM, not the model: qwen3.7-plus:alibaba
        # shows 430 of 431, while deepseek-v4-flash:deepseek shows 778 of 778 null
        # and grok-4.6 has no continuations at all.
        # A single-shot NON-streaming probe cannot see any of this (3/3 clean on
        # this route, 2/2 clean on qwen3.7-max, the known-bad control) — which is
        # why the earlier "only a real match convicts" reading was too pessimistic:
        # a STREAMING delta probe convicts too, for the price of one call.
        # All seven effort levels live-probed accepted 2026-08-25; `max` re-checked
        # over the Anthropic endpoint, which returns a server-minted toolu_ id.
        slug="qwen-3-8-27b",
        zenmux_name="qwen/qwen3.8-27b:alibaba",
        display_name="Qwen 3.8 27B",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # Z.AI's flash tier for the 5.3 generation, published 2026-08-26. Roughly
        # 18x cheaper than glm-5.3 itself ($0.075/$0.25 vs $1.40/$4.40 per MTok),
        # 1M context, and vision-capable — a flash-tier contestant, not a cheap
        # stand-in for the pro model.
        # Dispatched over the ANTHROPIC wire format (pinned in
        # agent_channels.yaml) by INHERITANCE from the GLM family, because the
        # streaming delta probe cannot settle it either way here: on 2026-08-27
        # the :bigmodel upstream emitted the whole tool call in ONE delta (0
        # continuation deltas at an 8192-token budget; glm-5.3 identical), so
        # there is nothing to blank and the instrument is blind. 0 of 0 is NOT
        # MEASURED — never read it as a pass. Consistent with the family's real
        # signature, which is a blanked id on the FIRST delta under full
        # orchestrator load and reproduces on no isolated probe.
        # Effort ladder MEASURED 2026-08-27 and deliberately SPARSE: the upstream
        # accepts low/high/max and rejects none/minimal/medium/xhigh, declaring so
        # itself ("请使用 low、high 或 max"). glm-5.2 accepts all seven, so this
        # could not have been inherited — and the hole in the MIDDLE of the ladder
        # is why a board must pin per model, never per run.
        slug="glm-5-3-flash",
        zenmux_name="z-ai/glm-5.3-flash:bigmodel",
        display_name="GLM 5.3 Flash",
        default_config=_DEFAULT_CONFIG,
    ),
    ArenaModel(
        # Qwen's flash tier for the 3.8 generation, published 2026-08-27. About 6x
        # cheaper on completion than qwen3.8-27b ($0.16/$0.47 vs $0.50/$3.00 per
        # MTok), 1M context, vision-capable.
        # Dispatched over the ANTHROPIC wire format (pinned in
        # agent_channels.yaml), MEASURED on this exact route rather than inherited:
        # streaming the raw SSE deltas over openai_chat on 2026-08-27 showed the
        # :alibaba upstream sending id="" on 263 of 263 tool-call CONTINUATION
        # deltas. langchain merges those over the real id from the first delta, so
        # task() would dispatch nothing and every match would score the 7.7
        # prohibition floor — exactly deepseek-v4-flash on runs #121/#122/#125.
        # The known-bad control reproduced in the same sweep (qwen3.8-27b: 240 of
        # 240), which is what makes this a measurement and not just a number.
        # Effort ladder MEASURED 2026-08-27: low/medium/high/xhigh/max; none and
        # minimal are rejected.
        # Probing note: this upstream refuses `tool_choice: "required"` in thinking
        # mode, so a probe has to steer the tool call from the prompt instead.
        slug="qwen-3-8-flash",
        zenmux_name="qwen/qwen3.8-flash:alibaba",
        display_name="Qwen 3.8 Flash",
        default_config=_DEFAULT_CONFIG,
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


def arena_model_to_selection(
    model: ArenaModel,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, str]:
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

    ``reasoning_effort`` pins the run's effort level. It is **omitted when None**
    so an unpinned board produces exactly the three-key selection every board from
    run #8 onward used — the historical default is vendor-chosen effort, and a
    key present-but-null would both misrepresent that and defeat the desk's
    prebuilt-orchestrator reuse check.

    Raises:
        ValueError: if zenmux_name does not contain a '<vendor>/<model>' slash.
    """
    name = model.zenmux_name
    if "/" not in name:
        raise ValueError(
            f"zenmux_name '{name}' must be '<vendor>/<model>' (e.g. 'openai/gpt-5.5')."
        )
    # `provider` in a desk selection means the ZenMux UPSTREAM PROVIDER as of
    # 2026-08-25. It is DERIVED from the `:upstream` pin already inside
    # zenmux_name rather than declared beside it, so the two can never disagree —
    # they did once, and the stale one (qwen3.8-27b) survived five weeks in the
    # live YAML while its own comment denied it. Falls back to an explicit
    # `model.provider`, then the vendor prefix, for an UNPINNED name.
    _base, _sep, _upstream = name.partition(":")
    provider = _upstream or model.provider or name.split("/", 1)[0]
    selection = _forced_channel_selection(name) or {
        "channel": "zenmux", "provider": provider, "model": name,
    }
    if reasoning_effort:
        selection["reasoning_effort"] = reasoning_effort
    # Omitted when unset for the same reason as effort — a fourth/fifth key
    # present on every turn defeats the prebuilt-orchestrator reuse check.
    if max_output_tokens:
        selection["max_output_tokens"] = max_output_tokens
    return selection


def _forced_channel_selection(zenmux_name: str) -> dict[str, str] | None:
    """Route a contestant through a DIFFERENT channel, for protocol studies.

    ``OPEN_OTC_ARENA_FORCE_CHANNEL=<channel>`` re-points every contestant at that
    channel in ``config/agent_channels.yaml``, matching the model by its full
    zenmux id or by the part after the vendor prefix (the direct DeepSeek channel
    calls it ``deepseek-v4-flash``, ZenMux calls it
    ``deepseek/deepseek-v4-flash``). Returns None when unset.

    Exists because the arena hardcodes ``channel: "zenmux"``, which makes
    "is this defect the GATEWAY or the MODEL?" unanswerable — and that question
    only a real match can settle: run #122's malformed-tool-call defect does not
    reproduce in a single-shot probe.

    **Fails loudly** when the channel or the model is absent. A silent fallback
    to zenmux would produce a study arm that measured the control, which is the
    worst possible outcome for a comparison.
    """
    import os
    target = (os.getenv("OPEN_OTC_ARENA_FORCE_CHANNEL") or "").strip()
    if not target:
        return None
    from app.services.deep_agent.channel_registry import get_registry

    registry = get_registry()
    channel = next((c for c in registry.channels if c.name == target), None)
    if channel is None:
        raise ValueError(
            f"OPEN_OTC_ARENA_FORCE_CHANNEL={target!r}: no such channel in the "
            f"registry (have {[c.name for c in registry.channels]})."
        )
    # Strip the `:upstream` pin before matching: the pin names a ZenMux upstream
    # and means nothing on the channel we are forcing onto, whose ids are bare.
    unpinned = zenmux_name.partition(":")[0]
    bare = unpinned.split("/", 1)[1] if "/" in unpinned else unpinned
    model = next(
        (m for m in channel.models if m.id in (unpinned, bare)), None
    )
    if model is None:
        raise ValueError(
            f"OPEN_OTC_ARENA_FORCE_CHANNEL={target!r}: channel does not carry "
            f"{zenmux_name!r} (nor {bare!r}). Routing it through the default "
            "channel anyway would silently make this arm a duplicate of the "
            "control."
        )
    return {"channel": target, "provider": model.provider, "model": model.id}


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
