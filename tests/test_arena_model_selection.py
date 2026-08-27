import pytest

from app.services.arena.models import ArenaModel, arena_model_to_selection


def _m(zenmux_name: str) -> ArenaModel:
    return ArenaModel(slug="x", zenmux_name=zenmux_name, display_name="X", default_config={})


def test_splits_vendor_keeps_full_model_id():
    sel = arena_model_to_selection(_m("openai/gpt-5.5"))
    # model carries the FULL zenmux id (the registry keys models by it)
    assert sel == {"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.5"}


def test_anthropic_slug():
    sel = arena_model_to_selection(_m("anthropic/claude-opus-4.8"))
    assert sel == {
        "channel": "zenmux", "provider": "anthropic", "model": "anthropic/claude-opus-4.8",
    }


def test_missing_slash_raises():
    with pytest.raises(ValueError):
        arena_model_to_selection(_m("gpt-5.5"))


def test_explicit_provider_is_a_fallback_for_an_unpinned_name():
    """`ArenaModel.provider` survives only for an UNPINNED zenmux_name. Every real
    candidate carries a `:upstream` pin, and the pin always wins — see the next
    test for why declaring it twice is the failure mode, not the safeguard.
    """
    sel = arena_model_to_selection(
        ArenaModel(
            slug="glm",
            zenmux_name="z-ai/glm-5.2",
            display_name="GLM",
            default_config={},
            provider="bigmodel",
        )
    )
    assert sel == {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.2"}


def test_the_pin_wins_over_a_stale_declared_provider():
    """One source of truth for the upstream: the pin inside zenmux_name.

    Declaring it in two places is how qwen3.8-27b's protocol sat wrong in the live
    YAML for five weeks while the row's own comment denied it. A derived value
    cannot disagree with itself.
    """
    sel = arena_model_to_selection(
        ArenaModel(
            slug="ds",
            zenmux_name="deepseek/deepseek-v4-flash:deepseek",
            display_name="DS",
            default_config={},
            provider="alibaba",  # stale, and the upstream that broke this model
        )
    )
    assert sel["provider"] == "deepseek"


def test_selection_is_accepted_by_the_real_registry():
    """The selection must validate against the desk channel registry, otherwise
    a live match fails before any turn (regression for the stripped-id bug).

    gpt-5-5 (openai/gpt-5.5) is a config-default zenmux candidate; asserting it
    resolves pins the model-id format the registry expects.
    """
    from app.services.arena.models import get_model
    from app.services.deep_agent.channel_registry import get_registry
    from app.services.deep_agent.model_factory import resolve_agent_model_selection

    sel = arena_model_to_selection(get_model("gpt-5-5"))
    resolved = resolve_agent_model_selection(get_registry(), sel)  # must not raise
    # Base id plus an explicit upstream pin. The pin itself is not asserted —
    # it may be repinned — but its PRESENCE is, because an unpinned zenmux id is
    # a routing lottery (2026-08-25).
    assert resolved["model"].split(":")[0] == "openai/gpt-5.5"
    assert ":" in resolved["model"]


@pytest.mark.parametrize(
    "slug",
    ["glm-5-2", "kimi-2-7", "minimax-m3", "mimo-2-5-pro", "deepseek-v4-pro", "qwen-3-7-max"],
)
def test_new_vendor_selections_resolve_against_real_registry(slug):
    """Every newly-added third-party candidate must be dispatchable: its
    selection has to validate against the desk channel registry (i.e. there is a
    matching config/agent_channels.yaml entry). Catches arena-registry vs
    YAML drift before a live run fails per-match.
    """
    from app.services.arena.models import get_model
    from app.services.deep_agent.channel_registry import get_registry
    from app.services.deep_agent.model_factory import resolve_agent_model_selection

    model = get_model(slug)
    sel = arena_model_to_selection(model)
    resolved = resolve_agent_model_selection(get_registry(), sel)  # must not raise
    assert resolved["channel"] == "zenmux"
    # `provider` is the ZenMux UPSTREAM as of 2026-08-25, DERIVED from the pin
    # already inside zenmux_name so the two cannot drift. It used to be the
    # constant gateway label "openai" for every third-party vendor, which is
    # what made three vendor-shaped words on one row mean three different things.
    assert resolved["provider"] == model.zenmux_name.partition(":")[2]
    assert resolved["provider"] not in ("", "openai_chat")


def test_effort_is_omitted_when_unpinned():
    """An unpinned board must produce exactly the three-key selection every board
    from run #8 to #104 used — a present-but-null key would misstate the
    historical regime and defeat the desk's prebuilt-orchestrator reuse check."""
    sel = arena_model_to_selection(_m("openai/gpt-5.5"))
    assert "reasoning_effort" not in sel
    assert arena_model_to_selection(_m("openai/gpt-5.5"), None) == sel


def test_effort_rides_along_when_pinned():
    sel = arena_model_to_selection(_m("openai/gpt-5.5"), "high")
    assert sel == {
        "channel": "zenmux", "provider": "openai",
        "model": "openai/gpt-5.5", "reasoning_effort": "high",
    }


def test_pinned_effort_selection_resolves_against_the_tracked_registry():
    """A pinned selection must still validate end-to-end, or a live board would die
    per-match after launch rather than at launch.

    Sourced from the TRACKED `agent_channels.example.yml`, not `get_registry()`:
    the live YAML is gitignored and per-environment (and the Model Maintenance UI
    rewrites it at runtime), and the process-global registry is order-dependently
    leaked by the wider suite. The sibling `*_real_registry` tests in this file
    still use `get_registry()` and fail in a full-suite run for exactly that
    reason — a pre-existing hermeticity bug, not one to reproduce here.
    """
    from app.services.arena.models import get_model
    from app.services.deep_agent import channel_registry as cr
    from app.services.deep_agent.model_factory import resolve_agent_model_selection

    registry = cr.load_from_path(
        cr._REPO_ROOT / "config" / "agent_channels.example.yml"
    )
    sel = arena_model_to_selection(get_model("gpt-5-5"), "low")
    assert resolve_agent_model_selection(registry, sel)["reasoning_effort"] == "low"
