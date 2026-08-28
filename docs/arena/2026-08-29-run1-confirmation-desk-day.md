# Run #1 — confirmation-desk-day, the first vision board

**Date:** 2026-08-29 (matches run 2026-08-28)
**Workflow:** `confirmation-desk-day`, 9 steps / 33 checks, uncalibrated par
**Contestants:** the four `vision`-tagged models, 1 trial each, effort and budget
unpinned
**DB:** isolated worktree database, not the live desk

> This board ran in a worktree DB, so its run id (#1) is local and does **not**
> continue the main desk's run numbering.

---

## Board

| # | Contestant | Objective | OVR | GRD | ADH | SYN | PRC | EFF | calls | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **glm-5.3-flash** | **97.0** | **84** | 89 | 99 | 99 | 99 | 24 | 36 | 0 |
| 2 | gemini-3.7-flash | 84.8 | 78 | 89 | 99 | 99 | 59 | 13 | 64 | 16 |
| 3 | gpt-5.6-luna | 84.8 | 72 | 59 | 99 | 99 | 89 | 14 | 51 | 0 |
| 4 | deepseek-v4-flash-vision | 36.4 | 37 | 50 | 44 | 0 | 30 | 39 | 9 | 22 |

`CON` is null for every row — one trial each, so consistency is not measured.

## The question this board was built to answer

**GLM 5.3 Flash — z.ai's first multimodal model — tops it.** 97.0 objective, and
its single miss across 33 checks was the faint notional. It read the image-only
scan, the mixed text/scan document, the struck-through-and-amended strike and the
ticked checkbox; it reported the absent Initial Price rather than substituting
the strike; it booked only what validated and wrote a grounded summary. It did so
in **36 tool calls with zero errors** — the leanest clean run on the board.

Against `gemini-3.7-flash` specifically: identical grounding (89), identical
adherence and synthesis. GLM wins on **procedure** (99 vs 59) and on **volume**
(36 calls vs 64, and 0 errors vs 16).

## What actually discriminated — and what did not

Per-check tally across the field. **8 of 33 checks are DEAD** (4/4 or 0/4):

| Check | Rate | Reading |
|---|---|---|
| `s0` parse + counts + skill routing (5 checks) | 4/4 | Everyone parses the batch |
| `s1` conf-04 strike + reference (2 checks) | 4/4 | **The floor is saturated, as predicted** |
| `s8` `create_report` prohibition | 4/4 | Inaction satisfies a prohibition |
| session `book_position` prohibition | 4/4 | Same |
| **`s5` faint notional** | **1/4** | **The strongest discriminator on the board** |
| `s7` `skipped_count` | 1/4 | Booking restraint / counting |
| `s1`,`s4`,`s5`,`s6` `get_confirmation_batch` | 2/4 | Procedure-following |

This matches the pre-board measurement: **OCR-level vision is saturated at this
tier** and the grounding checks mostly do not separate the field.

**One prediction was wrong, and interestingly so.** The pre-board probe scored the
faint notional 4/4 on a pointed single-shot question *and* 4/4 through the raw
two-stage pipeline. On the live board it fell to **1/4** — the single best
discriminator here. A value that is trivially readable when you ask for it
directly becomes hard when the model is nine steps into a desk workflow deciding
for itself what to extract. **A fixture probe measures the document; only a board
measures the task.**

## Caveats a reader must carry

- **Vision is not what ranks this board.** Grounding is 89/89 for the top two.
  The separation is procedural and volumetric. Do not read the ordering as a
  vision ranking.
- **EFF is on the legacy hyperbolic curve** (`par_tool_calls` unset), against a
  `designed_par` of 9 — the theoretical minimum, which no realistic run
  approaches. Every EFF here is therefore crushed, and **deepseek scores the
  HIGHEST EFF (39) while being by far the worst model**, purely for making only 9
  calls before failing. Do not compare these EFF/OVR values with calibrated
  boards.
- **No fully-correct trial exists yet (0 of 4)**, so par cannot be calibrated
  from this run. Keep it unset until a board produces some.
- **One trial per contestant.** No CON, and no claim about run-to-run stability.
- **deepseek-v4-flash-vision made 9 tool calls with 22 errors** and produced no
  artifact (SYN 0). That is a real post-fix result, but it is a failure to
  operate the desk, not a failure to see: its grounding is 50, i.e. it read half
  the graded values correctly while barely executing.

## Two harness defects this board found

Both invisible to the golden replay, which is why the live smoke is
non-negotiable.

1. **`StreamChunkTimeoutError` after 120s.** Step 1 parses six documents in one
   tool call, each costing two multimodal calls — 12+ vision calls synchronously
   inside a single tool body, so the outer agent stream emits nothing and
   langchain_openai's default fires on a connection that is idle rather than
   dead. Every `openai_chat` contestant would have died `invalid`. Fixed by
   `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=900` for the whole board.
2. **Upload paths were unaddressable.** Told the files were at
   `/artifacts/uploads/confirmations/`, gpt-5.6-luna tried that, then
   `/uploads/...`, then bare filenames — four reasonable attempts, all rejected
   with an error that never revealed the accepted form. Its arm scored **30.3
   with grounding 0/10**. After the fix the same model scored **84.8**: a
   54-point swing that was entirely harness.

**`--resume` does not undo a harness fix.** It re-runs only *non-scored* arms, so
luna's contaminated 30.3 was skipped and had to be deleted explicitly. An arm that
scored badly *because of a bug* is indistinguishable, to the resume logic, from
one that scored badly on merit.

## Next

- Run more trials to get CON and a fully-correct trial for par calibration.
- Consider whether the `get_confirmation_batch` `expected_tools` checks should
  stand: a model answering from context it already holds is arguably efficient,
  not wrong, and this is the same class as the "step-scoped check penalises
  reading the evidence one step early" defect already recorded in `CLAUDE.md`.
  They do discriminate (10/6/3), so they are not dead — but they measure
  procedure, not sight.
