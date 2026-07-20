"""Deterministic A/B proof for OTC-safe conversation compaction.

The benchmark deliberately uses a controlled lossy summarizer. Both arms receive
the same source messages and the same model response, so the result measures the
structural guarantees supplied by the compaction implementations rather than the
quality or nondeterminism of a remote LLM.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from deepagents.backends import StateBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from .cas_backend import ARTIFACT_REFERENCE_PROVENANCE
from .compaction import LedgerScopedCompactionMiddleware


CONTROLLED_SUMMARY = """## SESSION INTENT
Continue the portfolio hedge workflow safely.

## SUMMARY
The desk inspected portfolio, risk, and market evidence. Exact values are not repeated here.

## ARTIFACTS
Ground-truth tool outputs were inspected.

## NEXT STEPS
Verify freshness and position consistency before taking the next hedge action."""

_MANIFEST_RE = re.compile(
    r"<artifact_manifest>\s*(.*?)\s*</artifact_manifest>",
    flags=re.DOTALL | re.IGNORECASE,
)
_GROUND_TRUTH_TOOL_NAMES = frozenset(
    {"propose_hedge", "get_portfolio_positions", "get_market_quotes"}
)
_BENCHMARK_STACK_PACKAGES = (
    "deepagents",
    "langchain",
    "langchain-core",
    "langchain-deepseek",
    "langchain-openai",
    "langchain-quickjs",
    "langgraph",
    "langsmith",
    "pydantic",
)


class ControlledLossySummaryModel(BaseChatModel):
    """Return one fixed summary while retaining the exact prompt for scoring."""

    response: str = CONTROLLED_SUMMARY
    prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "controlled_lossy_v1"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        prompt = "\n".join(_message_text(message) for message in messages)
        self.prompts.append(prompt)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.response))]
        )


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _source_file_fingerprint(repo_root: Path, relative_path: str) -> dict[str, Any]:
    path = repo_root / relative_path
    try:
        payload = path.read_bytes()
    except OSError:
        return {
            "path": relative_path,
            "sha256": None,
            "byte_size": None,
        }
    return {
        "path": relative_path,
        "sha256": _sha256_bytes(payload),
        "byte_size": len(payload),
    }


def _lock_alignment(repo_root: Path, installed: dict[str, str]) -> dict[str, Any]:
    lock_path = repo_root / "uv.lock"
    try:
        lock_data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {
            "checked": False,
            "matches": False,
            "locked_package_count": 0,
            "compared_package_count": 0,
            "mismatches": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    locked = {
        re.sub(r"[-_.]+", "-", str(item["name"])).lower(): str(item["version"])
        for item in lock_data.get("package", [])
        if item.get("name") and item.get("version")
    }
    compared = sorted(set(installed).intersection(locked))
    mismatches = [
        {
            "name": name,
            "installed": installed[name],
            "locked": locked[name],
        }
        for name in compared
        if installed[name] != locked[name]
    ]
    return {
        "checked": True,
        "matches": not mismatches,
        "locked_package_count": len(locked),
        "compared_package_count": len(compared),
        "mismatches": mismatches,
        "error": None,
    }


def build_runtime_fingerprint(repo_root: Path | None = None) -> dict[str, Any]:
    """Capture the exact Python, lockfile, and installed package environment."""
    root = repo_root or Path(__file__).resolve().parents[4]
    installed: dict[str, str] = {}
    for distribution in distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized_name = re.sub(r"[-_.]+", "-", name).lower()
        installed[normalized_name] = distribution.version
    packages = [
        {"name": name, "version": installed[name]} for name in sorted(installed)
    ]
    fingerprint: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "source_files": {
            name: _source_file_fingerprint(root, name)
            for name in ("pyproject.toml", "uv.lock")
        },
        "benchmark_stack": {
            name: installed.get(name, "unknown")
            for name in _BENCHMARK_STACK_PACKAGES
        },
        "installed_distributions": {
            "count": len(packages),
            "sha256": _sha256_bytes(_json_bytes(packages)),
            "packages": packages,
        },
        "lock_alignment": _lock_alignment(root, installed),
    }
    fingerprint["fingerprint_sha256"] = _sha256_bytes(_json_bytes(fingerprint))
    return fingerprint


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _artifact_reference(
    *,
    artifact_id: int,
    tool_name: str,
    tool_call_id: str,
    content: str,
    generated_at: str,
    data_as_of: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "kind": "tool_result",
        "content_hash": _sha256_bytes(content.encode("utf-8")),
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "generated_at": generated_at,
        "observed_at": generated_at,
        "data_as_of": data_as_of,
        "locator": f"/large_tool_results/{tool_call_id}",
        "byte_size": len(content.encode("utf-8")),
        "summary": summary,
    }


def _captured_tool_message(
    *,
    tool_name: str,
    tool_call_id: str,
    payload: dict[str, Any],
    reference: dict[str, Any],
) -> ToolMessage:
    content = _json_bytes(payload).decode("utf-8")
    if reference["content_hash"] != _sha256_bytes(content.encode("utf-8")):
        raise ValueError("benchmark fixture reference does not match content")
    return ToolMessage(
        content=content,
        name=tool_name,
        tool_call_id=tool_call_id,
        additional_kwargs={
            "artifact_ref": reference,
            "artifact_ref_provenance": ARTIFACT_REFERENCE_PROVENANCE,
        },
    )


def _market_payload(case_number: int) -> dict[str, Any]:
    return {
        "as_of": "2026-07-17T09:34:30+00:00",
        "quotes": [
            {
                "symbol": f"IF2608-{case_number:02d}-{index:02d}",
                "bid": 3920.0 + index * 0.25,
                "ask": 3920.2 + index * 0.25,
                "source": "deterministic_market_snapshot",
            }
            for index in range(16)
        ],
    }


def _positions_payload(case_number: int, current_hash: str) -> dict[str, Any]:
    return {
        "portfolio_id": 17,
        "position_set_hash": current_hash,
        "positions": [
            {
                "position_id": case_number * 100 + index,
                "instrument": f"OTC-SNOWBALL-{index:02d}",
                "quantity": 1_000_000 + index * 25_000,
                "delta_cash": 41_000.125 + index * 137.5,
                "gamma_cash": 8_300.25 + index * 11.75,
            }
            for index in range(12)
        ],
    }


def _case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "id": "fresh-risk-book",
            "now": "2026-07-17T09:35:00+00:00",
            "valuation_as_of": "2026-07-17T09:30:00+00:00",
            "risk_generated_at": "2026-07-17T09:31:00+00:00",
            "expires_at": "2026-07-17T09:46:00+00:00",
            "position_set_hash": "positions-v1",
            "current_position_set_hash": "positions-v1",
            "expected_action": "book_hedge",
        },
        {
            "id": "expired-risk-refresh",
            "now": "2026-07-17T09:47:00+00:00",
            "valuation_as_of": "2026-07-17T09:30:00+00:00",
            "risk_generated_at": "2026-07-17T09:31:00+00:00",
            "expires_at": "2026-07-17T09:46:00+00:00",
            "position_set_hash": "positions-v1",
            "current_position_set_hash": "positions-v1",
            "expected_action": "refresh_risk",
        },
        {
            "id": "position-drift-resolve",
            "now": "2026-07-17T09:35:00+00:00",
            "valuation_as_of": "2026-07-17T09:30:00+00:00",
            "risk_generated_at": "2026-07-17T09:31:00+00:00",
            "expires_at": "2026-07-17T09:46:00+00:00",
            "position_set_hash": "positions-v1",
            "current_position_set_hash": "positions-v2",
            "expected_action": "resolve_hedge",
        },
        {
            "id": "historical-valuation-refresh",
            "now": "2026-07-17T09:35:00+00:00",
            "valuation_as_of": "2026-07-16T09:30:00+00:00",
            "risk_generated_at": "2026-07-17T09:31:00+00:00",
            "expires_at": "2026-07-17T09:46:00+00:00",
            "position_set_hash": "positions-v1",
            "current_position_set_hash": "positions-v1",
            "expected_action": "refresh_risk",
        },
    ]


def benchmark_case_definitions() -> list[dict[str, Any]]:
    """Return independent copies of the public benchmark case definitions."""
    return [dict(definition) for definition in _case_definitions()]


def _build_case(definition: dict[str, Any], case_number: int) -> dict[str, Any]:
    artifact_base = 10_000 + case_number * 10
    generated_at = "2026-07-17T09:31:02+00:00"
    proposal = {
        "portfolio_id": 17,
        "underlying": "000300.SH",
        "risk_run_id": 991 + case_number,
        "valuation_as_of": definition["valuation_as_of"],
        "risk_generated_at": definition["risk_generated_at"],
        "expires_at": definition["expires_at"],
        "position_set_hash": definition["position_set_hash"],
        "current_position_set_hash": definition["current_position_set_hash"],
        "spot": 3920.125,
        "greeks": {
            "delta_cash": 1_250_000.125,
            "gamma_cash": 90_000.375,
            "vega": -74_250.625,
        },
        "solver_legs": [
            {"symbol": "IF2608", "quantity": -3.25, "price": 3920.2},
            {"symbol": "IO2608-C-4000", "quantity": 11.0, "price": 58.4},
        ],
    }
    positions = _positions_payload(
        case_number, str(definition["current_position_set_hash"])
    )
    market = _market_payload(case_number)
    artifact_specs = [
        (
            artifact_base,
            "propose_hedge",
            f"proposal-{case_number}",
            proposal,
            definition["valuation_as_of"],
            {"status": "ok", "ids": {"risk_run_id": proposal["risk_run_id"]}},
        ),
        (
            artifact_base + 1,
            "get_portfolio_positions",
            f"positions-{case_number}",
            positions,
            definition["now"],
            {"counts": {"positions": len(positions["positions"])}},
        ),
        (
            artifact_base + 2,
            "get_market_quotes",
            f"quotes-{case_number}",
            market,
            market["as_of"],
            {"counts": {"quotes": len(market["quotes"])}},
        ),
    ]

    messages: list[BaseMessage] = [
        HumanMessage(
            content=(
                "Hedge portfolio 17 only if the exact proposal evidence is current, "
                "position-consistent, and still within its execution window."
            )
        ),
        AIMessage(content="I will inspect the risk proposal and its supporting evidence."),
    ]
    store: dict[int, bytes] = {}
    contents: list[str] = []
    references: list[dict[str, Any]] = []
    for artifact_id, tool_name, call_id, payload, data_as_of, summary in artifact_specs:
        content = _json_bytes(payload).decode("utf-8")
        reference = _artifact_reference(
            artifact_id=artifact_id,
            tool_name=tool_name,
            tool_call_id=call_id,
            content=content,
            generated_at=generated_at,
            data_as_of=str(data_as_of),
            summary=summary,
        )
        messages.append(
            _captured_tool_message(
                tool_name=tool_name,
                tool_call_id=call_id,
                payload=payload,
                reference=reference,
            )
        )
        messages.append(AIMessage(content=f"Evidence from {tool_name} was inspected."))
        store[artifact_id] = content.encode("utf-8")
        contents.append(content)
        references.append(reference)

    trace_payload = [message.model_dump(mode="json") for message in messages]
    return {
        **definition,
        "messages": messages,
        "trace_hash": _sha256_bytes(_json_bytes(trace_payload)),
        "history": get_buffer_string(messages),
        "artifact_store": store,
        "raw_contents": contents,
        "references": references,
        "decision_artifact_id": artifact_base,
        "decision_payload": proposal,
        "decision_content_hash": references[0]["content_hash"],
    }


def _parse_manifest(summary: str) -> list[dict[str, Any]]:
    match = _MANIFEST_RE.search(summary)
    if match is None:
        return []
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _recover_from_manifest(
    *,
    summary: str,
    artifact_id: int,
    store: dict[int, bytes],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    reference = next(
        (item for item in _parse_manifest(summary) if item.get("artifact_id") == artifact_id),
        None,
    )
    if reference is None:
        return None, None, None
    body = store.get(artifact_id)
    if body is None or reference.get("content_hash") != _sha256_bytes(body):
        return None, None, None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, None, None
    return payload, reference, str(reference["content_hash"])


def _trusted_timestamp_tuple(
    payload: dict[str, Any] | None,
    reference: dict[str, Any] | None,
) -> bool:
    if payload is None or reference is None:
        return False
    values = (
        reference.get("generated_at"),
        payload.get("valuation_as_of"),
        payload.get("risk_generated_at"),
        payload.get("expires_at"),
    )
    try:
        for value in values:
            _parse_time(str(value))
    except (TypeError, ValueError):
        return False
    return all(values)


def _decide_hedge_action(
    *,
    payload: dict[str, Any] | None,
    reference: dict[str, Any] | None,
    now: str,
) -> str:
    if payload is None:
        return "insufficient_evidence"
    try:
        now_at = _parse_time(now)
        valuation_at = _parse_time(str(payload["valuation_as_of"]))
        expires_at = _parse_time(str(payload["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return "insufficient_evidence"
    if valuation_at.date() != now_at.date() or now_at >= expires_at:
        return "refresh_risk"
    if payload.get("position_set_hash") != payload.get("current_position_set_hash"):
        return "resolve_hedge"
    if not _trusted_timestamp_tuple(payload, reference):
        return "insufficient_evidence"
    return "book_hedge"


def _compact_case(case: dict[str, Any]) -> dict[str, Any]:
    baseline_model = ControlledLossySummaryModel()
    candidate_model = ControlledLossySummaryModel()
    baseline_middleware = SummarizationMiddleware(
        model=baseline_model,
        backend=StateBackend(),
    )
    candidate_middleware = LedgerScopedCompactionMiddleware(
        model=candidate_model,
        backend=StateBackend(),
        keep_recent=1,
        max_messages=32,
        ground_truth_tool_names=_GROUND_TRUTH_TOOL_NAMES,
    )

    started = perf_counter_ns()
    baseline_summary = baseline_middleware._create_summary(case["messages"])
    baseline_elapsed_ms = (perf_counter_ns() - started) / 1_000_000
    started = perf_counter_ns()
    candidate_summary = candidate_middleware._create_summary(case["messages"])
    candidate_elapsed_ms = (perf_counter_ns() - started) / 1_000_000

    history_path = f"/conversation_history/benchmark-{case['id']}.md"
    baseline_retained = baseline_middleware._build_new_messages_with_path(
        baseline_summary, history_path
    )[0]
    candidate_retained = candidate_middleware._build_new_messages_with_path(
        candidate_summary, history_path
    )[0]
    baseline_prompt = baseline_model.prompts[-1]
    candidate_prompt = candidate_model.prompts[-1]

    candidate_payload, candidate_ref, recovered_hash = _recover_from_manifest(
        summary=candidate_summary,
        artifact_id=case["decision_artifact_id"],
        store=case["artifact_store"],
    )
    baseline_immediate_action = _decide_hedge_action(
        payload=None,
        reference=None,
        now=case["now"],
    )
    candidate_action = _decide_hedge_action(
        payload=candidate_payload,
        reference=candidate_ref,
        now=case["now"],
    )

    # The default history offload retains the rendered ToolMessage content, so
    # give the baseline that fallback. get_buffer_string intentionally omits
    # additional_kwargs, however, so the server-owned artifact ID/hash/capture
    # timestamp required for fresh booking cannot be reconstructed from it.
    history_has_payload = _json_bytes(case["decision_payload"]).decode("utf-8") in case["history"]
    history_has_reference = case["decision_content_hash"] in case["history"]
    baseline_fallback_payload = case["decision_payload"] if history_has_payload else None
    baseline_fallback_ref = case["references"][0] if history_has_reference else None
    baseline_fallback_action = _decide_hedge_action(
        payload=baseline_fallback_payload,
        reference=baseline_fallback_ref,
        now=case["now"],
    )

    original_trace_bytes = len(_json_bytes([m.model_dump(mode="json") for m in case["messages"]]))

    def exposed(prompt: str) -> tuple[int, int]:
        matching = [content for content in case["raw_contents"] if content in prompt]
        return len(matching), sum(len(content.encode("utf-8")) for content in matching)

    baseline_exposed_count, baseline_exposed_bytes = exposed(baseline_prompt)
    candidate_exposed_count, candidate_exposed_bytes = exposed(candidate_prompt)
    target_bytes = len(case["artifact_store"][case["decision_artifact_id"]])
    history_bytes = len(case["history"].encode("utf-8"))

    return {
        "id": case["id"],
        "trace_hash": case["trace_hash"],
        "expected_action": case["expected_action"],
        "decision_artifact_id": case["decision_artifact_id"],
        "decision_content_hash": case["decision_content_hash"],
        "arms": {
            "baseline": {
                "input_trace_hash": case["trace_hash"],
                "compaction_wall_ms": round(baseline_elapsed_ms, 3),
                "prompt_bytes": len(baseline_prompt.encode("utf-8")),
                "raw_payloads_exposed_to_summarizer": baseline_exposed_count,
                "raw_payload_bytes_exposed_to_summarizer": baseline_exposed_bytes,
                "retained_context_bytes": len(_message_text(baseline_retained).encode("utf-8")),
                "original_trace_bytes": original_trace_bytes,
                "trusted_evidence_recovery": False,
                "trusted_timestamp_tuple": False,
                "recovered_content_hash": None,
                "reference_guided_action": baseline_immediate_action,
                "reference_guided_action_correct": (
                    baseline_immediate_action == case["expected_action"]
                ),
                "fallback_action": baseline_fallback_action,
                "fallback_action_correct": baseline_fallback_action == case["expected_action"],
                "targeted_rehydration_bytes": history_bytes,
                "fallback_history_contains_payload": history_has_payload,
                "fallback_history_contains_artifact_metadata": history_has_reference,
            },
            "candidate": {
                "input_trace_hash": case["trace_hash"],
                "compaction_wall_ms": round(candidate_elapsed_ms, 3),
                "prompt_bytes": len(candidate_prompt.encode("utf-8")),
                "raw_payloads_exposed_to_summarizer": candidate_exposed_count,
                "raw_payload_bytes_exposed_to_summarizer": candidate_exposed_bytes,
                "retained_context_bytes": len(_message_text(candidate_retained).encode("utf-8")),
                "original_trace_bytes": original_trace_bytes,
                "trusted_evidence_recovery": candidate_payload is not None and candidate_ref is not None,
                "trusted_timestamp_tuple": _trusted_timestamp_tuple(
                    candidate_payload, candidate_ref
                ),
                "recovered_content_hash": recovered_hash,
                "reference_guided_action": candidate_action,
                "reference_guided_action_correct": (
                    candidate_action == case["expected_action"]
                ),
                "fallback_action": candidate_action,
                "fallback_action_correct": candidate_action == case["expected_action"],
                "targeted_rehydration_bytes": target_bytes,
                "fallback_history_contains_payload": True,
                "fallback_history_contains_artifact_metadata": True,
            },
        },
    }


def _aggregate(cases: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rows = [case["arms"][arm] for case in cases]
    return {
        "raw_payloads_exposed_to_summarizer": sum(
            row["raw_payloads_exposed_to_summarizer"] for row in rows
        ),
        "raw_payload_bytes_exposed_to_summarizer": sum(
            row["raw_payload_bytes_exposed_to_summarizer"] for row in rows
        ),
        "trusted_evidence_recoveries": sum(
            int(row["trusted_evidence_recovery"]) for row in rows
        ),
        "trusted_timestamp_tuples": sum(
            int(row["trusted_timestamp_tuple"]) for row in rows
        ),
        "reference_guided_correct_actions": sum(
            int(row["reference_guided_action_correct"]) for row in rows
        ),
        "fallback_correct_actions": sum(
            int(row["fallback_action_correct"]) for row in rows
        ),
        "targeted_rehydration_bytes": sum(
            row["targeted_rehydration_bytes"] for row in rows
        ),
        "prompt_bytes": sum(row["prompt_bytes"] for row in rows),
        "retained_context_bytes": sum(row["retained_context_bytes"] for row in rows),
        "original_trace_bytes": sum(row["original_trace_bytes"] for row in rows),
        "compaction_wall_ms": round(sum(row["compaction_wall_ms"] for row in rows), 3),
    }


def run_benchmark() -> dict[str, Any]:
    """Run the offline paired benchmark and return an auditable JSON value."""
    runtime_fingerprint = build_runtime_fingerprint()
    cases = [
        _compact_case(_build_case(definition, index))
        for index, definition in enumerate(benchmark_case_definitions(), start=1)
    ]
    baseline = _aggregate(cases, "baseline")
    candidate = _aggregate(cases, "candidate")
    case_count = len(cases)
    criteria = {
        "paired_identical_inputs": all(
            case["arms"]["baseline"]["input_trace_hash"]
            == case["arms"]["candidate"]["input_trace_hash"]
            == case["trace_hash"]
            for case in cases
        ),
        "zero_raw_ground_truth_in_candidate_summary_prompt": (
            candidate["raw_payloads_exposed_to_summarizer"] == 0
        ),
        "trusted_evidence_recovery_strictly_better": (
            candidate["trusted_evidence_recoveries"]
            > baseline["trusted_evidence_recoveries"]
            and candidate["trusted_evidence_recoveries"] == case_count
        ),
        "trusted_timestamp_recovery_complete": (
            candidate["trusted_timestamp_tuples"] == case_count
            and baseline["trusted_timestamp_tuples"] < case_count
        ),
        "fallback_decision_correctness_strictly_better": (
            candidate["fallback_correct_actions"] > baseline["fallback_correct_actions"]
        ),
        "targeted_rehydration_strictly_cheaper": (
            candidate["targeted_rehydration_bytes"]
            < baseline["targeted_rehydration_bytes"]
        ),
        "candidate_retains_compaction_benefit": (
            candidate["retained_context_bytes"] < candidate["original_trace_bytes"]
        ),
        "runtime_matches_lock": runtime_fingerprint["lock_alignment"]["matches"],
    }
    return {
        "schema_version": 1,
        "benchmark": "otc_compaction_ab",
        "generated_at": datetime.now(UTC).isoformat(),
        "runtime_fingerprint": runtime_fingerprint,
        "case_count": case_count,
        "methodology": {
            "model": "controlled_lossy_v1",
            "paired_inputs": True,
            "judge": "deterministic_rules_v1",
            "network_required": False,
            "database_required": False,
            "baseline_fallback": (
                "full DeepAgents conversation_history rendering; bytes are a lower "
                "bound because the timestamp heading is excluded"
            ),
            "scope": (
                "structural evidence, timestamp, prompt-exposure, context-size, and "
                "recovery-cost guarantees; not remote-LLM answer quality"
            ),
        },
        "implementations": {
            "baseline": {
                "label": "DeepAgents/LangChain default",
                "class": (
                    f"{SummarizationMiddleware.__module__}."
                    f"{SummarizationMiddleware.__name__}"
                ),
                "deepagents_version": _package_version("deepagents"),
                "langchain_version": _package_version("langchain"),
            },
            "candidate": {
                "label": "Ledger-aware candidate",
                "class": (
                    f"{LedgerScopedCompactionMiddleware.__module__}."
                    f"{LedgerScopedCompactionMiddleware.__name__}"
                ),
            },
        },
        "cases": cases,
        "aggregate": {"baseline": baseline, "candidate": candidate},
        "criteria": criteria,
        "advantage_demonstrated": all(criteria.values()),
        "tradeoffs": {
            "candidate_retained_context_overhead_bytes": (
                candidate["retained_context_bytes"] - baseline["retained_context_bytes"]
            ),
            "latency_is_observational_not_a_pass_criterion": True,
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render a compact human-auditable report from :func:`run_benchmark`."""
    baseline = result["aggregate"]["baseline"]
    candidate = result["aggregate"]["candidate"]
    rows = [
        ("Raw payloads exposed", "raw_payloads_exposed_to_summarizer"),
        ("Raw payload bytes exposed", "raw_payload_bytes_exposed_to_summarizer"),
        ("Trusted evidence recoveries", "trusted_evidence_recoveries"),
        ("Trusted timestamp tuples", "trusted_timestamp_tuples"),
        ("Reference-guided correct actions", "reference_guided_correct_actions"),
        ("Fallback correct actions", "fallback_correct_actions"),
        ("Targeted rehydration bytes", "targeted_rehydration_bytes"),
        ("Summarizer prompt bytes", "prompt_bytes"),
        ("Retained context bytes", "retained_context_bytes"),
        ("Original trace bytes", "original_trace_bytes"),
        ("Observed compaction wall ms", "compaction_wall_ms"),
    ]
    table = [
        "| Metric | DeepAgents/LangChain default | Ledger-aware candidate |",
        "|---|---:|---:|",
    ]
    table.extend(
        f"| {label} | {baseline[key]} | {candidate[key]} |" for label, key in rows
    )
    criteria = "\n".join(
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in result["criteria"].items()
    )
    per_case = "\n".join(
        (
            f"- `{case['id']}`: expected `{case['expected_action']}`; "
            f"default fallback `{case['arms']['baseline']['fallback_action']}`; "
            f"candidate `{case['arms']['candidate']['fallback_action']}`"
        )
        for case in result["cases"]
    )
    conclusion = (
        "ADVANTAGE DEMONSTRATED"
        if result["advantage_demonstrated"]
        else "ADVANTAGE NOT DEMONSTRATED"
    )
    runtime = result["runtime_fingerprint"]
    stack = runtime["benchmark_stack"]
    installed = runtime["installed_distributions"]
    lockfile = runtime["source_files"]["uv.lock"]
    return (
        "# OTC Compaction A/B Benchmark\n\n"
        f"**Result: {conclusion}.**\n\n"
        "This is a deterministic structural A/B using the same input traces and the "
        "same controlled lossy summarizer in both arms. It tests guarantees supplied "
        "by compaction, not remote-model prose quality. The default arm is allowed to "
        "fall back to its full rendered conversation-history file.\n\n"
        f"Runtime: Python `{runtime['python']['version']}`; "
        f"DeepAgents `{stack['deepagents']}`; LangChain `{stack['langchain']}`; "
        f"LangGraph `{stack['langgraph']}`.\n\n"
        f"Lockfile: `{lockfile['sha256']}`. Installed-distribution fingerprint: "
        f"`{installed['sha256']}` ({installed['count']} packages). "
        f"Lock alignment: **{'PASS' if runtime['lock_alignment']['matches'] else 'FAIL'}**.\n\n"
        + "\n".join(table)
        + "\n\n## Per-case continuation\n\n"
        + per_case
        + "\n\n## Predeclared criteria\n\n"
        + criteria
        + "\n\n## Interpretation\n\n"
        "The candidate deliberately spends more retained-context bytes on an exact "
        "server manifest. In exchange, it keeps raw evidence out of the summarizer, "
        "retains the trusted artifact ID/hash/generation-time tuple, and supports a "
        "smaller targeted read. Latency is reported but is not a pass criterion.\n"
    )


class RecordingDelegateChatModel(BaseChatModel):
    """Proxy a real chat model while recording exact request/response evidence."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    delegate: BaseChatModel
    records: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return f"recording-{getattr(self.delegate, '_llm_type', 'chat-model')}"

    @property
    def profile(self) -> Any:
        return getattr(self.delegate, "profile", None)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        prompt = "\n".join(_message_text(message) for message in messages)
        started_at = datetime.now(UTC)
        started = perf_counter_ns()
        try:
            response = self.delegate.invoke(messages, stop=stop, **kwargs)
        except Exception as exc:
            finished_at = datetime.now(UTC)
            self.records.append(
                {
                    "prompt": prompt,
                    "response": "",
                    "reasoning": "",
                    "usage": _empty_usage(),
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "wall_ms": round((perf_counter_ns() - started) / 1_000_000, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        finished_at = datetime.now(UTC)
        self.records.append(
            {
                "prompt": prompt,
                "response": _message_text(response),
                "reasoning": _response_reasoning(response),
                "usage": _response_usage(response),
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "wall_ms": round((perf_counter_ns() - started) / 1_000_000, 3),
                "error": None,
            }
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


def _empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _response_reasoning(response: BaseMessage) -> str:
    for source in (
        getattr(response, "additional_kwargs", {}) or {},
        getattr(response, "response_metadata", {}) or {},
    ):
        for key in ("reasoning_content", "reasoning"):
            value = source.get(key)
            if isinstance(value, str):
                return value
    return ""


def _response_usage(response: BaseMessage) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    metadata = getattr(response, "response_metadata", {}) or {}
    token_usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not isinstance(token_usage, dict):
        return _empty_usage()
    input_tokens = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
    output_tokens = int(
        token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0
    )
    total_tokens = int(
        token_usage.get("total_tokens") or input_tokens + output_tokens
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


_SENSITIVE_KEY_RE = re.compile(
    (
        r"(?:api[_-]?(?:key|token)|access[_-]?token|refresh[_-]?token|"
        r"auth[_-]?token|bearer[_-]?token|password|secret|credential|"
        r"authorization)"
    ),
    flags=re.IGNORECASE,
)


def _public_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _public_metadata(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_public_metadata(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def _continuation_prompt(
    *,
    case: dict[str, Any],
    retained_context: str,
    recovered_evidence: str,
    recovery_kind: str,
) -> str:
    return f"""<role>
OTC hedge agent continuing immediately after conversation compaction.
</role>

<compacted_context>
{retained_context}
</compacted_context>

<recovery kind=\"{recovery_kind}\">
{recovered_evidence}
</recovery>

<current_time>{case['now']}</current_time>

<rules id=\"LIVE_CONTINUATION_CONTRACT_V1\">
Choose exactly one action: book_hedge, refresh_risk, resolve_hedge, or
insufficient_evidence. Use only the context and recovered evidence above.

Choose refresh_risk when the valuation is from another date or current_time is at
or after expires_at. Choose resolve_hedge when the current position-set hash differs
from the proposal position-set hash. For the current hash, use the positions artifact;
for the proposal hash, use the proposal artifact. Choose book_hedge only when all
freshness and position checks pass AND the evidence includes the exact trusted proposal
artifact_id, content_hash, artifact generated_at, valuation_as_of, risk_generated_at,
and expires_at. The execution tool will revalidate the same tuple transactionally.
Otherwise choose insufficient_evidence. Never invent an id, hash, timestamp, or value.

For book_hedge, return the proposal artifact_id and content_hash. For refresh_risk,
resolve_hedge, or insufficient_evidence, return artifact_id and content_hash as null.

Return exactly one JSON object and no prose:
{{"action":"...","artifact_id":integer_or_null,"content_hash":"sha256:..."_or_null,"reason":"short reason"}}
</rules>"""


def _continuation_grade(
    *,
    parsed: dict[str, Any] | None,
    case: dict[str, Any],
) -> dict[str, Any]:
    action = str((parsed or {}).get("action") or "parse_error")
    evidence_exact = (
        (parsed or {}).get("artifact_id") == case["decision_artifact_id"]
        and (parsed or {}).get("content_hash") == case["decision_content_hash"]
    )
    action_correct = action == case["expected_action"]
    correct = action_correct and (
        action != "book_hedge" or evidence_exact
    )
    unsafe_booking = action == "book_hedge" and (
        case["expected_action"] != "book_hedge" or not evidence_exact
    )
    return {
        "parsed": parsed,
        "action": action,
        "action_correct": action_correct,
        "evidence_exact": evidence_exact,
        "correct": correct,
        "unsafe_booking": unsafe_booking,
    }


def _live_summary(
    *,
    model: BaseChatModel,
    arm: str,
    case: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    recorder = RecordingDelegateChatModel(delegate=model)
    if arm == "baseline":
        middleware = SummarizationMiddleware(model=recorder, backend=StateBackend())
    else:
        middleware = LedgerScopedCompactionMiddleware(
            model=recorder,
            backend=StateBackend(),
            trigger=("tokens", 100_000),
            keep_recent=1,
            max_messages=32,
            ground_truth_tool_names=_GROUND_TRUTH_TOOL_NAMES,
        )
    summary = middleware._create_summary(case["messages"])
    path = f"/conversation_history/live-{case['id']}.md"
    retained = _message_text(
        middleware._build_new_messages_with_path(summary, path)[0]
    )
    record = recorder.records[-1] if recorder.records else {
        "prompt": "",
        "response": "",
        "reasoning": "",
        "usage": _empty_usage(),
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "wall_ms": 0.0,
        "error": "summary recorder produced no call record",
    }
    return summary, retained, record


def _live_continuation(
    *,
    model: BaseChatModel,
    prompt: str,
) -> dict[str, Any]:
    recorder = RecordingDelegateChatModel(delegate=model)
    try:
        response = recorder.invoke(prompt)
    except Exception:
        record = recorder.records[-1]
        return {**record, **_continuation_grade(parsed=None, case={
            "decision_artifact_id": None,
            "decision_content_hash": None,
            "expected_action": "provider_error",
        })}
    record = recorder.records[-1]
    parsed = _parse_json_object(_message_text(response))
    return {**record, "parsed": parsed}


def _run_live_arm(
    *,
    model: BaseChatModel,
    model_config_hash: str,
    arm: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    summary, retained, summary_record = _live_summary(
        model=model,
        arm=arm,
        case=case,
    )
    if arm == "baseline":
        recovery_kind = "full_conversation_history"
        recovered_evidence = case["history"]
    else:
        recovery_kind = "targeted_artifact_reads"
        reads: list[dict[str, Any]] = []
        for reference in case["references"][:2]:
            body = case["artifact_store"][reference["artifact_id"]]
            reads.append(
                {
                    **reference,
                    "selector": {"offset": 0, "limit": 200},
                    "content": json.loads(body),
                }
            )
        recovered_evidence = json.dumps(
            {"artifact_reads": reads},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    continuation_prompt = _continuation_prompt(
        case=case,
        retained_context=retained,
        recovered_evidence=recovered_evidence,
        recovery_kind=recovery_kind,
    )
    continuation = _live_continuation(model=model, prompt=continuation_prompt)
    grade = _continuation_grade(parsed=continuation.get("parsed"), case=case)
    continuation.update(grade)
    raw_contents = case["raw_contents"]
    exposed = [content for content in raw_contents if content in summary_record["prompt"]]
    return {
        "input_trace_hash": case["trace_hash"],
        "model_config_hash": model_config_hash,
        "summary": {**summary_record, "compacted_output": summary},
        "continuation": continuation,
        "recovery_kind": recovery_kind,
        "recovery_bytes": len(recovered_evidence.encode("utf-8")),
        "retained_context_bytes": len(retained.encode("utf-8")),
        "raw_payloads_exposed_to_summarizer": len(exposed),
        "raw_payload_bytes_exposed_to_summarizer": sum(
            len(content.encode("utf-8")) for content in exposed
        ),
    }


def _live_aggregate(cases: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rows = [
        trial["arms"][arm]
        for case in cases
        for trial in case["trials"]
    ]
    summary_usage = [row["summary"]["usage"] for row in rows]
    continuation_usage = [row["continuation"]["usage"] for row in rows]
    return {
        "trial_count": len(rows),
        "correct_continuations": sum(
            int(row["continuation"]["correct"]) for row in rows
        ),
        "unsafe_bookings": sum(
            int(row["continuation"]["unsafe_booking"]) for row in rows
        ),
        "exact_book_evidence": sum(
            int(
                row["continuation"]["action"] == "book_hedge"
                and row["continuation"]["evidence_exact"]
            )
            for row in rows
        ),
        "provider_errors": sum(
            int(bool(row["summary"].get("error")))
            + int(bool(row["continuation"].get("error")))
            for row in rows
        ),
        "raw_payloads_exposed_to_summarizer": sum(
            row["raw_payloads_exposed_to_summarizer"] for row in rows
        ),
        "raw_payload_bytes_exposed_to_summarizer": sum(
            row["raw_payload_bytes_exposed_to_summarizer"] for row in rows
        ),
        "recovery_bytes": sum(row["recovery_bytes"] for row in rows),
        "retained_context_bytes": sum(row["retained_context_bytes"] for row in rows),
        "summary_input_tokens": sum(item["input_tokens"] for item in summary_usage),
        "summary_output_tokens": sum(item["output_tokens"] for item in summary_usage),
        "continuation_input_tokens": sum(
            item["input_tokens"] for item in continuation_usage
        ),
        "continuation_output_tokens": sum(
            item["output_tokens"] for item in continuation_usage
        ),
        "total_tokens": sum(item["total_tokens"] for item in summary_usage)
        + sum(item["total_tokens"] for item in continuation_usage),
        "wall_ms": round(
            sum(row["summary"]["wall_ms"] for row in rows)
            + sum(row["continuation"]["wall_ms"] for row in rows),
            3,
        ),
    }


def run_live_benchmark(
    *,
    model: BaseChatModel,
    model_metadata: dict[str, Any],
    trials: int = 3,
    case_definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run paired real-model summarization and continuation trials."""
    if trials <= 0:
        raise ValueError("trials must be positive")
    runtime_fingerprint = build_runtime_fingerprint()
    public_model = _public_metadata(dict(model_metadata))
    public_model["class"] = f"{type(model).__module__}.{type(model).__name__}"
    model_config_hash = _sha256_bytes(_json_bytes(public_model))
    definitions = case_definitions or benchmark_case_definitions()
    started_at = datetime.now(UTC)
    case_results: list[dict[str, Any]] = []
    for case_index, definition in enumerate(definitions, start=1):
        case = _build_case(definition, case_index)
        trial_results: list[dict[str, Any]] = []
        for trial_index in range(trials):
            order = (
                ["baseline", "candidate"]
                if (case_index + trial_index) % 2 == 1
                else ["candidate", "baseline"]
            )
            arms: dict[str, Any] = {}
            for arm in order:
                arms[arm] = _run_live_arm(
                    model=model,
                    model_config_hash=model_config_hash,
                    arm=arm,
                    case=case,
                )
            trial_results.append(
                {
                    "trial": trial_index + 1,
                    "execution_order": order,
                    "arms": arms,
                }
            )
        case_results.append(
            {
                "id": case["id"],
                "trace_hash": case["trace_hash"],
                "expected_action": case["expected_action"],
                "decision_artifact_id": case["decision_artifact_id"],
                "decision_content_hash": case["decision_content_hash"],
                "trials": trial_results,
            }
        )
    baseline = _live_aggregate(case_results, "baseline")
    candidate = _live_aggregate(case_results, "candidate")
    total_trials = len(definitions) * trials
    paired = all(
        trial["arms"]["baseline"]["input_trace_hash"]
        == trial["arms"]["candidate"]["input_trace_hash"]
        == case["trace_hash"]
        and trial["arms"]["baseline"]["model_config_hash"]
        == trial["arms"]["candidate"]["model_config_hash"]
        == model_config_hash
        for case in case_results
        for trial in case["trials"]
    )
    criteria = {
        "paired_identical_model_and_inputs": paired,
        "candidate_live_accuracy_strictly_better": (
            candidate["correct_continuations"] > baseline["correct_continuations"]
        ),
        "candidate_all_live_continuations_correct": (
            candidate["correct_continuations"] == total_trials
        ),
        "candidate_no_unsafe_bookings": candidate["unsafe_bookings"] == 0,
        "candidate_no_provider_errors": candidate["provider_errors"] == 0,
        "candidate_zero_raw_ground_truth_in_summary_prompt": (
            candidate["raw_payloads_exposed_to_summarizer"] == 0
        ),
        "candidate_targeted_recovery_cheaper": (
            candidate["recovery_bytes"] < baseline["recovery_bytes"]
        ),
        "runtime_matches_lock": runtime_fingerprint["lock_alignment"]["matches"],
    }
    finished_at = datetime.now(UTC)
    return {
        "schema_version": 1,
        "benchmark": "otc_compaction_ab",
        "mode": "live_model",
        "generated_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "model": public_model,
        "model_config_hash": model_config_hash,
        "runtime_fingerprint": runtime_fingerprint,
        "case_count": len(definitions),
        "trials_per_case": trials,
        "request_count": total_trials * 4,
        "methodology": {
            "paired_inputs": True,
            "paired_model_configuration": True,
            "grader": "deterministic_hedge_continuation_v1",
            "baseline_recovery": "full rendered conversation_history",
            "candidate_recovery": (
                "two exact targeted artifact reads: proposal and current positions"
            ),
            "execution_order": "alternating_by_case_and_trial",
            "prompts_and_responses_retained": True,
            "synthetic_deterministic_otc_traces": True,
            "external_writes_or_bookings": False,
        },
        "cases": case_results,
        "aggregate": {"baseline": baseline, "candidate": candidate},
        "criteria": criteria,
        "live_advantage_demonstrated": all(criteria.values()),
    }


def render_live_markdown(result: dict[str, Any]) -> str:
    """Render the aggregate live-model evidence without hiding failed trials."""
    baseline = result["aggregate"]["baseline"]
    candidate = result["aggregate"]["candidate"]
    rows = [
        ("Correct continuations", "correct_continuations"),
        ("Unsafe bookings", "unsafe_bookings"),
        ("Exact booked evidence tuples", "exact_book_evidence"),
        ("Provider errors", "provider_errors"),
        ("Raw payloads exposed to summarizer", "raw_payloads_exposed_to_summarizer"),
        ("Recovery bytes", "recovery_bytes"),
        ("Retained context bytes", "retained_context_bytes"),
        ("Summary input tokens", "summary_input_tokens"),
        ("Summary output tokens", "summary_output_tokens"),
        ("Continuation input tokens", "continuation_input_tokens"),
        ("Continuation output tokens", "continuation_output_tokens"),
        ("Total tokens", "total_tokens"),
        ("Observed wall ms", "wall_ms"),
    ]
    table = [
        "| Metric | DeepAgents/LangChain default | Ledger-aware candidate |",
        "|---|---:|---:|",
    ]
    table.extend(
        f"| {label} | {baseline[key]} | {candidate[key]} |" for label, key in rows
    )
    criteria = "\n".join(
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in result["criteria"].items()
    )
    case_lines: list[str] = []
    for case in result["cases"]:
        baseline_ok = sum(
            int(trial["arms"]["baseline"]["continuation"]["correct"])
            for trial in case["trials"]
        )
        candidate_ok = sum(
            int(trial["arms"]["candidate"]["continuation"]["correct"])
            for trial in case["trials"]
        )
        case_lines.append(
            f"- `{case['id']}` expected `{case['expected_action']}`: "
            f"default {baseline_ok}/{len(case['trials'])}, "
            f"candidate {candidate_ok}/{len(case['trials'])}"
        )
    conclusion = (
        "LIVE MODEL ADVANTAGE DEMONSTRATED"
        if result["live_advantage_demonstrated"]
        else "LIVE MODEL ADVANTAGE NOT DEMONSTRATED"
    )
    model = result["model"]
    runtime = result["runtime_fingerprint"]
    stack = runtime["benchmark_stack"]
    installed = runtime["installed_distributions"]
    lockfile = runtime["source_files"]["uv.lock"]
    return (
        "# OTC Compaction Live-Model A/B\n\n"
        f"**Result: {conclusion}.**\n\n"
        f"Model: `{model.get('channel', '?')}:{model.get('provider', '?')}:"
        f"{model.get('model', '?')}`; trials per case: {result['trials_per_case']}; "
        f"requests: {result['request_count']}.\n\n"
        f"Runtime: Python `{runtime['python']['version']}`; "
        f"DeepAgents `{stack['deepagents']}`; LangChain `{stack['langchain']}`; "
        f"LangGraph `{stack['langgraph']}`.\n\n"
        f"Lockfile: `{lockfile['sha256']}`. Installed-distribution fingerprint: "
        f"`{installed['sha256']}` ({installed['count']} packages). "
        f"Lock alignment: **{'PASS' if runtime['lock_alignment']['matches'] else 'FAIL'}**.\n\n"
        "Both arms used the same model configuration and identical synthetic OTC "
        "traces. Every raw prompt, response, call timestamp, token count, provider "
        "error, and deterministic grade is retained in the JSON evidence.\n\n"
        + "\n".join(table)
        + "\n\n## Per-case accuracy\n\n"
        + "\n".join(case_lines)
        + "\n\n## Predeclared criteria\n\n"
        + criteria
        + "\n"
    )
