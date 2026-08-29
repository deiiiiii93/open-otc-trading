# Run #1 — confirmation-desk-day, the first vision board

**Date:** 2026-08-29
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
| 1 | **deepseek-v4-flash-vision** | **100.0** | **89** | 99 | 99 | 99 | 99 | 39 | **23** | 0 |
| 2 | glm-5.3-flash | 97.0 | 84 | 89 | 99 | 99 | 99 | 24 | 36 | 0 |
| 3 | gemini-3.7-flash | 81.8 | 75 | 79 | 99 | 99 | 59 | 14 | 58 | 0 |
| 4 | gpt-5.6-luna | 84.8 | 72 | 59 | 99 | 99 | 89 | 14 | 51 | 0 |

`CON` is null for every row — one trial each, so consistency is not measured.
**Zero errors across the whole board**, which is the point: the first pass of
this board recorded 16 and 22 errors on two contestants, all of them harness.

> **luna ranks below gemini on a higher objective score.** OVR is not the
> objective percentage: grounding carries 0.32 of it, and luna's GRD (59) is two
> checks worse than gemini's (79). That is the ranking behaving as designed —
> grounding is the axis the board exists to measure.

## The question this board was built to answer

**GLM 5.3 Flash — z.ai's first multimodal model — reads these documents as well
as anything in the field.** 97.0 objective; its single miss across 33 checks was
the faint notional. It read the image-only scan, the mixed text/scan document,
the struck-through-and-amended strike and the ticked checkbox; it reported the
absent Initial Price rather than substituting the strike; it booked only what
validated and wrote a grounded summary — in 36 tool calls with zero errors.

Against `gemini-3.7-flash` specifically, GLM wins on grounding (89 vs 79),
procedure (99 vs 59) and volume (36 calls vs 58).

It does not top the board, though. **`deepseek-v4-flash-vision` swept all 33
checks in 23 calls** — the only perfect score, and the leanest run by a wide
margin.

## What actually discriminated — and what did not

Per-check tally across the field. **25 of 33 checks are DEAD** (4/4 or 0/4):

| Check | Rate | Reading |
|---|---|---|
| **`s5` faint notional 636,000** | **1/4** | **The strongest discriminator on the board** |
| `s1` `get_confirmation_batch` | 2/4 | Procedure, not sight |
| `s7` `skipped_count` | 2/4 | Booking restraint / counting |
| `s2` conf-08 strike + initial_price | 3/4 each | The mixed text-and-scan document |
| `s2`,`s4`,`s6` `get_confirmation_batch` | 3/4 each | Procedure, not sight |
| conf-01/04/09/10 terms, the absent-term trap, booking, the artifact, both prohibitions | 4/4 | Saturated |

This confirms the pre-board measurement, and more strongly than the first pass
suggested: **OCR-level vision is saturated at this tier.** Every contestant read
the scanned call, the amended strike, the ticked barrier checkbox and the
down-and-out type; every one declined to invent the missing Initial Price; every
one produced a grounded artifact.

**A contaminated arm manufactures false discrimination.** The first pass of this
board scored only 8 checks dead — because `deepseek-v4-flash-vision` was failing
everything for infra reasons, which made 17 saturated checks look like they were
separating the field. Repairing one arm moved the dead count from 8 to 25.
**Compute the tally only on a board where every arm is healthy**, or it measures
the outage.

**One prediction was wrong, and interestingly so.** The pre-board probe scored the
faint notional 4/4 on a pointed single-shot question *and* 4/4 through the raw
two-stage pipeline. On the live board it is 1/4 — the single best discriminator
here. A value that is trivially readable when you ask for it directly becomes
hard when the model is nine steps into a desk workflow deciding for itself what
to extract. **A fixture probe measures the document; only a board measures the
task.**

## Caveats a reader must carry

- **Vision is not what ranks this board.** Three of four contestants score GRD
  79–99, and 25 of 33 checks are saturated. The separation is procedural and
  volumetric. Do not read the ordering as a vision ranking.
- **EFF is on the legacy hyperbolic curve** (`par_tool_calls` unset), against a
  `designed_par` of 9 — the theoretical minimum, which no realistic run
  approaches. Every EFF here is therefore crushed (14–39 on a 0–99 scale), and
  EFF is 0.16 of OVR. Do not compare these EFF/OVR values with calibrated boards.
- **One trial per contestant.** No CON, and no claim about run-to-run stability.
  `gemini-3.7-flash` scored 84.8 then 81.8 on two clean runs of the same arm, so
  ±3 points of single-trial noise is the observed floor — treat rank 3 vs 4 as a
  tie.
- **Par is now calibratable, but not yet calibrated.** deepseek's perfect trial
  took 23 counted calls (the metric excludes `task`/`read_file`/`write_todos`).
  That is one data point; the flagship's par came from a distribution. Run more
  trials before setting `par_tool_calls`.

## Three harness defects this board found

All invisible to the golden replay, which is why the live smoke is
non-negotiable. Each cost a real contestant real points.

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

3. **`read_file` on a PDF poisons the conversation permanently.** deepagents
   returns a binary read as a media content block
   (`{"type": "file", "base64": …, "mime_type": …}`). `langchain_openai`
   translates that correctly into the documented OpenAI wire shape — verified
   against the exact `ToolMessage` recorded in the trace — so **the client is not
   at fault**. The gateways are. Probed with a real confirmation PDF:

   | contestant | PDF in a **user** message | PDF in a **tool** message |
   |---|---|---|
   | glm-5.3-flash | 200 | **200** |
   | gemini-3.7-flash | 200 | 400 `contents[N].parts[0].data: required oneof field 'data'` |
   | gpt-5.6-luna | 200 | 400 `Missing required parameter: 'input[N].output[0].text'` |
   | deepseek-v4-flash-vision | 400 | 400 `file must have a file_id or file_data` |

   Three of four routes reject the only shape `read_file` can produce, each in
   its own dialect. Fixed by `BinaryReadGuardMiddleware`, registered in all three
   agent stacks.

### Why the third one is the worst of the three

**It is unrecoverable.** The rejected message stays in the history, so every
later turn re-sends it and draws the same 400. `deepseek-v4-flash-vision` read
one PDF at step 3 and then made **zero tool calls for steps 4–8**.

**It is invisible to every gate we have.** Nothing truncates, so the truncation
flag reads a clean zero. The `read_file` call itself is `status=success`, so
`_is_infra_blank` — which corroborates blankness with step *errors* — sees a
healthy step and the match is recorded `scored`. Only the provider span carries
the 400. **A model that stops calling tools has not necessarily given up; check
whether it was still being asked.**

**Blast radius is set by which history was poisoned.** `gemini-3.7-flash` hit the
identical defect and survived, because its reads happened inside `task()`
subagents whose checkpoint namespaces are discarded. Counting error spans by
chain name: gemini recorded `trader` 3, `general-purpose` 1, and **zero**
`otc_desk_orchestrator`; deepseek recorded **six** `otc_desk_orchestrator`,
which is terminal. The span name tells you whether a run is wounded or dead.

**And it inverted the board.** Pre-fix, `deepseek-v4-flash-vision` scored 36.4
and placed last, and the first draft of this report read that as "a failure to
operate the desk, not a failure to see." Post-fix it scores **100.0** and places
**first**. The worst-looking contestant was the best one.

**The harness created the hazard.** The workflow names
`/artifacts/uploads/confirmations/`, and `read_file` is available and reads it.
Two of four contestants took that reasonable path and were punished; two never
tried. **A behavioural spread caused by a harness hazard is not a capability
signal** — which is why the guard is uniform rather than per-route: letting the
one tolerant gateway through would hand glm-5.3-flash an advantage conferred by
its gateway rather than by its ability.

**The tell was in the log the whole time.** Every poisoned turn emitted
`UserWarning: OpenAI may require a filename for file uploads … Using placeholder
filename 'LC_AUTOGENERATED'` — from langchain's block translator, at the exact
moment a PDF entered the history. It scrolled past unread during the run.

**`--resume` does not undo a harness fix.** It re-runs only *non-scored* arms,
and all three contaminated rows here were recorded `scored`. Each had to be
deleted explicitly. An arm that scored badly *because of a bug* is
indistinguishable, to the resume logic, from one that scored badly on merit.

## Next

- Run more trials for CON, and to build a distribution behind a calibrated
  `par_tool_calls` (deepseek's clean 23 is the first data point).
- Consider whether the `get_confirmation_batch` `expected_tools` checks should
  stand. They are 4 of the 8 surviving discriminators, but they measure
  procedure, not sight: a model answering from context it already holds is
  arguably efficient rather than wrong. Same class as the "step-scoped check
  penalises reading the evidence one step early" defect recorded in `CLAUDE.md`.
- With 25 of 33 checks saturated, the next iteration of this workflow needs
  harder traps if it is to keep discriminating — the field has outgrown
  OCR-level difficulty.
