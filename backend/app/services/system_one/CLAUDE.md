# System One (TypeSafe Jev) — agent guidance

Part of [Open OTC Trading](../../../../CLAUDE.md). Spec:
`docs/superpowers/specs/2026-09-21-jev-system-one-design.md`.

Jev is a **non-generative** classifier: `state` + typed questions in, calibrated
probabilities out (`noul` yes/no, `choice` one-of-N, `score` ordered levels). It
cannot generate text, call tools or see images — never a persona model, never an
arena contestant, never a source of numbers.

## Reaching it

- `POST https://zenmux.ai/api/v1/systemone`, `Bearer $ZENMUX_API_KEY`,
  `"model": "typesafe/jev-1.13"`. **Absent from `/v1/models`**, and it 404s on
  chat/completions, responses and messages. To tell "exists" from "doesn't",
  compare error TYPES against a fake-slug control: a fake slug is
  `invalid_model`, the real one on a wrong endpoint is `model_not_supported`.
- A malformed body returns an opaque `400 invalid_params "Server encountered an
  unexpected error"` — that is why `validate_questions` exists.
- It is NOT a channel-registry model (D7): listing it would make it selectable
  as an agent model, and broken.

## Measured, not claimed

- Latency: **1.03 / 1.33 / 2.30 s** min/median/max over 61 calls (vendor: 70–500 ms).
  ~1 s of that is the ZenMux round-trip.
- **Not deterministic**: identical requests drift up to 0.10 (typically ≤ 0.04),
  quantised to 0.01. Anything that must be replayable (the guard's interrupt set)
  reads a COMMITTED verdict, never a fresh answer.
- **Policy goes IN the question.** A generic "is this risky / did the user ask
  for this?" predicate FAILED the ops-settlement-day step-8 trap (0.49–0.52 vs a
  correct implied step at 0.61–0.66). A policy-specific predicate separated them
  (+0.61) — post-hoc. Jev evaluates a crisply stated predicate; it does not
  supply desk judgment.

## Rules

- `client.ask()` is the **only** exit. It sanitizes `state` (never `questions`),
  budgets it by `len()` of the one serialization it sends, and maps every failure
  to one of `UNAVAILABLE_REASONS`. It never truncates and never reads the master
  switch.
- **Disabled ≠ broken (D17).** Switch off ⇒ no call, no row, field `NULL`.
  Switch on but unreachable ⇒ an explicit `unscored` record with the reason.
- Tests: conftest hard-sets `OPEN_OTC_SYSTEM_ONE=false` and replaces
  `client._default_post` with a `pytest.fail`. Inject `post=` or patch
  `_default_post` with a fake from `tests/_system_one_fakes.py`.
