"""The arena must route document extraction to the CONTESTANT.

Without this, `resolve_confirmation_extractor_selection` picks the extraction
model by REGISTRY TAG, so every contestant on a vision board reads every document
with whichever model holds `confirmation_extractor` -- currently
google/gemini-3.6-flash. Every vision check would then land N/N across the field
and carry zero ability signal while still occupying the denominator: the defect
the Run #58 scoring-validity audit found in 15 of 50 checks.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.golden_workflows.schema import GoldenWorkflow


def _wf(**over):
    base = dict(
        id="x", schema_version=1, persona="trader", title="T", objective="O",
        fixtures="x.fixtures.json",
        steps=[{"user": "u", "expected_skill": None, "outcome": "o", "replay": "r"}],
        success={"assertions": [], "rubric": []},
    )
    base.update(over)
    return GoldenWorkflow(**base)


# ---------------------------------------------------------------------------
# the manifest field
# ---------------------------------------------------------------------------


def test_extractor_model_defaults_to_none():
    """Unset must be byte-identical to today for the five existing workflows."""
    assert _wf().extractor_model is None


def test_extractor_model_accepts_contestant():
    assert _wf(extractor_model="contestant").extractor_model == "contestant"


def test_extractor_model_rejects_an_arbitrary_model_id():
    """The field names a ROUTING POLICY, not a model.

    Allowing a model id would let a manifest pin extraction to one model and
    silently un-level the very board it is scoring.
    """
    with pytest.raises(Exception):
        _wf(extractor_model="google/gemini-3.6-flash")


def test_no_existing_workflow_declares_the_field():
    """Every board through #132 must keep the production tag ladder."""
    from app.golden_workflows.registry import list_workflow_bundles

    for bundle in list_workflow_bundles():
        if bundle.workflow.id == "confirmation-desk-day":
            continue
        assert bundle.workflow.extractor_model is None, bundle.workflow.id


# ---------------------------------------------------------------------------
# the runner seam
# ---------------------------------------------------------------------------


def test_drive_stamps_the_contestant_selection_when_routing_is_on(monkeypatch):
    from app.services.arena import runner

    seen: dict = {}

    def _fake_drive_step(thread_id, content, selection, *, accounting_date,
                         extractor_selection=None):
        seen["extractor_selection"] = extractor_selection

    monkeypatch.setattr(runner, "_drive_step", _fake_drive_step)
    selection = {"channel": "zenmux", "provider": "bigmodel",
                 "model": "z-ai/glm-5.3-flash"}

    drive = runner._make_default_drive(None, route_extractor_to_contestant=True)
    drive(1, "hello", selection)
    assert seen["extractor_selection"] == selection


def test_drive_stamps_nothing_when_routing_is_off(monkeypatch):
    from app.services.arena import runner

    seen: dict = {"extractor_selection": "unset"}

    def _fake_drive_step(thread_id, content, selection, *, accounting_date,
                         extractor_selection=None):
        seen["extractor_selection"] = extractor_selection

    monkeypatch.setattr(runner, "_drive_step", _fake_drive_step)
    drive = runner._make_default_drive(None)
    drive(1, "hello", {"channel": "c", "provider": "p", "model": "m"})
    assert seen["extractor_selection"] is None


def test_drive_still_carries_the_accounting_date(monkeypatch):
    """The new kwarg must not displace the existing one."""
    from app.services.arena import runner

    seen: dict = {}

    def _fake_drive_step(thread_id, content, selection, *, accounting_date,
                         extractor_selection=None):
        seen["accounting_date"] = accounting_date

    monkeypatch.setattr(runner, "_drive_step", _fake_drive_step)
    drive = runner._make_default_drive("2026-08-12", route_extractor_to_contestant=True)
    drive(1, "hello", {"channel": "c", "provider": "p", "model": "m"})
    assert seen["accounting_date"] == "2026-08-12"


# ---------------------------------------------------------------------------
# BOTH configurable builds inside stream_and_persist
# ---------------------------------------------------------------------------


def test_both_configurable_builds_stamp_the_key():
    """stream_and_persist has TWO independent `configurable_extra` dicts -- the
    workflow-routed path (_prepare_workflow_routed_stream_turn) and the direct
    path -- and which one runs depends on settings.feature_workflow_routing.

    Stamping only one means the override silently does not apply on the other,
    and the board would measure the tag-resolved model with no error anywhere.
    Same class as the `done` SSE event, which had to be emitted from both
    finalize paths for exactly this reason.
    """
    source = Path("backend/app/services/agents.py").read_text()
    tree = ast.parse(source)

    builds = 0
    stamped = 0
    for node in ast.walk(tree):
        # A `configurable_extra: dict[str, Any] = { ... }` annotated assignment.
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if not (isinstance(target, ast.Name) and target.id == "configurable_extra"):
            continue
        builds += 1
        literal = ast.unparse(node.value) if node.value is not None else ""
        if "CONFIRMATION_EXTRACTOR_SELECTION_KEY" in literal:
            stamped += 1

    assert builds == 2, f"expected 2 configurable_extra builds, found {builds}"
    assert stamped == builds, (
        f"only {stamped} of {builds} configurable_extra builds stamp the extractor "
        "override key -- the unstamped path silently falls back to tag routing"
    )
