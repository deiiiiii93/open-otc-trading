# Trade confirmation → book

Counterparty confirmation documents (including scanned pages) into booked positions.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** Lifecycle events a booking emits: [`../domains/CLAUDE.md`](../domains/CLAUDE.md). The cash it implies: [`../settlement/CLAUDE.md`](../settlement/CLAUDE.md).

---

## Trade confirmation → book

Turn counterparty confirmation documents (PDF/DOCX, **including scanned/image-only
pages**) into booked positions. One shared service owns the pipeline; the REST
surface, the **Confirmations** page, and the agent tools are all thin clients over
it — there is no second booking path.

**Package:** `backend/app/services/confirmations/` — `extract.py` (pure PDF/DOCX text
extraction + image-page detection/render), `llm.py` (two-stage extraction + model
routing), `service.py` (batch → parse → validate → review → book). REST:
`backend/app/routers/confirmations.py` (`/api/confirmations`). Tools:
`backend/app/tools/confirmations.py`. Skill:
`skills/workflows/positions/book-trade-confirmation/`. Frontend:
`frontend/src/routes/Confirmations.{tsx,live.tsx,css}`. Migration `0052`
(`confirmation_batches` → `confirmation_documents` → `extracted_trades`).
Deps: `pypdf`, `pypdfium2`.

### The pipeline

Text-first: a PDF page under `MIN_TEXT_CHARS_PER_PAGE` (40) is treated as a scan and
rendered to PNG for the vision path; `extract_mode` records `text`/`vision`/`mixed`
honestly. Then **two LLM stages** — stage 1 segments the document into trades
(`{family, pages, anchor}`, family constrained to the `build_product` vocabulary),
stage 2 fills that family's `get_product_term_schema` with per-field
`{quote, page}` evidence. Every draft is immediately re-validated through
`prepare_booking_product_spec`; `validation_status` is `valid` / `invalid` /
`unsupported`, and validation failures are **data, not errors** (the reviewer edits
and the server re-validates).

### Invariants

- **The underlying must be a BOOKABLE instrument: `status="active"` AND tagged
  `"underlying"`.** Both halves, always — `services/underlyings.py::is_bookable_underlying_row`
  is the single predicate, and `services/instruments.py::resolve_bookable_underlying` is the
  shared gate wrapping it (used by `validate_trade_terms` and `book_position_tool`; the tag
  alone used to be enough, which let a draft instrument through). **This is a fail-CLOSED
  gate because the failure mode downstream is silent:** an unrecognised underlying is not
  rejected by booking — `book_position` → `link_position_underlying` → `ensure_underlying`
  **mints a new Instrument** for any non-empty string (`normalize_underlying_symbol` is just
  `.strip()`), so the position books "fine" and is then unpriceable forever. Never resolve a
  legal name to a symbol automatically; report candidates and let a human/agent choose.
  Candidates come from the confirmation's own evidence quote, which is why
  `"Shares: Apple Inc. (Ticker: AAPL)"` can suggest `AAPL` — suggestion only, never rewrite.
- **Review-first is the whole design.** `book_trade` re-runs validation at booking
  time (never trusts the stored status) and books through the existing
  `book_position` gate. The web page books per trade on a human click; the agent's
  `book_extracted_trade` is HITL **`"irreversible"`**. `parse_trade_confirmation` is
  write-class but deliberately **not** HITL — parsing writes only draft rows.
- **`"write"` is not a HITL level for anything that books.** `interrupt_on_config(
  yolo_mode=True)` — i.e. **AUTO mode** — strips every `"write"`-level tool from the
  interrupt map, so a booking tool classified `"write"` raises **no approval card at
  all** and persists a real position unattended (this happened: thread 682 booked
  position 27 off a PDF with `pending_actions: []`). Every booking tool is therefore
  `"irreversible"` — `book_position`, `book_rfq_to_position`, `book_hedge`,
  `book_extracted_trade` — and positions have no delete to make it otherwise. When
  adding a HITL tool, ask which of the three modes must still stop it: `"write"`
  means "interactive only", **not** "gated".
- **A gated tool needs a `_SUMMARY_BUILDERS` entry when its args are ids.** The
  interrupt fires *before* the tool body runs, so the card can only see the raw args —
  `book_extracted_trade`'s are `{portfolio_id, trade_id}`. `_summarize_book_extracted_trade`
  opens its own short-lived read-only session to state the product, size, counterparty
  and destination, exactly like `_summarize_register_underlying`. Without one the gate
  is theater: a human clicking Approve on two integers is not review.
- **Idempotency:** `source_trade_id` = the confirmation's own `external_trade_id`,
  else `conf:{sha256[:12]}:{seq}`. A pre-check on `(portfolio_id, source_trade_id)`
  returns `already_booked` + the existing position id. Note `book_position` itself
  does **not** dedup and there is no DB unique constraint — the service pre-check is
  the only guard (single-writer posture, same as the xlsx importer).
- **Per-document failure isolation:** `parse_document` never raises for extraction
  failures — the document lands `status="failed"` with the error (and the raw LLM
  response in `extraction_debug`), and the rest of the batch continues.
- `extracted_terms` is **immutable** (what the model returned); `terms` is the
  human-editable copy. Both are kept so a review can always be audited against the
  original parse.
- **`family_check` is advisory.** System One's independent read of the family
  (`extracted_trades.family_check`, migration `0063`) is shown on the row and on
  the booking card; it never changes validation or bookability, and it is never
  computed on arena turns. See [`../system_one/CLAUDE.md`](../system_one/CLAUDE.md).

### Gotchas

- **Extraction output is builder INPUT, not a termsheet — `synthesize_booking_terms`
  is mandatory.** The extractor fills `get_product_term_schema(family)`, so a vanilla
  legitimately comes back as `{initial_price, exercise_date, strike, option_type}`
  (three of them REQUIRED by that schema). But `booking._RAW_TERMSHEET_VOCAB` — the
  sniffer choosing synthesize-vs-validate-verbatim — lists only
  `maturity_years|maturity_date|expiry_date|expiry`, so those terms match nothing, take
  the verbatim path, and QuantArk rejects `initial_price` as an unsupported kwarg.
  **Do NOT "fix" this by adding `exercise_date` to that tuple:** already-built vanillas
  persist `exercise_date` *without* `initial_price`, so widening the tuple flips them
  from the working verbatim path onto a synthesize path that then fails for the missing
  field — the same trap its comment documents for `initial_price`. The service instead
  runs `build_product` itself (the repo's single producer) before the gate, exactly as an
  agent would. `validate_trade_terms` and `book_trade` MUST use the same helper or
  "valid" and "bookable" diverge. **Only the live smoke caught this** — the unit fixtures
  used `maturity_years`, which happens to match the raw-vocab list, so every offline test
  passed while the real model's schema-faithful output was unbookable.
- **A test that books must seed a bookable underlying.** `Instrument.status` defaults to
  `"draft"`, so tagging alone leaves a row unbookable — the `registered_underlying` conftest
  fixture (active + tagged, committed so tools opening their own session can see it) exists
  for this. Symptom of missing it: every booking test suddenly reports `invalid`.
- **The extractor model must be vision-capable.** Routing is by registry tag,
  two-tier: `confirmation_extractor` (tag exactly one model) → `fast` → registry
  default. Tag edits go to **both** `config/agent_channels.yaml` and the tracked
  `.example.yml`. Pinned here: `google/gemini-3.6-flash`. A document needing vision
  under a text-only model fails loudly rather than silently degrading.
- **No module-scope `app.tools` import may live in `services/confirmations/`.**
  `llm.py`/`service.py` need `app.tools.product_term_schema`, but `app.tools`'s
  package init imports `tools/confirmations.py`, which imports this package — a real
  cycle. Both use **function-scope** imports, and
  `test_confirmations_tools.py::test_service_import_before_app_tools_has_no_import_cycle`
  is a fresh-subprocess probe that pins it.
- The three tools need the **full registration checklist** (`QUANT_AGENT_TOOLS`,
  `DEEP_AGENT_TOOL_NAMES`, and for the book tool all three `hitl.py` structures) plus
  the exact-set pins in `test_capability_assignments.py` (tool count) and
  `test_hitl.py`. Adding the SKILL.md broke exact-set assertions in four catalog test
  files — enumerate with `grep -rln "book-position" tests/`.
- **Chat attachments are a separate seam:** `POST /api/chat/uploads` stores under
  `artifact_dir/uploads/chat/` (inside the parse tool's containment root), and
  `stream_chat_message` appends an attachment manifest to the **agent-run** content
  only — the persisted user message keeps the user's original text.
- **Never let the chat report a write through prose alone.** `ChatBubble`'s
  reasoning-fold heuristic (`resolveAssistantContentPresentation`) hides the *entire*
  message body when a reply is long, mentions its own tool names, and carries ≥5 tool
  events — and `findPublicContentBoundary` only rescues a body whose heading contains
  `portfolio|report|summary|risk|hedg|snapshot|recommendation`, which a booking reply's
  headings do not. A real booking confirmation was therefore invisible. The fix is
  structural, not lexical: `book_trade` returns a `booking` payload on **every** exit
  path → `_capture_booking_result_from_tool_end` (reads the tool **result** at
  `on_tool_end`, unlike the term-form/reply-option captures which read **args**) →
  `collector.booking_result` → `meta.booking_result` → `BookingResultCard`. Capture is
  keyed by tool **name** (`BOOKING_RESULT_TOOLS`) so an unrelated tool can't spoof a
  card, and the record survives `reset_user_facing_output_for_retry` because — like a
  tool event — it reports a write that really happened. **Adding a tool to
  `BOOKING_RESULT_TOOLS` requires that tool to return the same `booking` shape.**
- **Result-message scanning cannot see anything a PERSONA did.** The obvious
  implementation — scan `result["messages"]` like `_term_form_from_result` — is
  structurally wrong here: the orchestrator delegates via `task()`, and the persona runs
  in its **own LangGraph checkpoint namespace**. Measured on a live gated booking:
  `book_extracted_trade` appeared in **28 subagent checkpoints and 0 orchestrator ones**.
  `propose_term_form` escapes this only because the *orchestrator itself* calls it. The
  `wrap_tool_call` seam is the only place that sees a subagent's tool calls — the same
  reason `AuditTrailMiddleware` lives there and is registered in all three stacks.
  `BookingResultMiddleware` (`deep_agent/booking_capture.py`) sits beside it.
- **A tool result body is NOT plain JSON at the `wrap_tool_call` seam.**
  `GroundTruthArtifactMiddleware` runs *inside* it and appends
  `<artifact_ref>{...}</artifact_ref>` to the content, so `json.loads(content)` raises
  `Extra data` and silently drops the payload — while a repr still shows the JSON
  perfectly, which makes it look like the middleware never ran. Use
  `json.JSONDecoder().raw_decode()` (first value, ignore the trailing evidence ref), and
  also handle content-block **lists** and `Command` results — the seam returns all three
  shapes. Note `AuditTrailMiddleware` is immune because it only *stores* `str(content)`.
- **Do not key run-scoped state on `configurable["thread_id"]`.** `graph_run_config`
  documents it as the *checkpointer* key ("sometimes a composite string, NOT necessarily an
  AgentThread id"), and inside a persona it is neither. The stable turn identity is
  `AUDIT_CONTEXT_KEY['thread_id']`, stamped at every entry point and readable from inside
  subagents. `current_run_keys()` records under both.
- **The agent does not always route through `book_extracted_trade`.** Live runs booked the
  same confirmation via plain `book_position` roughly half the time, which the booking card
  does not cover (that tool returns no `booking` payload). Worth closing before treating
  card coverage as complete.
- The frontend vitest suite is **flaky under load** (slow route tests hit the 5s
  timeout; `main` alone varies 12→18 failures run to run). Compare failing-file sets
  against a same-machine `main` run before blaming a branch.
