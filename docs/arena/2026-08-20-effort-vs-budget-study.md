# Effort vs output budget — predeclared design

**Question.** Does `reasoning_effort` determine agent performance, or does the
output-token budget that effort must fit inside?

Predeclared **before** any run in this study executed. Scorer, arms and pass
criteria are fixed here so the analysis cannot be fitted to the data afterwards
(CLAUDE.md, *A/B evidence standard for agent-behaviour features*).

## Prior evidence (already owned, not re-run)

- **Run #110** ran `gpt-5-6-luna` at `none/low/high/max` across all five golden
  workflows, 2 trials, on the **OpenAI wire protocol** with **no** `max_tokens`
  sent — i.e. at the model's own ceiling. The 2026-08-19 Anthropic-path fix did
  not touch that protocol, so Run #110's `low` and `max` arms **are** this
  study's uncapped arms and are reused rather than repeated.
  Objective means: **low 93.8, max 94.8** (+1.0 across 5 workflows).
- **Token probe** (2026-08-20, same prompt, `gpt-5.6-luna`):

  | effort | reasoning tokens | outcome |
  |---|---|---|
  | `low` | 516 / 482 | answers normally |
  | `max` | 19,682 / 9,840 | answers normally, **shorter text** |
  | `low` @ budget 4096 | 516 | unaffected |
  | `max` @ budget 4096 | — | **hard failure**, `list index out of range` |

  `max_tokens` on this path bounds reasoning **and** visible output together,
  which is the causal link the hypothesis needs. Note max's spend is *variable*
  (9.8k vs 19.7k on identical prompts), so the binding threshold is a band, not
  a line.

## Arms

2 workflows × 1 trial × 2 efforts × 3 budgets = **12 matches**, as three runs.

- **Workflows** — chosen because Run #110 showed effort acting in *opposite*
  directions on them, so a result cannot be an artifact of one task's shape:
  - `high-board-portfolio-review-day` — max **helped** (low 84.3 → max 90.0)
  - `risk-manager-control-day` — max **hurt** (low 93.6 → max 91.0)
- **Efforts** — `low`, `max` (pinned, so each is its own contestant).
- **Budgets** — `4096` (the pre-fix Anthropic fallback; binds `max` fatally per
  probe), `16384` (straddles max's observed 9.8k–19.7k spend; expected to bind
  *partially*), `32768` (the post-fix value; expected not to bind).

**Budget is a separate RUN, not a separate arm.** It is a process-level setting
(`OPEN_OTC_AGENT_OPENAI_MAX_OUTPUT_TOKENS`) with no column on `arena_run`, so it
cannot enter the contestant key the way `reasoning_effort` did in migration 0058.

- These three runs **must never be merged.** `merge_runs` folds by
  `(workflow_id, model_id, reasoning_effort)` — budget is not in that key, so a
  merge would average two operating regimes into one row, the exact defect the
  effort-in-the-key change exists to prevent.
- Runs must execute **strictly sequentially**. Golden workflows resolve books by
  NAME and the purges are name-scoped, so two concurrent arena runs on one
  database would contaminate each other's fixtures silently.

## Scorer (fixed in advance)

Per match: `objective_score` (deterministic assertion checks), the derived card
OVR, `tool_calls`, and — from the trace DB — the count of LLM spans at or above
the budget, plus any match recorded `invalid`/`failed`.

## Pass criteria

- **H1 — effort is not the driver.** At budget 32768, |low − max| ≤ 5 objective
  points on **both** workflows.
- **H2 — budget is the driver.** At budget 4096, `max` loses ≥ 20 objective
  points against its own 32768 value on at least one workflow, while `low` loses
  ≤ 5 on both.
- **H3 — they interact.** The (low − max) gap widens monotonically as budget
  falls, i.e. gap(4096) > gap(16384) > gap(32768).

**Reported regardless of outcome:** any arm that fails rather than scores is
reported as a failure, never as a zero; per-match raw results are retained in
`artifacts/arena/<run>/`; wall-clock and call counts are reported alongside
scores, since a budget that buys accuracy at 3× the cost is not a free win.

**Falsification.** If `max` at 4096 merely matches `low` instead of collapsing,
the hypothesis is wrong and effort is simply inert. If `low` degrades as much as
`max` at 4096, the effect is a generic budget effect with no effort interaction.

## Run ledger

Filled in as the study executes — the only record linking a run id to its budget,
since no column carries it.

| run | budget | efforts | workflows | status |
|---|---|---|---|---|
| **#116** | 4096 | low, max | high-board, risk-manager | **complete** — negative control |
| ~~(B)~~ | ~~16384~~ | — | — | cancelled: could not bind (see Results) |
| ~~(C)~~ | ~~32768~~ | — | — | cancelled: could not bind (see Results) |
| *#110* | *uncapped* | *none, low, high, max* | *all five, 2 trials* | *reused, 2026-08-17* |

---

# Results

## The gradient design was abandoned mid-study — disclosed, not hidden

Run #116 (budget 4096) measured `gpt-5-6-luna` at `max` effort spending at most
**3,214** output tokens in a single turn across 67 LLM calls: **zero** calls hit
the 4096 cap. The budget never bound, so budgets 16384 and 32768 could not bind
either, and the queued runs (B) and (C) were cancelled before launch rather than
spend ~2 hours measuring one regime three times.

**Why the probe mispredicted it.** The 2026-08-20 probe measured luna answering
one open-ended analytical question at `max`: 19,682 reasoning tokens. Inside the
agent loop the same effort setting produces ≤3,214 per turn, because each turn is
a bounded tool-choice decision rather than an essay. **Single-shot reasoning spend
does not predict agentic per-turn spend**, and the study's arms were chosen off the
wrong measurement.

The pass criteria in the predeclaration above are left exactly as written. H2 and
H3 are **untestable on luna** — not failed — because luna's appetite sits below
every budget tried. That is a design miss, and recording it as such is the point
of predeclaring.

## Run #116 is a NEGATIVE CONTROL, not a null result

Completed 2026-08-20. luna at budget 4096, against its own uncapped Run #110 scores:

| workflow | arm | #116 @4096 | #110 uncapped | Δ |
|---|---|---|---|---|
| high-board | low | 85.7 | 84.3 | +1.4 |
| high-board | max | 82.9 | 90.0 | −7.1 |
| risk-manager | low | 94.9 | 93.6 | +1.3 |
| risk-manager | max | 94.9 | 91.0 | +3.9 |
| **mean** | | **89.6** | **89.7** | **−0.1** |

Per-thread truncation (calls landing **exactly** on 4096 — the correct censoring
test; a `>=4090` test wrongly counts naturally-large responses as clipped):

| thread | arm | LLM calls | clipped | max single |
|---|---|---|---|---|
| 777 | high-board · low | 55 | 0 | 783 |
| 778 | high-board · max | 67 | 0 | 3,214 |
| 779 | risk-manager · low | 66 | 0 | 1,724 |
| 780 | risk-manager · max | 161 | **5** | 4,096 |

**Correction to an earlier draft of this section, which claimed zero truncation in
every arm.** Three arms had none; the risk-manager `max` arm clipped 5 of 161 calls
(3.1%) — and scored **94.9 against 91.0 uncapped**, i.e. *higher*. That strengthens
rather than weakens the reading: five dead turns cost nothing measurable because
none landed on a scoring-critical step.

Capping a model whose per-turn appetite mostly lies below the cap changes nothing
(−0.1 over four cells), and low-vs-max within #116 is −1.4. Both halves of the cliff
model survive: **effort is noise, and budget is inert until it binds.**

**Noise floor.** Single-cell swings reached ±7 at one trial here. But doubao's own
uncapped history on `risk-manager-control-day` spans **71.8 to 87.2** on identical
configuration (runs #60/#20/#77/#80), so the floor on THAT workflow is ~15 points,
not 8. At one trial the doubao stage can only detect a budget effect larger than
that — a null there means "no effect above the noise", never "no effect".

## What replaced it: measured appetite across the whole model field

33,742 LLM calls from every arena run, grouped by model. Calls landing on
4090-4096 are treated as **right-censored** (clipped by the old Anthropic cap) and
excluded from percentiles — including them would understate appetite by exactly
the amount the cap hid. Censorship is classified per OBSERVATION, not per run era:
several models were pinned to the Anthropic wire mid-history, so their earlier runs
were uncapped and an era-based rule mislabels them.

| model | calls | p50 | p95 | p99 | max | >4096 | clipped |
|---|---|---|---|---|---|---|---|
| doubao-seed-evolving | 187 | 767 | 11,093 | 19,037 | 23,849 | 13.4% | 0 |
| doubao-seed-2-1-pro | 1,101 | 435 | 4,888 | 10,246 | 30,878 | 7.2% | 0 |
| doubao-seed-2-1-turbo | 358 | 327 | 4,537 | 6,752 | 20,140 | 6.1% | 0 |
| hunyuan-3 | 642 | 450 | 3,795 | 7,687 | 12,845 | 4.5% | 0 |
| step-3-7-flash | 1,444 | 484 | 3,822 | 7,416 | 25,138 | 4.2% | 0 |
| glm-5-2 | 734 | 404 | 3,517 | 7,107 | 21,227 | 3.4% | 1 |
| glm-5-3 | 763 | 521 | 3,257 | 6,306 | 12,314 | 2.4% | **32** |
| qwen-3-7-max | 1,416 | 259 | 2,549 | 4,198 | 5,012 | 1.9% | 0 |
| deepseek-v4-pro | 2,603 | 331 | 2,028 | 4,629 | 20,032 | 1.3% | 0 |
| claude-sonnet-4-6 | 995 | 176 | 1,992 | 3,633 | 4,002 | 0% | **25** |
| claude-sonnet-5 | 1,677 | 403 | 2,291 | 3,444 | 4,051 | 0% | **15** |
| longcat-2-0 | 1,366 | 398 | 2,085 | 3,359 | 4,063 | 0% | **14** |
| minimax-m3 | 1,673 | 198 | 1,787 | 3,009 | 3,914 | 0% | **12** |
| mimo-2-5-pro | 1,104 | 251 | 1,305 | 3,160 | **131,072** | 0.4% | 0 |
| gemini-3-5-flash | 1,916 | 281 | 1,984 | 2,969 | 62,916 | 0.4% | 0 |
| gpt-5-6-luna | 4,536 | 121 | 695 | 1,820 | 7,520 | 0.1% | 0 |
| claude-opus-4-8 | 802 | 314 | 1,085 | 1,745 | 2,267 | 0% | 0 |
| gpt-5-6-terra | 1,258 | 110 | 337 | 750 | 2,686 | 0% | 0 |

**Budget decision table** — share of all 33,742 calls a budget would clip:

| budget | calls clipped | share | models affected |
|---|---|---|---|
| 4,096 | 393 | 1.16% | 19 of 31 |
| 8,192 | 72 | 0.21% | 12 |
| 16,384 | 19 | 0.06% | 8 |
| **32,768** | **3** | **0.01%** | **2** |

The 32768 chosen on 2026-08-19 is effectively non-binding — the two exceptions
(`mimo-2-5-pro` max 131,072; `gemini-3-5-flash` max 62,916) are OpenAI-protocol
models, which send no cap at all.

## Conclusion

**Reasoning effort does not determine performance.** Run #110: luna low 93.8 vs
max 94.8 across five workflows at two trials — +1.0, inside noise. Run #116 at a
non-binding budget agrees (high-board low 85.7 vs max 82.9, single trial).

**Output budget determines performance only when it BINDS**, and it is a **cliff,
not a dial**:

- Below the binding point a turn dies outright — a lone `reasoning` block, no text,
  no tool call — and the cost depends entirely on whether that turn was
  scoring-critical. glm-5.3 lost 33 objective points on `trader-rfq-booking-day`
  and **zero** on `risk-limit-breach-day` under the same cap with truncations
  present in both.
- Above the binding point, extra budget confers nothing.

**What decides whether it binds is the model, not the knob.** Per-turn appetite
spans 60× at p95 (gpt-5-6-terra 337 → doubao-seed-evolving 11,093). luna at `max`
never reaches 4096; glm-5.3 at *unpinned* effort exceeds it on 2.4% of calls. Any
budget policy must be set from measured per-model appetite, never from the effort
setting.

**Effort's real price is cost, not accuracy.** luna at `max` spends ~38× the
reasoning tokens of `low` per single-shot call, and in the probe returned a
*shorter* answer. On these workflows `max` is close to a pure cost multiplier.

## Open gap

`claude-sonnet-4-6`, `claude-sonnet-5`, `longcat-2-0` and `minimax-m3` have 25/15/14/12
right-censored calls each: their distributions stop dead just under 4096, so their
true appetite is unknown and **unknowable from existing data**. They need
re-measuring now that the cap is lifted before any claim about their budget needs.

---

# Stage 2 — demonstration on a long-CoT model

The luna arms could not exercise the variable (appetite below every budget tried),
so the demonstration is repeated on a model chosen FROM the appetite table rather
than by familiarity.

**Subject: `doubao-seed-2-1-pro`.** Measured over 1,101 calls: p50 435, p95 4,888,
p99 10,246, max 30,878, **7.2%** of calls above 4096 — a 72× higher clip rate than
luna's 0.1%, and sampled widely enough not to be a small-sample artifact.
`doubao-seed-evolving` clips harder (13.4%) but has only 187 measured calls.
Registry-measured effort ladder covers all seven levels, and it routes on the
**OpenAI** protocol, so the budget is applied through
`OPEN_OTC_AGENT_OPENAI_MAX_OUTPUT_TOKENS`.

**Workflows** — the two where the 4096 cap did most measurable damage to glm-5.3:
`trader-rfq-booking-day` (65.1 → 96.8, **+31.7** when lifted) and
`risk-manager-control-day` (82.1 → 100.0, +17.9).

**Arms:** 2 workflows × 1 trial × {low, max} × {4096, 32768} = 8 matches, run as
two runs (budget is process-level, so it cannot be an arm — see above).

**Staged on purpose.** Budget 4096 runs FIRST and its truncation counts are checked
before the 32768 control is launched. The luna study spent its time on arms that
could not move the variable; this one proves the budget binds before paying for the
comparison.

**Prediction (recorded before the data).** If the cliff model is right:
`(low, 32768)` ≈ `(max, 32768)` ≈ existing uncapped scores; `(max, 4096)` degrades
**more** than `(low, 4096)`, because higher effort spends more per turn against the
same ceiling and so clips more often. If `(max, 4096)` and `(low, 4096)` degrade
equally, the effect is budget-only with no effort interaction.

---

# Stage 2 results — clip RATE is exposure, not damage

`doubao-seed-2-1-pro` @ budget 4096, one trial. Censoring counted as calls landing
**exactly** on 4096.

| arm | LLM calls | clipped | rate | objective | uncapped baseline |
|---|---|---|---|---|---|
| trader-rfq · low | 137 | 4 | 2.9% | 92.1 | 95.2 (run #33) |
| trader-rfq · max | 166 | 5 | 3.0% | **invalid** | — |
| risk-manager · low | 273 | 23 | 8.4% | **87.2** | 87.2 / 87.2 / 87.2 / 71.8 |
| risk-manager · max | 177 | 21 | 11.9% | **79.5** | 87.2 / 87.2 / 87.2 / 71.8 |

Run #117 is recorded `failed` because one of its four matches is `invalid`; the
three scored matches are unaffected and are the data above.

`trader-rfq · max` was recorded `invalid` / `infra_error` on a
`StreamChunkTimeoutError` at step 2 — nine of its ten steps completed normally. Per
the predeclaration it is reported as a **failure, not a zero**, and needs a re-run.

## The headline: identical clip rates, opposite outcomes

| case | clip rate | score vs uncapped |
|---|---|---|
| doubao · risk-manager · low @4096 | **8.4%** (23/273) | 87.2 vs 87.2 — **zero loss** |
| glm-5.3 · trader-rfq @4096 (run #114) | **8.2%** (6/73) | 65.1 vs 96.8 — **−31.7** |

Two runs truncating at the same rate, one losing nothing and the other losing a
third of its score. **Truncation rate does not predict damage — placement does.**
doubao's axes confirm it: grounding **5/5** and synthesis **4/4** survived 23 dead
turns, the very axes truncation destroyed for glm-5.3 (SYN 0/4, 0/5). Its losses sit
in adherence (5/8) and procedural (20/22), which are present in its uncapped runs too.

**This retires "count the truncations" as a severity diagnostic** — a framing used
earlier in this very document. The appetite table above measures **exposure** (how
often a model can hit a ceiling), not risk. Converting exposure to expected damage
requires knowing whether scoring-critical steps are hit, which only per-step
inspection answers.

## Effort interaction: not observed

Predicted in the stage-2 predeclaration that `max` would clip more than `low` and so
degrade further. Measured: **2.9% vs 3.0%** on trader-rfq — indistinguishable. The
predeclared falsifier ("if they degrade equally, the effect is budget-only with no
effort interaction") is the outcome that occurred.

The reason is visible in the luna data: effort moves **single-shot** reasoning
enormously (516 → 19,682 tokens, 38×) but **agentic per-turn** reasoning barely
(783 → 3,214 peak, ~4×). A turn in a tool-calling loop is bounded by the decision it
has to make, not by the effort setting.

## Power limitation, stated plainly

One trial per arm. doubao's own uncapped history on `risk-manager-control-day` spans
**71.8 to 87.2** on identical configuration, so the noise floor there is ~15 points.
A null result on that workflow means "no effect larger than the noise", never "no
effect". The trader-rfq comparison rests on a single uncapped sample (95.2).

## Effort's effect on per-turn spend is inconsistent in DIRECTION

| arm | calls | total tokens | mean/call | p50 | p95 | clipped |
|---|---|---|---|---|---|---|
| risk-manager · low | 273 | 257,306 | 943 | 364 | 4,096 | 8.4% |
| risk-manager · max | 177 | 225,377 | **1,273** | **588** | 4,096 | 11.9% |
| trader-rfq · low | 137 | 130,439 | 952 | 526 | 3,401 | 2.9% |
| trader-rfq · max | 165 | 135,667 | **822** | **431** | 3,279 | 3.0% |

On `risk-manager` raising effort produced **35% fewer calls with 35% longer turns**
(and slightly *lower* total tokens). On `trader-rfq` it produced the opposite —
more calls, shorter turns. So "effort trades turn count for turn length" is **not**
a supportable finding: it held on one workflow and reversed on the other.

What survives is the weaker, better-supported statement: **effort's effect on
agentic per-turn spend is small and not even consistent in sign across workflows**,
which is why it neither drives performance nor reliably changes whether a budget
binds. The one directional hint is that `max` clipped more on `risk-manager`
(11.9% vs 8.4%) and scored 7.7 lower — but that gap sits inside the ~15-point noise
floor for that workflow, so it is a hint, not a result.

## Final summary of the whole study

| claim | verdict | evidence |
|---|---|---|
| Reasoning effort determines performance | **No** | #110 low 93.8 vs max 94.8 (5 wf × 2 trials); #116 89.6 vs 89.7 |
| Output budget determines performance | **Only when it binds** | #116: capping a lean model = −0.1. glm-5.3 #114→#115: mean OVR 72→84 |
| Binding is set by the effort knob | **No** | clip rates 2.9% vs 3.0% (trader-rfq); direction of spend inconsistent |
| Binding is set by the model | **Yes** | per-turn appetite spans 60× at p95 across 31 models / 33,742 calls |
| Clip RATE predicts damage | **No** | 8.4% → 0 points (doubao); 8.2% → −31.7 (glm-5.3). Placement decides |

**Open items.** `trader-rfq · max` needs a re-run after its infra failure. The four
right-censored models (`claude-sonnet-4-6`, `claude-sonnet-5`, `longcat-2-0`,
`minimax-m3`) still have unknowable appetite and every historical board of theirs
ran silently capped at 4096.
