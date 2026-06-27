"""Tests for the pure composition builder + writer (Phase 3, Task 15).

TDD — tests were written before the implementation.

Coverage:
1.  ``build_composition`` returns a ``CompositionBundle`` with correct shape.
2.  ``build_composition`` is pure — no files written to the filesystem.
3.  Section 0 carries the correct narration block and a tool_call event for
    ``get_latest_risk_run`` (the step-1 tool in the flagship workflow).
4.  ``narrator_scripts`` has one entry per section.
5.  ``write_composition(bundle, out_dir=tmp)`` writes the two JSON files.
6.  ``write_composition(bundle, out_dir=None)`` computes the default path
    ``artifacts/demos/<workflow_id>/<source>/``.
7.  ValueError raised when transcript step count ≠ workflow step count.
8.  ValueError raised when narration block count ≠ workflow step count
    (simulated by patching narration).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.demo.composition import (
    CompositionBundle,
    Section,
    build_composition,
    write_composition,
)


FLAGSHIP_ID = "risk-manager-control-day"
FLAGSHIP_STEP_COUNT = 7


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loaded():
    return get_workflow_bundle(FLAGSHIP_ID)


@pytest.fixture(scope="module")
def transcript(loaded):
    return transcript_from_replay(loaded)


@pytest.fixture(scope="module")
def bundle(loaded, transcript):
    return build_composition(loaded, transcript, source="regression")


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------

def test_bundle_workflow_id(bundle):
    assert bundle.workflow_id == FLAGSHIP_ID


def test_bundle_source(bundle):
    assert bundle.source == "regression"


def test_bundle_section_count(bundle):
    assert len(bundle.section_plan) == FLAGSHIP_STEP_COUNT


def test_bundle_narrator_scripts_count(bundle):
    assert len(bundle.narrator_scripts) == FLAGSHIP_STEP_COUNT


def test_sections_are_ordered(bundle):
    for i, section in enumerate(bundle.section_plan):
        assert section.index == i


def test_bundle_is_composition_bundle(bundle):
    assert isinstance(bundle, CompositionBundle)


def test_sections_are_section_instances(bundle):
    for s in bundle.section_plan:
        assert isinstance(s, Section)


# ---------------------------------------------------------------------------
# Section-0 (step-1: Read stale risk) content
# ---------------------------------------------------------------------------

def test_section_0_user(bundle, loaded):
    expected_user = loaded.workflow.steps[0].user
    assert bundle.section_plan[0].user == expected_user


def test_section_0_narration_non_empty(bundle):
    assert bundle.section_plan[0].narration.strip() != ""


def test_section_0_narration_matches_workflow(bundle, loaded):
    assert bundle.section_plan[0].narration == loaded.workflow.narration[0]


def test_section_0_has_tool_call_event_for_get_latest_risk_run(bundle):
    """Step 1 in the flagship calls get_latest_risk_run (or get_latest_risk_run_tool);
    the section must carry at least one tool_call event whose name normalises to
    'get_latest_risk_run' (trailing '_tool' stripped)."""
    from app.golden_workflows.schema import normalize_tool_name

    section = bundle.section_plan[0]
    tool_call_events = [e for e in section.events if e["kind"] == "tool_call"]
    names = [normalize_tool_name(e["name"]) for e in tool_call_events]
    assert "get_latest_risk_run" in names, (
        f"Expected 'get_latest_risk_run' (normalised) in tool_call events; got: {names}"
    )


def test_section_0_has_outcome_event(bundle):
    section = bundle.section_plan[0]
    outcome_events = [e for e in section.events if e["kind"] == "outcome"]
    assert len(outcome_events) == 1
    assert outcome_events[0]["text"].strip() != ""


def test_section_0_outcome_text_matches_workflow(bundle, loaded):
    outcome_events = [e for e in bundle.section_plan[0].events if e["kind"] == "outcome"]
    assert outcome_events[0]["text"] == loaded.workflow.steps[0].outcome


# ---------------------------------------------------------------------------
# Narrator scripts content
# ---------------------------------------------------------------------------

def test_narrator_scripts_match_narration(bundle, loaded):
    """narrator_scripts[i] must equal the narration prose for section i."""
    for i, script in enumerate(bundle.narrator_scripts):
        assert script == loaded.workflow.narration[i], (
            f"narrator_scripts[{i}] does not match workflow.narration[{i}]"
        )


# ---------------------------------------------------------------------------
# Purity test — build_composition writes NO files
# ---------------------------------------------------------------------------

def test_build_composition_is_pure(loaded, transcript, tmp_path, monkeypatch):
    """build_composition must not write to or create any files.

    Strategy: change cwd to a fresh tmp_path, run build_composition, then
    assert tmp_path is still empty.  This catches any accidental relative-path
    writes.  We also assert that build_composition does not resolve to the
    artifacts/ path (i.e. it doesn't touch absolute paths either, but that is
    harder to assert universally — cwd isolation is the normative check).
    """
    monkeypatch.chdir(tmp_path)

    build_composition(loaded, transcript, source="regression")

    # tmp_path must remain empty — build_composition wrote nothing
    all_files = list(tmp_path.rglob("*"))
    assert all_files == [], (
        f"build_composition wrote unexpected files: {all_files}"
    )


# ---------------------------------------------------------------------------
# write_composition — explicit out_dir
# ---------------------------------------------------------------------------

def test_write_composition_creates_files(bundle, tmp_path):
    result_dir = write_composition(bundle, out_dir=tmp_path)

    assert result_dir == tmp_path
    assert (tmp_path / "section_plan.json").exists()
    assert (tmp_path / "narrator_scripts.json").exists()


def test_write_composition_section_plan_json_valid(bundle, tmp_path):
    write_composition(bundle, out_dir=tmp_path)
    data = json.loads((tmp_path / "section_plan.json").read_text())
    assert isinstance(data, list)
    assert len(data) == FLAGSHIP_STEP_COUNT
    # Each entry must have the required fields
    for entry in data:
        assert "index" in entry
        assert "narration" in entry
        assert "user" in entry
        assert "events" in entry
        assert isinstance(entry["events"], list)


def test_write_composition_narrator_scripts_json_valid(bundle, tmp_path):
    write_composition(bundle, out_dir=tmp_path)
    data = json.loads((tmp_path / "narrator_scripts.json").read_text())
    assert isinstance(data, list)
    assert len(data) == FLAGSHIP_STEP_COUNT
    for script in data:
        assert isinstance(script, str)


def test_write_composition_returns_path(bundle, tmp_path):
    result = write_composition(bundle, out_dir=tmp_path)
    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# write_composition — default path (out_dir=None)
# ---------------------------------------------------------------------------

def test_write_composition_default_path_shape(bundle, tmp_path, monkeypatch):
    """When out_dir is None the default path must be
    artifacts/demos/<workflow_id>/<source>/ relative to cwd."""
    monkeypatch.chdir(tmp_path)

    result = write_composition(bundle, out_dir=None)

    expected = (
        tmp_path / "artifacts" / "demos" / bundle.workflow_id / bundle.source
    )
    assert result == expected
    assert (result / "section_plan.json").exists()
    assert (result / "narrator_scripts.json").exists()


def test_write_composition_default_path_string(bundle, tmp_path, monkeypatch):
    """The returned path string must contain artifacts/demos/<id>/<source>."""
    monkeypatch.chdir(tmp_path)
    result = write_composition(bundle, out_dir=None)
    path_str = str(result)
    assert "artifacts" in path_str
    assert "demos" in path_str
    assert bundle.workflow_id in path_str
    assert bundle.source in path_str


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_build_composition_wrong_transcript_length(loaded, transcript):
    """Passing a transcript with the wrong number of steps raises ValueError."""
    from app.golden_workflows.transcript import MatchTranscript

    # Truncate to 3 steps
    short_transcript = MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=transcript.workflow_id,
        model_id=transcript.model_id,
        started_at=None,
        finished_at=None,
        steps=transcript.steps[:3],
    )
    with pytest.raises(ValueError, match="transcript step count"):
        build_composition(loaded, short_transcript, source="regression")


def test_build_composition_wrong_narration_length(loaded, transcript):
    """If the workflow narration is mismatched, build_composition raises ValueError."""
    from unittest.mock import patch

    # Patch the narration to have fewer entries
    truncated_narration = loaded.workflow.narration[:3]
    with patch.object(loaded.workflow, "narration", truncated_narration):
        with pytest.raises(ValueError, match="narration block count"):
            build_composition(loaded, transcript, source="regression")
