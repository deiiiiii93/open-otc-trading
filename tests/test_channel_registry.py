from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent import channel_registry
from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)


def test_model_descriptor_is_frozen():
    md = ModelDescriptor(id="m", provider="p", label="L")
    assert md.id == "m"
    assert md.tags == ()


def test_channel_descriptor_default_anthropic_base_url_is_none():
    cd = ChannelDescriptor(
        name="c", label="C", type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(), healthy=True,
    )
    assert cd.anthropic_base_url is None


def test_registry_find_model_returns_descriptors():
    md = ModelDescriptor(id="m", provider="anthropic", label="L")
    cd = ChannelDescriptor(
        name="zenmux", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url="https://y",
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zenmux", "anthropic", "m"))
    found_channel, found_model = reg.find_model("zenmux", "anthropic", "m")
    assert found_channel is cd
    assert found_model is md


def test_registry_find_model_raises_on_unknown():
    reg = ChannelRegistry(channels=(), default=("?", "?", "?"))
    with pytest.raises(KeyError, match="unknown selection"):
        reg.find_model("zenmux", "anthropic", "m")


def test_find_model_ignores_a_stale_provider_value():
    """`provider` is NOT part of the lookup key, and that is load-bearing.

    It changed meaning on 2026-08-25 (ZenMux gateway routing label -> upstream
    provider), and 13k+ persisted `AgentMessage.meta` selections carry the old
    value. Async resume replays them verbatim, so a provider-sensitive lookup
    would fail every one. Ids are already unique per channel, so nothing is lost.
    """
    md = ModelDescriptor(id="m", provider="deepseek", label="L", dispatch_id="m:deepseek")
    cd = ChannelDescriptor(
        name="zm", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zm", "deepseek", "m"))
    _, found = reg.find_model("zm", "openai", "m")  # the legacy gateway label
    assert found is md


def test_find_model_accepts_either_id_spelling():
    md = ModelDescriptor(id="m", provider="deepseek", label="L", dispatch_id="m:deepseek")
    cd = ChannelDescriptor(
        name="zm", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zm", "deepseek", "m"))
    assert reg.find_model("zm", "", "m")[1] is md            # bare
    assert reg.find_model("zm", "", "m:deepseek")[1] is md    # upstream-pinned


def test_find_model_refuses_an_ambiguous_bare_id_across_upstreams():
    """Two upstream arms of one model are legal; picking one for a bare id is not.

    Silently choosing would BE the routing lottery the upstream pin exists to
    prevent — the `:deepseek` vs `:alibaba` split scored 94.9 and 7.7 on the same
    model. The caller has to say which.
    """
    good = ModelDescriptor(id="m", provider="deepseek", label="A", dispatch_id="m:deepseek")
    bad = ModelDescriptor(id="m", provider="alibaba", label="B", dispatch_id="m:alibaba")
    cd = ChannelDescriptor(
        name="zm", label="Z", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(good, bad), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd,), default=("zm", "deepseek", "m"))
    with pytest.raises(KeyError, match="ambiguous"):
        reg.find_model("zm", "", "m")
    assert reg.find_model("zm", "", "m:alibaba")[1] is bad


def test_find_model_searches_past_first_channel():
    md = ModelDescriptor(id="m", provider="p", label="L")
    cd1 = ChannelDescriptor(
        name="a", label="A", type="zenmux",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(), healthy=True,
    )
    cd2 = ChannelDescriptor(
        name="b", label="B", type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(md,), healthy=True,
    )
    reg = ChannelRegistry(channels=(cd1, cd2), default=("b", "p", "m"))
    found_ch, _ = reg.find_model("b", "p", "m")
    assert found_ch is cd2


def test_registry_default_selection_returns_dict():
    reg = ChannelRegistry(channels=(), default=("zenmux", "anthropic", "m"))
    assert reg.default_selection() == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "m",
    }


# ---------------------------------------------------------------------------
# select_by_tag: tier-selection API powering MemoryConfig.extractor_model
# routing (cheap "fast"-tagged extractor model instead of the agent default).
# ---------------------------------------------------------------------------

def _md(id_, *, provider="openai", tags=()):
    return ModelDescriptor(id=id_, provider=provider, label=id_, tags=tags)


def _ch(name, models, *, healthy=True):
    return ChannelDescriptor(
        name=name, label=name.upper(), type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=tuple(models), healthy=healthy,
    )


def test_select_by_tag_returns_first_healthy_match():
    reg = ChannelRegistry(
        channels=(_ch("flashc", (_md("slow", tags=("reasoning",)),
                                 _md("quick", tags=("fast", "tool-use")))),),
        default=("flashc", "openai", "slow"),
    )
    assert reg.select_by_tag("fast") == {
        "channel": "flashc", "provider": "openai", "model": "quick",
    }


def test_select_by_tag_declaration_order_is_tiebreak():
    reg = ChannelRegistry(
        channels=(_ch("c", (_md("first", tags=("fast",)),
                            _md("second", tags=("fast",)))),),
        default=("c", "openai", "first"),
    )
    assert reg.select_by_tag("fast")["model"] == "first"


def test_select_by_tag_skips_unhealthy_channels():
    reg = ChannelRegistry(
        channels=(_ch("dead", (_md("dead-fast", tags=("fast",)),), healthy=False),
                  _ch("live", (_md("live-fast", tags=("fast",)),))),
        default=("live", "openai", "live-fast"),
    )
    sel = reg.select_by_tag("fast")
    assert sel["channel"] == "live" and sel["model"] == "live-fast"


def test_select_by_tag_returns_none_when_no_match():
    reg = ChannelRegistry(
        channels=(_ch("c", (_md("m", tags=("reasoning",)),)),),
        default=("c", "openai", "m"),
    )
    assert reg.select_by_tag("fast") is None


def test_select_by_tag_returns_none_when_only_unhealthy_has_tag():
    reg = ChannelRegistry(
        channels=(_ch("dead", (_md("m", tags=("fast",)),), healthy=False),),
        default=("dead", "openai", "m"),
    )
    assert reg.select_by_tag("fast") is None


# ---------------------------------------------------------------------------
# Template guard: the shipped agent_channels.example.yml must pin EXACTLY ONE
# model with the dedicated 'extractor' tag, and it must be the Zenmux-proxied
# deepseek-v4-flash (provider 'openai') — NOT the deepseek-channel variant,
# which needs a separate key + langchain-deepseek. select_by_tag returns the
# FIRST match, so >1 'extractor'-tagged model would make the pin ambiguous.
# ---------------------------------------------------------------------------

def test_example_config_pins_exactly_one_extractor_model():
    example = Path(__file__).resolve().parents[1] / "config" / "agent_channels.example.yml"
    reg = channel_registry.load_from_path(example, force_reread_dotenv=False)
    tagged = [(ch.name, md.provider, md.id)
              for ch in reg.channels for md in ch.models if "extractor" in md.tags]
    assert len(tagged) == 1, f"expected exactly one extractor-tagged model, got {tagged}"
    _channel, provider, model_id = tagged[0]
    # The three axes, post-2026-08-25: bare id, upstream provider, and a
    # composed dispatch id. ZenMux routes an UNPINNED id to an arbitrary
    # upstream, and the `alibaba` one breaks streaming tool calls.
    assert model_id == "deepseek/deepseek-v4-flash"
    assert provider == "deepseek"
    md = reg.resolve_default_model("zenmux", model_id)
    assert md is not None and md.wire_id == "deepseek/deepseek-v4-flash:deepseek"
    assert md.protocol == "openai_chat"


YAML_FIXTURE = """
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: TEST_ZENMUX_KEY
    base_url: https://zenmux.test/api/v1
    anthropic_base_url: https://zenmux.test/api/anthropic
    models:
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Sonnet 4.6
        tags: [tool-use]
      - id: openai/gpt-5.4
        provider: openai
        label: GPT-5.4
  - name: deepseek
    label: DeepSeek
    type: openai_compatible
    api_key_env: TEST_DEEPSEEK_KEY
    base_url: https://api.deepseek.test
    models:
      - id: deepseek-v4-flash
        provider: deepseek
        label: DeepSeek V4 Flash
        description: fast
        tags: [fast]
"""


def _write_yaml(tmp_path: Path, body: str = YAML_FIXTURE) -> Path:
    path = tmp_path / "agent_channels.yaml"
    path.write_text(body)
    return path


def test_load_parses_yaml_and_returns_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert {ch.name for ch in reg.channels} == {"zenmux", "deepseek"}
    zenmux = next(ch for ch in reg.channels if ch.name == "zenmux")
    assert zenmux.healthy is True
    assert zenmux.api_key == "zm_fake"
    assert zenmux.anthropic_base_url == "https://zenmux.test/api/anthropic"
    assert len(zenmux.models) == 2
    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")


def test_load_raises_when_declared_default_model_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    body = YAML_FIXTURE.replace(
        "model: anthropic/claude-sonnet-4-6",
        "model: anthropic/claude-opus-4-7",
        1,
    )
    path = _write_yaml(tmp_path, body)

    with pytest.raises(ValueError, match="default model .* is not declared"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_when_declared_default_channel_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    body = YAML_FIXTURE.replace("channel: zenmux", "channel: missing", 1)
    path = _write_yaml(tmp_path, body)

    with pytest.raises(ValueError, match="default channel .* is not declared"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_marks_channel_unhealthy_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    zenmux = next(ch for ch in reg.channels if ch.name == "zenmux")
    assert zenmux.healthy is False
    assert zenmux.api_key is None


def test_load_treats_missing_api_key_env_as_local_channel(tmp_path):
    body = """
channels:
  - name: ollama
    label: Local
    type: openai_compatible
    api_key_env: null
    base_url: http://localhost:11434/v1
    models:
      - id: llama-3
        provider: meta
        label: Llama 3
"""
    path = _write_yaml(tmp_path, body)
    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    ollama = reg.channels[0]
    assert ollama.healthy is True
    assert ollama.api_key is None


def test_load_raises_on_duplicate_channel_name(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: dup
    label: A
    type: openai_compatible
    api_key_env: K
    base_url: https://a
    models:
      - id: m
        provider: x
        label: M
  - name: dup
    label: B
    type: openai_compatible
    api_key_env: K
    base_url: https://b
    models:
      - id: m2
        provider: x
        label: M2
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="duplicate channel name"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_when_zenmux_missing_anthropic_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    models:
      - id: m
        provider: openai
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="anthropic_base_url"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_on_zenmux_invalid_model_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: m
        provider: "not a slug"
        protocol: openai_chat
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="must be a ZenMux upstream slug"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_raises_when_a_zenmux_model_declares_no_protocol(tmp_path, monkeypatch):
    """protocol is REQUIRED on a new-style zenmux row.

    It used to default to `provider`, which worked only while provider held the
    gateway routing label. Now that provider names the upstream, silently
    defaulting would route `deepseek/deepseek-v4-flash` over a protocol called
    "deepseek" — so the loader asks instead of guessing.
    """
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: deepseek/deepseek-v4-flash
        provider: deepseek
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="protocol is required"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_the_three_axes_are_independent(tmp_path, monkeypatch):
    """id / provider / protocol describe three unrelated things, and a real row
    disagrees on all three: MiniMax's model, on MiniMax's metal, spoken over
    Anthropic Messages because the OpenAI endpoint leaves its tool calls
    unparsed. The dispatch id is composed from the first two only.
    """
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: minimax/minimax-m3
        provider: minimax
        protocol: anthropic
        label: MiniMax M3
"""
    path = _write_yaml(tmp_path, body)
    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)
    md = reg.channels[0].models[0]
    assert md.id == "minimax/minimax-m3"
    assert md.provider == "minimax"
    assert md.protocol == "anthropic"
    assert md.wire_id == "minimax/minimax-m3:minimax"


def test_legacy_row_is_translated_in_place(tmp_path, monkeypatch):
    """A pre-2026-08-25 row still loads: the upstream is recovered from the id's
    `:suffix` and the old gateway routing label becomes the protocol.

    The discriminator is the SUFFIX, never the provider value — "anthropic" is
    both a legacy gateway label and a real upstream, so only the row's shape
    tells the two schemas apart.
    """
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: deepseek/deepseek-v4-flash:deepseek
        provider: openai
        label: Legacy DeepSeek
      - id: z-ai/glm-5.2:bigmodel
        provider: openai
        protocol: anthropic
        label: Legacy GLM
"""
    path = _write_yaml(tmp_path, body)
    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)
    ds, glm = reg.channels[0].models
    # provider was the gateway label; the upstream came out of the id.
    assert (ds.id, ds.provider, ds.protocol) == (
        "deepseek/deepseek-v4-flash", "deepseek", "openai_chat")
    assert ds.wire_id == "deepseek/deepseek-v4-flash:deepseek"
    # An explicitly declared protocol survives translation untouched.
    assert (glm.id, glm.provider, glm.protocol) == ("z-ai/glm-5.2", "bigmodel", "anthropic")
    assert glm.wire_id == "z-ai/glm-5.2:bigmodel"


def test_legacy_unpinned_row_is_left_unpinned_not_guessed(tmp_path, monkeypatch):
    """No suffix and no protocol: the pre-pinning shape.

    It must NOT compose `<id>:openai` — the gateway label is not an upstream, and
    minting a pin nobody chose is worse than the lottery it would pretend to fix.
    """
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: google/gemini-3.5-flash
        provider: openai
        label: Old Gemini
"""
    path = _write_yaml(tmp_path, body)
    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)
    md = reg.channels[0].models[0]
    assert md.wire_id == "google/gemini-3.5-flash"   # unpinned, not ":openai"
    assert md.protocol == "openai_chat"


def test_load_raises_on_zenmux_invalid_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: m
        provider: openai
        protocol: hermes
        label: M
"""
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match=r"protocol must be .* got 'hermes'"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


@pytest.mark.parametrize("declared,canonical", [
    ("openai_chat", "openai_chat"),
    ("anthropic", "anthropic"),
    ("openai_responses", "openai_responses"),
    # Pre-2026-08-25 spellings. "openai" hid WHICH of OpenAI's two APIs was
    # meant, which is exactly the distinction the deepseek investigation turned
    # on — but old YAML must keep loading, so they alias rather than fail.
    ("openai", "openai_chat"),
    ("responses", "openai_responses"),
])
def test_protocol_values_and_their_legacy_aliases(tmp_path, monkeypatch, declared, canonical):
    monkeypatch.setenv("K", "x")
    body = f"""
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: vendor/m
        provider: vendor
        protocol: {declared}
        label: M
"""
    reg = channel_registry.load_from_path(_write_yaml(tmp_path, body), force_reread_dotenv=False)
    assert reg.channels[0].models[0].protocol == canonical


def test_protocol_and_provider_are_no_longer_the_same_allowlist(tmp_path, monkeypatch):
    """`openai_responses` is a protocol, never an upstream — and an upstream may
    be any vendor slug, which a protocol may not. The two axes stopped sharing a
    value space when provider was redefined; this pins that they cannot converge
    again by accident.
    """
    monkeypatch.setenv("K", "x")
    body = """
channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: K
    base_url: https://x
    anthropic_base_url: https://y
    models:
      - id: vendor/m
        provider: openai_responses
        protocol: openai_responses
        label: M
"""
    reg = channel_registry.load_from_path(_write_yaml(tmp_path, body), force_reread_dotenv=False)
    md = reg.channels[0].models[0]
    # A protocol-shaped slug is a legal (if odd) upstream: there is no ZenMux
    # enumeration endpoint, so upstreams are shape-checked, never allowlisted.
    assert md.provider == "openai_responses"
    assert md.wire_id == "vendor/m:openai_responses"


def test_load_raises_when_no_channels_declared(tmp_path):
    body = "channels: []\n"
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="at least one channel"):
        channel_registry.load_from_path(path, force_reread_dotenv=False)


def test_load_falls_back_to_first_healthy_when_default_unhealthy(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    # Zenmux (the YAML default) is unhealthy; fall back to first healthy.
    assert reg.default == ("deepseek", "deepseek", "deepseek-v4-flash")


def test_load_default_is_zenmux_default_when_zenmux_healthy(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")


def test_load_when_no_healthy_channels_returns_disabled_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ZENMUX_KEY", raising=False)
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    path = _write_yaml(tmp_path)

    reg = channel_registry.load_from_path(path, force_reread_dotenv=False)

    assert all(not ch.healthy for ch in reg.channels)
    # Default still points somewhere — the YAML's stated default — so callers can
    # render "disabled" without crashing on attribute access.
    assert reg.default == ("zenmux", "anthropic", "anthropic/claude-sonnet-4-6")


def test_configure_registry_overrides_singleton():
    md = ModelDescriptor(id="m", provider="x", label="M")
    cd = ChannelDescriptor(
        name="c", label="C", type="openai_compatible",
        api_key="k", base_url="https://x", anthropic_base_url=None,
        models=(md,), healthy=True,
    )
    fake = ChannelRegistry(channels=(cd,), default=("c", "x", "m"))

    channel_registry.configure_registry(fake)
    try:
        assert channel_registry.get_registry() is fake
    finally:
        channel_registry.configure_registry(None)


def test_get_registry_loads_from_default_path_when_unconfigured(tmp_path, monkeypatch):
    # Ensure clean override
    channel_registry.configure_registry(None)

    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))

    reg = channel_registry.get_registry()
    assert {ch.name for ch in reg.channels} == {"zenmux", "deepseek"}

    channel_registry.configure_registry(None)


def test_reload_swaps_registry_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))

    reg1 = channel_registry.reload(force_reread_dotenv=False)
    assert {ch.name for ch in reg1.channels} == {"zenmux", "deepseek"}

    # Rewrite YAML to remove deepseek
    path.write_text(YAML_FIXTURE.replace(
        "  - name: deepseek", "  # deepseek removed"
    ).split("  # deepseek removed")[0])
    reg2 = channel_registry.reload(force_reread_dotenv=False)
    assert {ch.name for ch in reg2.channels} == {"zenmux"}
    assert channel_registry.get_registry() is reg2

    channel_registry.configure_registry(None)


def test_reload_keeps_old_registry_on_bad_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ZENMUX_KEY", "zm_fake")
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "ds_fake")
    path = _write_yaml(tmp_path)
    monkeypatch.setenv("AGENT_CHANNELS_FILE", str(path))
    good = channel_registry.reload(force_reread_dotenv=False)

    path.write_text("channels: []\n")  # invalid: zero channels

    with pytest.raises(ValueError):
        channel_registry.reload(force_reread_dotenv=False)
    assert channel_registry.get_registry() is good

    channel_registry.configure_registry(None)
