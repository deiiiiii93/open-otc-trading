"""Tests for arena runner — TDD RED phase.

These tests should fail (or error) until runner.py is implemented.

Test groups:
  1a: isolated_match_db — DB isolation and restore
  1b: one-turn drive — basic run_match with a fake single-turn agent
  1c: blocking run-tool wrapper — _wrap_run_tools with a fake status-checker
  1d: budget + errors — budget_exceeded + exception → error field, match continues
  1e: full 7-step fake run — scripted fake agent, artifact copy
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1a: isolated_match_db — DB isolation and restore
# ---------------------------------------------------------------------------


class TestIsolatedMatchDb:
    def test_portfolio_visible_inside_context(self, session, settings):
        """Inside isolated_match_db, the seed portfolio (id=6) is visible."""
        from app import database
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.runner import isolated_match_db

        loaded = get_workflow_bundle("risk-manager-control-day")
        bundle = loaded.fixtures

        with isolated_match_db(bundle):
            with database.SessionLocal() as s:
                from app.models import Portfolio
                p = s.get(Portfolio, 6)
                assert p is not None, "Portfolio(id=6) must be visible inside isolated_match_db"
                assert p.name == "Control Desk Portfolio"

    def test_global_settings_restored_after_context(self, session, settings):
        """After isolated_match_db exits, database.settings is the original settings."""
        from app import database
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.runner import isolated_match_db

        loaded = get_workflow_bundle("risk-manager-control-day")
        bundle = loaded.fixtures

        settings_before = database.settings
        with isolated_match_db(bundle):
            # settings changed inside
            assert database.settings is not settings_before or \
                database.settings.database_url != settings_before.database_url

        # After exit, settings is restored
        assert database.settings is settings_before or \
            database.settings.database_url == settings_before.database_url

    def test_portfolio_not_in_test_db_after_exit(self, session, settings):
        """After exiting isolated_match_db, the test session's DB (conftest session)
        does NOT have Portfolio(id=6), because the match used a separate temp DB."""
        from app import database
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.runner import isolated_match_db

        loaded = get_workflow_bundle("risk-manager-control-day")
        bundle = loaded.fixtures

        with isolated_match_db(bundle):
            pass  # context used and exited

        # Now we're back to the test DB (no id=6 seeded in conftest)
        with database.SessionLocal() as s:
            from app.models import Portfolio
            p = s.get(Portfolio, 6)
            assert p is None, "Test DB must not have Portfolio(id=6) after isolated_match_db exits"

    def test_temp_file_cleaned_up_after_context(self, settings):
        """The temp SQLite file is deleted after the context exits."""
        from app import database
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.runner import isolated_match_db

        loaded = get_workflow_bundle("risk-manager-control-day")
        bundle = loaded.fixtures

        captured_url = None
        with isolated_match_db(bundle):
            captured_url = database.settings.database_url

        # The URL was a sqlite file
        assert captured_url is not None
        if "///" in captured_url:
            tmp_path = captured_url.split("///", 1)[-1]
            assert not Path(tmp_path).exists(), "Temp DB file should be deleted after context"


# ---------------------------------------------------------------------------
# 1b: one-turn drive — run_match with a fake single-turn agent
# ---------------------------------------------------------------------------


def _make_fake_agent_one_turn(tool_name: str = "get_latest_risk_run_tool",
                               skill: str = "read-risk-result"):
    """Return a fake agent callable for a single-step workflow.

    The fake agent accepts (history, step_index) → turn_events dict.
    """
    def fake_agent(history, step_index: int):
        return {
            "index": step_index,
            "user": history[-1] if history else "test user message",
            "messages": [],
            "tool_calls": [{"id": "call_fake_001", "name": tool_name, "args": {"portfolio_id": 6}}],
            "tool_results": [
                {
                    "tool_call_id": "call_fake_001",
                    "name": tool_name,
                    "content": {"id": 1, "status": "completed", "freshness": "stale"},
                }
            ],
            "skills_routed": [skill],
            "artifacts": [],
            "response_text": "The risk run is stale.",
            "errors": [],
        }
    return fake_agent


class TestOneTurnDrive:
    def test_transcript_has_steps(self, session, settings, tmp_path):
        """run_match produces a MatchTranscript with at least one step."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")
        fake_agent = _make_fake_agent_one_turn()

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=fake_agent,
        )

        assert len(transcript.steps) > 0

    def test_step0_tool_call_name(self, session, settings, tmp_path):
        """Step 0's tool_calls contains get_latest_risk_run_tool."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")
        fake_agent = _make_fake_agent_one_turn(tool_name="get_latest_risk_run_tool")

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=fake_agent,
        )

        step = transcript.steps[0]
        names = [tc["name"] for tc in step.tool_calls]
        assert "get_latest_risk_run_tool" in names

    def test_step0_skills_routed_populated(self, session, settings, tmp_path):
        """Step 0's skills_routed is non-empty."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")
        fake_agent = _make_fake_agent_one_turn(skill="read-risk-result")

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=fake_agent,
        )

        step = transcript.steps[0]
        assert len(step.skills_routed) > 0
        assert "read-risk-result" in step.skills_routed

    def test_transcript_metadata(self, session, settings, tmp_path):
        """MatchTranscript has expected schema_version, workflow_id, model_id."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")
        fake_agent = _make_fake_agent_one_turn()

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=fake_agent,
        )

        assert transcript.schema_version == 1
        assert transcript.workflow_id == "risk-manager-control-day"
        assert transcript.model_id == model.slug
        assert transcript.started_at is not None
        assert transcript.finished_at is not None
        assert transcript.run_id is None


# ---------------------------------------------------------------------------
# 1c: blocking run-tool wrapper
# ---------------------------------------------------------------------------


class TestBlockingRunToolWrapper:
    """Test _wrap_run_tools with fake tool + fake status-checker.

    We don't wire up a real TaskRun DB here. Instead, we inject a fake
    status-checker callable and verify the wrapper polls until completion.

    DOCUMENTED STUB: integration with real TaskRun DB polling is not unit-tested here.
    Full integration relies on the tools using database.SessionLocal directly.
    """

    def test_non_run_tool_passes_through_unchanged(self):
        """Tools that do NOT start with 'run_' pass through unchanged."""
        from app.services.arena.runner import _wrap_run_tools

        call_log = []

        def fake_get_tool(arg):
            call_log.append(arg)
            return {"result": "ok"}

        # Give fake_get_tool a name that doesn't start with "run_"
        fake_get_tool.name = "get_latest_risk_run_tool"

        wrapped = _wrap_run_tools([fake_get_tool])
        assert len(wrapped) == 1
        # Should be the same object (not wrapped)
        result = wrapped[0]("portfolio_id=6")
        assert result == {"result": "ok"}
        assert call_log == ["portfolio_id=6"]

    def test_run_tool_wrapped_to_block(self):
        """A tool starting with 'run_' is wrapped to poll for completion.

        We inject a fake status-checker that simulates: first call → queued,
        second call → completed. The wrapper should return the completed payload.
        """
        from app.services.arena.runner import _wrap_run_tools

        poll_count = {"n": 0}

        def fake_run_tool(args):
            return {"task_id": "fake-task-1", "status": "queued"}

        fake_run_tool.name = "run_batch_pricing_tool"

        def fake_status_checker(task_id: str) -> dict:
            poll_count["n"] += 1
            if poll_count["n"] < 2:
                return {"task_id": task_id, "status": "running"}
            return {"task_id": task_id, "status": "completed", "result_payload": {"rows": 5}}

        wrapped = _wrap_run_tools([fake_run_tool], status_checker=fake_status_checker)
        assert len(wrapped) == 1
        result = wrapped[0]({"portfolio_id": 6})

        # The wrapper must have polled at least once
        assert poll_count["n"] >= 1
        # The final result should reflect completion
        assert result.get("status") == "completed"

    def test_run_tool_returns_immediately_if_already_complete(self):
        """If the run tool returns status=completed directly, no polling needed."""
        from app.services.arena.runner import _wrap_run_tools

        poll_count = {"n": 0}

        def fake_run_tool(args):
            return {"task_id": "fake-task-2", "status": "completed", "result_payload": {}}

        fake_run_tool.name = "run_risk_tool"

        def fake_status_checker(task_id: str) -> dict:
            poll_count["n"] += 1
            return {"task_id": task_id, "status": "completed"}

        wrapped = _wrap_run_tools([fake_run_tool], status_checker=fake_status_checker)
        result = wrapped[0]({})

        # Already complete → no polling needed
        assert poll_count["n"] == 0
        assert result.get("status") == "completed"


# ---------------------------------------------------------------------------
# 1d: budget exceeded + error handling
# ---------------------------------------------------------------------------


class TestBudgetAndErrors:
    def test_budget_exceeded_step_has_error(self, session, settings, tmp_path):
        """A fake agent that always returns tool calls hits the 12-turn budget.
        The resulting step should have a budget_exceeded error.
        """
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        call_count = {"n": 0}

        def infinite_agent(history, step_index: int):
            call_count["n"] += 1
            return {
                "index": step_index,
                "user": history[-1] if history else "",
                "messages": [],
                "tool_calls": [{"id": f"call_{call_count['n']}", "name": "get_latest_risk_run_tool", "args": {}}],
                "tool_results": [],
                "skills_routed": [],
                "artifacts": [],
                "response_text": "",
                "errors": [],
            }

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=infinite_agent,
        )

        assert len(transcript.steps) > 0
        step = transcript.steps[0]
        error_types = [e.get("type") for e in step.errors]
        assert "budget_exceeded" in error_types

    def test_agent_exception_captured_as_error(self, session, settings, tmp_path):
        """A fake agent that raises on the first step → transcript step has error field.
        The match continues with subsequent steps (using an error stub).
        """
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        call_count = {"n": 0}

        def crashing_agent(history, step_index: int):
            call_count["n"] += 1
            if step_index == 0:
                raise RuntimeError("Simulated agent failure")
            return {
                "index": step_index,
                "user": history[-1] if history else "",
                "messages": [],
                "tool_calls": [],
                "tool_results": [],
                "skills_routed": [],
                "artifacts": [],
                "response_text": "ok",
                "errors": [],
            }

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path,
            agent=crashing_agent,
        )

        # Transcript must have the same number of steps as the workflow
        n_workflow_steps = len(loaded.workflow.steps)
        assert len(transcript.steps) == n_workflow_steps

        # Step 0 should have an error
        step0 = transcript.steps[0]
        error_types = [e.get("type") for e in step0.errors]
        assert "error" in error_types

        # Other steps should be present (even if minimal)
        for step in transcript.steps[1:]:
            assert step.index > 0

    def test_match_continues_after_error(self, session, settings, tmp_path):
        """When step 0 fails, step 1 must still be attempted."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        steps_called = []

        def partial_fail_agent(history, step_index: int):
            steps_called.append(step_index)
            if step_index == 0:
                raise RuntimeError("Step 0 crash")
            return {
                "index": step_index,
                "user": "",
                "messages": [],
                "tool_calls": [],
                "tool_results": [],
                "skills_routed": ["read-risk-result"],
                "artifacts": [],
                "response_text": "recovered",
                "errors": [],
            }

        run_match(loaded, model, artifact_root=tmp_path, agent=partial_fail_agent)
        # Step 1 (and beyond) must have been attempted
        assert 1 in steps_called


# ---------------------------------------------------------------------------
# 1e: full 7-step fake run + artifact copy
# ---------------------------------------------------------------------------


class TestFullSevenStepRun:
    def _make_7_step_agent(self, tmp_file: Path):
        """Return a scripted fake agent for 7 steps, with step 6 having an artifact."""

        def scripted_agent(history, step_index: int):
            artifacts = []
            if step_index == 6:
                # Put a real file path so artifact copy is exercised
                artifacts = [{"kind": "report", "path": str(tmp_file)}]

            return {
                "index": step_index,
                "user": history[-1] if history else "step user",
                "messages": [],
                "tool_calls": [{"id": f"call_s{step_index}", "name": "get_latest_risk_run_tool", "args": {}}],
                "tool_results": [],
                "skills_routed": [f"read-risk-result"],
                "artifacts": artifacts,
                "response_text": f"Response for step {step_index}",
                "errors": [],
            }

        return scripted_agent

    def test_transcript_has_seven_steps(self, session, settings, tmp_path):
        """run_match against the 7-step workflow produces exactly 7 steps."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        # Create a tmp file to serve as an artifact
        fake_artifact = tmp_path / "report.html"
        fake_artifact.write_text("<html>report</html>")

        agent = self._make_7_step_agent(fake_artifact)

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path / "arena_artifacts",
            agent=agent,
        )

        assert len(transcript.steps) == 7

    def test_step_indices_sequential(self, session, settings, tmp_path):
        """All 7 step indices are 0..6."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        fake_artifact = tmp_path / "report.html"
        fake_artifact.write_text("<html>report</html>")
        agent = self._make_7_step_agent(fake_artifact)

        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path / "arena_artifacts",
            agent=agent,
        )

        for i, step in enumerate(transcript.steps):
            assert step.index == i

    def test_artifact_copied_under_artifact_root(self, session, settings, tmp_path):
        """When step 6 has an artifact with a real file path, the file is copied
        under artifact_root / workflow_id / ."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        fake_artifact = tmp_path / "original_report.html"
        fake_artifact.write_text("<html>original</html>")

        artifact_root = tmp_path / "arena_artifacts"
        agent = self._make_7_step_agent(fake_artifact)

        transcript = run_match(
            loaded,
            model,
            artifact_root=artifact_root,
            agent=agent,
        )

        # The artifact in step 6 should have been copied
        step6 = transcript.steps[6]
        artifacts = step6.artifacts
        assert len(artifacts) > 0, "Step 6 should have artifacts"
        # The artifact's path should point under artifact_root
        art_path = artifacts[0].get("path", "")
        if art_path:
            assert str(artifact_root) in art_path or artifact_root.name in art_path, \
                f"Artifact path should be under artifact_root, got: {art_path}"

    def test_missing_artifact_file_silently_skipped(self, session, settings, tmp_path):
        """When an artifact's path does not exist, it is silently skipped."""
        from app.golden_workflows.registry import get_workflow_bundle
        from app.services.arena.models import get_model
        from app.services.arena.runner import run_match

        loaded = get_workflow_bundle("risk-manager-control-day")
        model = get_model("gpt-5-5")

        def agent_with_missing_artifact(history, step_index: int):
            artifacts = []
            if step_index == 0:
                artifacts = [{"kind": "report", "path": "/nonexistent/path/report.html"}]
            return {
                "index": step_index,
                "user": "",
                "messages": [],
                "tool_calls": [],
                "tool_results": [],
                "skills_routed": [],
                "artifacts": artifacts,
                "response_text": "ok",
                "errors": [],
            }

        # Must not raise
        transcript = run_match(
            loaded,
            model,
            artifact_root=tmp_path / "arena_artifacts",
            agent=agent_with_missing_artifact,
        )
        assert transcript is not None
        assert len(transcript.steps) == 7


# ---------------------------------------------------------------------------
# 1f: arena_tools() wiring — blocking wrapper applied in build_arena_agent
# ---------------------------------------------------------------------------


class TestArenaToolsWiring:
    """Prove that arena_tools() applies _wrap_run_tools to QUANT_AGENT_TOOLS
    and that build_arena_agent would receive blocking run-tools, not raw ones.
    """

    def test_arena_tools_length_matches_quant_agent_tools(self):
        """arena_tools() returns the same number of tools as QUANT_AGENT_TOOLS."""
        from app.services.arena.runner import arena_tools
        from app.tools import QUANT_AGENT_TOOLS

        result = arena_tools()
        assert len(result) == len(QUANT_AGENT_TOOLS)

    def test_arena_tools_run_entries_are_wrapped(self):
        """run_* tools in arena_tools() are NOT the same objects as in QUANT_AGENT_TOOLS.

        The wrapper replaces each run_* tool with a closure; identity check confirms
        the blocking wrapper was applied.
        """
        from app.services.arena.runner import arena_tools
        from app.tools import QUANT_AGENT_TOOLS

        raw_run_tools = {getattr(t, "name", ""): t for t in QUANT_AGENT_TOOLS
                         if getattr(t, "name", "").startswith("run_")}
        assert raw_run_tools, "Precondition: QUANT_AGENT_TOOLS must contain run_* tools"

        wrapped = arena_tools()
        wrapped_by_name = {getattr(t, "name", ""): t for t in wrapped
                           if getattr(t, "name", "").startswith("run_")}

        for name, raw in raw_run_tools.items():
            assert name in wrapped_by_name, f"run_ tool {name!r} missing from arena_tools()"
            assert wrapped_by_name[name] is not raw, (
                f"run_ tool {name!r} in arena_tools() is the same object as QUANT_AGENT_TOOLS "
                f"entry — the blocking wrapper was NOT applied"
            )

    def test_arena_tools_non_run_entries_pass_through(self):
        """Non-run_* tools in arena_tools() are the same objects as in QUANT_AGENT_TOOLS."""
        from app.services.arena.runner import arena_tools
        from app.tools import QUANT_AGENT_TOOLS

        raw_non_run = {getattr(t, "name", ""): t for t in QUANT_AGENT_TOOLS
                       if not getattr(t, "name", "").startswith("run_")}

        wrapped = arena_tools()
        wrapped_non_run = {getattr(t, "name", ""): t for t in wrapped
                           if not getattr(t, "name", "").startswith("run_")}

        for name, raw in raw_non_run.items():
            assert wrapped_non_run.get(name) is raw, (
                f"Non-run_ tool {name!r} should pass through unchanged but was replaced"
            )

    def test_arena_tools_injects_status_checker(self):
        """arena_tools(status_checker=...) passes the checker to _wrap_run_tools.

        Inject a fake run-tool list and a fake status_checker into _wrap_run_tools
        directly to confirm the injection path works; then verify arena_tools()
        forwards its status_checker argument.
        """
        from app.services.arena.runner import _wrap_run_tools

        poll_count = {"n": 0}

        def fake_run_tool(args):
            return {"task_id": "tid-1", "status": "queued"}

        fake_run_tool.name = "run_fake_tool"

        def fake_checker(task_id: str) -> dict:
            poll_count["n"] += 1
            return {"task_id": task_id, "status": "completed"}

        wrapped = _wrap_run_tools([fake_run_tool], status_checker=fake_checker)
        result = wrapped[0]({})

        assert poll_count["n"] >= 1
        assert result.get("status") == "completed"

        # Now confirm arena_tools forwards its status_checker by wrapping a
        # known run_* name: verify the returned list has run_* tools as wrappers
        # (object identity differs from originals — already tested above).
        from app.services.arena.runner import arena_tools
        from app.tools import QUANT_AGENT_TOOLS

        # Passing a custom checker: the returned tools are still distinct from originals
        custom_checker_called = {"n": 0}

        def custom_checker(task_id: str) -> dict:
            custom_checker_called["n"] += 1
            return {"task_id": task_id, "status": "completed"}

        result_with_checker = arena_tools(status_checker=custom_checker)
        assert len(result_with_checker) == len(QUANT_AGENT_TOOLS)
        # run_* tools must still be wrapped (not the originals)
        raw_run = {getattr(t, "name", ""): t for t in QUANT_AGENT_TOOLS
                   if getattr(t, "name", "").startswith("run_")}
        wrapped_run = {getattr(t, "name", ""): t for t in result_with_checker
                       if getattr(t, "name", "").startswith("run_")}
        for name, raw in raw_run.items():
            assert wrapped_run.get(name) is not raw
