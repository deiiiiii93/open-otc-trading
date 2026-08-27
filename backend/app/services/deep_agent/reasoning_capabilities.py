"""Per-model reasoning-effort capabilities, from a vendored models.dev snapshot.

The problem this solves: **there is no universal reasoning-effort ladder.** Across
the models this desk can route to, models.dev records `low,medium,high` for most,
`none,low,medium,high,xhigh,max` for the GPT-5.6 family, `high,max` for GLM-5.2,
and *no ladder at all* for MiniMax M3 / MiMo / Qwen3.7 (reasoning is a plain
on/off toggle). A single hardcoded list is therefore wrong for nearly every model
in both directions — it offers levels a model rejects and hides levels it has.

It is also **per route, not per model**: models.dev lists `deepseek-v4-pro` as
`high,max` on the direct DeepSeek API but `low,medium,high` through the ZenMux
gateway. So lookups are keyed by `(channel, model_id)`, matching the desk's own
selection triple.

Data comes from `config/model_reasoning.json`, refreshed by
`scripts/refresh_model_reasoning.py`. Nothing here touches the network: the
snapshot is vendored so a request path never depends on a third-party service, and
so an upstream change arrives as a reviewable diff rather than as silently
different behaviour — the same reason `quantark` is pinned exactly and arena
fixtures are harvested rather than recomputed.

**Unknown is permissive, never "unsupported".** models.dev lags new releases
(`x-ai/grok-4.6` has no ZenMux entry as of this snapshot), and a stale registry
must not be able to block a model that genuinely works.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SNAPSHOT_PATH = _REPO_ROOT / "config" / "model_reasoning.json"

_LOCK = threading.Lock()
_CACHE: dict | None = None


@dataclass(frozen=True)
class EffortSupport:
    """What one (channel, model) route accepts for reasoning effort."""

    reasoning: bool
    #: Ordered ladder for this route. Empty means "no levels" — either the model
    #: does not reason at all, or it only has an on/off toggle.
    efforts: tuple[str, ...]
    #: The model exposes reasoning as on/off. Orthogonal to `efforts`: DeepSeek
    #: declares both, Qwen3.7 declares only this.
    toggle: bool
    #: ``"measured"`` (a live probe of THIS route) or ``"models.dev"`` (a
    #: third-party claim about the model). Load-bearing: only a measured ladder is
    #: trusted enough to REJECT a level. See `rejection_reason`.
    source: str = "models.dev"

    @property
    def has_levels(self) -> bool:
        return bool(self.efforts)

    @property
    def measured(self) -> bool:
        return self.source == "measured"


def _load() -> dict:
    global _CACHE
    with _LOCK:
        cached = _CACHE
        if cached is None:
            try:
                loaded = json.loads(_SNAPSHOT_PATH.read_text())
            except (OSError, ValueError):
                # A missing or corrupt snapshot degrades to "everything unknown",
                # i.e. permissive — it must never take the desk down.
                loaded = {"routes": {}}
            cached = loaded if isinstance(loaded, dict) else {"routes": {}}
            _CACHE = cached
        return cached


def reload_snapshot(data: dict | None = None) -> None:
    """Drop the cache, or install `data` directly.

    Passing `data` is the test seam. Tests MUST use it rather than asserting
    against the vendored snapshot: models.dev is a live upstream, so a unit test
    keyed to a real model's current ladder would break the day that model gains a
    level — the self-invalidating-assertion trap this repo has been bitten by
    four times (see CLAUDE.md, "Tests must not assert against moving targets").
    """
    global _CACHE
    with _LOCK:
        _CACHE = data


def snapshot_provenance() -> dict[str, str]:
    """Source URL / sha256 / fetch date, for surfacing where the data came from."""
    data = _load()
    return {
        key: str(data.get(key, ""))
        for key in ("source", "source_sha256", "fetched_at")
    }


def effort_support(channel: str, model_id: str) -> EffortSupport | None:
    """Return what this route accepts, or None when the route/model is unknown.

    None is the caller's cue to fall back to permissive validation. Do not treat
    it as "no support" — see the module docstring.
    """
    routes = (_load().get("routes") or {}).get(channel) or {}
    entry = routes.get(model_id)
    if not isinstance(entry, dict) and ":" in model_id:
        # ZenMux pins an upstream with a `:provider` suffix
        # (`deepseek/deepseek-v4-flash:deepseek`), which is a DIFFERENT route key
        # from its unpinned twin. Fall back to the unpinned entry rather than to
        # "unknown": the base id's ladder is measured evidence about the same
        # model on the same gateway, and it is strictly better than the permissive
        # default that an unknown route gets. A pinned entry, when one has been
        # probed, still wins — so a provider whose ladder genuinely differs can be
        # recorded and will override this fallback.
        entry = routes.get(model_id.split(":", 1)[0])
    if not isinstance(entry, dict):
        return None
    return EffortSupport(
        reasoning=bool(entry.get("reasoning")),
        efforts=tuple(str(v) for v in (entry.get("efforts") or [])),
        toggle=bool(entry.get("toggle")),
        source=str(entry.get("source") or "models.dev"),
    )


def rejection_reason(channel: str, model_id: str, effort: str) -> str | None:
    """Why `effort` cannot be honoured on this route, or None if it can.

    **Only a MEASURED ladder can reject.** A live probe found models.dev wrong in
    both directions for our routes — it lists `openai/gpt-5.5` as low/medium/high
    when the gateway also accepts `none` and `xhigh`, lists `z-ai/glm-5.2` as
    high/max when every level is accepted, and calls `moonshotai/kimi-k2.7-code`
    non-reasoning when it reasons and refuses to be switched off. Rejecting on that
    data would deny levels that demonstrably work, which is worse than not gating:
    the gateway itself refuses an unsupported level with a precise message
    ("Unsupported value: 'minimal' is not supported with ..."), so the provider is
    a reliable backstop and we do not need to guess ahead of it.

    Unknown routes are permissive for the same reason plus one more: models.dev
    lags new releases, and a stale registry must not block a working model.

    So the gate is exactly as strong as the evidence behind it:
      measured    → reject a level outside the probed ladder
      models.dev  → allow; the provider decides
      unknown     → allow; the provider decides
    """
    support = effort_support(channel, model_id)
    if support is None or not support.measured:
        return None
    if not support.reasoning:
        return (
            f"{model_id} is not a reasoning model, so it has no reasoning_effort "
            "(measured). Leave the effort unset for this model."
        )
    if not support.has_levels:
        extra = (
            " Its reasoning is an on/off toggle with no effort ladder."
            if support.toggle
            else ""
        )
        return (
            f"{model_id} accepts no reasoning_effort level (measured).{extra} "
            "Leave the effort unset for this model."
        )
    if effort not in support.efforts:
        return (
            f"{model_id} does not accept reasoning_effort {effort!r} — "
            f"measured levels are {list(support.efforts)}."
        )
    return None
