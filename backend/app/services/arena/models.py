"""Arena model registry.

Defines candidate models for the LLM arena and provides lookup helpers.

Slugs are filesystem/URL-safe: lowercase, dashes only, no slashes or dots.
Zenmux names use the vendor/model-name convention used by Zenmux routing.

At module load the registry validates uniqueness of both slugs and zenmux_names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArenaModel:
    """Immutable descriptor for a candidate model in the arena.

    Attributes:
        slug: Filesystem/URL-safe identifier (lowercase, dashes only).
        zenmux_name: The model name as Zenmux routes it (e.g. "openai/gpt-5.5").
        display_name: Human-readable label shown in the UI.
        default_config: Fallback inference params (temperature, max_tokens, …).
    """

    slug: str
    zenmux_name: str
    display_name: str
    default_config: dict[str, Any]


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
# Seed registry — 4 plausible candidate models
# ---------------------------------------------------------------------------

CANDIDATE_MODELS: list[ArenaModel] = [
    ArenaModel(
        slug="gpt-5-5",
        zenmux_name="openai/gpt-5.5",
        display_name="GPT-5.5",
        default_config={"temperature": 0, "max_tokens": 4096},
    ),
    ArenaModel(
        slug="claude-opus-4-8",
        zenmux_name="anthropic/claude-opus-4.8",
        display_name="Claude Opus 4.8",
        default_config={"temperature": 0, "max_tokens": 4096},
    ),
    ArenaModel(
        slug="claude-sonnet-4-6",
        zenmux_name="anthropic/claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6",
        default_config={"temperature": 0, "max_tokens": 4096},
    ),
    ArenaModel(
        slug="gemini-2-5-pro",
        zenmux_name="google/gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        default_config={"temperature": 0, "max_tokens": 4096},
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
    zenmux_name; ``provider`` is the vendor prefix. Returning a stripped model id
    would fail ``resolve_agent_model_selection`` before any turn is driven.

    Raises:
        ValueError: if zenmux_name does not contain a '<vendor>/<model>' slash.
    """
    name = model.zenmux_name
    if "/" not in name:
        raise ValueError(
            f"zenmux_name '{name}' must be '<vendor>/<model>' (e.g. 'openai/gpt-5.5')."
        )
    provider = name.split("/", 1)[0]
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
