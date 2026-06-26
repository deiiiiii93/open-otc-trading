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


def test_third_party_vendor_uses_explicit_openai_provider():
    """Third-party vendors routed through Zenmux carry their real vendor in the
    model id but dispatch as 'openai'. The selection must report provider=openai
    (not the 'z-ai' prefix), else it won't match the channel-registry entry.
    """
    sel = arena_model_to_selection(
        ArenaModel(
            slug="glm",
            zenmux_name="z-ai/glm-5.2",
            display_name="GLM",
            default_config={},
            provider="openai",
        )
    )
    assert sel == {"channel": "zenmux", "provider": "openai", "model": "z-ai/glm-5.2"}


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
    assert resolved["model"] == "openai/gpt-5.5"


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

    sel = arena_model_to_selection(get_model(slug))
    resolved = resolve_agent_model_selection(get_registry(), sel)  # must not raise
    assert resolved["channel"] == "zenmux"
    assert resolved["provider"] == "openai"
