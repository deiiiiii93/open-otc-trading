from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config import get_settings
from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)
from app.services.deep_agent.model_factory import (
    agent_model_config,
    build_agent_model,
    default_agent_model_selection,
    resolve_agent_model_selection,
)


def _registry(*, zenmux_healthy: bool = True, deepseek_healthy: bool = True) -> ChannelRegistry:
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key="zm_fake" if zenmux_healthy else None,
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=zenmux_healthy,
        models=(
            ModelDescriptor(id="anthropic/claude-sonnet-4-6", provider="anthropic", label="Sonnet 4.6"),
            ModelDescriptor(id="openai/gpt-5.4", provider="openai", label="GPT-5.4"),
        ),
    )
    deepseek = ChannelDescriptor(
        name="deepseek",
        label="DeepSeek",
        type="openai_compatible",
        api_key="ds_fake" if deepseek_healthy else None,
        base_url="https://api.deepseek.test",
        anthropic_base_url=None,
        healthy=deepseek_healthy,
        models=(
            ModelDescriptor(id="deepseek-v4-flash", provider="deepseek", label="DeepSeek V4 Flash", tags=("fast",)),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux, deepseek),
        default=("zenmux", "anthropic", "anthropic/claude-sonnet-4-6"),
    )


def test_build_agent_model_returns_chat_anthropic_for_zenmux_anthropic():
    from langchain_anthropic import ChatAnthropic
    model = build_agent_model(_registry())
    assert isinstance(model, ChatAnthropic)


def test_build_agent_model_uses_zenmux_anthropic_base_url():
    model = build_agent_model(_registry())
    base_url_attr = (
        getattr(model, "anthropic_api_url", None)
        or getattr(model, "base_url", None)
    )
    assert "zenmux.test/api/anthropic" in str(base_url_attr)


def test_build_agent_model_returns_chat_openai_for_zenmux_openai():
    from langchain_openai import ChatOpenAI
    model = build_agent_model(
        _registry(),
        selection={"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"},
    )
    assert isinstance(model, ChatOpenAI)


def _registry_with_anthropic_protocol_model() -> ChannelRegistry:
    """A zenmux model whose provider is 'openai' but wire protocol is 'anthropic'
    (the minimax case): find_model still keys on provider, client routes on protocol."""
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key="zm_fake",
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=True,
        models=(
            ModelDescriptor(
                id="minimax/minimax-m3",
                provider="openai",
                label="MiniMax M3",
                protocol="anthropic",
            ),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux,),
        default=("zenmux", "openai", "minimax/minimax-m3"),
    )


def test_wire_protocol_defaults_to_provider():
    md = ModelDescriptor(id="openai/gpt-5.4", provider="openai", label="GPT-5.4")
    assert md.wire_protocol == "openai"
    md2 = ModelDescriptor(id="x", provider="openai", label="x", protocol="anthropic")
    assert md2.wire_protocol == "anthropic"


def test_build_agent_model_routes_anthropic_protocol_openai_provider_to_chat_anthropic():
    """A provider='openai' model with protocol='anthropic' builds ChatAnthropic
    on the Anthropic endpoint — the minimax tool-format fix."""
    from langchain_anthropic import ChatAnthropic
    reg = _registry_with_anthropic_protocol_model()
    model = build_agent_model(
        reg,
        selection={"channel": "zenmux", "provider": "openai", "model": "minimax/minimax-m3"},
    )
    assert isinstance(model, ChatAnthropic)
    base_url_attr = (
        getattr(model, "anthropic_api_url", None) or getattr(model, "base_url", None)
    )
    assert "zenmux.test/api/anthropic" in str(base_url_attr)


def test_build_agent_model_returns_deepseek_wrapper_for_deepseek_channel():
    from app.services.deep_agent.model_factory import DeepSeekReasoningChat

    model = build_agent_model(
        _registry(),
        selection={"channel": "deepseek", "provider": "deepseek", "model": "deepseek-v4-flash"},
    )
    assert isinstance(model, DeepSeekReasoningChat)
    base_url_attr = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None)
    assert "api.deepseek.test" in str(base_url_attr)


def test_deepseek_wrapper_replays_reasoning_content_after_tool_call():
    model = build_agent_model(
        _registry(),
        selection={
            "channel": "deepseek",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        },
    )

    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [
            HumanMessage(content="How many positions?"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "I should call the tool."},
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "get_positions",
                        "args": {},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content='{"count": 3}', tool_call_id="call_1"),
        ]
    )

    assistant_payload = payload["messages"][1]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["reasoning_content"] == "I should call the tool."
    assert assistant_payload["tool_calls"][0]["id"] == "call_1"


def test_deepseek_wrapper_replays_reasoning_content_from_upgrade_shapes():
    model = build_agent_model(
        _registry(),
        selection={
            "channel": "deepseek",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        },
    )

    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [
            AIMessage(
                content="answer from metadata",
                response_metadata={"reasoning": "metadata thinking"},
            ),
            AIMessage(
                content=[
                    {"type": "reasoning", "text": "block thinking"},
                    {"type": "text", "text": "answer from blocks"},
                ],
            ),
        ]
    )

    assert payload["messages"][0]["reasoning_content"] == "metadata thinking"
    assert payload["messages"][1]["reasoning_content"] == "block thinking"


def test_build_agent_model_returns_none_when_selected_channel_unhealthy():
    reg = _registry(zenmux_healthy=False)
    assert build_agent_model(reg) is None


def test_build_agent_model_raises_on_unknown_selection():
    with pytest.raises(KeyError, match="unknown selection"):
        build_agent_model(
            _registry(),
            selection={"channel": "zenmux", "provider": "anthropic", "model": "does-not-exist"},
        )


def test_default_agent_model_selection_returns_registry_default():
    assert default_agent_model_selection(_registry()) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_returns_default_when_none():
    assert resolve_agent_model_selection(_registry(), None) == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_back_fills_legacy_channel():
    # Legacy thread-history rows have only {provider, model}.
    resolved = resolve_agent_model_selection(
        _registry(),
        {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-6"},
    )
    assert resolved == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_resolve_agent_model_selection_raises_on_unknown():
    with pytest.raises(ValueError, match="unsupported"):
        resolve_agent_model_selection(
            _registry(),
            {"channel": "zenmux", "provider": "anthropic", "model": "ghost"},
        )


def test_agent_model_config_returns_nested_catalog():
    cfg = agent_model_config(_registry())
    assert cfg["enabled"] is True
    assert cfg["active"]["channel"] == "zenmux"
    names = [ch["name"] for ch in cfg["channels"]]
    assert names == ["zenmux", "deepseek"]
    zenmux_models = cfg["channels"][0]["models"]
    assert any(m["model"] == "openai/gpt-5.4" for m in zenmux_models)


def test_agent_model_config_marks_disabled_when_no_healthy_channels():
    reg = _registry(zenmux_healthy=False, deepseek_healthy=False)
    cfg = agent_model_config(reg)
    assert cfg["enabled"] is False


# ---------------------------------------------------------------------------
# reasoning_effort — explicit per-turn/per-run effort on the model selection
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _controlled_reasoning_snapshot():
    """Pin the per-model effort ladders these tests reason about.

    Entries carry ``source: "measured"`` because ONLY a measured ladder gates: a
    live probe found models.dev wrong for every route that mattered (too narrow for
    13 of 22 models), so an unmeasured ladder is advisory and never rejects.

    models.dev is a LIVE upstream. Asserting against the vendored snapshot would
    make these tests fail the day a real model gains or loses a level — the
    self-invalidating-assertion trap CLAUDE.md documents. The registry fixture's
    `openai/gpt-5.4` really does exist upstream, so without this the parametrized
    ladder test silently graded real-world data.
    """
    from app.services.deep_agent import reasoning_capabilities as rc

    measured = {"source": "measured", "measured_at": "2026-08-14"}
    rc.reload_snapshot({
        "routes": {
            "zenmux": {
                # A narrow ladder, like most models really have.
                "openai/gpt-5.4": {
                    "reasoning": True, "efforts": ["low", "medium", "high"],
                    "toggle": False, **measured,
                },
                # A wide ladder, like the GPT-5.6 family.
                "openai/wide": {
                    "reasoning": True,
                    "efforts": ["none", "minimal", "low", "medium", "high",
                                "xhigh", "max"],
                    "toggle": False, **measured,
                },
                # Reasoning is on/off with NO ladder, like Qwen3.7 / MiniMax M3.
                "qwen/toggle-only": {
                    "reasoning": True, "efforts": [], "toggle": True, **measured,
                },
                # Not a reasoning model at all.
                "openai/plain": {
                    "reasoning": False, "efforts": [], "toggle": False, **measured,
                },
            },
        },
    })
    yield
    rc.reload_snapshot()


def _reg_with(model_id: str, provider: str = "openai") -> ChannelRegistry:
    """A registry whose zenmux channel carries one extra model id."""
    base = _registry()
    zenmux = base.channels[0]
    extended = ChannelDescriptor(
        name=zenmux.name, label=zenmux.label, type=zenmux.type,
        api_key=zenmux.api_key, base_url=zenmux.base_url,
        anthropic_base_url=zenmux.anthropic_base_url, healthy=zenmux.healthy,
        models=(*zenmux.models,
                ModelDescriptor(id=model_id, provider=provider, label=model_id)),
    )
    return ChannelRegistry(channels=(extended, base.channels[1]), default=base.default)


def _sel(model_id: str, effort: str | None = None, provider: str = "openai") -> dict:
    out = {"channel": "zenmux", "provider": provider, "model": model_id}
    if effort is not None:
        out["reasoning_effort"] = effort
    return out


def test_resolve_selection_omits_reasoning_effort_when_unset():
    """The load-bearing property: an unpinned selection must be the SAME dict it
    always was.

    AgentService reuses its prebuilt orchestrator only when the resolved selection
    compares equal to ``default_model_selection``. A fourth key present on every
    turn — even as an explicit None — would silently stop that reuse for every
    default turn and rebuild the graph per request.
    """
    reg = _registry()
    resolved = resolve_agent_model_selection(reg, _sel("openai/gpt-5.4"))
    assert resolved == {
        "channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4",
    }
    assert "reasoning_effort" not in resolved

    # An explicit null (what the UI sends when the picker reads "Default") and an
    # empty string must both behave as unset, not as a level.
    for blank in (None, "", "  "):
        assert "reasoning_effort" not in resolve_agent_model_selection(
            reg, _sel("openai/gpt-5.4", blank),
        )


def test_unset_selection_still_equals_the_default_selection():
    """Guards the reuse check end-to-end rather than by inspection."""
    reg = _registry()
    assert resolve_agent_model_selection(
        reg, dict(default_agent_model_selection(reg))
    ) == default_agent_model_selection(reg)


@pytest.mark.parametrize("given,expected", [("High", "high"), ("  LOW ", "low")])
def test_resolve_selection_canonicalizes_an_accepted_effort(given, expected):
    resolved = resolve_agent_model_selection(
        _registry(), _sel("openai/gpt-5.4", given),
    )
    assert resolved["reasoning_effort"] == expected


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
)
def test_a_wide_ladder_model_accepts_every_level(effort):
    """There is no universal ladder: the GPT-5.6 family really does span
    none…max, so the outer bound must not be narrower than that."""
    resolved = resolve_agent_model_selection(
        _reg_with("openai/wide"), _sel("openai/wide", effort),
    )
    assert resolved["reasoning_effort"] == effort


@pytest.mark.parametrize("effort", ["minimal", "xhigh", "max", "none"])
def test_a_narrow_ladder_model_rejects_levels_it_does_not_declare(effort):
    """The whole point of per-model data: `openai/gpt-5.4` takes only
    low/medium/high, so offering it `xhigh` must fail loudly rather than be
    forwarded and silently ignored."""
    with pytest.raises(ValueError, match="does not accept reasoning_effort"):
        resolve_agent_model_selection(_registry(), _sel("openai/gpt-5.4", effort))


def test_toggle_only_model_rejects_any_effort():
    """Qwen3.7 / MiniMax M3 expose reasoning as a bare on/off toggle — there is no
    ladder to pick from, so an effort there is meaningless, not merely wrong."""
    with pytest.raises(ValueError, match="accepts no reasoning_effort level"):
        resolve_agent_model_selection(
            _reg_with("qwen/toggle-only", provider="openai"),
            _sel("qwen/toggle-only", "high"),
        )


def test_non_reasoning_model_rejects_any_effort():
    with pytest.raises(ValueError, match="not a reasoning model"):
        resolve_agent_model_selection(
            _reg_with("openai/plain"), _sel("openai/plain", "high"),
        )


def test_unknown_model_falls_back_to_the_permissive_outer_bound():
    """models.dev LAGS new releases (x-ai/grok-4.6 had no ZenMux entry when this
    snapshot was taken). A stale registry must never block a model that works."""
    resolved = resolve_agent_model_selection(
        _reg_with("x-ai/brand-new"), _sel("x-ai/brand-new", "xhigh"),
    )
    assert resolved["reasoning_effort"] == "xhigh"


def test_unknown_model_still_rejects_a_typo():
    with pytest.raises(ValueError, match="unsupported reasoning_effort"):
        resolve_agent_model_selection(
            _reg_with("x-ai/brand-new"), _sel("x-ai/brand-new", "ultra"),
        )


def test_the_string_none_is_a_request_not_an_absence():
    """`"none"` explicitly asks the model to skip reasoning — a request that gets
    SENT. Collapsing it to unset would silently turn "no thinking" into "vendor
    default thinking"."""
    reg = _reg_with("openai/wide")
    resolved = resolve_agent_model_selection(reg, _sel("openai/wide", "none"))
    assert resolved["reasoning_effort"] == "none"
    assert build_agent_model(reg, resolved).reasoning_effort == "none"


def test_resolve_selection_rejects_an_unknown_effort():
    """A typo must fail loudly. Forwarded to the provider it would be IGNORED, so
    the operator would read 'effort had no effect' instead of 'effort never applied'."""
    with pytest.raises(ValueError, match="unsupported reasoning_effort"):
        resolve_agent_model_selection(_registry(), _sel("openai/gpt-5.4", "ultra"))


def test_anthropic_protocol_carries_effort_via_output_config():
    """The anthropic protocol does NOT block effort — it uses a different field.

    Measured: the Messages API takes `output_config.effort` (a named level), so an
    anthropic-routed model is configured, not refused. An earlier version refused
    every such model on the belief that the protocol only budgets thinking in
    tokens, which wrongly denied effort to 7 of 8 models routed this way —
    including four that are not Claude at all.
    """
    from app.services.deep_agent import reasoning_capabilities as rc

    rc.reload_snapshot({"routes": {"zenmux": {"anthropic/claude-sonnet-4-6": {
        "reasoning": True, "efforts": ["low", "medium", "high", "max"],
        "toggle": False, "source": "measured",
    }}}})
    sel = _sel("anthropic/claude-sonnet-4-6", "max", provider="anthropic")
    resolved = resolve_agent_model_selection(_registry(), sel)
    assert resolved["reasoning_effort"] == "max"

    model = build_agent_model(_registry(), resolved)
    assert model.output_config == {"effort": "max"}

    # And a level this model was measured to reject still fails (sonnet-4.6 really
    # does refuse xhigh: "This model does not support effort level 'xhigh'").
    with pytest.raises(ValueError, match="does not accept reasoning_effort"):
        resolve_agent_model_selection(
            _registry(),
            _sel("anthropic/claude-sonnet-4-6", "xhigh", provider="anthropic"),
        )
    rc.reload_snapshot()


def test_anthropic_model_sends_no_output_config_when_unset():
    """Unset must reproduce the original request exactly."""
    model = build_agent_model(
        _registry(), _sel("anthropic/claude-sonnet-4-6", provider="anthropic")
    )
    assert getattr(model, "output_config", None) is None


def test_build_agent_model_applies_an_explicit_effort():
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4", "low"))
    assert model.reasoning_effort == "low"


def test_build_agent_model_sends_no_effort_when_unset():
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4"))
    assert getattr(model, "reasoning_effort", None) is None


def test_explicit_effort_beats_the_env_sweep(monkeypatch):
    """explicit-arg → process-env → vendor default, the same ladder the gateway
    bridge documents for model choice."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "low")
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4", "high"))
    assert model.reasoning_effort == "high"


def test_env_sweep_still_applies_when_no_explicit_effort(monkeypatch):
    # `low`, not `minimal`: gpt-5.4 declares low/medium/high, and the sweep is now
    # filtered against the model's own ladder — this test used to pass by sending a
    # level the model does not accept.
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "low")
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4"))
    assert model.reasoning_effort == "low"


def test_agent_model_config_exposes_a_per_model_effort_ladder():
    """The composer offers only levels the selected model accepts, so the UI can
    never present a level the server would reject."""
    cfg = agent_model_config(_reg_with("openai/wide"))
    by_id = {
        m["model"]: m
        for ch in cfg["channels"] for m in ch["models"]
    }
    assert by_id["openai/gpt-5.4"]["reasoning_efforts"] == ["low", "medium", "high"]
    assert "max" in by_id["openai/wide"]["reasoning_efforts"]
    # Anthropic-protocol models are NOT excluded — they carry effort through
    # output_config.effort. Unmeasured here, so they get the permissive bound.
    assert by_id["anthropic/claude-sonnet-4-6"]["reasoning_efforts"] != []
    # Unknown to the snapshot → the permissive outer bound, matching the server.
    assert by_id["deepseek-v4-flash"]["reasoning_efforts"] != []


def test_env_sweep_is_skipped_for_a_model_with_no_ladder(monkeypatch, caplog):
    """The env sweep has no request boundary to validate at, so it is filtered
    here. It previously sent `reasoning_effort=high` to a toggle-only model, which
    the provider ignores — an A/B sweep would have silently included models that
    never varied and read as "effort had no effect on this one"."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "high")
    reg = _reg_with("qwen/toggle-only", provider="openai")
    with caplog.at_level("WARNING"):
        model = build_agent_model(reg, _sel("qwen/toggle-only"))
    assert getattr(model, "reasoning_effort", None) is None
    # Skipped, but never SILENTLY — the operator must be able to see why.
    assert "not applied to qwen/toggle-only" in caplog.text


def test_env_sweep_is_skipped_for_a_non_reasoning_model(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "high")
    model = build_agent_model(_reg_with("openai/plain"), _sel("openai/plain"))
    assert getattr(model, "reasoning_effort", None) is None


def test_env_sweep_is_skipped_when_the_level_is_outside_the_model_ladder(monkeypatch):
    """`openai/gpt-5.4` takes low/medium/high; a sweep at xhigh must not reach it."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "xhigh")
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4"))
    assert getattr(model, "reasoning_effort", None) is None


def test_env_sweep_still_reaches_a_model_that_accepts_it(monkeypatch):
    """The filter must not be so broad that it disables the sweep entirely."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "high")
    model = build_agent_model(_registry(), _sel("openai/gpt-5.4"))
    assert model.reasoning_effort == "high"


def test_a_misspelled_env_sweep_fails_loudly(monkeypatch):
    """Unvalidated, a typo was forwarded verbatim to the provider, which ignores
    it — so a mistyped sweep looked like a finding rather than a config error."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "bogus")
    with pytest.raises(ValueError, match="unsupported reasoning_effort"):
        build_agent_model(_registry(), _sel("openai/gpt-5.4"))


def test_an_unknown_model_still_receives_the_env_sweep(monkeypatch):
    """Permissive fallback applies to the sweep too, or a new release would be
    silently excluded from an A/B."""
    monkeypatch.setenv("OPEN_OTC_MODEL_REASONING_EFFORT", "xhigh")
    model = build_agent_model(_reg_with("x-ai/brand-new"), _sel("x-ai/brand-new"))
    assert model.reasoning_effort == "xhigh"


def test_an_unmeasured_ladder_never_rejects():
    """Only MEASURED data gates. A live probe found models.dev too narrow for 13 of
    22 routes — it lists `openai/gpt-5.5` without `none`/`xhigh` and `z-ai/glm-5.2`
    as high/max when every level is accepted — so rejecting on it would deny levels
    that demonstrably work. The gateway refuses an unsupported level itself, with a
    precise message, so it is a reliable backstop.
    """
    from app.services.deep_agent import reasoning_capabilities as rc

    rc.reload_snapshot({"routes": {"zenmux": {"openai/gpt-5.4": {
        "reasoning": True, "efforts": ["low"], "toggle": False,
        "source": "models.dev",       # NOT measured
    }}}})
    resolved = resolve_agent_model_selection(_registry(), _sel("openai/gpt-5.4", "xhigh"))
    assert resolved["reasoning_effort"] == "xhigh"
    rc.reload_snapshot()


def test_effort_rejection_is_the_shared_seam():
    """`queue_arena_run`, `resolve_agent_model_selection` and both UI ladder
    builders must agree, or a board passes launch validation and then dies
    per-match. All of them call `effort_rejection`."""
    from app.services.deep_agent.model_factory import effort_rejection

    # Measured + off-ladder → rejected.
    assert effort_rejection(
        _registry(), "zenmux", "openai", "openai/gpt-5.4", "xhigh"
    ) is not None
    # Measured + on-ladder → allowed.
    assert effort_rejection(
        _registry(), "zenmux", "openai", "openai/gpt-5.4", "high"
    ) is None
    # Unknown selection → the caller reports it; not this seam's job.
    assert effort_rejection(
        _registry(), "zenmux", "openai", "no/such-model", "high"
    ) is None


def test_anthropic_protocol_gets_an_explicit_max_tokens_not_langchains_fallback():
    """The Anthropic branch must SEND max_tokens, never inherit langchain's default.

    langchain-anthropic applies ``_FALLBACK_MAX_OUTPUT_TOKENS`` (4096) whenever it
    has no profile for the model id, and it has none for any id routed through the
    ZenMux gateway — ``anthropic/…`` ones included, because the vendor prefix
    defeats its lookup. The resulting cap truncated reasoning models mid-thought:
    the turn emits a lone ``reasoning`` block with no text and no tool call, so it
    produces nothing while the span still reports ``success``. Arena run #114 lost
    five turns that way and the infra-blank gate could not see it, because that
    gate corroborates blankness with step ERRORS and a truncation raises none.
    """
    from langchain_anthropic.chat_models import _FALLBACK_MAX_OUTPUT_TOKENS

    model = build_agent_model(_registry())
    assert model.max_tokens == get_settings().agent_max_output_tokens
    assert model.max_tokens > _FALLBACK_MAX_OUTPUT_TOKENS


def test_openai_protocol_sends_no_max_tokens_so_the_provider_default_applies():
    """The two protocols must not handicap each other.

    ChatOpenAI deliberately sends no ``max_tokens``, so an OpenAI-protocol
    contestant runs at its provider default. Capping it here would re-introduce
    the asymmetry the Anthropic fix exists to remove — in the other direction.
    """
    model = build_agent_model(
        _registry(),
        selection={"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"},
    )
    assert getattr(model, "max_tokens", None) is None


# ---------------------------------------------------------------------------
# A pinned output budget rides the model-selection dict (M4)
# ---------------------------------------------------------------------------

def test_pinned_budget_overrides_the_settings_default_on_the_anthropic_branch():
    model = build_agent_model(_registry(), selection={
        "channel": "zenmux", "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6", "max_output_tokens": 4096,
    })
    assert model.max_tokens == 4096


def test_pinned_budget_also_applies_on_the_openai_branch():
    """A budget arm must not be silently anthropic-only.

    Without this the OpenAI-protocol contestants would run at their provider
    default under BOTH arms of a budget A/B and report a null result as if the
    budget had actually been varied.
    """
    model = build_agent_model(_registry(), selection={
        "channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4",
        "max_output_tokens": 8192,
    })
    assert model.max_tokens == 8192


def test_resolved_selection_omits_the_budget_when_unset():
    """Omitted-when-unset keeps the prebuilt-orchestrator reuse check working:
    the resolved dict is compared by EQUALITY against the default selection, so
    a fifth key present on every turn would end that reuse for every turn."""
    from app.services.deep_agent.model_factory import resolve_agent_model_selection
    resolved = resolve_agent_model_selection(_registry(), {
        "channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4",
    })
    assert "max_output_tokens" not in resolved


def test_resolved_selection_refuses_a_nonsense_budget_rather_than_clamping():
    """0 is the DB sentinel for "unpinned", so accepting it would make a pin
    indistinguishable from none; a coerced value would report a regime that
    never ran."""
    from app.services.deep_agent.model_factory import resolve_agent_model_selection
    for bad in (0, -1, "abc"):
        with pytest.raises(ValueError):
            resolve_agent_model_selection(_registry(), {
                "channel": "zenmux", "provider": "openai",
                "model": "openai/gpt-5.4", "max_output_tokens": bad,
            })
