# Does the wire protocol affect an LLM's agentic performance? — predeclared design

**Question.** The desk reaches one gateway (ZenMux) through three different wire
protocols — OpenAI chat-completions, Anthropic messages, and (candidate) the OpenAI
Responses API. Which protocol a model uses has always been decided *ad hoc*, as a
workaround for a defect, never as a measured choice. Does the protocol itself move
agentic performance, and does it move it differently per model?

Predeclared **before** any arm-B run executed and before arm A's results were read.
Arms, scorer and pass criteria are fixed here so the analysis cannot be fitted
afterwards (CLAUDE.md, *A/B evidence standard for agent-behaviour features*).

## Hypotheses

**H1.** Protocol materially affects agentic performance for at least one model, via
**tool-call integrity on reasoning-heavy turns**. Chat-completions carries reasoning
and tool calls in one content stream; the Responses API emits `reasoning` as a
separate `output` item. If the failure mechanism is serialization interference, it
should appear on chat-completions at high effort and vanish on Responses.

**H0.** Protocol is neutral once the output budget is equalised. The observed
failures are model-specific and reproduce on any protocol.

H1 predicts an *interaction*, not a main effect: the best protocol should differ by
model. A finding that one protocol is uniformly better would be evidence against the
mechanism as stated.

## Prior evidence (already owned, not re-run)

- **glm-5.2, runs 51/91.** On chat-completions the gateway returned tool calls with
  an **empty-string id**; deepagents' `task()` guards on it, so every persona
  delegation raised and the objective collapsed to **12.0**. Fixed by pinning
  `protocol: anthropic` (`5db78b3`, 2026-07-29).
- **minimax-m3** carries the same pin for the same class: on chat-completions its
  Anthropic-format tool calls are left unparsed and leak into text as `<invoke …>`
  markup.
- **Historical protocol comparisons are confounded by budget.** Until 2026-08-21 the
  Anthropic path silently capped output at 4096
  (`_FALLBACK_MAX_OUTPUT_TOKENS`), worth **+11.6 OVR** for glm-5.3 alone
  (#114→#115). This study runs entirely post-fix, which is why it can be run at all.
- **Run #121, 2026-08-21 — the trigger.** `deepseek-v4-flash` at effort `max` on
  chat-completions returned tool calls with **empty `id` *and* empty `name`** across
  9 steps and **both** trials. Result: 0 tool calls, 0 errors, blank transcript,
  objective **7.7** (the prohibition floor). Same model on the same workflow,
  unpinned effort: 93.6 / 89.7 / 87.2 ×4 / 79.5 / 76.9 / 75.9. Across all workflows
  unpinned: n=30, mean **81.2**, min 41.9 — never near the floor.
  Because 0 errors are raised, `_is_infra_blank` cannot see it and the match is
  recorded `scored`, not `invalid`.
- **The failure is load-dependent.** A single-shot probe (one tool, short prompt) is
  clean **4/4** at `max` on chat-completions. The failing call was a `task()`
  delegation at ~15k prompt tokens. No cheap probe convicts — only a real match does.
  (Same lesson recorded for longcat-2.0.)
- **Responses API feasibility, measured 2026-08-21.** ZenMux implements
  `POST /api/v1/responses`. All six contestants return a well-formed `function_call`
  with a real `call_id` and `name` at their ceiling efforts, **including minimax-m3**,
  which today needs the Anthropic pin.
- **A ZenMux bug blocks the naive migration.** The gateway silently drops the entire
  `tools` array when an input item is exactly `{"type":"message","content":"<string>"}`
  — the shape `langchain-openai` emits. Measured **0/4** tool calls vs **4/4** on
  chat-completions; the model answers in prose with no error. Every other shape
  works, including `type:"message"` with a content-parts list. A payload normalizer
  that promotes string content to `input_text`/`output_text` parts restores **4/4**.

## Arms

Held constant across all arms: workflow `risk-manager-control-day` (39 checks, the
most discriminating, and the workflow the run #121 failure occurred on, so it doubles
as a reproduction control); per-model reasoning effort at its **measured ceiling**
(`deepseek-v4-flash` max, `hunyuan-3` xhigh, `mimo-2-5` high, `gpt-5-6-luna` max,
`minimax-m3` max, `gemini-3-7-flash` xhigh); trials 2; output budget **unpinned**;
`OPEN_OTC_AGENT_RECURSION_LIMIT=300`; `quantark==0.3.0`; same DB and fixtures.

| Arm | Protocol | Models | Status |
|---|---|---|---|
| **A — production** | chat-completions for five; **anthropic** for `minimax-m3` | all 6 | run **#122** — complete, 6/6 scored |
| **B — responses** | Responses API + payload normalizer | all 6 | run **#123** — launched 2026-08-24 12:09 UTC |
| **C — exploratory** | anthropic for the five not currently pinned | ≤5 | only if routable; reported separately and never pooled with A/B |

Arm B is selected with `OPEN_OTC_ZENMUX_FORCE_PROTOCOL=responses` at the launcher,
not by editing `config/agent_channels.yaml`, so the shipped desk config is identical
under both arms and cannot drift between them. **The arm is self-evidencing in
durable storage**: the trace records the chat model's class per span, so arm A's LLM
spans read `ChatOpenAI` / `ChatAnthropic` and arm B's read `_ZenmuxResponsesChat`.
The override is *not* persisted on the run, so **any `--resume` of arm B must
re-supply the env var**, exactly as it must re-supply
`OPEN_OTC_AGENT_RECURSION_LIMIT=300`. A resume that omits it would silently finish
arm B on arm A's protocol — the same class of hole migration 0059 closed for the
output budget, and the reason protocol should graduate to the contestant key if the
effect proves real.

### Arm A result (recorded before arm B launched)

| model | effort | protocol | objective | malformed tool calls |
|---|---|---|---|---|
| `gpt-5-6-luna` | max | chat-completions | 97.5 | 0 |
| `hunyuan-3` | xhigh | chat-completions | 93.6 | 0 |
| `gemini-3-7-flash` | xhigh | chat-completions | 92.3 | 0 |
| `mimo-2-5` | high | chat-completions | 82.1 | 0 |
| `minimax-m3` | max | anthropic | 78.2 | 0 |
| `deepseek-v4-flash` | max | chat-completions | **7.7** | **95 (50.0%)** |

Five of six run cleanly at their ceiling efforts, so `max` is not broadly unsafe —
the failure is model-specific. All 95 malformed tool calls in the entire arm belong
to `deepseek-v4-flash`, at **exactly 50.0% of its emissions on both trials**
(74→37 and 116→58); the other five emitted 722 tool calls with zero malformed. The
model emitted 190 tool calls across two trials and the harness executed **zero**.

Three arms (`gpt-5-6-luna`, `minimax-m3`, `gemini-3-7-flash`) were first swept to
`invalid`/`infra_blank` by an `APIConnectionError` network outage and re-run via
`--resume 122`. Those are environmental casualties, not results; the `invalid`
classification is the infra gate working as designed.

Arm A is the *shipping* configuration, per the standard's requirement that the old
production path is arm A. Arm C is exploratory because it may not be routable at all;
it will not be used to support any headline claim.

## Scorer (fixed in advance)

**Primary:** the existing deterministic golden-workflow objective score (39 checks)
and its four axes. **No jury** — `OPEN_OTC_ARENA_JURY` stays off, matching every
board since run #11.

**Secondary, all deterministic, per trial:**

- `tool_calls` count and `skills_routed` (from `counts_detail`)
- **malformed tool calls** — tool calls whose `id` or `name` is an empty string,
  counted from the trace `outputs` blob. This is the mechanism instrument.
- the `truncation` block (`calls` / `steps` / `severed_tool_calls`)
- completion tokens and wall-clock per match

No LLM judge is used anywhere in this study.

## Pass criteria (predeclared)

1. A protocol effect is declared **REAL** for a model only when **both** hold:
   |Δ mean objective| **≥ 10 points** between arms, **and** a mechanism signal is
   present in the worse arm (malformed tool calls > 0, or `tool_calls == 0`).
2. Δ ≥ 10 with **no** mechanism signal is reported as **unexplained**, not as a
   protocol effect.
3. Δ < 10 is reported as **no detected effect**, explicitly acknowledging that n=2
   cannot resolve small differences.
4. A blank transcript with 0 tool calls, 0 errors **and** malformed-id evidence is
   classified a **harness artifact**, not a capability result, and is excluded from
   any capability claim about that model.
5. Results are reported **per model**. No single pooled "protocol X wins" number will
   be published — the hypothesis is explicitly an interaction.

## Reporting commitments

- Per-trial transcripts retained for every cell (`transcript.trial<N>.json`).
- Regressions and costs reported alongside wins: latency, completion tokens, and any
  cell where an arm could not run at all.
- Arm B carries a **workaround for a third-party gateway bug**. The result is
  timestamped and explicitly conditional on ZenMux's current behaviour; if they fix
  or change the `{type:"message", content:"str"}` handling, arm B's numbers describe
  a gateway that no longer exists.
- **Not comparable to boards #8–#120**: effort is pinned here, and arm B is a
  different API.
- If arm B requires a truncation-detector change (Responses reports
  `status='incomplete'` + `incomplete_details.reason=='max_output_tokens'`, not
  `finish_reason='length'`), that change ships **before** arm B runs, or arm B's
  truncation column is reported as `null` (never as zero).

## Threats to validity

- **n=2 per cell.** A 10-point threshold may sit inside trial-to-trial variance for a
  high-variance model; requiring a mechanism signal is the mitigation.
- **New code in arm B.** A bug in the payload normalizer would masquerade as a
  protocol effect. Mitigation: any model clean in arm A but broken in arm B is
  treated as suspected arm-B implementation failure until the trace shows otherwise.
- **Model drift under a stable id** is documented for `deepseek-v4-pro` and
  `longcat-2.0`. Arms must run close together in time; the gap is reported.
- **ZenMux routing opacity.** The same model id may be served by different upstream
  providers per request; we cannot observe this and do not control it.
