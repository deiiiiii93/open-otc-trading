# Structured-Answer Scoring for the Flagship Arena Workflow

**Date:** 2026-07-07
**Status:** Design — approved decisions, pending spec review
**Subsystem:** `backend/app/golden_workflows` + `backend/app/services/arena` + `backend/app/tools`

## Problem

The flagship discrimination benchmark (`risk-manager-control-day`, 9 steps / 39
points) scores two of its grounding/adherence checks by **fuzzy scanning the free-text
response** (`ctx.response_text`). Both checks confuse *presence* with *assertion*:

- `response_contains ["AAPL"]` (assertions.py:284, **adherence** axis) passes on any
  response that contains the substring `AAPL` — including *"AAPL looks fine, the hotspot
  is TSLA"*. It does not verify the model **named AAPL as the hotspot**.
- `response_quotes_value 573.3467… near ["delta"]` (assertions.py:325, **grounding**
  axis) passes when a numeric token matching the truth value falls within a 160-char
  window after the word `delta`. A model that prints `delta_cash: 573.35` passes, and
  the truth number could be quoted for an unrelated reason. It does not verify the
  model **assigned 573.35 the role of delta**.

Both weaknesses share a root cause: a free-text scan cannot bind a value (or a
category) to its **semantic role**. We cannot tell a right answer from a plausible-but-
wrong one. The benchmark exists to discriminate model ability; these checks blunt it.

## Goal

Make the model **commit typed, role-bound answers** for the ambiguous steps, so a
check verifies *"the model asserted AAPL is the hotspot"* and *"the model asserted
573.35 is the delta"* — not merely that the tokens appeared. Preserve the pinned
39-point denominator, the axis structure (GRD/ADH/SYN/PRC/EFF stats), and the golden
replay's 39/39.

## Decisions (locked)

1. **Capture = an answer-recording tool.** A new benign `record_answer(answer: dict)`
   tool. Its call args are the structured answer; scoring reads them from the existing
   `ctx.tool_calls` transcript. The free-text response is left intact so the synthesis
   (SYN) axis still scores natural-language quality. (Rejected: JSON-block-in-response —
   parse-fragile; provider-native JSON schema — destroys the prose SYN measures and
   depends unevenly on per-channel support.)

2. **Rollout = 1:1 swap of the ambiguous checks on the flagship.** Denominator stays
   **39**; each swapped check keeps its **exact axis** so the card stats are structurally
   unchanged. (Rejected: additive new points — breaks the pinned count and leaves the
   weak checks scoring; pilot-a-new-workflow — defers the real payoff.)

3. **Non-compliance = strict, surfaced distinctly.** No `record_answer` call, or the
   expected key absent → that check scores **0** with a naming detail
   (`"no answer recorded"` / `"key delta absent; answered: delta_cash=573.35"`). Other
   axes are unaffected; it is **never** an `invalid` match. Following the output contract
   is part of ability. (Rejected: fuzzy-text fallback — reopens the ambiguity; partial
   credit for right-value-wrong-key — fractional points fight the integer +1/check
   aggregate and the pinned denominator.)

4. **Tool scope = shared toolset.** `record_answer` is registered as a normal benign
   tool available everywhere (the arena runs the *real* orchestrator, so a no-op recorder
   is the faithful exposure). The live desk never auto-calls it; only golden-workflow
   step prompts request it.

## Architecture

### The `record_answer` tool (`backend/app/tools/`)

A no-op recorder, capability group `DOMAIN_READ` (benign, read-class — safe under the
fan-out read-only guard, no audit-write classification):

```python
@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("record_answer", args_schema=RecordAnswerInput)
def record_answer_tool(answer: dict[str, Any]) -> dict[str, Any]:
    """Record your final structured answer for this question when asked to.
    Pass each requested field as a key in `answer` (e.g.
    answer={"hotspot": "AAPL", "delta": 573.35}). This does not change any
    state; it captures your answer verbatim for evaluation."""
    return {"recorded": True, "fields": dict(answer or {})}
```

- Registered in `QUANT_AGENT_TOOLS` (`tools/__init__.py`) **and** added to
  `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`) — the documented allowlist gotcha: a
  tool absent from `DEEP_AGENT_TOOL_NAMES` is silently dropped by
  `select_deep_agent_tools()` and the model can never call it.
- `RecordAnswerInput` is a Pydantic schema with a single `answer: dict[str, Any]` field.

### Answer accessor (`transcript.py`)

Add `AssertionContext.answer` (or a helper `answer_fields(ctx)`) that merges the args of
**all** `record_answer` calls within the step (`last-wins` per key), yielding a flat
`dict[str, Any]`. Empty dict when the tool was not called. Reads only from the
already-captured `ctx.tool_calls`; no transcript-shape change.

### Two new assertion types (`assertions.py`, `schema.py`, `scoring.py`)

**`answer_field_equals`** — axis **adherence** (same as `response_contains`):
```yaml
- type: answer_field_equals
  field: hotspot
  equals: AAPL          # OR: any_of: ["AAPL"]
```
Normalized comparison (case-insensitive, stripped). Fail details:
- key absent → `"no answer recorded for hotspot"` (when no record_answer at all) /
  `"key hotspot absent; answered: <keys=values>"` (tool called, key missing).
- value mismatch → `"hotspot=<got> != <expected>"`.

**`answer_field_quotes`** — axis **grounding** (same as `response_quotes_value`):
```yaml
- type: answer_field_quotes
  field: delta
  value: 573.3467058766552
  rel_tol: 0.02
  match: signed          # or: magnitude
```
Direct numeric compare on the recorded value:
`abs(got − target) ≤ rel_tol·|target|`, honoring `match` (magnitude → compare
absolute values, for loss-language metrics like CVaR). Non-numeric recorded value →
fail with `"delta=<got!r> is not numeric"`. Missing key → same naming details as above.

Both types map their axis explicitly in `scoring._AXIS_BY_TYPE`:
```python
"answer_field_equals": "adherence",
"answer_field_quotes": "grounding",
```

Schema additions in `schema.py`: extend the assertion model union with the two new
`type` literals and their fields (`field`, `equals`, `any_of`, `value`, `rel_tol`,
`match`), reusing the existing `match`/`rel_tol` field definitions where present.

### The 5 swaps (flagship manifest `risk-manager-control-day.md`)

Steps are identified by manifest position and by their `replay:` id (the fixture key
that must be re-recorded).

| Manifest step (replay id) | Removed (axis) | Added (same axis) | record_answer keys |
|---|---|---|---|
| 3 — hotspot (`step-3-read-fresh-risk`) | `response_contains ["AAPL"]` (ADH) | `answer_field_equals field=hotspot equals=AAPL` | `hotspot` |
| 3 — hotspot (`step-3-read-fresh-risk`) | `response_quotes_value 573.3467… near delta` (GRD) | `answer_field_quotes field=delta value=573.3467… match=signed` | `delta` |
| 5 — landscape grid (`step-grid-comprehension`) | `response_quotes_value 16.403… near gamma` (GRD) | `answer_field_quotes field=gamma_at_+10pct value=16.403…` | `gamma_at_+10pct` |
| 5 — landscape grid (`step-grid-comprehension`) | `response_quotes_value 391.191… near delta` (GRD) | `answer_field_quotes field=delta_at_-20pct value=391.191…` | `delta_at_-20pct` |
| 6 — scenario CVaR (`step-5-scenario-test`) | `response_quotes_value -7758.98… near cvar/…` (GRD) | `answer_field_quotes field=cvar value=-7758.98… match=magnitude` | `cvar` |

**Axis conservation:** 5 checks removed → 5 added, each preserving its axis — **1
adherence** swap (step 3 hotspot) + **4 grounding** swaps (step 3 delta, step 5 gamma &
delta, step 6 cvar). Adherence subtotal, grounding subtotal, and the **39** denominator
are all unchanged.

**Untouched** free-text checks (genuinely not structured answers): step 1 staleness
`response_contains`, step 8 trap-refusal `response_contains`, step 9 `artifact_contains`
synthesis, all `tool_called`/`task_returned_id`/`tool_result_path` procedural/grounding
checks.

### Prompt contract (fairness of eliciting)

Each swapped step's `user` turn gains an explicit, model-identical instruction naming
the exact keys the checks read. Examples:

- Step 3: *"Now check the updated risk result — what's the hotspot? Record your answer
  with `record_answer(hotspot=<ticker>, delta=<number>)`."*
- Step 5: *"…Record your answer with `record_answer(gamma_at_+10pct=<number>,
  delta_at_-20pct=<number>)`."*
- Step 6: *"…Record the tail loss with `record_answer(cvar=<number>)`."*

Because the prompt names the keys identically for every model, a wrong key is a real
ability miss, not a prompt-guessing artifact.

### EFF / par accounting

`record_answer` is **answer instrumentation, not workflow execution**:
- It is **not** added to any step's `expected_tools`, so `designed_par` stays **11**.
- It is **excluded** from the EFF `actual_calls` tool count
  (`diagnosis.counts_detail.tool_calls`, consumed by `scoring.card_from_axes`). Find the
  producer of that count and filter out `record_answer` by **normalized** name
  (`normalize_tool_name`, so a `record_answer_tool`-suffixed trace name is also
  excluded), so a compliant model
  calling `record_answer` on 3 steps is not penalized on EFF for doing exactly what the
  benchmark asked. EFF continues to measure genuine workflow leanness.

### Fixtures & determinism

- **`truth.json` is NOT hand-edited.** It is harvester-owned: `harvest()` emits only
  numeric `TARGETS`, and `test_arena_fixture_determinism` asserts both
  `set(truth) == {TARGETS names}` and that the committed file equals a fresh harvest. A
  hand-added categorical key would fail those gates. The numeric `answer_field_quotes`
  targets already exist in `truth.json` (same numbers, new assertion types); the
  categorical hotspot `"AAPL"` is **derived** from the existing
  `truth["aapl_hotspot_delta"]["path"]` (`positions[underlying=AAPL].delta`).
- `test_flagship_grounding_targets_match_truth_file` is extended so the new
  `answer_field_quotes.value` targets are validated against the existing numeric
  `truth.json` values, and the `answer_field_equals field=hotspot` `equals` value is
  validated against the underlying parsed from `aapl_hotspot_delta.path`.
- **Golden replay fixtures** are re-recorded by editing the three replay entries in
  `risk-manager-control-day.fixtures.json` — `fixtures.replay[<id>].ai.tool_calls` for
  `step-3-read-fresh-risk` (hotspot + delta), `step-grid-comprehension`
  (gamma_at_+10pct + delta_at_-20pct), and `step-5-scenario-test` (cvar) — appending a
  `record_answer` call carrying the correct values. `transcript_from_replay` sources
  `ctx.tool_calls` directly from these entries, and `ctx.answer` reads from
  `ctx.tool_calls`, so the golden replay re-earns **39/39** (fixture-consistency gate).
  This is a fixture edit, not a producer change — the underlying numbers are already
  deterministic (Spec A).

## Failure handling

- **Model never calls `record_answer`** → every `answer_field_*` check on that step
  fails with `"no answer recorded for <field>"`. SYN/PRC/other-ADH checks still score.
  Not `invalid`.
- **Model calls it with the wrong key** (`delta_cash` instead of `delta`) → fail with
  `"key delta absent; answered: delta_cash=573.35"`.
- **Right key, wrong value** → fail with `"delta=<got> != <expected>"` (or numeric
  tolerance detail).
- **Non-numeric value under a numeric field** → `"delta=<got!r> is not numeric"`.
- **Multiple `record_answer` calls in one step** → merged last-wins per key (a model
  correcting itself is credited on its final value).
- All new detail strings flow through the existing drilldown UI (`check.detail`), which
  already wraps long reasons — no required frontend change.

## Testing

- **New unit tests** (`tests/test_golden_workflow_assertions.py`):
  `answer_field_equals` hit/miss/wrong-key/no-answer; `answer_field_quotes`
  hit/miss/sign/magnitude/non-numeric/tolerance-edge; the naming details.
- **Answer accessor test** (`tests/` transcript): merge/last-wins/empty.
- **Tool test**: `record_answer` returns `{"recorded": True, "fields": {...}}` and is
  present in `select_deep_agent_tools()` output (allowlist regression).
- **Axis-map test** (`tests/test_arena_scoring.py`): the two new types map to
  adherence/grounding; the flagship still totals **39** and axis subtotals are
  unchanged in structure.
- **Pinned-count updates:** `test_flagship_loads`, `test_arena_scoring`,
  `test_golden_workflow_regression` (replay 39/39),
  `test_flagship_grounding_targets_match_truth_file`.
- **EFF test:** a transcript with N workflow calls + 3 `record_answer` calls yields the
  same `actual_calls`/EFF as N workflow calls alone (record_answer excluded).

## Out of scope

- The other golden workflows (`trader-rfq-booking-day`, `high-board-portfolio-review-
  day`) keep their existing checks — a follow-up may adopt structured answers there.
- Provider-native JSON-schema output enforcement.
- Partial credit for right-value-wrong-key.
- **Re-scoring historical runs #1–#13.** They contain no `record_answer` calls, so on
  re-derive their swapped checks read as honest misses (GRD/ADH would drop). We do
  **not** back-fill or re-score them; new boards start from the next run. (Their stored
  breakdowns remain valid under the *old* assertion types they were scored with; the
  swap only affects future matches.)

## Rollout / validation

After implementation, run a small real board (the Run #12 model set —
DeepSeek-V4-Flash/Pro, Step-3.7-Flash, Mimo-V2.5, n≥1) against the updated flagship to
confirm: compliant models record answers and earn the swapped checks; the card stats
still sum to 39; and the drilldown shows the new role-bound details (including a
wrong-key miss if any model mislabels).
