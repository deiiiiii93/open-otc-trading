# Live Compaction A/B Evidence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce repeatable live-model evidence comparing DeepAgents/LangChain default compaction with the OTC ledger-aware candidate under identical model settings and OTC traces.

**Architecture:** Extend the offline benchmark with a live runner that invokes one real chat model for both arms. Each paired trial performs a live summary call, grants production-style recovery (the default gets its full rendered conversation history; the candidate gets exact targeted reads of the proposal and current positions), then performs a live continuation call constrained to the hedge evidence contract. Deterministic code grades actions, exact evidence copying, unsafe bookings, prompt exposure, tokens, and latency; JSON retains complete prompts/responses and Markdown renders aggregates.

**Tech Stack:** Python 3.11, clean lock-synchronized worktree `.venv`, LangChain chat models, DeepAgents middleware, pytest, direct DeepSeek channel.

---

### Task 1: Characterize the live runner with a fake chat model

**Files:**
- Modify: `tests/test_compaction_ab_benchmark.py`
- Modify: `backend/app/services/deep_agent/compaction_benchmark.py`

**Step 1: Write a failing fake-live test**

Use a prompt-aware fake model that emits a lossy summary, refuses fresh booking when the default arm has no trusted artifact tuple, and copies the exact candidate artifact id/hash after targeted disclosure. Assert identical configuration fingerprints, complete prompt/response capture, deterministic grading, token/latency fields, and secret-free serialization.

**Step 2: Run the test and verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_compaction_ab_benchmark.py`

Expected: FAIL because `run_live_benchmark` and `render_live_markdown` do not exist.

**Step 3: Implement the live core**

Add a recording chat-model wrapper, live paired-arm execution, strict JSON response parsing, evidence-aware action grading, safe model metadata, aggregate metrics, predeclared live criteria, and report rendering. Accept optional case definitions so later features can add cases without changing the runner.

**Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest -q tests/test_compaction_ab_benchmark.py`

Expected: PASS without network access.

### Task 2: Add live CLI configuration

**Files:**
- Modify: `scripts/compaction_ab_benchmark.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Step 1: Add `--live`, channel/provider/model, trials, env-file, and channel-file options**

Load credentials without serializing them, construct the configured project model, force temperature zero and bounded output, and write separate `compaction_ab_live_<model>.json/.md` artifacts.

**Step 2: Document evidence levels**

Keep the deterministic structural gate mandatory and describe the live-model supplement, paired settings, request count, stored transcripts, and rerun command.

### Task 3: Run the real paired experiment

**Files:**
- Generate: `outputs/compaction_ab_live_deepseek-v4-flash.json`
- Generate: `outputs/compaction_ab_live_deepseek-v4-flash.md`

**Step 1: Run three trials over four cases**

Require `uv lock --check --quiet`, synchronize a clean environment with
`uv sync --locked --extra dev`, then run that `.venv` against the direct
`deepseek-v4-flash` channel, producing 24 live summary calls and 24 live continuation
calls. Record Python/platform, source-file hashes, the complete installed-distribution
list and hash, the core agent stack, and lock alignment in the JSON evidence.

**Step 2: Inspect every failed/inconclusive case**

Confirm input/config fingerprints, timestamps, token usage, raw prompts, raw responses, exact action/evidence grading, and provider errors. Do not rewrite results to make the candidate win.

**Step 3: Verify code and regressions**

Run the benchmark unit tests, compaction/capture/artifact slices, compileall, and `git diff --check`.
