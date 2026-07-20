from __future__ import annotations

import json
from importlib.metadata import version
import subprocess
import sys
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class _PromptAwareLiveModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "prompt-aware-live-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        prompt = "\n".join(str(message.content) for message in messages)
        if "LIVE_CONTINUATION_CONTRACT_V1" not in prompt:
            text = (
                "The hedge evidence was reviewed. Verify exact provenance and freshness "
                "before acting."
            )
        elif "<artifact_manifest>" not in prompt:
            text = json.dumps(
                {
                    "action": "insufficient_evidence",
                    "artifact_id": None,
                    "content_hash": None,
                    "reason": "trusted artifact tuple is unavailable",
                }
            )
        else:
            manifest_text = prompt.split("<artifact_manifest>", 1)[1].split(
                "</artifact_manifest>", 1
            )[0]
            reference = json.loads(manifest_text)[0]
            text = json.dumps(
                {
                    "action": "book_hedge",
                    "artifact_id": reference["artifact_id"],
                    "content_hash": reference["content_hash"],
                    "reason": "fresh and position-consistent",
                }
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def test_compaction_ab_uses_installed_default_and_identical_input_traces():
    from app.services.deep_agent.compaction_benchmark import run_benchmark

    result = run_benchmark()

    assert result["schema_version"] == 1
    assert result["generated_at"].endswith("+00:00")
    assert result["methodology"]["model"] == "controlled_lossy_v1"
    assert result["implementations"]["baseline"]["class"].endswith(
        "summarization._DeepAgentsSummarizationMiddleware"
    )
    assert result["implementations"]["candidate"]["class"].endswith(
        "compaction.LedgerScopedCompactionMiddleware"
    )
    fingerprint = result["runtime_fingerprint"]
    assert fingerprint["fingerprint_sha256"].startswith("sha256:")
    assert fingerprint["python"]["version"]
    assert fingerprint["platform"]["system"]
    assert fingerprint["source_files"]["pyproject.toml"]["sha256"].startswith(
        "sha256:"
    )
    assert fingerprint["source_files"]["uv.lock"]["sha256"].startswith("sha256:")
    assert fingerprint["benchmark_stack"]["deepagents"] == version("deepagents")
    assert fingerprint["benchmark_stack"]["langchain"] == version("langchain")
    assert fingerprint["installed_distributions"]["count"] > 0
    assert fingerprint["installed_distributions"]["sha256"].startswith("sha256:")
    assert {
        "name": "deepagents",
        "version": version("deepagents"),
    } in fingerprint["installed_distributions"]["packages"]
    assert fingerprint["lock_alignment"]["checked"] is True
    assert fingerprint["lock_alignment"]["matches"] is True
    assert fingerprint["lock_alignment"]["mismatches"] == []
    assert result["criteria"]["runtime_matches_lock"] is True
    assert len(result["cases"]) >= 4
    for case in result["cases"]:
        assert case["arms"]["baseline"]["input_trace_hash"] == case["trace_hash"]
        assert case["arms"]["candidate"]["input_trace_hash"] == case["trace_hash"]


def test_compaction_ab_demonstrates_structural_advantage_without_hiding_tradeoffs():
    from app.services.deep_agent.compaction_benchmark import run_benchmark

    result = run_benchmark()
    baseline = result["aggregate"]["baseline"]
    candidate = result["aggregate"]["candidate"]

    assert baseline["raw_payloads_exposed_to_summarizer"] > 0
    assert candidate["raw_payloads_exposed_to_summarizer"] == 0
    assert candidate["trusted_evidence_recoveries"] == result["case_count"]
    assert baseline["trusted_evidence_recoveries"] == 0
    assert candidate["reference_guided_correct_actions"] > baseline[
        "reference_guided_correct_actions"
    ]
    assert candidate["fallback_correct_actions"] > baseline["fallback_correct_actions"]
    assert candidate["targeted_rehydration_bytes"] < baseline["targeted_rehydration_bytes"]
    assert candidate["retained_context_bytes"] > baseline["retained_context_bytes"]
    assert candidate["retained_context_bytes"] < candidate["original_trace_bytes"]
    assert all(result["criteria"].values())
    assert result["advantage_demonstrated"] is True


def test_compaction_ab_preserves_exact_timestamp_tuple_and_content_hash():
    from app.services.deep_agent.compaction_benchmark import run_benchmark

    result = run_benchmark()

    for case in result["cases"]:
        baseline = case["arms"]["baseline"]
        candidate = case["arms"]["candidate"]
        assert baseline["trusted_timestamp_tuple"] is False
        assert candidate["trusted_timestamp_tuple"] is True
        assert candidate["recovered_content_hash"] == case["decision_content_hash"]
        assert candidate["reference_guided_action"] == case["expected_action"]


def test_compaction_ab_result_is_json_serializable_and_markdown_is_auditable():
    from app.services.deep_agent.compaction_benchmark import (
        render_markdown,
        run_benchmark,
    )

    result = run_benchmark()
    encoded = json.dumps(result, sort_keys=True)
    report = render_markdown(result)

    assert '"advantage_demonstrated": true' in encoded
    assert "DeepAgents/LangChain default" in report
    assert "Ledger-aware candidate" in report
    assert "Raw payloads exposed" in report
    assert "Retained context bytes" in report
    assert "ADVANTAGE DEMONSTRATED" in report
    assert "controlled lossy summarizer" in report.lower()


def test_compaction_ab_cli_writes_both_evidence_formats(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    json_out = tmp_path / "result.json"
    markdown_out = tmp_path / "result.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "compaction_ab_benchmark.py"),
            "--json-out",
            str(json_out),
            "--markdown-out",
            str(markdown_out),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ADVANTAGE DEMONSTRATED" in completed.stdout
    assert json.loads(json_out.read_text(encoding="utf-8"))[
        "advantage_demonstrated"
    ] is True
    assert "Predeclared criteria" in markdown_out.read_text(encoding="utf-8")


def test_live_compaction_ab_records_real_call_contract_and_grades_continuation():
    from app.services.deep_agent.compaction_benchmark import (
        benchmark_case_definitions,
        render_live_markdown,
        run_live_benchmark,
    )

    result = run_live_benchmark(
        model=_PromptAwareLiveModel(),
        model_metadata={
            "channel": "test",
            "provider": "fake",
            "model": "prompt-aware-live-fake",
            "temperature": 0,
            "max_tokens": 1600,
            "api_key": "must-not-leak",
            "access_token": "must-not-leak",
        },
        trials=1,
        case_definitions=benchmark_case_definitions()[:1],
    )

    assert result["schema_version"] == 1
    assert result["mode"] == "live_model"
    assert result["request_count"] == 4
    assert result["aggregate"]["baseline"]["correct_continuations"] == 0
    assert result["aggregate"]["candidate"]["correct_continuations"] == 1
    assert result["aggregate"]["candidate"]["unsafe_bookings"] == 0
    assert result["live_advantage_demonstrated"] is True
    assert result["model"]["max_tokens"] == 1600
    assert "api_key" not in json.dumps(result["model"], sort_keys=True).lower()
    assert "access_token" not in json.dumps(result["model"], sort_keys=True).lower()
    assert result["runtime_fingerprint"]["benchmark_stack"]["deepagents"] == version(
        "deepagents"
    )
    assert result["runtime_fingerprint"]["source_files"]["uv.lock"][
        "sha256"
    ].startswith("sha256:")
    assert result["runtime_fingerprint"]["lock_alignment"]["matches"] is True
    assert result["criteria"]["runtime_matches_lock"] is True

    trial = result["cases"][0]["trials"][0]
    baseline = trial["arms"]["baseline"]
    candidate = trial["arms"]["candidate"]
    assert baseline["input_trace_hash"] == candidate["input_trace_hash"]
    assert baseline["model_config_hash"] == candidate["model_config_hash"]
    assert baseline["continuation"]["parsed"]["action"] == "insufficient_evidence"
    assert candidate["continuation"]["parsed"]["action"] == "book_hedge"
    assert candidate["continuation"]["evidence_exact"] is True
    assert '"artifact_id":10010' in candidate["continuation"]["prompt"]
    assert '"artifact_id":10011' in candidate["continuation"]["prompt"]
    assert '"generated_at":"2026-07-17T09:31:02+00:00"' in candidate[
        "continuation"
    ]["prompt"]
    for arm in (baseline, candidate):
        assert arm["summary"]["prompt"]
        assert arm["summary"]["response"]
        assert arm["continuation"]["prompt"]
        assert arm["continuation"]["response"]
        assert arm["summary"]["started_at"].endswith("+00:00")
        assert arm["summary"]["finished_at"].endswith("+00:00")

    report = render_live_markdown(result)
    assert "LIVE MODEL ADVANTAGE DEMONSTRATED" in report
    assert "prompt-aware-live-fake" in report
    assert f"DeepAgents `{version('deepagents')}`" in report
    assert "Installed-distribution fingerprint: `sha256:" in report
    assert "Lock alignment: **PASS**" in report
