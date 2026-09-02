# Instant-messaging gateway (Feishu/Lark)

Driving the full desk agent over IM with web parity.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

---

## Instant-messaging gateway (Feishu/Lark)

Drive the full desk agent over IM with web parity — streaming markdown replies,
HITL Approve/Reject cards, pickable reply-option cards, linking-code enrollment.

**Package:** `backend/app/services/gateway/` — `runtime` (single-worker DB-lease
election + heartbeat), `connectors/{base,fake,feishu}`, `dispatch` (dedup +
message/card lanes + per-chat serialization), `bridge` (threads `actor=binding.desk_user`
into the agent service), `coalescer` (StreamRenderer: streaming, approval cards,
reply-option cards, revocation, rate limit), `identity`, `actions`, `cards`,
`config`, `sse`, `types`. Endpoints: `/api/gateway/*` in `main.py`. Migration
`0037_gateway_tables`. Tests: `tests/gateway/` (run from **repo root**).

### Run model — dedicated worker, NOT `--reload`

The lark WS client (`lark_oapi.ws.Client`) owns a **module-global event loop**
and `start()` is a **blocking** call run on a daemon thread (events are marshalled
to dicts via `lark.JSON.marshal` and dispatched onto the server loop with
`run_coroutine_threadsafe`). This **does not survive uvicorn `--reload`** — a
hot-reload while the worker holds the lock wedges it. So: keep
`GATEWAY_ENABLED_CONNECTORS` **empty in `.env`** (reloading dev servers stay inert)
and enable the connector via a launch override on a stable, non-reload worker:
`GATEWAY_ENABLED_CONNECTORS=feishu uvicorn app.main:app --app-dir backend --port 8001`.
A single-row `gateway_worker_lock` lease guarantees one Feishu handler; every other
backend stays in standby (and standby workers do **not** retry — they pick up the
lock only on restart).

### lark schema-2.0 card realities (all live-only — fakes hid them)

- **Cards must be schema 2.0.** Buttons are elements inside `body.elements` — there
  is **no top-level `actions`** (that's 1.x). Buttons fire via a `behaviors`
  callback array, **not** a `value` field. The `note` element is gone — use
  `markdown`. Wrong shape → `code=230099 / 200621`.
- **Text bubbles can't render markdown**; replies are sent as a headerless
  `markdown`-element card (`_text_to_markdown_card`), and in-place streaming edits
  use `message.patch` (interactive), not `message.update` (text).
- **Outbound builders:** `receive_id_type` goes on the *request* builder, not the
  body; `PatchMessageRequestBody` has only `content`. Always check `resp.success()`
  — failures are otherwise swallowed.

### HITL & reply options ride the `done` SSE event

`agents.py::_done_payload` enriches the terminal `done` event with `thread_id` +
`pending_actions` + `reply_options` (read back from the persisted message meta) so
IM connectors can render cards — the web UI re-fetches and ignores the extras. Both
finalize paths emit it (`_finalize_turn` **and** `_finalize_workflow_stream_turn`;
`DESK_WORKFLOW` envelope uses the latter). Approval buttons carry a one-time token
(→ `resume`); reply-option buttons carry `{reply, label}` and are replayed as a
normal `message` turn that also locks their card (`InboundMessage.card_lock_ref`).

### Config & gotchas

- `GATEWAY_AGENT_MODEL` (`channel:provider:model`, e.g.
  `zenmux:openai:deepseek/deepseek-v4-flash`) selects the IM-turn model; it's a real
  `Settings` field (loads from `.env`), resolved by the bridge as explicit-arg →
  settings → process-env → registry default. `GATEWAY_WEB_BASE_URL` points card
  deep-links at the web desk (e.g. `http://localhost:5173`).
- `lark-oapi` is a hard dep in `pyproject.toml`; a stale `.venv` may lack it
  (`uv sync`). WS long-connection mode needs **no** public webhook URL.
- **`.env` leak in tests — FIXED, no longer a caveat.** Real `FEISHU_*` / `GATEWAY_*`
  values used to bleed into `Settings()` and fail the "defaults are None/empty"
  assertions in `tests/gateway/test_config.py` / one `test_identity.py` case, and the
  guidance was "validate those in a no-`.env` environment". The suite is now hermetic
  via `app.config.dotenv_path()` + the `OPEN_OTC_ENV_FILE` pin in `tests/conftest.py`
  (see *Tests must not assert against moving targets*), so it passes identically with
  and without a `.env`. All gateway tests are connector-agnostic (FakeConnector).
