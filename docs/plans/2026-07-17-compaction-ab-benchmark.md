# Compaction A/B Benchmark Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and run a cheap, reproducible A/B benchmark that compares the installed DeepAgents/LangChain default compaction path with the OTC ledger-aware compaction strategy on identical traces.

**Architecture:** A deterministic benchmark runner will construct realistic captured OTC tool traces, pass the same messages through the installed default `SummarizationMiddleware` and `LedgerScopedCompactionMiddleware`, and score both arms without an LLM judge. A controlled lossy chat model isolates structural compaction guarantees from model quality. JSON output will preserve implementation provenance, input hashes, per-case evidence, aggregate metrics, and predeclared pass/fail criteria so future features can add cases without changing the scoring contract.

**Tech Stack:** Python 3.11, LangChain/DeepAgents middleware, LangChain message models, pytest, JSON/Markdown reports.

---

### Task 1: Define the paired benchmark and scoring contract

**Files:**
- Create: `backend/app/services/deep_agent/compaction_benchmark.py`
- Test: `tests/test_compaction_ab_benchmark.py`

**Step 1: Write the failing tests**

Cover identical trace hashes, use of the installed default middleware, exact server-manifest recovery, default loss of message metadata, deterministic hedge continuation decisions, raw-payload exposure, targeted rehydration bytes, and stable aggregate pass criteria.

**Step 2: Run the focused tests and verify they fail**

Run: `pytest -q tests/test_compaction_ab_benchmark.py`

Expected: FAIL because the benchmark module does not exist.

**Step 3: Implement the benchmark core**

Build deterministic OTC cases for fresh hedge booking, expired risk refresh, position-set drift, and historical valuation. Instantiate `deepagents.middleware.summarization.SummarizationMiddleware` as arm A and `LedgerScopedCompactionMiddleware` as arm B with the same controlled model response and identical source messages. Capture prompts, parse only server manifests, rehydrate by exact artifact ID/hash, and compute transparent per-case metrics.

**Step 4: Run the focused tests**

Run: `pytest -q tests/test_compaction_ab_benchmark.py`

Expected: PASS.

### Task 2: Add a reusable CLI and durable reports

**Files:**
- Create: `scripts/compaction_ab_benchmark.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Step 1: Add CLI coverage**

Test JSON serialization, Markdown rendering, and a nonzero exit when any predeclared advantage criterion fails.

**Step 2: Implement the CLI**

Support `--json-out` and `--markdown-out`, print the metric table, and exit according to `advantage_demonstrated`. Do not require credentials, a database, or network access.

**Step 3: Document extension and execution**

Document the one-command benchmark, what the deterministic arm proves, what it does not prove, and how future feature cases extend the case factory and evaluator.

### Task 3: Run and verify the proof

**Files:**
- Generate: `outputs/compaction_ab_benchmark.json`
- Generate: `outputs/compaction_ab_benchmark.md`

**Step 1: Run the A/B benchmark**

Run: `python scripts/compaction_ab_benchmark.py`

Expected: exit 0 with `ADVANTAGE DEMONSTRATED` and both report files.

**Step 2: Run focused regression tests**

Run: `pytest -q tests/test_compaction_ab_benchmark.py tests/test_long_agent_compaction.py tests/test_ground_truth_artifacts.py tests/test_artifact_tools.py`

Expected: PASS.

**Step 3: Run static checks**

Run: `python -m compileall -q backend/app scripts/compaction_ab_benchmark.py` and `git diff --check`.

Expected: both commands succeed.
