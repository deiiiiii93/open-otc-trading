# Trade Confirmation → Book — design

**Date:** 2026-08-04
**Status:** Approved (sections reviewed interactively; approach A selected)

## Purpose

Let the desk turn counterparty trade confirmation documents (PDF, DOCX — including
scanned/image-only files) into booked positions. Upload via a new **Confirmations**
web page or as chat attachments to the **Desk Agent** / **Pet Agent** (floating
mini-chat); the system extracts structured trade terms, validates them
deterministically, and books them through the existing single booking gate after
human review.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Parse engine | LLM extraction + deterministic gate; **vision-capable model required** for image-only pages (scanned PDFs, image-bearing DOCX) |
| Booking gate | **Always human-review first** — nothing books without a person confirming (web review screen; agent HITL card) |
| Document shapes | Single-trade files, multi-trade documents, multi-file batch upload — all in v1 |
| Family scope | **All `build_product` families** (~15), driven by `get_product_term_schema`; anything else lands honest `unsupported` |
| Architecture | **Approach A** — one shared `services/confirmations` package; REST + web page + agent tools are thin clients |

## Architecture

```
upload (web multipart | chat attachment)
  → stored under artifact_dir/uploads/confirmations/ (_store_upload seam)
  → ConfirmationBatch + ConfirmationDocument rows
  → async parse task (submit_async_task; agent tool path parses synchronously)
      per document:
        text extraction (pypdf / python-docx)
        image-page detection → pypdfium2 page render → vision content parts
        stage 1 LLM: segment & classify trades [{family, page_range, anchor_snippet}]
        stage 2 LLM per trade: fill get_product_term_schema(family) + evidence + confidence
        deterministic re-validation via prepare_booking_product_spec / build_product
  → ExtractedTrade rows (reviewable)
  → human review (web card edit/book/reject | agent HITL book_extracted_trade)
  → book_position (the existing single gate) → Position
```

Numbers never come from an LLM for pricing: extraction output is data entry that
cannot reach pricing without passing `build_product` validation and human review.

## Data model (one Alembic migration)

**`ConfirmationBatch`** — one upload action. `id`, `source` (`web`/`agent`),
`default_portfolio_id` (nullable FK; target book chosen at upload, overridable per
trade), `task_id` (nullable FK → `TaskRun`; null for the synchronous agent path),
`created_at`.

**`ConfirmationDocument`** — one uploaded file. `batch_id`, `filename`,
`stored_path`, `sha256`, `byte_len`, `mime`, `page_count`, `extract_mode`
(`text`/`vision`/`mixed`), `status` (`pending → parsing → parsed | failed`),
`error`, `model_provenance` (channel/provider/model actually used),
`extraction_debug` (raw LLM responses per stage), timestamps.

**`ExtractedTrade`** — the reviewable unit (1..n per document). `document_id`,
`seq`, `family`, `extracted_terms` (**immutable** — exactly what the LLM
returned), `terms` (current, human-editable), `underlying`, `quantity`,
`entry_price`, `currency`, `counterparty`, `trade_date`, `external_trade_id`
(confirmation's own reference, if present), `confidence`, `evidence` (per-field
`{quote, page}` against `extracted_terms`), `validation_status`
(`valid | invalid | unsupported`) + `validation_errors`, lifecycle `status`
(`extracted → booked | rejected`), `booked_position_id` (nullable FK), reject
reason, timestamps.

**Idempotency:** booking sets `source_trade_id = external_trade_id` when
extracted, else `conf:{sha256[:12]}:{seq}`. `book_position` already dedups on
`(portfolio_id, source_trade_id)`, so re-booking the same confirmation collides
loudly ("already booked" + link) instead of double-booking — shared vocabulary
with the xlsx importer.

**Counterparty** is provenance-only: kept on `ExtractedTrade` and in the booked
position's `source_payload` (xlsx-import pattern). No new `Position` columns.

## Extraction pipeline

1. **Text extraction** — PDF per-page via `pypdf`; DOCX via existing
   `python-docx` (paragraphs + tables; page-less, treated as one page).
2. **Image-page detection** — a PDF page under a small char threshold is an image
   page → rendered to PNG via `pypdfium2` (pure wheel) and sent as image content.
   A DOCX with near-empty text but embedded images routes those images the same
   way. `extract_mode` records `text`/`vision`/`mixed` honestly.
3. **Two-stage extraction:**
   - **Stage 1 (segment & classify):** one call per document → JSON list
     `{family, page_range, anchor_snippet}`, family constrained to the
     `build_product` family list. Enables multi-trade documents.
   - **Stage 2 (fill the legal schema):** one call per trade, prompted with
     `get_product_term_schema(family)` plus only that trade's pages/images →
     terms + per-field `{quote, page}` evidence + confidence. Invalid JSON → one
     retry → document `failed` honestly.
4. **Deterministic re-validation:** every trade is dry-run through
   `prepare_booking_product_spec` / `build_product` immediately;
   `validation_status` + structured `validation_errors` stored. LLM output is
   never trusted as bookable.

**Model routing** (memory-extractor precedent): dedicated registry tag
`confirmation_extractor` (tag exactly one **vision-capable** model in
`config/agent_channels.yaml` **and** the tracked `.example.yml`) → fallback tag
`fast` → registry default. If a document needs vision and the resolved model
rejects images, the document fails with an explicit "vision-capable extractor
required" error — no silent text-only degradation.

**Async:** REST upload returns the batch immediately; a `submit_async_task` job
parses documents sequentially with per-document failure isolation, updating
status as it goes. Frontend polls only while non-terminal (arena-runs pattern).

## REST API (`/api/confirmations`)

- `POST /api/confirmations` — multipart, multiple files, optional `portfolio_id`
  → batch + documents + parse task; returns batch.
- `GET /api/confirmations` — batch list; `GET /api/confirmations/{id}` — batch
  detail (documents + trades).
- `PUT /api/confirmations/trades/{trade_id}` — edit `terms`, the scalar booking
  fields (`family`, `underlying`, `quantity`, `entry_price`, `currency`), and/or
  target portfolio; server re-validates in the same operation (validation status
  can never be stale).
- `POST /api/confirmations/trades/{trade_id}/book` — books via the service gate;
  sets `booked_position_id`.
- `POST /api/confirmations/trades/{trade_id}/reject` — terminal, optional reason.

## Frontend — Confirmations nav page

Shared primitives (`DataTablePage`/`TableToolbar` pager — same control as
Positions/Audit, not bespoke). Upload dropzone (multi-file) + portfolio select →
batch table (polls while parsing) → batch detail: each document with status +
its extracted trades. Trade review card: family badge, editable term fields,
per-field evidence quote + page, inline validation errors, confidence, **Book**
/ **Reject**; booked trades deep-link to the position. Token-only styling per
`frontend/CLAUDE.md`.

## Agent integration (Desk Agent + Pet Agent)

1. **Chat attachments (new seam):** `POST /api/chat/uploads` (multipart →
   `{path, filename, sha256}`); new optional `attachments` list on
   `AgentMessageCreate`; persisted in user-message meta; attachment manifest
   injected into the turn context. Composer paperclip implemented once in
   `useAgentChatController` → both Desk Agent page and Pet mini-chat get it.
2. **Three tools** (thin wrappers over the same service):
   - `parse_trade_confirmation(paths, portfolio_id?)` — creates batch, parses
     synchronously in the tool, returns extracted trades + validation results.
     Write-class (`__capability_group__` → audit captured), **not** HITL.
   - `get_confirmation_batch(batch_id)` — read.
   - `book_extracted_trade(trade_id, portfolio_id?)` — **HITL "write"**; approval
     card shows family, terms, validation status, target book.
3. **Skill** `booking/book-trade-confirmation` **with a routing line in the
   orchestrator prompt** (unroutable-skill lesson).

**Registration checklist:** `QUANT_AGENT_TOOLS` (+ count-pin test),
`DEEP_AGENT_TOOL_NAMES`, HITL three structures for the book tool
(`INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL` → `"write"`, `_LABEL_BY_TOOL`;
`test_hitl` exact-set guard), six skill-catalog exact-set test files.

## Error handling

- Per-document failure isolation — one bad file never kills a batch.
- Vision needed, model can't → explicit per-document error.
- Invalid LLM JSON → one retry → `failed` with raw response in
  `extraction_debug`.
- Validation failures are data, not errors: trade lands `invalid`, reviewer
  edits, server re-validates.
- Duplicate `source_trade_id` in target book → "already booked" + link, never a
  duplicate or a 500.
- Unsupported family → honest `unsupported`.

## Audit

REST path: `record_audit` events `confirmations.uploaded`,
`confirmations.parsed`, `confirmations.trade_booked`,
`confirmations.trade_rejected` (mirrors `positions.imported`). Agent path:
captured by `AuditTrailMiddleware` via `__capability_group__`.

## Testing

- Extraction unit tests with tiny generated fixture PDFs/DOCX (text / scanned /
  mixed): image-page detection, mode selection, DOCX table text.
- **Fake extractor seam** (injectable client, arena `judge_fn` pattern):
  deterministic canned stage-1/stage-2 responses; full pipeline tested with zero
  live LLM calls.
- Gate tests: invalid terms can never reach `book_position`; edit → re-validate;
  idempotent re-book; per-document failure isolation.
- Registration pins updated in the same commit.
- Frontend vitest (page states: parsing/failed/review/booked; attachment
  composer) + `tsc --noEmit`.
- **Live smoke before merge** (one text PDF + one scanned PDF, real vision
  model): replay proves satisfiability, never reachability.

## New dependencies

`pypdf` (PDF text), `pypdfium2` (page rendering). Both permissive licenses;
`python-docx` already present.

## Out of scope (v1)

Feishu/IM gateway file upload; auto-booking; per-counterparty template parsers;
`Position.counterparty` column; embedding/RAG anything.
