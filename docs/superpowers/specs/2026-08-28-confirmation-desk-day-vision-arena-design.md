# confirmation-desk-day — a vision-grounded golden workflow

**Date:** 2026-08-28
**Status:** design approved, ready for implementation planning
**Subjects:** `z-ai/glm-5.3-flash` (z.ai's first multimodal model) vs
`google/gemini-3.7-flash`, with `openai/gpt-5.6-luna` as a cross-family reference.

---

## 1. Why this workflow exists

The arena's five golden workflows all grade text reasoning over deterministic desk
state. None of them exercises **vision**, so the board says nothing about whether a
model can read a document — which is the first thing a real desk asks of a
multimodal model.

`services/confirmations/` already turns counterparty confirmation documents
(PDF/DOCX, including image-only scans) into booked positions, and
`docs/confirmations/samples/` already holds an ISDA-modelled synthetic corpus
covering all three `extract_mode` values. This workflow grades a contestant on
that pipeline, with **the contestant's own eyes doing the reading**.

### 1.1 The defect this design exists to avoid

`resolve_confirmation_extractor_selection` picks the extraction model by **registry
tag** (`confirmation_extractor` → `fast` → default), currently pinned to
`google/gemini-3.6-flash`. A workflow built naively on top of the existing
pipeline would therefore have every contestant read every document with
gemini-3.6-flash's eyes, and every vision check would land N/N across the field —
a check occupying the denominator while carrying zero ability signal. That is the
same defect class the Run #58 scoring-validity audit found in 15 of 50 checks, and
the same one behind "registered ≠ allowlisted" (`assemble_breach_report`) and
"unrouted skill" (`read-risk-result`).

**The whole design turns on routing the extraction call to the contestant.**

---

## 2. Feasibility probe (run 2026-08-28, before any code)

One direct call per contestant, sending `conf-04-scanned-call-googl.pdf`'s
image-only page as the same `image_url` content parts the real extractor builds,
asking for four fields that exist only in the image.

| Contestant | Route | Saw image | Fields correct | `.content` shape |
|---|---|---|---|---|
| `gpt-5.6-luna` | `openai/gpt-5.6-luna:openai` | yes | 4/4 | `str` |
| `glm-5.3-flash` | `z-ai/glm-5.3-flash:bigmodel` | yes | 4/4 | **`list[thinking, text]`** |
| `gemini-3.7-flash` | `google/gemini-3.7-flash:google-vertex` | yes | 4/4 | `str` |

Truth: `GOOGL` / strike `205.00` / notional `615,000` / ref `ARD-EQO-2026-04688`.

Three findings, all load-bearing:

1. **`bigmodel` accepts image parts over the Anthropic protocol.** The premise
   holds. `langchain_anthropic` 1.4.8's `_format_image` converts OpenAI-shaped
   `image_url` data URIs into Anthropic image blocks, so the existing
   `_content_parts` payload is protocol-portable with no fork.
2. **`RegistryExtractorClient.complete()` cannot consume GLM.** It does
   `if not isinstance(content, str): raise ExtractionError(...)`. GLM returns a
   block list, so **every parse would die the moment the override routes
   extraction to it** — and the board would read that as "GLM cannot see"
   rather than "the harness dropped GLM's answer". Same class as the 4096-token
   cap and the malformed-tool-call lottery: the harness loses the output and the
   model takes the blame. **This must be fixed as part of this work** (§4.1).
   Note the failure is invisible today: calling `RegistryExtractorClient()`
   resolves by *tag* to gemini-3.6-flash, which returns a clean string.
3. **`conf-04` is saturated.** All three read it perfectly. It is the **floor**,
   not a discriminator — which is exactly why §5 adds harder documents.

---

## 3. Scope

**In scope**

- A sixth golden workflow, `confirmation-desk-day` (8 steps, persona `trader`).
- An arena-only, server-stamped **extractor override** so the contestant reads
  the documents (§4).
- Making the synthetic corpus + generator **tracked**, and extending it with
  vision-trap documents (§5).
- A `vision` registry tag and a workflow-level capability requirement enforced
  **at launch** (§7).
- A `_purge_match_confirmations` sweep closing a real FK gap (§6.2).

**Out of scope**

- Changing the production `confirmation_extractor` tag pin (gemini-3.6-flash
  stays the desk default).
- Any new pricing, QuantArk interaction, or fixture harvesting. This workflow
  has **zero QuantArk dependency**, like `ops-settlement-day`; its truth is the
  documents we author.
- Calibrating `par_tool_calls` (deliberately deferred — §9).
- Publishing a board to artena.one (a separate, later decision).

---

## 4. The vision seam

### 4.1 Making the extractor client protocol-agnostic

`RegistryExtractorClient.complete()` currently accepts only `str` content. It
gains a flattening step that concatenates the `text` blocks of a content list and
ignores `thinking`/`reasoning` blocks, raising `ExtractionError` only when **no**
text block is present. This is a straight bug fix independent of the arena: any
Anthropic-protocol reasoning model tagged `confirmation_extractor` hits it today.

### 4.2 The override

A new server-stamped `configurable` key carries the override, following the
`fanout_attribution_extra` precedent — **never** from model or tool input.

| Site | Change |
|---|---|
| `services/confirmations/llm.py` | `resolve_confirmation_extractor_selection(registry, override=None)` — override wins; otherwise the `confirmation_extractor → fast → default` ladder is untouched. `build_extractor_client(selection=None)`. |
| `tools/confirmations.py::parse_trade_confirmation` | Gains `config: RunnableConfig = None`; reads `configurable[CONFIRMATION_EXTRACTOR_SELECTION_KEY]` and threads it into the service. |
| `services/agents.py` | Stamps the key into `configurable` only when the drive path supplies it, mirroring how `accounting_date` is already threaded per match. |
| `arena/runner.py` | Supplies the **match's own model selection** as the override, only when the loaded workflow declares it (§4.3). |

**Why `configurable` and not a `ContextVar`.** `booking_capture.py` documents the
answer: *"LangGraph may execute nodes on worker threads."* `configurable` is the
established subagent-safe route — `limits.py`, `hedging.py` and `artifacts.py`
all take `config: RunnableConfig = None` and read it — and it survives async
resume.

### 4.3 The manifest declares the experimental condition

New optional frontmatter field:

```yaml
extractor_model: contestant   # unset (default) = registry tag ladder, unchanged
```

Without it the override would be an invisible harness behaviour, and any future
workflow touching `parse_trade_confirmation` would be silently rerouted. With it,
the workflow file states the condition and the board records it — the
"predeclare the arm" discipline `CLAUDE.md` requires of A/B evidence.

**Effort and budget ride along.** The arena selection dict carries
`reasoning_effort` when pinned, so the extraction call inherits the same regime
as the agent turn. A contestant is `(model, effort, max_output_tokens)`; the
vision call must not silently run at a different one.

---

## 5. The corpus

### 5.1 Making it tracked

`.gitignore:50` excludes all of `docs/confirmations/` — almost certainly to keep
the third-party ISDA template (`equity-share-option.pdf`, 238 KB) out of the
repo, but it takes the synthetic samples and their generator down with it. A
graded benchmark cannot rest on untracked per-environment files; that is the
"asserting on a gitignored file" failure `CLAUDE.md` already records for
`agent_channels.yaml`.

- Narrow `.gitignore` to ignore **only** the ISDA reference PDF.
- Move the generator into the package as
  `backend/app/golden_workflows/documents/make_confirmations.py`, emitting both
  the existing 8 samples and the new arena documents into that tracked
  directory. One generator, one corpus, reproducible from a clean clone.
- The generator also emits `confirmation-desk-day.truth.json` **from the same
  trade dicts it renders from**, so graded constants cannot drift from the
  documents. No harvesting, no QuantArk.

### 5.2 The graded documents

Every graded value lives **only in an image**, and the generator must place each
one far outside `rel_tol` of every other figure in its own document, so a swapped
or hallucinated value fails rather than coincidentally passing.

| Doc | Vision demand | Role |
|---|---|---|
| `conf-04` (existing) | image-only skewed, grainy scan of ordinary terms | **Floor** — measured saturated in §2; a contestant failing here makes nothing downstream interpretable |
| `conf-08` (existing) | text p1 + scanned p2 carrying the priced terms | Stage-1 page selection; baseline measured at 1-in-6 failures |
| **NEW** `amended-strike` | printed strike struck through, amended value inked in the margin with initials | Reading a *correction*, not just a field |
| **NEW** `ticked-barrier` | barrier direction expressed only by which checkbox is ticked | Categorical read with no textual fallback |
| **NEW** `faint-notional` | notional in a low-contrast column beside a decoy of similar magnitude | Precision under degradation |
| `conf-07` (existing) | no Initial Price anywhere | **Trap** — baseline measured: the incumbent substituted the strike in 2 of 6 runs |

Determinism: the generator seeds its RNG fixedly, so scan degradation
(rotation, grain, blur) is byte-reproducible.

---

## 6. Fixtures, staging, and cleanup

### 6.1 Staging the documents

`parse_trade_confirmation` resolves paths under `artifact_dir/uploads`. The
fixture bundle gains a `documents:` block that copies tracked corpus files into
`artifact_dir/uploads/confirmations/` at match setup under fixed basenames.

This is the binary analogue of `artifact_bodies`, which only writes `str` bodies
and so cannot carry a PDF. Same rationale as that mechanism: **a fixture that
declares a document must create it**, or the agent chases a dangling pointer and
burns calls hunting a file that was never written.

### 6.2 A real gap in the purge

`_delete_portfolios_with_dependents` finds dependents by scanning for columns
literally named `portfolio_id` / `position_id`, then recursing over foreign keys —
but the recursion explicitly skips the `portfolios` table itself
(`if child is parent_table or child is portfolio_table: continue`).

`ConfirmationBatch.default_portfolio_id` is an FK to `portfolios` under a
different column name, so a batch parsed into the arena book is **never swept**
and the final portfolio delete dies on a foreign-key constraint.
`ExtractedTrade.booked_position_id` *is* reachable, via the recursion from
`positions`, so the leak is exactly one table wide — and fatal.

Fix: `_purge_match_confirmations`, in a `finally`, on the same trace-plus-baseline
evidence pattern as `_purge_match_rfqs` and `_purge_match_scenario_sets`. A leak
here is permanent for the same reason theirs are: the next baseline is taken
above the leaked row.

---

## 7. Capability gating

No `vision` tag exists anywhere in the registry — multimodal capability is
undeclared for every model. A text-only contestant on this workflow would score
garbage that pollutes the board while looking like a real result.

- Add a `vision` tag to genuinely multimodal rows in **both**
  `config/agent_channels.yaml` and the tracked `config/agent_channels.example.yml`
  (tag edits always go to both).
- The manifest declares `requires: [vision]`.
- `queue_arena_run` rejects an untagged model **at launch**, not per match —
  the lesson `effort_rejection` taught when `queue_arena_run` kept its own
  narrower check and a board passed validation then died arm by arm after
  spending money.

---

## 8. The workflow manifest

`confirmation-desk-day.md`, persona `trader`, 8 steps.

| # | Step | Skill | Axis focus |
|---|---|---|---|
| 1 | Parse the staged batch (the 6 documents of §5.2) | `book-trade-confirmation` | procedural — segmentation + `extract_mode` |
| 2 | Read back the scanned-only doc (`conf-04`) → `record_answer` | null | **grounding** (floor) |
| 3 | Read back the mixed doc (`conf-08`) — value on scanned p2 | null | **grounding** (page selection) |
| 4 | Amended strike: report the effective strike | null | **grounding** (correction) |
| 5 | Ticked-box barrier: report `barrier_type` | null | **grounding** (categorical) |
| 6 | **Trap** — `conf-07` has no Initial Price; report it | null | adherence — `answer_field_equals {is_null: true}` |
| 7 | Repair the invalid trade with the operator-supplied value, re-validate | null | adherence |
| 8 | Book the valid trades into the arena book | null | synthesis — `booking` payload + position ids |

Rules carried over deliberately from the existing manifests:

- **Grade the answer, not the trace.** Every vision check is
  `answer_field_quotes` / `answer_field_equals` against a value that lives only
  in the image, with the field names spelled out in the `user:` turn (a check
  graded on output the prompt never requests is unwinnable).
- **Step 6's wording is neutral** — it names the answer field without hinting
  that absence is correct, and pairs `is_null` with a `tool_not_called` on the
  booking tool. The graded sin is *substitution*, not attempt.
- **One skill, graded once.** `book-trade-confirmation` carries a real
  `routing:` block (verified), so it is discoverable and gradeable. Every other
  step declares `expected_skill: null`, because the runtime never re-reads a
  loaded SKILL.md and a repeat-skill check can never pass.
- **`args_any_of`** for any legitimate calling convention, so an arbitrary
  lexical choice is never graded.
- Session-wide `success.assertions`: never book a trade whose
  `validation_status` is `invalid`.

---

## 9. `par_tool_calls` is deliberately UNSET

An uncalibrated workflow must stay on the **legacy hyperbolic** EFF curve.
Setting a guessed `par` would opt this workflow into golf scoring against a
denominator no live run has justified, and because cards are derived on read it
would re-score every stored board containing it.

Calibration happens after the first real board, from the **median of
fully-correct trials, excluding merged runs** — the same method used for
`risk-limit-breach-day`, `high-board-portfolio-review-day` and
`ops-settlement-day`. Sanity-check the sample size against the number of runs
that could have produced it: a median hides duplicate-counting, `n` reveals it.

---

## 10. Testing

- Registry load test; exact check-count pin (a new workflow breaks exact-set
  assertions in the catalog test files — enumerate with
  `grep -rln "book-trade-confirmation" tests/`).
- Golden replay must earn 100%.
- A guard that the manifest's graded constants equal the generator-emitted
  `truth.json`.
- Fresh-migration chain stays green (no migration is expected, but the guard is
  standing).
- Purge test: a match that books from a confirmation leaves no
  `confirmation_batches` / `extracted_trades` rows and its portfolio deletes
  cleanly.
- **A live smoke before merge is non-negotiable.** The golden replay fixture is
  hand-written and satisfies each assertion by construction, so it proves
  *satisfiability, never reachability*. This repo has been bitten by that twice
  (trader-rfq, and the Run #58 audit's 15 dead checks).

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| ~~`bigmodel` rejects images~~ | **Retired** — probe passed (§2) |
| GLM's block-list content breaks extraction | Fixed in §4.1; covered by a unit test with a block-list fake |
| New documents are *unwinnable* (0/N) rather than discriminating | `conf-04` floor plus measured mid-tier baselines bracket the difficulty; the first board's per-check tally decides whether any new check is retired |
| Vision checks saturate anyway (all N/N) | Acceptable outcome, honestly reported — a saturated axis is a finding about the field, not a defect, provided the per-check tally is published with it |
| Extraction non-determinism inflates trial variance | Multi-trial runs; CON is the intended instrument for exactly this |
| The override leaks into non-arena paths | Gated on the manifest's `extractor_model: contestant`; default-unset preserves today's behaviour byte for byte |

---

## 12. Open items for the implementation plan

- Exact `configurable` key name and its constant's home module.
- Where the staged-document copy runs in `run_match`'s setup ordering relative
  to `apply_seed`, and whether the copy is idempotent across trials of one match.
- Whether the `documents:` staging block belongs in the fixtures JSON (beside
  `seed`/`replay`) or in the manifest frontmatter beside `fixtures:`.
