from __future__ import annotations

from datetime import timedelta

from sqlalchemy import inspect, text

from app import database
from app.config import Settings
from app.models import AgentMessage, AgentThread, Portfolio, Position


def test_workflow_routing_models_expose_c_mig_1_tables():
    from app.models import (
        AgentSession,
        AgentTask,
        ArtifactEvidenceRef,
        ContextPack,
        ContextPackPayload,
        DomainEvent,
        SessionArtifact,
        Workflow,
    )

    assert Workflow.__tablename__ == "workflows"
    assert AgentSession.__tablename__ == "agent_sessions"
    assert AgentTask.__tablename__ == "agent_tasks"
    assert SessionArtifact.__tablename__ == "session_artifacts"
    assert ArtifactEvidenceRef.__tablename__ == "artifact_evidence_refs"
    assert ContextPackPayload.__tablename__ == "context_pack_payloads"
    assert ContextPack.__tablename__ == "context_packs"
    assert DomainEvent.__tablename__ == "domain_events"


def test_c_mig_1_tables_and_columns_exist_in_initialized_db(session):
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    expected_tables = {
        "workflows",
        "agent_sessions",
        "agent_tasks",
        "session_artifacts",
        "artifact_evidence_refs",
        "context_pack_payloads",
        "context_packs",
        "domain_events",
    }
    assert expected_tables <= tables

    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in expected_tables
    }
    assert {
        "thread_id",
        "title",
        "intent",
        "status",
        "opened_by",
        "canonical_snapshot_ids",
    } <= columns["workflows"]
    assert {
        "workflow_id",
        "persona",
        "episode_id",
        "checkpointer_key",
        "current_task_id",
        "lease_acquired_at",
    } <= columns["agent_sessions"]
    assert {
        "workflow_id",
        "task_type",
        "inputs",
        "depends_on",
        "assigned_persona",
        "context_pack_id",
        "output_artifact_id",
    } <= columns["agent_tasks"]
    assert {
        "workflow_id",
        "kind",
        "schema_version",
        "payload",
        "context_pack_id",
        "pinned",
        "superseded_by",
    } <= columns["session_artifacts"]
    assert {"schema_version", "payload", "actor"} <= columns["domain_events"]


def test_c_mig_1_nullable_legacy_columns_keep_old_inserts_working(session):
    thread = AgentThread(title="legacy", character="trader")
    session.add(thread)
    session.flush()
    message = AgentMessage(
        thread_id=thread.id,
        role="user",
        character="trader",
        content="hello",
        meta={},
    )
    session.add(message)
    session.commit()

    assert thread.active_workflow_id is None
    assert message.workflow_id is None
    assert message.session_id is None


def test_c_mig_1_position_columns_exist_and_default(session):
    cols = {column.name: column for column in Position.__table__.columns}
    assert cols["version"].default.arg == 1
    assert cols["kwargs_migrated_at"].nullable is True

    db_cols = {column["name"] for column in inspect(session.bind).get_columns("positions")}
    assert {"version", "kwargs_migrated_at"} <= db_cols


def test_incremental_schema_repairs_structured_term_nullability(tmp_path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'stale.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Legacy Book", base_currency="USD")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={},
            engine_name="BlackScholesEngine",
            engine_kwargs={},
            quantity=1,
            entry_price=0,
            status="open",
        )
        session.add(position)
        session.commit()
        position_id = position.id

    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE option_core_terms"))
        connection.execute(text("DROP TABLE snowball_terms"))
        connection.execute(
            text(
                "CREATE TABLE option_core_terms ("
                "position_id INTEGER NOT NULL, "
                "strike FLOAT, "
                "expiry_date DATE NOT NULL, "
                "option_type VARCHAR(8), "
                "side VARCHAR(8) NOT NULL, "
                "currency VARCHAR(8) NOT NULL, "
                "notional FLOAT, "
                "PRIMARY KEY (position_id), "
                "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE snowball_terms ("
                "position_id INTEGER NOT NULL, "
                "initial_price FLOAT NOT NULL, "
                "ki_barrier FLOAT NOT NULL, "
                "coupon FLOAT NOT NULL, "
                "start_date DATE NOT NULL, "
                "knocked_in BOOLEAN DEFAULT 0 NOT NULL, "
                "ki_observation VARCHAR(20) NOT NULL, "
                "payoff_kind VARCHAR(40) NOT NULL, "
                "legacy_kwargs JSON, "
                "PRIMARY KEY (position_id), "
                "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO option_core_terms "
                "(position_id, strike, expiry_date, option_type, side, currency, notional) "
                "VALUES (:position_id, 100.0, '2026-12-31', 'call', 'long', 'USD', NULL)"
            ),
            {"position_id": position_id},
        )
        connection.execute(
            text(
                "INSERT INTO snowball_terms "
                "(position_id, initial_price, ki_barrier, coupon, start_date, "
                "knocked_in, ki_observation, payoff_kind, legacy_kwargs) "
                "VALUES (:position_id, 100.0, 80.0, 0.1, '2026-01-02', "
                "0, 'daily', 'snowball', NULL)"
            ),
            {"position_id": position_id},
        )

    database.init_db()

    option_columns = {
        column["name"]: column
        for column in inspect(database.engine).get_columns("option_core_terms")
    }
    snowball_columns = {
        column["name"]: column
        for column in inspect(database.engine).get_columns("snowball_terms")
    }
    assert option_columns["expiry_date"]["nullable"] is True
    assert snowball_columns["coupon"]["nullable"] is True
    with database.engine.connect() as connection:
        option_rows = connection.execute(
            text("SELECT position_id, expiry_date FROM option_core_terms")
        ).all()
        snowball_rows = connection.execute(
            text("SELECT position_id, coupon FROM snowball_terms")
        ).all()
    assert option_rows == [(position_id, "2026-12-31")]
    assert snowball_rows == [(position_id, 0.1)]


def test_agent_sessions_enforce_one_active_session_per_persona(session):
    from app.models import AgentSession, Workflow

    thread = AgentThread(title="wf", character="trader")
    session.add(thread)
    session.flush()
    workflow = Workflow(
        thread_id=thread.id,
        title="workflow",
        intent="ad_hoc",
        status="active",
        opened_by="router",
        canonical_snapshot_ids={"scope_kind": "ad_hoc"},
    )
    session.add(workflow)
    session.flush()
    session.add(
        AgentSession(
            workflow_id=workflow.id,
            persona="trader",
            episode_id=1,
            status="active",
            checkpointer_key="wf:trader:1",
        )
    )
    session.commit()

    session.add(
        AgentSession(
            workflow_id=workflow.id,
            persona="trader",
            episode_id=2,
            status="active",
            checkpointer_key="wf:trader:2",
        )
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
    else:  # pragma: no cover - failure path only
        raise AssertionError("second active trader session should violate index")

    with session.bind.connect() as connection:
        index_rows = connection.execute(
            text("PRAGMA index_list('agent_sessions')")
        ).mappings()
        assert any(
            row["name"] == "uq_agent_sessions_active_workflow_persona"
            for row in index_rows
        )


def test_backfill_thread_creates_meta_and_domain_workflow(session):
    from app.models import AgentSession, ContextPack, DomainEvent, Workflow
    from app.services.deep_agent.workflow_state import ensure_thread_workflow_state

    thread = AgentThread(title="Legacy Thread", character="trader")
    session.add(thread)
    session.flush()
    msg = AgentMessage(
        thread_id=thread.id,
        role="user",
        character="trader",
        content="existing",
        meta={},
    )
    session.add(msg)
    session.commit()

    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()

    workflows = (
        session.query(Workflow)
        .filter(Workflow.thread_id == thread.id)
        .order_by(Workflow.id)
        .all()
    )
    assert [workflow.intent for workflow in workflows] == [
        "workspace_meta",
        "ad_hoc",
    ]
    assert [workflow.canonical_snapshot_ids["scope_kind"] for workflow in workflows] == [
        "workspace_meta",
        "ad_hoc",
    ]
    assert all(workflow.canonical_snapshot_ids["captured_at"] for workflow in workflows)
    assert thread.active_workflow_id == state.domain_workflow_id

    router = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == state.meta_workflow_id,
            AgentSession.persona == "router",
        )
        .one()
    )
    orchestrator = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == state.domain_workflow_id,
            AgentSession.persona == "orchestrator",
        )
        .one()
    )
    assert router.checkpointer_key == f"thread:{thread.id}:router"
    assert orchestrator.checkpointer_key == str(thread.id)

    refreshed_msg = session.get(AgentMessage, msg.id)
    assert refreshed_msg.workflow_id == state.domain_workflow_id
    assert refreshed_msg.session_id == orchestrator.id
    assert (
        session.query(ContextPack)
        .filter_by(workflow_id=state.domain_workflow_id)
        .count()
        == 1
    )
    assert session.query(DomainEvent).filter_by(
        workflow_id=state.domain_workflow_id,
        kind="workflow_opened",
    ).count() == 1
    snapshot_events = (
        session.query(DomainEvent)
        .filter(DomainEvent.kind == "snapshot_captured")
        .order_by(DomainEvent.workflow_id)
        .all()
    )
    assert [event.workflow_id for event in snapshot_events] == [
        state.meta_workflow_id,
        state.domain_workflow_id,
    ]
    assert [event.payload["snapshot"]["scope_kind"] for event in snapshot_events] == [
        "workspace_meta",
        "ad_hoc",
    ]


def test_backfill_ignores_stale_workflows_from_reused_thread_id(session):
    from app.models import AgentSession, Workflow
    from app.services.deep_agent.workflow_state import ensure_thread_workflow_state

    thread = AgentThread(title="Reused Thread", character="trader")
    session.add(thread)
    session.flush()
    stale_opened_at = thread.created_at - timedelta(hours=1)
    stale_workflow = Workflow(
        thread_id=thread.id,
        title="deleted prior workflow",
        intent="ad_hoc",
        status="active",
        opened_by="system",
        opened_at=stale_opened_at,
        canonical_snapshot_ids={"scope_kind": "ad_hoc"},
    )
    session.add(stale_workflow)
    session.flush()
    stale_session = AgentSession(
        workflow_id=stale_workflow.id,
        persona="orchestrator",
        episode_id=1,
        status="active",
        checkpointer_key=str(thread.id),
        opened_at=stale_opened_at,
    )
    session.add(stale_session)
    session.commit()

    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()

    new_orchestrator = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == state.domain_workflow_id,
            AgentSession.persona == "orchestrator",
        )
        .one()
    )
    assert state.domain_workflow_id != stale_workflow.id
    assert new_orchestrator.id != stale_session.id
    assert new_orchestrator.checkpointer_key != str(thread.id)
    assert new_orchestrator.checkpointer_key.startswith(
        f"workflow:{state.domain_workflow_id}:persona:orchestrator:episode:1"
    )
    assert thread.active_workflow_id == state.domain_workflow_id


def test_backfill_thread_is_idempotent(session):
    from app.models import AgentSession, ContextPack, Workflow
    from app.services.deep_agent.workflow_state import ensure_thread_workflow_state

    thread = AgentThread(title="Legacy Thread", character="trader")
    session.add(thread)
    session.commit()

    first = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    second = ensure_thread_workflow_state(session, thread.id)
    session.commit()

    assert first == second
    assert session.query(Workflow).filter(Workflow.thread_id == thread.id).count() == 2
    assert (
        session.query(AgentSession)
        .join(Workflow)
        .filter(Workflow.thread_id == thread.id)
        .count()
        == 2
    )
    assert (
        session.query(ContextPack)
        .join(Workflow)
        .filter(Workflow.thread_id == thread.id)
        .count()
        == 1
    )


def test_legacy_agent_message_insert_dual_writes_active_workflow(session):
    from app.models import AgentMessage, AgentSession, Workflow
    from app.services.deep_agent.workflow_state import ensure_thread_workflow_state

    thread = AgentThread(title="Scoped Thread", character="trader")
    session.add(thread)
    session.commit()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()

    message = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        character="trader",
        content="new",
        meta={},
    )
    session.add(message)
    session.commit()

    orchestrator = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == state.domain_workflow_id,
            AgentSession.persona == "orchestrator",
        )
        .one()
    )
    assert message.workflow_id == state.domain_workflow_id
    assert message.session_id == orchestrator.id
    assert session.get(Workflow, message.workflow_id).intent == "ad_hoc"


def test_agent_service_create_thread_bootstraps_workflow_state(
    session, settings, monkeypatch
):
    from app.models import AgentSession, Workflow
    from app.services import agents as agents_module

    monkeypatch.setattr(
        agents_module, "build_agent_model", lambda *args, **kwargs: None
    )
    service = agents_module.AgentService(settings=settings)

    thread = service.create_thread(
        session, title="New Scoped Thread", character="trader"
    )
    session.commit()

    assert thread.active_workflow_id is not None
    workflows = (
        session.query(Workflow)
        .filter(Workflow.thread_id == thread.id)
        .order_by(Workflow.id)
        .all()
    )
    assert [workflow.intent for workflow in workflows] == [
        "workspace_meta",
        "ad_hoc",
    ]
    assert (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == thread.active_workflow_id,
            AgentSession.persona == "orchestrator",
            AgentSession.status == "active",
        )
        .count()
        == 1
    )


def test_agent_service_respond_backfills_legacy_thread_before_message_write(
    session, settings, monkeypatch
):
    from app.services import agents as agents_module

    monkeypatch.setattr(
        agents_module, "build_agent_model", lambda *args, **kwargs: None
    )
    service = agents_module.AgentService(settings=settings)

    thread = AgentThread(title="Legacy Respond", character="trader")
    session.add(thread)
    session.commit()

    service.respond(session, thread, "hello")
    session.commit()

    messages = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assert thread.active_workflow_id is not None
    assert all(
        message.workflow_id == thread.active_workflow_id for message in messages
    )
    assert all(message.session_id is not None for message in messages)


def test_stream_endpoint_backfills_legacy_thread_before_user_message_write(
    settings, monkeypatch
):
    from fastapi.testclient import TestClient

    from app import database
    from app.main import create_app
    from app.services import agents as agents_module

    monkeypatch.setattr(
        agents_module, "build_agent_model", lambda *args, **kwargs: None
    )
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as db_session:
        thread = AgentThread(title="Legacy Stream", character="trader")
        db_session.add(thread)
        db_session.commit()
        thread_id = thread.id

    with TestClient(create_app(settings=settings)) as api:
        response = api.post(
            f"/api/chat/threads/{thread_id}/messages/stream",
            json={"content": "hello", "character": "auto"},
        )

    assert response.status_code == 200
    assert "event: done" in response.text

    with database.SessionLocal() as db_session:
        thread = db_session.get(AgentThread, thread_id)
        messages = (
            db_session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id)
            .order_by(AgentMessage.id)
            .all()
        )
    assert [message.role for message in messages] == ["user", "assistant"]
    assert thread.active_workflow_id is not None
    assert all(
        message.workflow_id == thread.active_workflow_id for message in messages
    )
    assert all(message.session_id is not None for message in messages)
