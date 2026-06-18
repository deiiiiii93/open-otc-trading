# Booking Wizard Guidance — Term-Collection Card

- **Date:** 2026-05-31
- **Status:** Approved (brainstorm) — pending spec review → writing-plans
- **Owner:** fuxinyao
- **Related:** [[2026-05-29-agent-booking-and-product-builders-design]], [[2026-05-30-unified-product-schema-design]], [[2026-05-17-structured-reply-options-design]]; builds on the `ToolErrorBoundaryMiddleware` fix (commit `60b42f1`).

## 1. Context & motivation

A trader can book a structured product **directly** (no RFQ / quote-first). When the
product economics are incomplete, `build_product` (the single deterministic builder)
returns a precise `missing` list and refuses to fabricate terms; the Snowball booking
gate (`normalize_booking_product_spec`) surfaces it as
`"Incomplete SnowballOption booking terms; missing: …"`.

Today the agent's only affordance is the `build-product` skill's instruction to "ask
for exactly those fields in one message" as free text. We want a **guided, wizard-like
experience**: the agent presents the missing economics as an interactive
**term-collection card** with pickable choices and suggested defaults, the user fills
it, and the agent validates and books — without ever forcing a quote-first route and
without persisting anything until the booking is complete and legal.

This is a **direct-booking completion wizard**, not a routing change.

## 2. Goals / non-goals

**Goals**
- When a direct booking is incomplete/invalid, the agent emits a term-collection card
  covering exactly the missing fields, with convention-based choice chips and suggested
  defaults.
- The user completes the card (or types terms in chat); the agent re-validates via
  `build_product` and loops until legal, then confirms and books.
- Ship snowball-family guidance first (`SnowballOption`, `KnockOutResetSnowballOption`).

**Non-goals (YAGNI)**
- No draft/incomplete position persistence (nothing is stored until complete + valid).
- No server-side validation endpoint (no parallel, non-agent `build_product` caller).
- No static per-field metadata registry (the LLM authors the card — see §5).
- No quote-first routing (explicitly rejected by the user).
- No non-snowball convention guidance in v1 (other families render through the same
  card with thinner guidance; no per-family code gate).

## 3. Decisions log (from brainstorming)

1. **Booking model:** *Guide-then-book.* Nothing persisted until terms are complete +
   validated; one complete `book_position` call. No DB/schema change.
2. **Pacing:** *Rich single-round.* Ask for all missing fields at once in one structured
   card; re-loop only if still missing/invalid.
3. **Affordance:** *Mini-form.* A structured term-collection card the frontend renders
   (not flat reply-option buttons).
4. **Submit flow:** *Client checks + agent validates.* Instant client-side checks
   (required/number/date format); on submit the agent runs `build_product`
   (authoritative); no new backend endpoint.
5. **Field metadata:** *LLM-authored.* The agent fills labels/help/choices/defaults; no
   backend registry. Safe because of the invariant in §4.
6. **Tool mechanism:** *New `propose_term_form` tool* (not an extension of
   `propose_reply_options`).
7. **Layout:** *A · Stacked list* (chat-native; one field per block with help text,
   choice chips, dashed default chip, custom input, progress pill, Submit).

## 4. The safety invariant (linchpin)

> **`build_product` is the authoritative gate; the LLM-authored card is advisory.**

A wrong chip, a bad default, or even an omitted field cannot produce an illegal booking:
after submit the agent re-runs `build_product`, which re-reports any missing/invalid
field, and the agent re-emits the card. A missing field can never be permanently lost
(the loop self-corrects), and a malformed term can never be persisted (the gate rejects
it). This is what makes "LLM fills the card" sound without a registry.

Booking itself remains **HITL-confirmed and capability-gated** (`book_position`,
`DOMAIN_WRITE`). The `ToolErrorBoundaryMiddleware` fix guarantees any residual tool-body
raise returns to the agent as a recoverable error ToolMessage rather than crashing.

## 5. Architecture & components

The term-form reuses the **structured-reply-options plumbing** end to end:

| Layer | Component | Mirrors / file |
|---|---|---|
| Tool | `propose_term_form` — attaches a form payload to the next assistant message | `backend/app/services/reply_options/tool.py` (`ProposeReplyOptionsTool`) → new `term_form/tool.py` (or sibling in `reply_options/`) |
| Capture | read tool args at tool-end, normalize + cap, store on the stream collector | `agents.py:_capture_reply_options_from_tool_end` (~L128) + `_reply_options_from_result` (~L583) |
| Persist | attach normalized payload to `AgentMessage.meta["term_form"]` | `agents.py` (~L1441, ~L1742) |
| Frontend render | `TermForm` component renders layout A from `message.meta.term_form` | `frontend/src/components/replyOptions.ts` + `ChatBubble.tsx` precedent |
| Submit | client checks → user message carrying `meta.term_form_response` + text fallback | new in `TermForm` + `ChatBubble`/`MessageList` send path |
| Skill | wizard procedure + snowball conventions | revise `build-product` SKILL.md (§9); small reference from `book-position` |

The orchestrator tool list already includes `propose_reply_options`
(`orchestrator.py` → `ProposeReplyOptionsTool()`); add `propose_term_form` the same way,
and add it to the personas' tool set if domain personas (trader) need to emit it. (The
trader persona books, so the tool must be available where `book_position` is.)

## 6. Tool contract

`propose_term_form` validates **shape only** (mirrors `_normalize_reply_option`'s
defensive caps; never judges content — §4):

```python
class FieldSpec(BaseModel):
    key: str            # echoes the build_product missing-key, e.g. "barrier_config.ko_barrier"
    label: str          # human label, capped length
    help: str | None    # one-line hint, capped length
    type: Literal["percent", "number", "date", "enum", "text"]
    choices: list[Choice] | None   # ≤ 5 chips; Choice = {label, value}
    default: Choice | None         # the dashed "suggested" chip
    required: bool = True

class ProposeTermFormInput(BaseModel):
    title: str
    subtitle: str | None
    fields: list[FieldSpec]        # ≤ ~12
    submit_label: str = "Review & book"
```

Caps (mirror reply-options constants in `agents.py`): max fields, max choices/field (5),
string length caps. `_run`/`_arun` return `{"ok": True, "count": len(fields)}`. A
defensive `_normalize_term_field` re-checks shape when the orchestrator reads raw args
from event payloads.

## 7. Submit round-trip & validation loop

1. User fills the card; `TermForm` runs **client-side checks** per `type`
   (required present; `number`/`percent` numeric; `date` ISO). Bad fields block submit
   with inline errors.
2. On submit, the chat send path posts a user message with:
   - `meta.term_form_response = { key: value, … }` (structured, authoritative for the agent), and
   - a human-readable text fallback (e.g. `"S0=8359.56, KO=103%, KI=70%, freq=Monthly, start=2026-05-31"`) so the transcript stays readable and the agent has a fallback.
3. The agent merges the response into the prior `terms` and calls `build_product`:
   - `missing`/invalid → re-emit `propose_term_form` with only the offending fields + error hints;
   - `ok` → confirmation summary → `book_position` (HITL).
4. The card is an **affordance, not a gate**: if the user ignores it and types terms in
   chat, the agent validates those instead.

## 8. Families scope

The framework is family-agnostic — it keys off whatever `build_product.missing` returns.
v1 ships snowball-family **convention guidance** (choice chips, default rules) in the
skill. Other families work through the same card; the agent simply has less convention
guidance until added. No per-family code gate.

## 9. Skill changes — full `build-product` SKILL.md sketch

Enhance the existing `build-product` skill (avoids new-skill catalog-test churn:
`test_skills_catalog{,_v2}`, `test_workflow_skills_phase3`). `book-position` step 1 keeps
delegating to it.

```markdown
---
name: build-product
description: Construct a quant-ark-validated product from natural-language terms before
  booking or quoting, guiding the user through any missing economics with an interactive
  term-collection card. Use when a user states product economics that must become a
  concrete product, when book-position or draft-rfq needs validated product terms, or
  when a direct booking has incomplete terms that must be completed before persistence.
domain: products
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - request_text
optional_context:
  - product_family
  - trade_effective_date
write_actions: false
confirmation_required: false
success_criteria:
  - validated product terms and recommended engine are returned
  - missing economics are collected via a term-collection card, never invented
---

## When to use

- A user states product economics that must become a concrete, priceable product.
- `book-position` or `draft-rfq` needs validated product terms and an engine.
- A direct booking has incomplete/invalid terms that must be completed first.

## Required inputs

The user's request text plus any explicit family, underlying, tenor, barrier levels,
observation frequencies, and dates. Read `/skills/references/products/build-contract.md`
for the per-family term schema.

## Procedure

1. Identify the quant-ark family. Call `get_rfq_catalog` if unclear.
2. Extract the structured `terms` for that family per `build-contract.md`. Do not invent
   economics.
3. Call `build_product(family=<class>, terms=<extracted>)`.
4. If `missing` is non-empty (or validation fails), present a **term-collection card**
   via `propose_term_form` — one field per missing key:
   - Use clear labels and one-line `help`. Set `type` (`percent`/`number`/`date`/`enum`/`text`).
   - Offer convention-based `choices` (≤5) where they exist, e.g. Snowball KO barrier
     `100/103/105%`, KI barrier `70/75% / None`, observation frequency
     `Monthly/Quarterly/Semi-annual`.
   - Suggest a `default` (the dashed chip) but never silently adopt it: for
     `initial_price` (the initial fixing S0), call `fetch_market_snapshot` and propose the
     latest spot; for `trade_start_date`, propose today. The user confirms or overrides.
   - Then write a short reply telling the user to fill the card; do not list the fields as
     markdown bullets — the card renders them.
5. On the user's card response (or typed terms), merge and call `build_product` again.
   Loop step 4 until `missing` is empty and the build is `ok`.
6. On `ok`, hand the validated product terms and `engine_name` to the booking or RFQ step.

## Stop conditions

Do not guess the initial fixing, lockup, trade start, barrier levels, or coupon — present
them on the card and let the user fill them. Do not book or persist from this skill.

## Output shape

Return built-or-blocked first, then family, engine, the validated product terms summary,
and any still-missing terms (as a card, not prose).

## References

- `/skills/references/products/build-contract.md`

## Example

User: Book a 1Y CSI 500 Snowball, KO 103% monthly, into portfolio 6.
Assistant: Extract SnowballOption terms; call `build_product`; it reports
`initial_price`, `barrier_config.ki_barrier`, `trade_start_date`, `observation_frequency`
missing → fetch the latest 000905.SH spot, then `propose_term_form` with S0 (default
spot), KI barrier (70/75%/None), trade start (default today), and frequency
(Monthly/Quarterly/Semi). On submit, re-validate; once `ok`, hand to `book-position` to
confirm and book.
```

`book-position` SKILL.md: step 1 already routes natural-language terms through
`build-product`; add one line to its procedure noting that incomplete terms are completed
via the term-collection card in `build-product`, then proceed to confirm + book.

## 10. Error handling / edge cases

- Invalid free-text → client check blocks (format) or `build_product` rejects (semantic) → re-emit with the error on that field.
- Agent omits a needed field → `build_product` reports it → re-emit (self-corrects, §4).
- Stale card submitted after the conversation moved on → `build_product` against current terms remains the gate.
- User ignores the card and types terms → agent validates those; card is optional.
- `book_position` HITL + capability gating unchanged; residual raises return as recoverable ToolMessages (`ToolErrorBoundaryMiddleware`).

## 11. Testing strategy

- **Tool** (`propose_term_form`): payload shape validation + caps; defensive normalizer drops malformed fields. Mirrors `reply_options` tool tests.
- **Orchestrator**: a clean `propose_term_form` tool-end writes normalized `term_form` to message meta; last call wins. Mirrors `_capture_reply_options_from_tool_end` tests.
- **Frontend** (`TermForm`): renders fields/chips/default/progress from a payload; client-side checks block invalid submit; submit emits `meta.term_form_response` + text fallback. Vitest/RTL, in the style of `ChatComposer.test.tsx` / `replyOptions.test.ts`.
- **Skill**: catalog tests still pass (we revise, not add, so exact-set/count assertions are unaffected — confirm `build-product` description change doesn't break a description assertion).
- **Flow (light integration)**: given a card response with still-missing fields, the agent re-emits; given a complete response, it proceeds to confirm + book.

## 12. Risks / open questions

- **Non-determinism of the LLM-authored card** (accepted): mitigated by the §4 gate and
  by encoding conventions in the skill. If field labels/choices prove inconsistent in
  practice, a static metadata registry remains a future option (deferred, not built).
- **Tool availability on personas**: ensure `propose_term_form` is on the persona(s) that
  book (trader), not only the orchestrator.
- **Submit payload transport**: confirm the chat send path can carry structured
  `meta.term_form_response` (verify against the reply-options click→message mechanism in
  `replyOptions.ts` / `ChatBubble.tsx`).

## 13. References

- `backend/app/services/reply_options/tool.py`, `backend/app/services/agents.py`
- `backend/app/skills/workflows/products/build-product/SKILL.md`,
  `backend/app/skills/workflows/positions/book-position/SKILL.md`
- `backend/app/services/domains/product_builders.py` (`build_product`, `missing`)
- `frontend/src/components/replyOptions.ts`, `ChatBubble.tsx`, `MessageList.tsx`
- Mockups: `.superpowers/brainstorm/87025-1780231773/content/card-layout.html` (layout A)
```
