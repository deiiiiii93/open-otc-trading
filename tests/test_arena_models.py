"""Tests for arena model registry and Zenmux channel.

TDD: write tests first, then implement.
"""
import pytest
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Registry / canonical_model_id tests
# ---------------------------------------------------------------------------


def test_canonical_slug_by_slug():
    from app.services.arena.models import canonical_model_id
    assert canonical_model_id("gpt-5-5") == "gpt-5-5"


def test_canonical_slug_by_zenmux_name():
    from app.services.arena.models import canonical_model_id
    assert canonical_model_id("openai/gpt-5.5") == "gpt-5-5"


def test_canonical_unknown_raises_key_error():
    from app.services.arena.models import canonical_model_id
    with pytest.raises(KeyError):
        canonical_model_id("completely-unknown-model-xyz")


def test_get_model_by_slug():
    from app.services.arena.models import get_model
    m = get_model("gpt-5-5")
    assert m.slug == "gpt-5-5"
    assert m.zenmux_name == "openai/gpt-5.5"


def test_get_model_by_zenmux_name():
    from app.services.arena.models import get_model
    m = get_model("openai/gpt-5.5")
    assert m.slug == "gpt-5-5"


# ---------------------------------------------------------------------------
# Uniqueness enforcement via _index_models
# ---------------------------------------------------------------------------


def test_duplicate_slug_raises():
    from app.services.arena.models import ArenaModel, _index_models
    models = [
        ArenaModel("dup-slug", "vendor/a", "Model A", {"temperature": 0, "max_tokens": 4096}),
        ArenaModel("dup-slug", "vendor/b", "Model B", {"temperature": 0, "max_tokens": 4096}),
    ]
    with pytest.raises(ValueError, match="slug"):
        _index_models(models)


def test_duplicate_zenmux_name_raises():
    from app.services.arena.models import ArenaModel, _index_models
    models = [
        ArenaModel("slug-a", "vendor/same", "Model A", {"temperature": 0, "max_tokens": 4096}),
        ArenaModel("slug-b", "vendor/same", "Model B", {"temperature": 0, "max_tokens": 4096}),
    ]
    with pytest.raises(ValueError, match="zenmux_name"):
        _index_models(models)


# ---------------------------------------------------------------------------
# validate_model_ids
# ---------------------------------------------------------------------------


def test_validate_model_ids_unknown_raises():
    from app.services.arena.models import validate_model_ids
    with pytest.raises(ValueError, match="nope"):
        validate_model_ids(["nope"])


def test_validate_model_ids_canonicalizes():
    from app.services.arena.models import validate_model_ids
    # Both slug and zenmux_name for the same model → both map to the same slug
    result = validate_model_ids(["gpt-5-5", "openai/gpt-5.5"])
    assert result == ["gpt-5-5", "gpt-5-5"]


def test_validate_model_ids_valid_list():
    from app.services.arena.models import validate_model_ids, CANDIDATE_MODELS
    slugs = [m.slug for m in CANDIDATE_MODELS]
    result = validate_model_ids(slugs)
    assert result == slugs


# ---------------------------------------------------------------------------
# build_zenmux_chat — no network, assert object attributes
# ---------------------------------------------------------------------------


def test_build_zenmux_chat_model_and_base_url():
    from app.services.arena.models import get_model
    from app.services.arena.channel import build_zenmux_chat

    model = get_model("gpt-5-5")
    chat = build_zenmux_chat(model, api_key="test")

    # model_name holds the zenmux_name (what we passed as `model=` to ChatOpenAI)
    assert chat.model_name == model.zenmux_name

    # openai_api_base is the direct attribute exposed by langchain_openai.ChatOpenAI
    assert chat.openai_api_base == "https://zenmux.ai/api/v1"


def test_build_zenmux_chat_defaults_from_config():
    from app.services.arena.models import get_model
    from app.services.arena.channel import build_zenmux_chat

    model = get_model("gpt-5-5")
    chat = build_zenmux_chat(model, api_key="test")

    assert chat.temperature == model.default_config["temperature"]
    assert chat.max_tokens == model.default_config["max_tokens"]


def test_build_zenmux_chat_override_temperature():
    from app.services.arena.models import get_model
    from app.services.arena.channel import build_zenmux_chat

    model = get_model("gpt-5-5")
    chat = build_zenmux_chat(model, temperature=0.7, api_key="test")

    assert chat.temperature == 0.7


def test_build_zenmux_chat_override_max_tokens():
    from app.services.arena.models import get_model
    from app.services.arena.channel import build_zenmux_chat

    model = get_model("gpt-5-5")
    chat = build_zenmux_chat(model, max_tokens=1024, api_key="test")

    assert chat.max_tokens == 1024
