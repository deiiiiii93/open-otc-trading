# IM Message Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let desk users drive the OTC agent from instant messaging (Feishu first) with full web-desk parity — including HITL writes/bookings — behind the existing capability/envelope/HITL/audit gates.

**Architecture:** Approach C — an in-process subsystem (`backend/app/services/gateway/`) started in the FastAPI app lifecycle. Pure-transport connectors normalize IM events; a dispatcher handles dedup/identity/refusals; an `AgentBridge` facade wraps the existing service layer (`active_agent_service`); a coalescer turns the agent's SSE stream into IM messages and interactive HITL cards. Identity is bound via one-time linking codes. No LangGraph SDK / self-HTTP (unlike DeerFlow) — the service layer is the seam.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy + Alembic, asyncio, lark-oapi (Feishu WebSocket), pytest. Spec: `docs/superpowers/specs/2026-06-24-im-message-gateway-design.md`.

## Global Constraints

- Term **"gateway"**, never "channel" (collides with the model-channel registry `_channel_registry` / `/api/agent/channels/*`).
- **No RBAC is introduced.** Authorization parity = identical envelope (`DESK_WORKFLOW`) + identical per-tool HITL gates. The gateway only *captures identity*: bound `desk_user` becomes the audit `actor`.
- **Alembic migration uses migration-local Core tables, never ORM models/services** (project rule; ORM models drift to future schema).
- Migration head is `0031_asian_averaging_weight`; the new migration's `down_revision = "0031"`, id `0032`.
- `record_audit(session, *, event_type, actor, subject_type, subject_id, payload)` lives in `app/services/audit.py`.
- SSE frame format (from `app/services/agents.py:77`): `f"event: {event}\ndata: {json}\n\n"`.
- `active_agent_service.create_thread(session, title, character)`, `.stream_and_persist(...)`, `.normalize_model_selection(...)` are the reused seams.
- All length limits measured in **Unicode code points**.
- Tests must not require live Feishu creds; full existing suite must stay green (validate in a no-`.env` worktree per the tracing false-failure trap).
- All new config fields live on `Settings` in `app/config.py` with the documented defaults from the spec.
- `desk_user` / `issued_by` derive from `settings.gateway_default_desk_user` (default `"desk_user"`); never from the request body.

---

### Task 1: DB schema — migration + ORM models

**Files:**
- Create: `backend/alembic/versions/0032_gateway_tables.py`
- Create: `backend/tests/gateway/__init__.py` (empty — makes the test dir a package)
- Modify: `backend/app/models.py` (append 6 models)
- Test: `backend/tests/gateway/test_gateway_models.py`

**Interfaces:**
- Produces: ORM models `GatewayBinding`, `GatewayLinkingCode`, `GatewayThreadMap`, `GatewayInboundSeen`, `GatewayCardAction`, `GatewayWorkerLock` with the columns named in the spec. Tables: `gateway_binding`, `gateway_linking_code`, `gateway_thread_map`, `gateway_inbound_seen`, `gateway_card_action`, `gateway_worker_lock`.
- Key constraints: partial unique index `uq_gateway_binding_active` on `(provider, external_account_id, workspace_id) WHERE status='active'`; `uq_gateway_inbound_seen` on `(connector, workspace_id, provider_event_id)`; `uq_gateway_card_action_action` on `(thread_id, message_id, action_id, decision)`; `gateway_thread_map` unique `(binding_id, chat_id)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/gateway/test_gateway_models.py
from sqlalchemy import inspect
from app import models, database

def test_gateway_tables_exist_after_metadata_create(tmp_path):
    eng = database.make_engine(f"sqlite:///{tmp_path}/t.db")  # use project's engine factory
    models.Base.metadata.create_all(eng)
    names = set(inspect(eng).get_table_names())
    assert {
        "gateway_binding", "gateway_linking_code", "gateway_thread_map",
        "gateway_inbound_seen", "gateway_card_action", "gateway_worker_lock",
    } <= names

def test_binding_active_partial_unique(db_session):
    from app.models import GatewayBinding
    a = GatewayBinding(provider="feishu", external_account_id="ou_1", workspace_id="tk_1",
                        desk_user="desk_user", persona="trader", status="active")
    db_session.add(a); db_session.commit()
    dup = GatewayBinding(provider="feishu", external_account_id="ou_1", workspace_id="tk_1",
                         desk_user="desk_user", persona="trader", status="active")
    db_session.add(dup)
    import pytest, sqlalchemy
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.commit()
```

(If `database.make_engine` differs, use the project's existing engine factory — check `app/database.py`. Reuse the repo's `db_session` fixture from `backend/tests/conftest.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/gateway/test_gateway_models.py -v`
Expected: FAIL (models/tables not defined).

- [ ] **Step 3: Add ORM models to `app/models.py`**

Append models mirroring the spec schema. Example for the two trickiest; follow the same pattern for the rest:

```python
class GatewayBinding(Base):
    __tablename__ = "gateway_binding"
    id = Column(Integer, primary_key=True)
    provider = Column(String, nullable=False)
    external_account_id = Column(String, nullable=False)
    workspace_id = Column(String, nullable=False, default="")
    desk_user = Column(String, nullable=False)
    persona = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")  # active|revoked
    bound_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    supersedes_binding_id = Column(Integer, ForeignKey("gateway_binding.id"), nullable=True)
    __table_args__ = (
        Index("uq_gateway_binding_active", "provider", "external_account_id", "workspace_id",
              unique=True, sqlite_where=text("status='active'"),
              postgresql_where=text("status='active'")),
    )

class GatewayCardAction(Base):
    __tablename__ = "gateway_card_action"
    id = Column(Integer, primary_key=True)
    token = Column(String, nullable=False, unique=True)
    out_connector = Column(String, nullable=False)
    out_workspace_id = Column(String, nullable=False, default="")
    out_chat_id = Column(String, nullable=False)
    out_message_id = Column(String, nullable=False)
    binding_id = Column(Integer, ForeignKey("gateway_binding.id"), nullable=False)
    thread_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    action_id = Column(String, nullable=False)
    decision = Column(String, nullable=False)            # confirm|dismiss
    expires_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending|resolving|resolved|failed|unknown
    resolved_by_binding_id = Column(Integer, nullable=True)
    __table_args__ = (
        UniqueConstraint("thread_id", "message_id", "action_id", "decision",
                         name="uq_gateway_card_action_action"),
    )
```

Define `GatewayLinkingCode` (code unique, desk_user, persona, expires_at, redeemed_by_binding_id, issued_by, created_at), `GatewayThreadMap` (binding_id, chat_id, thread_id; unique `(binding_id, chat_id)`), `GatewayInboundSeen` (connector, workspace_id, provider_event_id, state, owner_token, claimed_at, attempts, seen_at; unique triple), `GatewayWorkerLock` (id default 1, owner_token, acquired_at, lease_expires_at) the same way. Import any missing names (`Index`, `UniqueConstraint`, `text`, `func`, `ForeignKey`) at the top of `models.py`.

- [ ] **Step 4: Write the Alembic migration (migration-local Core tables)**

```python
# backend/alembic/versions/0032_gateway_tables.py
"""gateway tables"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("gateway_binding",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("external_account_id", sa.String, nullable=False),
        sa.Column("workspace_id", sa.String, nullable=False, server_default=""),
        sa.Column("desk_user", sa.String, nullable=False),
        sa.Column("persona", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("bound_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("supersedes_binding_id", sa.Integer, nullable=True),
    )
    op.create_index("uq_gateway_binding_active", "gateway_binding",
        ["provider", "external_account_id", "workspace_id"], unique=True,
        sqlite_where=sa.text("status='active'"), postgresql_where=sa.text("status='active'"))
    # ... create the other 5 tables + their unique constraints/indexes identically to the models ...

def downgrade():
    for t in ("gateway_card_action", "gateway_inbound_seen", "gateway_thread_map",
              "gateway_linking_code", "gateway_worker_lock", "gateway_binding"):
        op.drop_table(t)
```

Fill in the remaining 5 `create_table` calls with the exact columns from Step 3. **Mirror every ORM foreign key in the migration** (`sa.ForeignKey`): `gateway_binding.supersedes_binding_id → gateway_binding.id`, `gateway_thread_map.binding_id → gateway_binding.id`, `gateway_card_action.binding_id → gateway_binding.id`. The metadata-created test DB (Step 1) and the Alembic-created DB must have identical constraints.

- [ ] **Step 5: Add a migration-application test (parity with ORM metadata)**

```python
# append to backend/tests/gateway/test_gateway_models.py
def test_migration_creates_same_schema_as_metadata(tmp_path):
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import create_engine, inspect
    url = f"sqlite:///{tmp_path}/m.db"
    cfg = Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0032")
    insp = inspect(create_engine(url))
    names = set(insp.get_table_names())
    assert {"gateway_binding","gateway_linking_code","gateway_thread_map",
            "gateway_inbound_seen","gateway_card_action","gateway_worker_lock"} <= names
    # partial unique index present
    assert any(ix["name"] == "uq_gateway_binding_active"
               for ix in insp.get_indexes("gateway_binding"))
    # FK present
    fks = insp.get_foreign_keys("gateway_card_action")
    assert any(fk["referred_table"] == "gateway_binding" for fk in fks)
```

- [ ] **Step 5b: Run tests + migration dry run**

Run: `cd backend && pytest tests/gateway/test_gateway_models.py -v && alembic upgrade head --sql | tail -5`
Expected: PASS; SQL prints the new tables.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0032_gateway_tables.py backend/tests/gateway/__init__.py backend/tests/gateway/test_gateway_models.py
git commit -m "feat(gateway): schema for IM gateway bindings, codes, cards, dedup, lock"
```

---

### Task 2: Normalized transport types

**Files:**
- Create: `backend/app/services/gateway/__init__.py`, `backend/app/services/gateway/types.py`
- Test: `backend/tests/gateway/test_types.py`

**Interfaces:**
- Produces: frozen dataclasses `ChatRef(connector, workspace_id, chat_id, chat_type)`, `MessageRef(connector, workspace_id, chat_id, message_id)`, `OutboundMessage(text)`, `CardAction(label, style, token)`, `CardSection(title, body)`, `OutboundCard(title, body, sections, actions, resolved, footer)`, `CardActionInbound(source_message_ref, token)`, `InboundMessage(connector, workspace_id, external_account_id, provider_event_id, chat, kind, text, action, raw)`, `ConnectorCapabilities(supports_edit_in_place_message, supports_edit_in_place_card, supports_interactive_cards, max_message_chars)`, `ConnectorHealth(name, state, detail)`, `AgentEvent(type, data)`. `kind ∈ {"message","card_action"}`; `chat_type ∈ {"dm","group"}`; `style ∈ {"primary","danger","default"}`; `decision ∈ {"confirm","dismiss"}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/gateway/test_types.py
from app.services.gateway.types import InboundMessage, ChatRef, OutboundCard, CardAction

def test_inbound_message_card_action_has_no_text():
    chat = ChatRef("feishu", "tk_1", "oc_1", "dm")
    msg = InboundMessage("feishu", "tk_1", "ou_1", "evt_1", chat, "card_action", None, None, {})
    assert msg.kind == "card_action" and msg.text is None

def test_card_action_carries_only_token_to_button():
    a = CardAction(label="Approve", style="primary", token="tok_abc")
    assert a.token == "tok_abc" and not hasattr(a, "action_id")
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && pytest tests/gateway/test_types.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `types.py`** — define every dataclass above as `@dataclass(frozen=True)` with the exact field names/order; use `typing.Literal` for the enums and `Any` for `raw`/`data`.

- [ ] **Step 4: Run to verify it passes** — same command → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/gateway/__init__.py backend/app/services/gateway/types.py backend/tests/gateway/test_types.py
git commit -m "feat(gateway): normalized transport types"
```

---

### Task 3: Connector protocol + FakeConnector

**Files:**
- Create: `backend/app/services/gateway/connectors/__init__.py`, `backend/app/services/gateway/connectors/base.py`, `backend/app/services/gateway/connectors/fake.py`
- Test: `backend/tests/gateway/test_fake_connector.py`

**Interfaces:**
- Consumes: types from Task 2.
- Produces: `MessageConnector` Protocol with `name`, `capabilities`, async `start(on_inbound)`, `stop()`, `send_message(chat, msg, *, idempotency_key) -> MessageRef`, `update_message(ref, msg)`, `send_card(chat, card, *, idempotency_key) -> MessageRef`, `update_card(ref, card)`, `health()`. `FakeConnector` implementing it with an in-memory `outbox: list` and `feed_inbound(InboundMessage)` test helper; honors `idempotency_key` (same key → same `MessageRef`, no duplicate outbox entry).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/gateway/test_fake_connector.py
import pytest
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import ChatRef, OutboundMessage

@pytest.mark.asyncio
async def test_idempotent_send_does_not_duplicate():
    c = FakeConnector()
    chat = ChatRef("fake", "", "chat1", "dm")
    r1 = await c.send_message(chat, OutboundMessage("hi"), idempotency_key="k1")
    r2 = await c.send_message(chat, OutboundMessage("hi"), idempotency_key="k1")
    assert r1 == r2
    assert len(c.outbox) == 1
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/gateway/test_fake_connector.py -v` → FAIL.

- [ ] **Step 3: Implement `base.py` (Protocol) and `fake.py`.** `FakeConnector.capabilities = ConnectorCapabilities(True, True, True, 10000)`; keep an `_idem: dict[str, MessageRef]` and append to `outbox` only on first key; `feed_inbound` calls the stored `on_inbound` callback.

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): connector protocol + FakeConnector"`.

---

### Task 4: GatewayConfig (settings)

**Files:**
- Modify: `backend/app/config.py` (add fields to `Settings`)
- Create: `backend/app/services/gateway/config.py`
- Test: `backend/tests/gateway/test_config.py`

**Interfaces:**
- Produces: `Settings` gains `gateway_default_desk_user="desk_user"`, `gateway_linking_code_ttl_s=600`, `gateway_card_action_ttl_s=1800`, `gateway_max_inbound_chars=4000`, `gateway_max_queued_per_chat=8`, `gateway_queue_max_age_s=120`, `gateway_dedupe_ttl_s=86400`, `gateway_dedupe_lease_s=120`, `gateway_lock_lease_s=30`, `gateway_code_issue_per_min=10`, `gateway_flush_interval_ms=700`, `gateway_flush_chars=280`, `gateway_web_base_url: str | None = None`, `gateway_enabled_connectors: str = ""` (comma list), Feishu creds `feishu_app_id/feishu_app_secret/feishu_verification_token/feishu_encrypt_key` (all `str | None`). `gateway/config.py` exposes `GatewayConfig.from_settings(settings) -> GatewayConfig` typed view + `web_thread_link(thread_id)` / `web_action_link(thread_id, message_id, action_id)` helpers (return `None` if `gateway_web_base_url` unset, logging once).

- [ ] **Step 1: Failing test** — assert defaults present and `web_action_link` returns `None` when base url unset, else formatted URL.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Add fields + `GatewayConfig`.** Link formats: thread `{base}/chat?thread={id}`, action `{base}/chat?thread={tid}&message={mid}&action={aid}`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): typed config + web deep-link helpers"`.

---

### Task 5: Identity & enrollment service

**Files:**
- Create: `backend/app/services/gateway/identity.py`
- Test: `backend/tests/gateway/test_identity.py`

**Interfaces:**
- Consumes: ORM models (Task 1), settings (Task 4), `record_audit`.
- Produces:
  - `issue_linking_code(session, *, persona, settings) -> tuple[str, datetime]` (validates persona ∈ known set; `desk_user`/`issued_by` = `settings.gateway_default_desk_user`; ≥128-bit base32 code; TTL).
  - `redeem_code(session, *, connector, external_account_id, workspace_id, code, settings) -> GatewayBinding | None` implementing the exact transaction: SELECT-FOR-UPDATE code → validate unexpired/unredeemed → revoke existing active binding for the identity → insert new active binding (`supersedes_binding_id` set if one was revoked) → mark code redeemed → audit `gateway.bound`/`gateway.transferred`/`gateway.rebound` → returns the binding (or `None` if code invalid).
  - `active_binding(session, *, connector, external_account_id, workspace_id) -> GatewayBinding | None`.
  - `revoke_binding(session, *, binding_id) -> str` (idempotent → returns `"revoked"`).
  - `is_code_shaped(text) -> bool`.
  - `KNOWN_PERSONAS` (import the persona names from `app/services/deep_agent/personas.py` / `persona_domains.py`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/gateway/test_identity.py
from app.services.gateway import identity
from app.config import get_settings  # or however settings are obtained

def test_redeem_binds_then_transfer_supersedes(db_session):
    s = get_settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    b1 = identity.redeem_code(db_session, connector="feishu", external_account_id="ou_1",
                              workspace_id="tk_1", code=code, settings=s)
    assert b1.status == "active"
    code2, _ = identity.issue_linking_code(db_session, persona="risk_manager", settings=s)
    b2 = identity.redeem_code(db_session, connector="feishu", external_account_id="ou_1",
                              workspace_id="tk_1", code=code2, settings=s)
    db_session.refresh(b1)
    assert b2.status == "active" and b2.supersedes_binding_id == b1.id
    assert b1.status == "revoked"
    assert identity.active_binding(db_session, connector="feishu",
                                   external_account_id="ou_1", workspace_id="tk_1").id == b2.id

def test_expired_code_rejected(db_session, monkeypatch):
    s = get_settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    # force expiry
    from app.models import GatewayLinkingCode
    row = db_session.query(GatewayLinkingCode).filter_by(code=code).one()
    import datetime as dt
    row.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1); db_session.commit()
    assert identity.redeem_code(db_session, connector="feishu", external_account_id="ou_2",
                                workspace_id="tk_1", code=code, settings=s) is None

def test_invalid_persona_rejected(db_session):
    import pytest
    with pytest.raises(ValueError):
        identity.issue_linking_code(db_session, persona="nope", settings=get_settings())
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `identity.py`** with the transaction order from the spec. Use `secrets.token_bytes(16)` → base32 for the code. For SQLite (no real `FOR UPDATE`), rely on the surrounding transaction + the conditional update on the code row; the test DB is fine.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): linking-code identity + transfer/revoke"`.

---

### Task 6: Resume refactor + `actor` threading (behavior-preserving)

**Files:**
- Modify: `backend/app/services/agents.py` (add `resume_pending_action(...)`; add `actor` kwarg to `stream_and_persist`)
- Modify: `backend/app/main.py` (`_resume_action` → thin wrapper; turn endpoint passes `actor="desk_user"`)
- Test: `backend/tests/gateway/test_resume_refactor_characterization.py`

**Interfaces:**
- Produces: `active_agent_service.resume_pending_action(*, thread_id, message_id, action_id, decision, actor, session) -> AgentMessage` (the body currently in `main.py:_resume_action`, audit `actor` substituted). `stream_and_persist(..., actor: str = "desk_user")` threading `actor` into the turn/resume audit calls that today hard-code `"desk_user"`.
- Consumes (later tasks): the bridge calls these with `actor=binding.desk_user`.

- [ ] **Step 1: Write the characterization test FIRST (pins existing web behavior)**

```python
# backend/tests/gateway/test_resume_refactor_characterization.py
# Drive the existing HTTP confirm path; assert the resumed message + that an audit row
# with actor="desk_user" is written. Capture current behavior BEFORE refactor.
def test_web_confirm_still_uses_desk_user_actor(client, seeded_pending_action):
    msg = seeded_pending_action
    r = client.post(f"/api/chat/threads/{msg.thread_id}/messages/{msg.id}/actions/{msg.action_id}/confirm")
    assert r.status_code == 200
    # assert an AuditEvent actor == "desk_user" exists for this resume
```

(Build `seeded_pending_action` from existing test helpers that create an assistant message with `meta.pending_actions`. If none exist, add a small factory in the test.)

- [ ] **Step 2: Run → PASS against current code** (this is characterization — it should pass now; it guards the refactor).
- [ ] **Step 2b: Write a failing test for the NEW actor behavior** — call `active_agent_service.resume_pending_action(..., actor="im_actor", session=...)` on a seeded pending action and assert the resulting `AuditEvent.actor == "im_actor"` (not `"desk_user"`). This independently verifies the threading, not just the web default.
- [ ] **Step 3: Extract `resume_pending_action` into `agents.py`;** make `main.py:_resume_action` call it with `actor="desk_user"`. Add `actor` kwarg to `stream_and_persist`, default `"desk_user"`, substituted into its audit calls.
- [ ] **Step 4: Re-run both tests → characterization PASS (web=`desk_user`) AND new-actor test PASS (`im_actor`).** Run the broader agent test module too: `pytest tests/test_agents*.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "refactor(agent): extract resume_pending_action + actor threading (no behavior change)"`.

---

### Task 7: SSE → AgentEvent parser

**Files:**
- Create: `backend/app/services/gateway/sse.py`
- Test: `backend/tests/gateway/test_sse_parser.py`

**Interfaces:**
- Produces: `parse_sse_stream(aiter_str) -> AsyncIterator[AgentEvent]` consuming the raw SSE strings yielded by `stream_and_persist`. Maps `event:` → `AgentEvent.type ∈ {token, done, error, heartbeat, tool_started, tool_finished, action_required, unknown}`; accumulates multi-line `data:`; ignores `:`-comment lines; JSON-decodes once per frame; malformed/unknown → `AgentEvent("unknown", {...})` (never raises).

- [ ] **Step 1: Failing test** — feed a hand-built async iterator of frames (`"event: token\ndata: {\"text\": \"hi\"}\n\n"`, a multi-line data frame, a malformed frame, a `: comment` line) and assert the parsed `AgentEvent` sequence (malformed → `unknown`, comment skipped).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `parse_sse_stream`.**
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): SSE->AgentEvent parser"`.

---

### Task 8: AgentBridge

**Files:**
- Create: `backend/app/services/gateway/bridge.py`
- Test: `backend/tests/gateway/test_bridge.py`

**Interfaces:**
- Consumes: `active_agent_service` (Task 6), `parse_sse_stream` (Task 7), `GatewayThreadMap`, types.
- Produces: `AgentBridge` with:
  - `thread_for(session, binding, chat) -> AgentThread` — `INSERT ... ON CONFLICT(binding_id, chat_id) DO NOTHING` into `gateway_thread_map`; on miss `create_thread(title=f"IM {chat.connector}:{chat.chat_id}", character=binding.persona)` then insert; return the mapped thread.
  - `async submit_turn(session, binding, thread, text) -> AsyncIterator[AgentEvent]` — calls `stream_and_persist(thread_id=thread.id, content=text, requested_character=binding.persona, page_context=None, context_usage=None, accounting_date=None, model_selection=normalize_model_selection(None), yolo_mode=False, envelope="DESK_WORKFLOW", confirmed_cost_preview=False, actor=binding.desk_user)` piped through `parse_sse_stream`. (`stream_and_persist` manages its own session for persistence exactly as the HTTP endpoint does today; the passed `session` is used for the bridge's own reads/audit, not handed into the generator.)
  - `resume(session, binding, thread_id, message_id, action_id, decision) -> AgentMessage` — `resume_pending_action(..., actor=binding.desk_user, session=session)`.

**Session ownership contract:** the bridge never opens its own session. Callers (the dispatcher) own a `sessionmaker` and pass a `session` per inbound; transaction boundaries are committed by the dispatcher at each terminal step (see Tasks 12a–12d).

- [ ] **Step 1: Failing tests** — (a) `thread_for` is idempotent (two calls same `(binding, chat)` → same thread id; concurrent-ish second insert doesn't error); (b) with a binding whose `desk_user != "desk_user"` (e.g. `"trader_alice"`), `resume` passes that exact actor to `resume_pending_action` (spy/record the `actor` arg) — proving the gateway propagates the bound identity, not the web default. Use the real service against `db_session`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `bridge.py`.**
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): AgentBridge facade over service layer"`.

---

### Task 9: Card-action tokens & atomic claim

**Files:**
- Create: `backend/app/services/gateway/actions.py`
- Test: `backend/tests/gateway/test_actions.py`

**Interfaces:**
- Consumes: `GatewayCardAction`, settings.
- Produces:
  - `mint_card_action(session, *, binding, thread_id, message_id, action_id, decision, out_ref, settings) -> str` — idempotent via `INSERT ... ON CONFLICT(thread_id, message_id, action_id, decision) DO NOTHING`; returns existing or new signed token; sets `expires_at = now + gateway_card_action_ttl_s`; stores `out_connector/out_workspace_id/out_chat_id/out_message_id`.
  - `verify_and_claim(session, *, token, source_message_ref) -> GatewayCardAction | ClaimError` — checks signature, existence, unexpired; **all** of out_connector/workspace/chat/message must equal `source_message_ref`; then atomic `UPDATE ... SET status='resolving' WHERE token=? AND status='pending'`; returns the row on a winning claim, else a typed error (`expired` / `already_resolved` / `source_mismatch` / `bad_token`).
  - `mark_resolved/mark_failed/mark_unknown(session, row, *, resolved_by_binding_id=None)`.

- [ ] **Step 1: Write failing tests** — idempotent mint (two mints same key → same token, one row); winning vs losing concurrent claim (simulate by calling `verify_and_claim` twice → second returns `already_resolved`); source mismatch rejected; expired rejected.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `actions.py`.** Sign tokens with `itsdangerous` or `hmac` over a random id + the row id; keep ≥128-bit entropy in the random part.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): signed card-action tokens + atomic claim"`.

---

### Task 10: Approval-card builder (per-tool content contract, fail-closed)

**Files:**
- Create: `backend/app/services/gateway/cards.py`
- Test: `backend/tests/gateway/test_cards.py`

**Interfaces:**
- Consumes: `OutboundCard`, `CardAction`, settings, `mint_card_action` (Task 9).
- Produces:
  - `REQUIRED_FIELDS: dict[str, list[str]]` — per gated tool name, the required payload keys from the spec table (`book_position`, `book_hedge`, `quote_rfq`, `submit_rfq_for_approval`, `approve_rfq`, `reject_rfq`, `release_rfq`, cost-preview).
  - `build_approval_card(session, *, binding, thread_id, message_id, pending_action, out_ref: MessageRef, settings) -> OutboundCard` — `out_ref` is the **already-sent placeholder card's** `MessageRef` (see two-phase send in 11b), so `mint_card_action` can store `out_message_id` for later source validation. Looks up required fields by the action's tool name; if the tool is unregistered or any required field is missing → returns a **non-approvable** card (no `actions`) with a web deep-link; else builds Approve/Reject `CardAction`s via `mint_card_action`, truncating oversized values (keeping each required field's identity) with a deep-link.

- [ ] **Step 1: Define the authoritative `REQUIRED_FIELDS` map in production `cards.py`.** Read `app/services/deep_agent/hitl.py` to find the real pending-action payload key for each logical field; the map below is the target (rename the right-hand payload keys to match `hitl.py`). This constant lives in `cards.py` (single source of truth); the golden tests import and assert against it.

```python
# backend/app/services/gateway/cards.py
REQUIRED_FIELDS = {
    "book_position":          ["tool", "instrument", "side", "notional", "terms", "portfolio", "preview"],
    "book_hedge":             ["tool", "instrument", "side", "notional", "linked_ref", "preview"],
    "quote_rfq":              ["tool", "rfq_id", "underlying", "structure", "size", "level", "preview"],
    "submit_rfq_for_approval":["tool", "rfq_id", "summary", "approver_step"],
    "approve_rfq":            ["tool", "rfq_id", "summary", "state"],
    "reject_rfq":             ["tool", "rfq_id", "summary", "state"],
    "release_rfq":            ["tool", "rfq_id", "counterparty", "final_terms"],
    "__cost_preview__":       ["tool", "estimated_cost", "scope"],
}
IRREVERSIBLE = {"book_position", "book_hedge", "approve_rfq", "release_rfq"}
```

- [ ] **Step 2: Write golden tests** (import `REQUIRED_FIELDS`/`IRREVERSIBLE` from `cards.py`) — `book_position` full payload → approvable card containing every required field + 2 actions + irreversible warning; `book_position` missing `notional` → non-approvable (0 actions) + web link; `quote_rfq` → approvable, no irreversible warning; oversized param → truncated but still approvable.
- [ ] **Step 2b: Run → FAIL.**
- [ ] **Step 3: Implement `build_approval_card` in `cards.py`.** A tool whose name is absent from `REQUIRED_FIELDS`, or any present tool missing a required key in its payload, yields a non-approvable card. Add the irreversible-warning line when the tool ∈ `IRREVERSIBLE`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): fail-closed approval-card builder"`.

---

### Task 11a: Coalescer — message buffering, chunking, rate limit

**Files:**
- Create: `backend/app/services/gateway/coalescer.py`
- Test: `backend/tests/gateway/test_coalescer_text.py`

**Interfaces:**
- Consumes: connector (3), `AgentEvent`, settings.
- Produces: `StreamRenderer(connector, settings, *, sleep=asyncio.sleep, monotonic=time.monotonic, jitter=random.uniform)` (the clock/sleep/jitter injection points make rate-limit and backoff deterministically testable) + `async render_turn(session, binding, chat, agent_events)` covering ONLY text: buffer `token`s, flush via `update_message` every `flush_interval_ms`/`flush_chars` when `capabilities.supports_edit_in_place_message` else single send at `done`; chunk/truncate at `capabilities.max_message_chars` (code points); token-bucket per `(connector, chat)` (burst 5, refill 5/s) with jittered exponential backoff (base 0.5s cap 8s, ≤3 retries) on send failure. Pending-action / resume / revocation behavior is added in 11b–11d.

- [ ] **Step 1: Failing tests** — token buffering → one edited message; single-send fallback when `supports_edit_in_place_message=False`; output > `max_message_chars` → chunked; a send raising once → retried then succeeds (assert backoff invoked via injected fake clock/sleep).
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement the text path.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): coalescer text buffering/chunking/rate-limit"`.

---

### Task 11b: Coalescer — HITL approval-card rendering on done/action_required

**Files:**
- Modify: `backend/app/services/gateway/coalescer.py`
- Test: `backend/tests/gateway/test_coalescer_cards.py`

**Interfaces:**
- Consumes: `build_approval_card` (10), 11a.
- Produces: a **reusable helper** `async _send_approval_card(session, binding, thread_id, message_id, pending_action, chat) -> None` (also called by Task 11c for chained actions) doing a **two-phase send** (resolves the chicken-and-egg of needing the provider `message_id` before minting tokens): (1) `send_card` a **buttonless placeholder** (`idempotency_key = f"{message_id}:{action_id}"`) → get its `MessageRef`; (2) `build_approval_card(..., out_ref=that MessageRef)` which mints tokens stamped with `out_message_id`; (3) `update_card` to the actionable card. In `render_turn`, on the FIRST of `action_required`/`done` for a `(message_id)`, load the assistant message and call `_send_approval_card` per pending action. Idempotent mint + idempotent placeholder key dedupe re-renders (both `action_required` and `done`). On `error` send notice + `web_thread_link`.

- [ ] **Step 1: Failing tests** — `done` with one pending action → placeholder then actionable card (2 actions), tokens carry the placeholder's `out_message_id`; both `action_required` and `done` arriving → still one card and one token row (idempotent); `error` event → notice contains the deep-link.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): coalescer HITL card rendering"`.

---

### Task 11c: Coalescer — resume result rendering + card finalization

**Files:**
- Modify: `backend/app/services/gateway/coalescer.py`
- Test: `backend/tests/gateway/test_coalescer_resume.py`

**Interfaces:**
- Consumes: `actions.mark_resolved/mark_failed/mark_unknown` (9), `build_approval_card` (10).
- Produces: `async render_resume_result(session, binding, claimed_action: GatewayCardAction, clicked_card_ref, outcome) -> None` where `outcome` is one of `ResumeOk(agent_message)`, `ResumeRaised`. On `ResumeOk`: `mark_resolved(session, claimed_action, resolved_by_binding_id=binding.id)`, update the clicked card to a resolved (buttonless) state, post the resumed `content`, render any NEW `pending_actions` as fresh cards. On `ResumeRaised`: `mark_unknown(session, claimed_action)`, update the card to a buttonless "outcome unknown — verify in web desk" state with `web_thread_link`, **no retry button, no new token**.
  - `async render_claim_error(session, source_message_ref, error: ClaimError) -> None` — updates the clicked card idempotently to the matching state: `already_resolved` → "already handled", `expired` → "expired — ask the agent to re-send", `source_mismatch`/`bad_token` → generic "couldn't process this click". (Called by the dispatcher 12c on a losing/invalid claim.)
  - Chained pending actions on `ResumeOk` reuse the **same two-phase helper** as Task 11b (placeholder send → mint with `out_ref` → `update_card`).

- [ ] **Step 1: Failing tests** — `ResumeOk` → card row `status='resolved'`, resolved card rendered, follow-up content posted, **chained new pending action → fresh card whose new token row stores the chained placeholder's `out_message_id`** (proves reuse of the 11b two-phase helper); `ResumeRaised` → card row `status='unknown'`, buttonless unknown card, no new token minted; `render_claim_error` for each `ClaimError` variant → matching idempotent card update.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): coalescer resume rendering + card finalization (unknown-outcome no-retry)"`.

---

### Task 11d: Coalescer — revocation re-check before each send

**Files:**
- Modify: `backend/app/services/gateway/coalescer.py`
- Test: `backend/tests/gateway/test_coalescer_revocation.py`

**Interfaces:**
- Consumes: `active_binding` (5), `record_audit`.
- Produces: before each flush/card send in `render_turn`, re-check `active_binding(...)`; if the binding is no longer active, stop sending, audit `gateway.revoked_midflight`, and (if a prior message exists) edit it to a "session ended" notice.

- [ ] **Step 1: Failing test** — revoke the binding after the first flush; assert no further connector sends and an audit row `gateway.revoked_midflight`.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): coalescer revocation re-check + audit"`.

---

### Task 12a: Dispatcher — dedup state machine + session ownership

**Files:**
- Create: `backend/app/services/gateway/dispatch.py`
- Test: `backend/tests/gateway/test_dispatch_dedup.py`

**Interfaces:**
- Consumes: `GatewayInboundSeen`, settings, a `sessionmaker`.
- Produces: `Dispatcher(connector, bridge, renderer, sessionmaker, settings)` (owns sessions: opens one per inbound). `_claim_inbound(session, inbound) -> "new"|"skip"|"reclaim"`: insert `processing` with `owner_token`/`claimed_at`; existing `processed` → `skip`; `processing` fresh-lease → `skip`; `processing` expired-lease → `reclaim` (`attempts++`). `_finish_inbound(session, inbound)` → set `processed`. **Transaction boundary:** a `new`/`reclaim` claim is **committed immediately** (before any long-running turn/resume work) so a redelivery or another worker observes the fresh lease and skips; a `skip` rolls back/closes; `_finish_inbound` commits in a separate terminal transaction after processing.

- [ ] **Step 1: Failing tests** — first event → `new`; immediate redelivery (fresh lease) → `skip`; after lease expiry → `reclaim`; after `_finish_inbound` → `skip`; **two-session test: session A claims (`new`) and commits, then session B sees the committed lease and returns `skip`** (proves the claim is committed before processing).
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): dispatcher dedup state machine"`.

---

### Task 12b: Dispatcher — message path (group refuse, identity, enroll, validate, turn)

**Files:**
- Modify: `backend/app/services/gateway/dispatch.py`
- Test: `backend/tests/gateway/test_dispatch_message.py`

**Interfaces:**
- Consumes: identity (5), bridge (8), coalescer (11a–11d), 12a.
- Produces: `handle()` `kind=="message"` branch in order: group-chat refuse → resolve identity → code redeem/enroll (DM only) → unbound refuse → text validation (trim/length/non-text→help) → `bridge.thread_for` → `bridge.submit_turn` → `renderer.render_turn`. Each refusal still flips the dedup row to `processed`.

- [ ] **Step 1: Failing tests** — valid code in group chat → no binding + refusal (assert bridge not called); unbound message → refusal, bridge not called; empty/non-text → help message; bound text → `submit_turn`+`render_turn` called once; every refusal path → dedup row `processed`. Stub bridge/renderer to record calls.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): dispatcher message path + refusals"`.

---

### Task 12c: Dispatcher — card-action path (priority lane, claim→resume→finalize)

**Files:**
- Modify: `backend/app/services/gateway/dispatch.py`
- Test: `backend/tests/gateway/test_dispatch_card_action.py`

**Interfaces:**
- Consumes: `actions.verify_and_claim` (9), bridge (8), `renderer.render_resume_result` (11c).
- Produces: `handle()` `kind=="card_action"` branch (separate priority lane, never queued behind turns): `verify_and_claim` → on win, look up the binding, call `bridge.resume` wrapped so success → `render_resume_result(..., ResumeOk)` and a raise → `render_resume_result(..., ResumeRaised)`; on a losing/invalid claim call `renderer.render_claim_error(session, source_message_ref, error)` (11c) with the typed `ClaimError`.

- [ ] **Step 1: Failing tests** — valid token → `bridge.resume` called once → `render_resume_result(ResumeOk)`; `bridge.resume` raises → `render_resume_result(ResumeRaised)` (card→unknown); second click of same token → no second `resume`, "already handled" card; source-mismatch → refused, no `resume`.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): dispatcher card-action path + finalization"`.

---

### Task 12d: Dispatcher — per-chat serialization + backpressure

**Files:**
- Modify: `backend/app/services/gateway/dispatch.py`
- Test: `backend/tests/gateway/test_dispatch_backpressure.py`

**Interfaces:**
- Consumes: 12a–12c, settings.
- Produces: per-`(binding, chat)` asyncio serialization lock for the message (turn) path; bounded queue `gateway_max_queued_per_chat` (drop-newest-with-notice), age cap `gateway_queue_max_age_s` (drop-with-notice). Card-actions (12c) bypass this lane.

- [ ] **Step 1: Failing tests** — two turns same key run serially (ordering asserted via recorded start/finish); queue overflow → newest dropped + notice sent; stale queued turn dropped + notice; a card-action is NOT blocked by a saturated turn queue.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): dispatcher serialization + backpressure"`.

---

### Task 13: Feishu connector

**Files:**
- Create: `backend/app/services/gateway/connectors/feishu.py`
- Test: `backend/tests/gateway/test_feishu_translation.py` (pure mapping/card JSON)
- Test: `backend/tests/gateway/test_feishu_auth.py` (`verify_event`)
- Test: `backend/tests/gateway/test_feishu_lifecycle.py` (mocked `lark_oapi` client)
- Modify: `backend/pyproject.toml` (add `lark-oapi` dependency)

**Interfaces:**
- Consumes: types (2), base protocol (3), settings (4).
- Produces: `FeishuConnector(settings)` implementing `MessageConnector`: WebSocket long-connection (`lark_oapi` `ws.Client`) with backoff; `capabilities = ConnectorCapabilities(True, True, True, 10000)`. Pure translation functions (unit-tested without network): `feishu_event_to_inbound(event_dict) -> InboundMessage` (`external_account_id`=sender `open_id`, `workspace_id`=`tenant_key`, `chat_id`=`chat_id`, `chat_type` from `p2p`→`dm` else `group`, `provider_event_id`=header `event_id`), `feishu_card_action_to_inbound(callback_dict) -> InboundMessage` (`kind="card_action"`, token from card value), `outbound_card_to_feishu(card) -> dict`. Event authenticity via `feishu_verification_token` + AES `feishu_encrypt_key` in `verify_event(raw) -> bool`.

Implemented in three test-aligned commits (translation, auth, lifecycle) so each is reviewable in isolation.

- [ ] **Step 1: Failing translation tests** (`test_feishu_translation.py`, pure, no network) — message-event fixture → `InboundMessage` (`external_account_id`=`open_id`, `workspace_id`=`tenant_key`, `chat_id`, `provider_event_id`=`event_id`, `chat_type` p2p→dm); **card-action callback fixture → `CardActionInbound(source_message_ref=MessageRef(connector="feishu", workspace_id=tenant_key, chat_id=chat_id, message_id=message_id), token=<card value>)` inside `InboundMessage(kind="card_action")`; assert ALL four `source_message_ref` fields are populated** (so `verify_and_claim`'s source match can succeed); `OutboundCard` → Feishu card JSON, one element per action carrying ONLY the token.
- [ ] **Step 2: Run → FAIL. Step 3: Implement translation functions. Step 4: Run → PASS. Step 5: Commit** — `git commit -m "feat(gateway): Feishu event/card translation + card-action source mapping"`.
- [ ] **Step 6: Failing auth tests** (`test_feishu_auth.py`) — `verify_event` → `True` for correct verification token + AES-decryptable body; `False` for wrong token and undecryptable body.
- [ ] **Step 7: Run → FAIL. Step 8: Implement `verify_event`. Step 9: Run → PASS. Step 10: Commit** — `git commit -m "feat(gateway): Feishu event verification + decryption"`.
- [ ] **Step 11: Failing mocked-lifecycle tests** (`test_feishu_lifecycle.py`) — inject a fake ws-client (`ws_client_factory`) + `sleep`; assert `start(on_inbound)` wires inbound to the callback, a simulated disconnect triggers reconnect with backoff, `stop()` closes the client. No network.
- [ ] **Step 12: Run → FAIL. Step 13: Implement lifecycle** (`ws_client_factory=<real lark client>`, `sleep=asyncio.sleep`; guard the `lark_oapi` import so the module loads without the lib/creds; connect only when configured). **Step 14: Run → PASS** (`pytest tests/gateway/test_feishu_*.py -v`). **Step 15: Commit** — `git add backend/app/services/gateway/connectors/feishu.py backend/tests/gateway/test_feishu_lifecycle.py backend/pyproject.toml && git commit -m "feat(gateway): Feishu WS lifecycle + reconnect"`.

---

### Task 14: Runtime + single-worker lease

**Files:**
- Create: `backend/app/services/gateway/runtime.py`
- Test: `backend/tests/gateway/test_runtime.py`

**Interfaces:**
- Consumes: connectors registry, dispatcher (12a–12d), config (4), `GatewayWorkerLock`, a `sessionmaker`.
- Produces: `GatewayRuntime(settings, sessionmaker)` with `async start()` (acquire lease on `gateway_worker_lock` via conditional insert/update where lease expired; if not owner → skip starting connectors; else for each enabled connector build a `Dispatcher(connector, bridge, renderer, sessionmaker, settings)` and `start(on_inbound=dispatcher.handle)`; heartbeat-refresh the lease every `gateway_lock_lease_s/2`), `async stop()` (release lease, stop connectors), `health() -> dict` (`worker_lock_owner`, per-connector state+detail), `async reload()` (owner-only; re-read config, restart connectors). `acquire_worker_lock(session, owner_token, settings) -> bool` and `refresh/release` helpers. The heartbeat loop also calls `prune_inbound_seen(session, settings)` which deletes `gateway_inbound_seen` rows with `seen_at` older than `gateway_dedupe_ttl_s` (bounds table growth).

- [ ] **Step 1: Write failing tests** — first runtime acquires lock (`worker_lock_owner True`); a second runtime against the same DB does **not** acquire (`worker_lock_owner False`, connectors not started); an expired lease is reclaimable; **`prune_inbound_seen` deletes rows older than `gateway_dedupe_ttl_s` and leaves fresh rows** (seed an old + a fresh `gateway_inbound_seen` row, run prune, assert only the old one is gone). Use FakeConnector via `gateway_enabled_connectors="fake"` (register FakeConnector in the runtime's connector factory for tests).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `runtime.py`** + a connector factory keyed by name (`"feishu"` → FeishuConnector, `"fake"` → FakeConnector for tests).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): runtime + single-worker sentinel lease"`.

---

### Task 15a: HTTP — enrollment endpoint + rate limit + auth

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/gateway/test_http_enroll.py`

**Interfaces:** Consumes identity (5). Produces `POST /api/gateway/linking-codes` (`{persona}` → `{code, expires_at}`; persona validated; rate-limited `gateway_code_issue_per_min` → 429) behind the same desk-web auth dependency existing internal write endpoints use.

- [ ] **Step 1: Failing tests** — issue returns code+expiry; invalid persona → 422; past the per-minute cap → 429; missing auth → 401/403.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): linking-code issuance endpoint"`.

---

### Task 15b: HTTP — bindings list + revoke

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/gateway/test_http_bindings.py`

**Interfaces:** Consumes identity (5). Produces `GET /api/gateway/bindings?status=&limit=&cursor=` (ordered `bound_at DESC, id DESC`; `limit` default 50/max 200; cursor = base64 of `(bound_at,id)`) and `DELETE /api/gateway/bindings/{id}` (idempotent; unknown → 404). Same auth dependency.

- [ ] **Step 1: Failing tests** — seed >limit bindings → page 1 `next_cursor` → page 2 disjoint (cursor round-trips); status filter; revoke idempotent (200 then 200); unknown id → 404; missing auth → 401/403.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): bindings list + revoke endpoints"`.

---

### Task 15c: HTTP — health + reload

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/gateway/test_http_health_reload.py`

**Interfaces:** Consumes runtime (14). Produces `GET /api/gateway/health` (schema `{worker_lock_owner, connectors:[{name,state,detail}]}`) and `POST /api/gateway/reload` (owner-only → 200; non-owner → 409). Same auth dependency. The endpoints read the runtime from `app.state.gateway_runtime`.

> **Ordering note:** Task 15d (lifecycle wiring) attaches the real runtime to `app.state`. To keep Task 15c independently verifiable, its tests install a **fake runtime object on `app.state.gateway_runtime`** (a stub exposing `health()` and `reload()` with owner/non-owner variants) rather than relying on 15d.

- [ ] **Step 1: Failing tests** — with a fake runtime on `app.state`: health returns the schema; reload as owner → 200 (records a `reload()` call); reload when the fake reports non-owner → 409.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): health + reload endpoints"`.

---

### Task 15d: App lifecycle wiring (runtime start/stop)

**Files:**
- Modify: `backend/app/main.py` (start `GatewayRuntime` after the startup-recovery block in `create_app`; stop on shutdown)
- Test: `backend/tests/gateway/test_http_lifecycle.py`

**Interfaces:** Consumes runtime (14). Produces: runtime constructed with the app's `sessionmaker`, `start()`ed at app startup, `stop()`ed at shutdown; FakeConnector enabled in test config so startup actually wires a connector.

- [ ] **Step 1: Failing test** — through the `client` lifespan, `GET /api/gateway/health` shows `worker_lock_owner: true` and the fake connector `up`; after lifespan exit the runtime released the lock (assert lock row free / reacquirable).
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement.** **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gateway): runtime lifecycle wiring in create_app"`.

---

### Task 16: End-to-end vertical slice (FakeConnector)

**Files:**
- Test: `backend/tests/gateway/test_e2e_vertical_slice.py`

**Interfaces:**
- Consumes: runtime + dispatcher + bridge + identity + a real agent thread.

- [ ] **Step 1: Write the end-to-end test**

```python
# Drive the full path with FakeConnector, no network:
# 1. issue a linking code (HTTP), DM it via FakeConnector.feed_inbound -> binding created
# 2. feed a read-only query -> assert a streamed answer lands in connector.outbox
# 3. feed a request that triggers a HITL booking -> assert an approval card with 2 actions
# 4. feed the Approve card-action (token from the card) -> assert booking executed
#    AND an AuditEvent with actor == "desk_user" (the bound desk_user) exists
# 5. feed an unbound user's message -> assert refusal + agent never called
```

**Concrete fixture (no ambiguity):** define a `fake_booking_tool` in `backend/tests/gateway/conftest.py` — a recorded no-op standing in for `book_position` that, on first turn, causes the assistant message to carry a `pending_actions` entry with a full `book_position` payload (per Task 10 `REQUIRED_FIELDS`), and on `resume(confirm)` records the booking call and returns a resumed `AgentMessage`. Monkeypatch it into the agent's tool set for this test. The assertions stay on the gateway plumbing (card built, token claimed, `resume` invoked, audit `actor`), since agent internals are tested elsewhere.

- [ ] **Step 2: Run → FAIL** (until all wiring present).
- [ ] **Step 3: Verify the concrete wiring checks** (no new features). Confirm each: runtime factory maps `"fake"`→FakeConnector; `Dispatcher` receives the test `sessionmaker`; `bridge.submit_turn` reaches `stream_and_persist` with `actor=binding.desk_user`; the approval card's token round-trips through `verify_and_claim`. If any check reveals missing behavior, **stop and file it as a new numbered task** (its own failing test + commit) rather than patching inline here; then resume this e2e test.
- [ ] **Step 4: Run the full gateway suite + a no-`.env` sanity run**

Run: `cd backend && pytest tests/gateway -v` then (in a no-`.env` worktree) `pytest -q`.
Expected: gateway suite PASS; full suite green.

- [ ] **Step 5: Commit** — `git commit -m "test(gateway): end-to-end vertical slice (enroll->query->HITL approve->book)"`.

---

## Self-Review

**Spec coverage:** Tables (T1) ✓; types (T2) ✓; protocol+Fake (T3) ✓; config/deep-links (T4) ✓; identity/transfer/revoke (T5) ✓; actor threading + resume extraction (T6) ✓; SSE/AgentEvent grammar (T7) ✓; AgentBridge + thread-per-(binding,chat) (T8) ✓; idempotent mint + atomic claim + source validation (T9) ✓; fail-closed per-tool card contract (T10) ✓; coalescer text/limits (T11a), HITL cards (T11b), resume + finalization/unknown-no-retry (T11c), revocation re-check (T11d) ✓; dispatcher dedup-state-machine (T12a), message path/refusals (T12b), card-action path/finalize (T12c), serialization/backpressure (T12d) ✓; Feishu identity mapping + event auth + card translation (T13) ✓; single-worker lease + health + reload (T14) ✓; HTTP contracts + auth + lifecycle (T15) ✓; full-parity vertical slice incl. unbound-refuse + audit actor (T16) ✓. Unknown-outcome-no-retry on resume failure → T11c `render_resume_result(ResumeRaised)` + T9 `mark_unknown`, driven by T12c.

**Type consistency:** `mint_card_action` key `(thread_id, message_id, action_id, decision)` matches `uq_gateway_card_action_action` (T1) and `verify_and_claim` (T9). `submit_turn(session, binding, thread, text)` and `resume(session, ...)` carry an explicit `session` (T8) supplied by the dispatcher's `sessionmaker` (T12a) and runtime (T14). `render_resume_result(session, binding, claimed_action, clicked_card_ref, outcome)` signature consistent across T11c and T12c. `actor` kwarg name consistent across T6/T8.

**Placeholder scan:** Where exact upstream shapes are environment-specific (pending-action payload field names in T10, persona names in T5, bookable fixture in T16), the task names the source file to read rather than inventing names — intentional, not a placeholder.
