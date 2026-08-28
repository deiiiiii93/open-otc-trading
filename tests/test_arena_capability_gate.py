"""A golden workflow may REQUIRE a model capability the registry declares.

Rejection happens at LAUNCH, not per match. `queue_arena_run` once kept its own
narrower check than the per-match path, and a board passed launch validation and
then died arm by arm after other pairs had already cost real money -- the lesson
that made `effort_rejection` a single shared seam.

The failure this gate prevents is worse than an error: a text-only model on a
vision workflow produces a real-LOOKING score that pollutes the board, and it is
indistinguishable from genuinely poor ability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.golden_workflows.schema import GoldenWorkflow


class _Model:
    def __init__(self, *tags):
        self.tags = tuple(tags)


class _Registry:
    def __init__(self, model=None, raises=False):
        self._model = model
        self._raises = raises

    def find_model(self, channel, provider, model):
        if self._raises:
            raise KeyError(model)
        return object(), self._model


_SEL = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}


# ---------------------------------------------------------------------------
# capability_rejection
# ---------------------------------------------------------------------------


def test_a_tagged_model_is_accepted():
    from app.services.arena.task import capability_rejection

    registry = _Registry(_Model("fast", "tool-use", "vision"))
    assert capability_rejection(registry, _SEL, "vision") is None


def test_an_untagged_model_is_rejected_and_the_reason_names_the_capability():
    from app.services.arena.task import capability_rejection

    registry = _Registry(_Model("fast", "tool-use"))
    reason = capability_rejection(registry, _SEL, "vision")
    assert reason is not None
    assert "vision" in reason
    # The reason must also name the MODEL, or a multi-model board's error is
    # unactionable.
    assert "glm-5.3-flash" in reason


def test_an_unresolvable_route_is_permissive():
    """UNKNOWN IS PERMISSIVE, never "unsupported".

    Same rule the reasoning-effort ladder follows: stale or missing registry data
    must never block a model that actually works.
    """
    from app.services.arena.task import capability_rejection

    assert capability_rejection(_Registry(raises=True), _SEL, "vision") is None


def test_a_model_with_no_tags_at_all_is_rejected_not_crashed():
    from app.services.arena.task import capability_rejection

    class _Untagged:
        tags = None

    reason = capability_rejection(_Registry(_Untagged()), _SEL, "vision")
    assert reason is not None


# ---------------------------------------------------------------------------
# the manifest field
# ---------------------------------------------------------------------------


def test_requires_defaults_to_empty():
    wf = GoldenWorkflow(
        id="x", schema_version=1, persona="trader", title="T", objective="O",
        fixtures="x.fixtures.json",
        steps=[{"user": "u", "expected_skill": None, "outcome": "o", "replay": "r"}],
        success={"assertions": [], "rubric": []},
    )
    assert wf.requires == []


def test_no_existing_workflow_requires_a_capability():
    """Every board through #132 must stay runnable by any model."""
    from app.golden_workflows.registry import list_workflow_bundles

    for bundle in list_workflow_bundles():
        if bundle.workflow.id == "confirmation-desk-day":
            continue
        assert bundle.workflow.requires == [], bundle.workflow.id


# ---------------------------------------------------------------------------
# the registry declaration
# ---------------------------------------------------------------------------


def _tracked_registry():
    from app.services.deep_agent.channel_registry import load_from_path

    return load_from_path(Path("config/agent_channels.example.yml"))


def test_the_tracked_template_declares_the_vision_capability():
    tagged = [
        md.id
        for ch in _tracked_registry().channels
        for md in ch.models
        if "vision" in md.tags
    ]
    assert tagged, "no model in the tracked template declares `vision`"


def test_every_vision_tagged_model_pins_an_upstream():
    """An unpinned id is a ZenMux provider LOTTERY, and a vision board must be
    reproducible: the same slug scoring 93.6 one week and 7.7 the next, with no
    code change, is exactly what the upstream pin exists to prevent."""
    for ch in _tracked_registry().channels:
        for md in ch.models:
            if "vision" not in md.tags:
                continue
            assert md.provider, f"{ch.name}/{md.id} declares vision with no upstream"


def test_the_three_board_contestants_declare_vision():
    """Probe-verified 2026-08-28: each read conf-04's image-only scan correctly
    (GOOGL / 205.00 / 615,000 / ARD-EQO-2026-04688)."""
    tagged = {
        md.id
        for ch in _tracked_registry().channels
        for md in ch.models
        if "vision" in md.tags
    }
    for model_id in (
        "openai/gpt-5.6-luna",
        "z-ai/glm-5.3-flash",
        "google/gemini-3.7-flash",
    ):
        assert model_id in tagged, f"{model_id} is a board contestant but untagged"


def test_the_production_extractor_declares_vision():
    """gemini-3.6-flash holds `confirmation_extractor`, so it reads scans on the
    real desk every day -- if it were not vision-capable, that pin is the bug."""
    tagged = {
        md.id
        for ch in _tracked_registry().channels
        for md in ch.models
        if "vision" in md.tags
    }
    assert "google/gemini-3.6-flash" in tagged


# ---------------------------------------------------------------------------
# the LAUNCH wiring (queue_arena_run)
# ---------------------------------------------------------------------------


def _requiring_bundle(capability: str | None):
    """A stand-in bundle declaring `requires`, so the launch gate is testable
    before confirmation-desk-day exists."""

    class _WF:
        requires = [capability] if capability else []

    class _Bundle:
        workflow = _WF()

    return _Bundle()


def _patch_bundle(monkeypatch, bundle):
    """Patch the REGISTRY module, not the arena_task attribute.

    `queue_arena_run` does a FUNCTION-SCOPE `from app.golden_workflows.registry
    import get_workflow_bundle`, so the name is rebound on every call and
    patching `arena_task.get_workflow_bundle` has no effect whatsoever -- the
    real lookup runs and raises "Unknown workflow_id", which an imprecise
    assertion can then match by accident.
    """
    from app.golden_workflows import registry as gw_registry

    monkeypatch.setattr(gw_registry, "get_workflow_bundle", lambda wid: bundle)


def _patch_channel_registry(monkeypatch, tags_by_model: dict[str, tuple[str, ...]]):
    """Inject a deterministic channel registry into the launch gate.

    Necessary for hermeticity, not convenience. Other tests in this suite repoint
    AGENT_CHANNELS_FILE, so the process-global registry is order-dependent; a gate
    test that reads it asserts on whatever the previous test left behind. Under
    that pollution the model does not resolve, `capability_rejection` correctly
    returns PERMISSIVE, and a test expecting a rejection fails -- for a reason
    that has nothing to do with the gate.

    Patched on the channel_registry module because queue_arena_run imports
    get_registry function-scope, same as get_workflow_bundle above.
    """
    from app.services.deep_agent import channel_registry as cr

    class _Stub:
        def find_model(self, channel, provider, model):
            for name, tags in tags_by_model.items():
                if name in str(model):
                    return object(), _Model(*tags)
            raise KeyError(model)

    monkeypatch.setattr(cr, "get_registry", lambda: _Stub())


# The distinctive half of the rejection message. Asserting on the bare word
# "vision" is NOT enough: the failure path embeds a filesystem path, and this
# worktree is literally named "arena-confirmation-vision", so a loose assertion
# passes on a completely unrelated error.
_REJECTION_MARKER = "does not declare"


def test_queue_rejects_a_model_lacking_a_required_capability(session, monkeypatch):
    from app.services.arena import task as arena_task

    _patch_bundle(monkeypatch, _requiring_bundle("vision"))
    _patch_channel_registry(monkeypatch, {"deepseek-v4-pro": ("tool-use", "reasoning")})
    with pytest.raises(ValueError) as exc:
        arena_task.queue_arena_run(
            session,
            workflow_ids=["any-workflow"],
            model_ids=["deepseek-v4-pro"],
        )
    message = str(exc.value)
    assert _REJECTION_MARKER in message, message
    assert "'vision'" in message, message
    assert "deepseek-v4-pro" in message, message


def test_queue_accepts_a_model_declaring_the_capability(session, monkeypatch):
    from app.services.arena import task as arena_task

    _patch_bundle(monkeypatch, _requiring_bundle("vision"))
    _patch_channel_registry(
        monkeypatch, {"gemini-3.7-flash": ("fast", "tool-use", "vision")}
    )
    # Must not raise. A false REJECT silently drops a real contestant from a
    # board, which is worse than a false accept (one bad match).
    arena_task.queue_arena_run(
        session,
        workflow_ids=["any-workflow"],
        model_ids=["gemini-3-7-flash"],
    )


def test_queue_is_unaffected_when_a_workflow_requires_nothing(session, monkeypatch):
    """Every existing board must launch exactly as before."""
    from app.services.arena import task as arena_task

    _patch_bundle(monkeypatch, _requiring_bundle(None))
    arena_task.queue_arena_run(
        session,
        workflow_ids=["any-workflow"],
        model_ids=["deepseek-v4-pro"],
    )


def test_local_and_tracked_registries_agree_on_vision_tags():
    """Tag edits go to BOTH files. The local one is gitignored and per-env, so a
    tag added only there would pass every test here and then be missing on any
    other machine -- and the launch gate would reject a real contestant."""
    from app.services.deep_agent.channel_registry import load_from_path

    local_path = Path("config/agent_channels.yaml")
    if not local_path.exists():  # a clean clone has no local file yet
        pytest.skip("no local agent_channels.yaml in this environment")

    def _tagged(registry):
        return {
            md.id for ch in registry.channels for md in ch.models if "vision" in md.tags
        }

    assert _tagged(load_from_path(local_path)) == _tagged(_tracked_registry())
