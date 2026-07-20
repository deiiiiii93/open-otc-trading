# Ground-Truth Compaction and Freshness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make agent compaction preserve exact deterministic evidence by immutable artifact reference, expose large artifacts through deterministic progressive disclosure, and fail closed when hedge evidence becomes stale before booking.

**Architecture:** Domain-read/write tool results are captured verbatim into the existing CAS and `session_artifacts` ledger before they may be compacted. Compaction sends only server-built artifact capsules to the summarizer and appends an exact manifest to the resulting summary. Context packs and new artifact tools provide ID-scoped manifest/inspect/read disclosure without embeddings or semantic retrieval. Hedge booking cites a captured guard/proposal artifact and revalidates its risk run, timestamps, portfolio fingerprint, and exact approved inputs inside the booking transaction.

**Tech Stack:** Python 3.11, FastAPI services, LangChain/DeepAgents middleware, SQLAlchemy/SQLite, existing content-addressed artifact backend, pytest.

---

### Task 1: Capture ground-truth tool results before compaction

**Files:**
- Create: `backend/app/services/deep_agent/ground_truth.py`
- Modify: `backend/app/services/deep_agent/cas_backend.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Modify: `backend/app/services/deep_agent/personas.py`
- Modify: `backend/app/services/async_agents/agent.py`
- Test: `tests/test_ground_truth_artifacts.py`
- Test: `tests/test_audit_registration.py`

**Steps:**
1. Add failing tests proving a small `DOMAIN_READ` `ToolMessage` is written byte-for-byte to CAS and receives a server-generated artifact reference containing `artifact_id`, `content_hash`, `generated_at`, and `data_as_of`.
2. Add a test proving a capture failure leaves the original message unmodified and without an artifact reference.
3. Refactor CAS persistence into an idempotent capture method shared by filesystem eviction and the new middleware.
4. Implement `GroundTruthArtifactMiddleware`, classifying tools from their server-owned capability group rather than a name allowlist.
5. Register it immediately inside the audit middleware on orchestrator, persona, and async-agent stacks.
6. Run the focused middleware/CAS tests.

### Task 2: Add no-RAG progressive artifact disclosure

**Files:**
- Create: `backend/app/services/deep_agent/artifact_access.py`
- Create: `backend/app/tools/artifacts.py`
- Modify: `backend/app/tools/__init__.py`
- Modify: `backend/app/services/agents.py`
- Modify: `backend/app/services/deep_agent/context_assembler.py`
- Test: `tests/test_artifact_tools.py`
- Test: `tests/test_long_agent_ledger.py`
- Test: `tests/test_agent_tools.py`
- Test: `tests/test_capability_assignments.py`

**Steps:**
1. Add failing tests for workflow-scoped `list_artifacts`, `inspect_artifact`, and exact `read_artifact` by JSON pointer or line range.
2. Add a cross-workflow denial test.
3. Implement deterministic artifact descriptors and raw-content resolution from CAS or inline payloads.
4. Implement structural section maps for JSON top-level keys and Markdown headings; do not add embeddings, vector indexes, or relevance ranking.
5. Register the three tools as `DOMAIN_READ` and expose them to the DeepAgent allowlist.
6. Add compact artifact descriptors to context-pack payloads while retaining `cited_artifact_ids` for compatibility.
7. Run the focused artifact/context-pack tests.

### Task 3: Replace factual summarization with artifact-reference compaction

**Files:**
- Modify: `backend/app/services/deep_agent/compaction.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Test: `tests/test_long_agent_compaction.py`

**Steps:**
1. Replace the existing test that allows raw `get_positions` compaction with tests requiring a durable artifact reference first.
2. Add a test proving the LLM summarizer sees only the artifact capsule, not raw prices/Greeks.
3. Add a test proving the final summary contains the exact server-rendered artifact manifest and preserves all timestamps/hashes unchanged.
4. Pass the server-owned ground-truth tool set into compaction.
5. Project referenced tool messages to deterministic capsules before summarization; protect uncaptured ground-truth messages so compaction fails closed.
6. Run compaction tests.

### Task 4: Enforce hedge evidence freshness at action time

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/hedging_greeks.py`
- Modify: `backend/app/services/domains/hedging_strategy.py`
- Modify: `backend/app/tools/hedging.py`
- Modify: `backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Test: `tests/test_hedging_greeks.py`
- Test: `tests/test_hedging_book.py`
- Test: `tests/test_hedging_tools.py`

**Steps:**
1. Add failing tests that expose `valuation_as_of`, computation/generation time, position fingerprint, artifact age, and policy expiry.
2. Add booking tests for nonexistent/wrong-portfolio/superseded/expired risk runs, changed portfolio fingerprints, source-artifact workflow mismatch, and tampered solver legs.
3. Add `OPEN_OTC_HEDGE_RISK_MAX_AGE_SECONDS` with a conservative configurable default.
4. Return explicit temporal/fingerprint metadata from hedge guard and proposal reads.
5. Require `source_artifact_id` on `book_hedge` and validate the artifact, exact proposal fields, risk run, valuation time, latest-run identity, portfolio fingerprint, and listed instruments inside the transaction.
6. Return a structured `stale_hedge_proposal` refusal; never refresh silently under an old approval.
7. Update the hedge workflow/prompt so solver and manual flows pass the captured source artifact ID.
8. Run hedging tests.

### Task 5: Document the invariant and verify the integrated runtime

**Files:**
- Modify: `docs/superpowers/specs/2026-05-25-long-agent-refactor.md`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`

**Steps:**
1. Rewrite Section C.10 around “compaction may reduce representation, never evidence.”
2. Document distinct artifact `data_as_of` / `generated_at` / `observed_at` and domain `valuation_as_of` / `risk_generated_at` / `expires_at` semantics.
3. Document the deterministic manifest → inspect → exact-read disclosure flow and explicit no-RAG rule.
4. Run focused tests for compaction, artifact capture/access, context packs, middleware registration, and hedging.
5. Run `python -m compileall`, `git diff --check`, and the relevant broader agent/hedging regression slice.

No commit steps are included because the user requested implementation but did not request staging or committing.
