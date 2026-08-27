# Does the wire protocol affect an LLM's agentic performance?

**Answer: yes for one model, catastrophically — and no for the rest.** The effect is an
interaction, not a main effect. There is no protocol that is simply "better".

Predeclared design: [`2026-08-24-protocol-effect-study-design.md`](2026-08-24-protocol-effect-study-design.md).
Arms, scorer and pass criteria were fixed before arm B ran; nothing below was chosen
after seeing the numbers.

| | |
|---|---|
| Workflow | `risk-manager-control-day` (39 checks) |
| Arms | **A** = production (chat-completions ×5, anthropic ×1) — run **#122**<br>**B** = OpenAI Responses API ×6 — run **#123** |
| Effort | per-model measured ceiling, identical across arms |
| Trials | 2 per cell; output budget unpinned; recursion limit 300 |
| Dates | 2026-08-24, arms ~10 h apart |

## Result

| model | effort | arm A | arm B | Δ | verdict |
|---|---|---|---|---|---|
| **`deepseek-v4-flash`** | max | **7.7** | **91.1** | **+83.4** | **REAL** (mechanism present) |
| `minimax-m3` | max | 78.2 | 93.6 | +15.4 | unexplained |
| `mimo-2-5` | high | 82.1 | 88.5 | +6.4 | no detected effect |
| `gemini-3-7-flash` | xhigh | 92.3 | 92.3 | 0.0 | no detected effect |
| `gpt-5-6-luna` | max | 97.5 | 93.6 | −3.9 | no detected effect |
| `hunyuan-3` | xhigh | 93.6 | 62.8 | **−30.8** | **unexplained regression** |

Mean movement across the six is +11.8, but reporting that number would be
meaningless: it is one enormous fix averaged with one unexplained regression and
three nulls. Per the predeclared commitment, no pooled figure is published.

## CORRECTION (2026-08-25): the deepseek effect is a gateway bug, not a protocol property

The A/B measured a real, reproducible difference between routes — but follow-up
isolation showed the *cause* is not the protocol. Two claims made in the first draft
of this report were wrong and are retracted here:

1. ~~"an effort × protocol interaction"~~ — **effort is irrelevant.**
2. ~~"the Responses API's separate `reasoning` output item is the mechanism"~~ —
   the Responses path is clean **incidentally**, because it does not use the
   streaming-delta merge at all.

**Root cause, proven on the wire.** In streaming chat-completions a tool call arrives
as deltas: `id` and `name` in the first chunk, `arguments` fragments after. ZenMux's
translation for `deepseek/deepseek-v4-flash` re-sends `id: ""` and `name: ""` in every
continuation delta instead of omitting them. langchain's accumulator treats a
present-but-empty string as an update and overwrites the real identifiers from the
first delta, while `arguments` fragments concatenate correctly — which is exactly the
observed signature: **intact args, hollow identifiers.**

| endpoint (streaming, one `task` call) | first delta | continuation deltas |
|---|---|---|
| ZenMux · `deepseek/deepseek-v4-flash` | real id + name | **`id: ""`, `name: ""`** (9 of 10) |
| ZenMux · `openai/gpt-5.6-luna` | real id + name | `null` (62 of 63) |
| ZenMux · `tencent/hy3` | real id + name | `null` (7 of 8) |
| ZenMux · `xiaomi/mimo-v2.5` | real id + name | `null` (13 of 14) |
| ZenMux · `google/gemini-3.7-flash` | real id + name | *single delta, no continuation* |
| **api.deepseek.com** · `deepseek-v4-flash` | real id + name | `null` (51 of 52) |

So it is neither "the model" nor "ZenMux" — it is **ZenMux's DeepSeek upstream
translation specifically**, and only on the streaming chat-completions path.

**Effort is not a factor.** The empty strings appear at `none`, `low`, `medium`,
`high` and `max` alike. Effort only *looked* causal because we began pinning `max`
in the same week the gateway regressed. Confirmed by **run #125** (unpinned, ZenMux
chat-completions, 2026-08-25): **7.7 with 77 malformed tool calls and 0 executed** —
the identical failure at the vendor default.

**It is a recent regression, and not on our side.** Run #112 scored **93.6** on this
exact route on 2026-08-17; every ZenMux chat-completions run from 2026-08-21 onward
scores 7.7. No dependency moved in that window — `langchain-openai` 1.2.1 was
installed 2026-06-18, `langchain-core` 1.4.9 on 2026-08-04, and `uv.lock` has not
changed since 2026-08-04.

| run | date | route | effort | objective | malformed |
|---|---|---|---|---|---|
| #20 | 2026-07-08 | zenmux chat-completions | unpinned | 87.2 | not measured |
| #112 | 2026-08-17 | zenmux chat-completions | unpinned | **93.6** | not measured |
| #121 | 2026-08-21 | zenmux chat-completions | max | 7.7 | not measured |
| #122 | 2026-08-24 | zenmux chat-completions | max | 7.7 | 95 (from trace) |
| #123 | 2026-08-24 | zenmux **responses** | max | 91.1 | 0 (from trace) |
| #124 | 2026-08-25 | **api.deepseek.com** | max | **92.3** | **0** (instrumented) |
| #125 | 2026-08-25 | zenmux chat-completions | **unpinned** | **7.7** | **77** (instrumented) |

**What this does not change:** the per-model A/B numbers below stand as measured, and
the practical guidance is unchanged — route `deepseek-v4-flash` over the Responses API
or the direct DeepSeek channel. What changes is the *explanation*: this is one
vendor's streaming bug that the Responses path happens to sidestep, not evidence that
Responses is architecturally safer. The `hunyuan-3` and `minimax-m3` movements remain
unexplained and are untouched by this correction.

## The one real effect

`deepseek-v4-flash` at `max` effort on chat-completions emitted **95 tool calls, 100%
of them with an empty `id` and an empty `name`**. deepagents' `task()` rejects such a
call, so the harness executed **zero** of them; the agent retried, looped to the
recursion limit, and produced nine empty steps. On the Responses API the same model,
same effort, same workflow emitted **132 tool calls with zero malformed**.

Every other model was clean on both protocols — 1,283 tool calls, zero malformed —
so this is not "the gateway is broken".

**It is invisible to every gate the harness has.** The calls return HTTP 200. No
exception is raised, so no step records an error, so `_is_infra_blank` cannot see it
and the match is recorded `scored`, not `invalid`. Nothing truncates, so the
truncation flag reads a clean zero. The only artefacts are a blank transcript and a
score of 7.7 — the prohibition floor, which inaction earns by satisfying every
`tool_not_called` check. A reader of the board would conclude that the most-used
model on OpenRouter cannot execute a workflow.

**Scope: this is an effort × protocol interaction, not protocol alone.** The same
model on chat-completions at *unpinned* effort scores 75.9–93.6 on this workflow
(n=9 historical runs; n=30 across all workflows, mean 81.2). We did not test lower
efforts on chat-completions in this study, so the honest statement is: *at `max`
effort, chat-completions corrupts this model's tool calls, and the Responses API does
not.* Where between `medium` and `max` it breaks is unmeasured.

The mechanism is consistent with the structural difference: chat-completions carries
reasoning and tool calls in one content stream, while the Responses API emits
`reasoning` as a separate `output` item. A model spending heavily on reasoning is
exactly the case where the shared stream would be expected to interfere.

## The unexplained regression — read this before adopting Responses

`hunyuan-3` fell 93.6 → 62.8 with **no mechanism signal**: zero malformed calls in
either arm. Per-trial, arm B split **35.9 / 89.7**, so one trial was normal and one
collapsed. In the collapsed trial the model worked correctly through step 3, then
from step 4 onward *narrated* each action without emitting a call — "Recording the
answer.", "Dispatching the historical delta-hedge backtest… via the risk_manager." —
nine steps, zero tool calls, zero errors.

That is **prose-instead-of-persist**, an already-documented failure mode (glm-5.3 in
the artifact-budget study, deepseek-v4-pro in run #104), not a new defect. Whether
the Responses path makes it *more likely* is precisely what n=2 cannot answer. One
line from that trial is worth chasing: *"The `start_async_agent` dispatch path isn't
available in this session"* — if the Responses path presents the tool list
differently, that would be a real cause, but the other hunyuan trial used 47 tool
calls happily, so the tools were plainly available.

`minimax-m3`'s +15.4 is likewise unexplained by mechanism, but the per-trial spread
is suggestive: **87.2 / 69.2 on anthropic versus 92.3 / 94.9 on Responses.** It was
both better and far more consistent. minimax carries `protocol: anthropic` purely as
a workaround for this same empty-tool-call-id defect on chat-completions; if this
holds up, Responses would let that workaround be retired rather than replaced.

## What we are NOT claiming

- **Not** that Responses is better in general. Three models showed no effect, one
  regressed 30 points, and the mean is an artefact of averaging incommensurable cells.
- **Not** that `max` effort is unsafe. Five of six models scored 78.2–97.5 at their
  ceilings in arm A. The ceiling smoke's purpose was to find collapses, and it found
  exactly one.
- **Not** a capability claim about `deepseek-v4-flash`. Its 7.7 measures our routing.

## Threats to validity

- **n=2 per cell.** Two of the three non-null results are single-trial swings.
  `hunyuan-3` and `minimax-m3` both need replication before either is acted on.
- **Arm B runs new code** — the payload normalizer below. A normalizer bug would
  masquerade as a protocol effect. Mitigating evidence: five of six models were
  unaffected, and the one large movement has an independent mechanism instrument.
- **Arm B depends on a third-party bug workaround.** ZenMux silently drops the entire
  `tools` array when an input item is exactly `{"type":"message","content":"<string>"}`
  — the shape `langchain-openai` emits (measured 0/4 tool calls vs 4/4). The
  normalizer promotes the string to a content-parts list. If ZenMux fixes this, arm B
  describes a gateway that no longer exists.
- **Arms ran ~10 h apart** on a link that dropped twice during the study. Three arm-A
  cells were swept to `invalid` by `APIConnectionError` and re-run via `--resume 122`;
  those are environmental casualties, correctly quarantined by the infra gate, not
  results.
- **Arm provenance is durable**, at least: the trace stores the chat model's class per
  span — arm A reads `ChatOpenAI`/`ChatAnthropic`, arm B `_ZenmuxResponsesChat`.

## Recommended next steps

1. **Do not run `deepseek-v4-flash` at `max` on chat-completions.** Either route it
   over Responses or leave its effort unpinned.
2. **Add a malformed-tool-call detector to the harness.** This defect currently
   reaches a published board as a legitimate score. It is cheap to instrument — the
   count is already derivable from the trace — and it belongs beside the truncation
   flag: a visible caveat, never an automatic invalidation.
3. **Replicate `hunyuan-3` and `minimax-m3` at n≥5** before adopting or rejecting
   Responses for either.
4. **If Responses is adopted for any model, protocol must join the contestant key**
   alongside effort and output budget. Two protocols are two regimes — that is what
   this study measured — and `merge_runs` would otherwise fold them into one row.
