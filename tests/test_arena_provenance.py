"""Every arena run records WHICH manifest and WHICH app produced it.

Before 2026-09-25 nothing did, so a manifest edit or a harness upgrade silently
changed what every later number meant, and a resume could splice two versions
of a test into one board.
"""
from __future__ import annotations

import shutil

from app.golden_workflows.registry import get_workflow_bundle, load_workflow_bundle
from app.services.arena import provenance

_WF = "risk-limit-breach-day"


def _copy_workflow(tmp_path):
    src = get_workflow_bundle(_WF).definition_path
    shutil.copy(src, tmp_path / src.name)
    fixtures = src.parent / get_workflow_bundle(_WF).workflow.fixtures
    shutil.copy(fixtures, tmp_path / fixtures.name)
    return tmp_path / src.name, tmp_path / fixtures.name


def test_fingerprint_is_stable_and_carries_version_and_par():
    a = provenance.manifest_fingerprint(get_workflow_bundle(_WF))
    b = provenance.manifest_fingerprint(get_workflow_bundle(_WF))
    assert a == b
    assert a["manifest_version"] >= 1
    assert len(a["sha256"]) == 64
    assert isinstance(a["par_tool_calls"], int)


def test_fingerprint_changes_when_the_fixtures_change(tmp_path):
    """The fixtures file scores as much as the .md does (seed, replay), so an
    edit there is a new test even when nobody bumps manifest_version."""
    md, fixtures = _copy_workflow(tmp_path)
    before = provenance.manifest_fingerprint(load_workflow_bundle(md))
    fixtures.write_text(fixtures.read_text().rstrip() + "\n\n")
    after = provenance.manifest_fingerprint(load_workflow_bundle(md))
    assert before["manifest_version"] == after["manifest_version"]
    assert before["sha256"] != after["sha256"]


def test_app_provenance_names_version_commit_and_stack():
    app = provenance.app_provenance()
    assert app["version"]
    assert app["label"].startswith(app["version"])
    assert "deepagents" in app["packages"] and "quantark" in app["packages"]


def test_drift_is_reported_against_the_stamp_and_silent_when_unstamped():
    stamped = provenance.run_provenance([_WF])
    assert provenance.manifest_drift(stamped, [_WF]) == {}

    stale = {"manifests": {_WF: {**stamped["manifests"][_WF], "sha256": "0" * 64}}}
    drift = provenance.manifest_drift(stale, [_WF])
    assert set(drift) == {_WF}
    assert drift[_WF]["current"]["sha256"] == stamped["manifests"][_WF]["sha256"]

    # A pre-stamp run cannot be checked; {} here means "unknown", and callers
    # that need a guarantee check for the stamp themselves.
    assert provenance.manifest_drift(None, [_WF]) == {}
