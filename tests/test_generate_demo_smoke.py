"""Smoke tests for scripts/generate_demo.py (Task 16, Phase 3).

TDD: these tests were written before the implementation.

Coverage
--------
1.  Smoke: regression run writes section_plan.json + narrator_scripts.json;
    exits 0; NO render/subprocess called when DEMO_RENDER is unset.
2.  Missing-input: --source arena without --transcript-path → non-zero exit.
3.  DEMO_RENDER=1 gating: _render IS called when DEMO_RENDER=1 (render mocked).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Import helpers — load generate_demo from scripts/ without installing it
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_generate_demo():
    """Load scripts/generate_demo.py as a module via importlib."""
    spec = importlib.util.spec_from_file_location(
        "generate_demo",
        _SCRIPTS_DIR / "generate_demo.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load once at module import time so all tests share the same module object.
generate_demo = _load_generate_demo()


# ---------------------------------------------------------------------------
# Test 1: Smoke — regression run, DEMO_RENDER unset
# ---------------------------------------------------------------------------

def test_smoke_regression_writes_files_no_render(tmp_path, monkeypatch):
    """main() writes section_plan.json + narrator_scripts.json and returns 0.

    DEMO_RENDER must be unset.  subprocess must NOT be called.
    """
    # Ensure DEMO_RENDER is not set
    monkeypatch.delenv("DEMO_RENDER", raising=False)

    # Patch _render so we can assert it was NOT called
    mock_render = MagicMock()
    with patch.object(generate_demo, "_render", mock_render):
        result = generate_demo.main([
            "--workflow-id", "risk-manager-control-day",
            "--source", "regression",
            "--output-dir", str(tmp_path),
        ])

    # Exit code must be 0
    assert result == 0, f"main() returned non-zero exit code: {result}"

    # Both JSON files must exist
    section_plan = tmp_path / "section_plan.json"
    narrator_scripts = tmp_path / "narrator_scripts.json"
    assert section_plan.exists(), "section_plan.json was not written"
    assert narrator_scripts.exists(), "narrator_scripts.json was not written"

    # Validate JSON is parseable and non-empty
    plan_data = json.loads(section_plan.read_text())
    assert isinstance(plan_data, list), "section_plan.json must be a JSON list"
    assert len(plan_data) > 0, "section_plan.json must not be empty"

    scripts_data = json.loads(narrator_scripts.read_text())
    assert isinstance(scripts_data, list), "narrator_scripts.json must be a JSON list"
    assert len(scripts_data) > 0, "narrator_scripts.json must not be empty"

    # _render must NOT have been called (DEMO_RENDER unset)
    mock_render.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: subprocess not called (belt-and-suspenders) when DEMO_RENDER unset
# ---------------------------------------------------------------------------

def test_smoke_subprocess_not_called_when_demo_render_unset(tmp_path, monkeypatch):
    """subprocess.run must not be called when DEMO_RENDER is not set."""
    monkeypatch.delenv("DEMO_RENDER", raising=False)

    with patch("subprocess.run") as mock_sub:
        generate_demo.main([
            "--workflow-id", "risk-manager-control-day",
            "--source", "regression",
            "--output-dir", str(tmp_path),
        ])

    mock_sub.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Missing transcript path for arena source → non-zero exit
# ---------------------------------------------------------------------------

def test_arena_without_transcript_path_exits_nonzero(tmp_path):
    """--source arena without --transcript-path must exit non-zero."""
    with pytest.raises(SystemExit) as exc_info:
        generate_demo.main([
            "--workflow-id", "risk-manager-control-day",
            "--source", "arena",
            "--output-dir", str(tmp_path),
            # intentionally omitting --transcript-path
        ])

    assert exc_info.value.code != 0, (
        "Expected non-zero SystemExit but got 0"
    )


# ---------------------------------------------------------------------------
# Test 4: DEMO_RENDER=1 → _render IS called
# ---------------------------------------------------------------------------

def test_demo_render_env_calls_render(tmp_path, monkeypatch):
    """When DEMO_RENDER=1, _render must be called after composition is written."""
    monkeypatch.setenv("DEMO_RENDER", "1")

    mock_render = MagicMock(return_value=None)  # mock: pretend render succeeds
    with patch.object(generate_demo, "_render", mock_render):
        result = generate_demo.main([
            "--workflow-id", "risk-manager-control-day",
            "--source", "regression",
            "--output-dir", str(tmp_path),
        ])

    # _render must have been called exactly once
    mock_render.assert_called_once()

    # The bundle and out_dir are the two positional args to _render
    call_args = mock_render.call_args
    bundle_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("bundle")
    out_dir_arg = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("out_dir")

    assert bundle_arg is not None, "_render was not passed a bundle"
    assert out_dir_arg is not None, "_render was not passed an out_dir"
    assert out_dir_arg == tmp_path

    # Exit code must still be 0 (render mock succeeded)
    assert result == 0


# ---------------------------------------------------------------------------
# Test 5: DEMO_RENDER=1 + _render raises → exit code 3
# ---------------------------------------------------------------------------

def test_demo_render_failure_returns_exit_3(tmp_path, monkeypatch):
    """When _render raises RuntimeError, main() returns 3."""
    monkeypatch.setenv("DEMO_RENDER", "1")

    mock_render = MagicMock(side_effect=RuntimeError("stage 'tts' failed"))
    with patch.object(generate_demo, "_render", mock_render):
        result = generate_demo.main([
            "--workflow-id", "risk-manager-control-day",
            "--source", "regression",
            "--output-dir", str(tmp_path),
        ])

    assert result == 3, f"Expected exit code 3 on render failure, got {result}"

    # Composition must still be written even when render fails
    assert (tmp_path / "section_plan.json").exists(), (
        "section_plan.json must be written even when render fails"
    )
    assert (tmp_path / "narrator_scripts.json").exists(), (
        "narrator_scripts.json must be written even when render fails"
    )
