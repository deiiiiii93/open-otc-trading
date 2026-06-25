import pytest

from app.services.arena.models import ArenaModel, arena_model_to_selection


def _m(zenmux_name: str) -> ArenaModel:
    return ArenaModel(slug="x", zenmux_name=zenmux_name, display_name="X", default_config={})


def test_splits_vendor_and_model():
    sel = arena_model_to_selection(_m("openai/gpt-5.5"))
    assert sel == {"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}


def test_anthropic_slug():
    sel = arena_model_to_selection(_m("anthropic/claude-opus-4.8"))
    assert sel == {"channel": "zenmux", "provider": "anthropic", "model": "claude-opus-4.8"}


def test_missing_slash_raises():
    with pytest.raises(ValueError):
        arena_model_to_selection(_m("gpt-5.5"))
