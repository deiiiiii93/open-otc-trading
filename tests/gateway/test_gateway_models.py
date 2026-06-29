"""Tests for IM gateway DB schema — migration + ORM models (Task 1)."""
from __future__ import annotations

import pytest
import sqlalchemy
from sqlalchemy import create_engine, inspect


def test_gateway_tables_exist_after_metadata_create(tmp_path):
    """metadata.create_all() produces all 6 gateway tables in a fresh DB."""
    from app import models
    from app.database import Base, _create_engine

    eng = _create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    names = set(inspect(eng).get_table_names())
    assert {
        "gateway_binding",
        "gateway_linking_code",
        "gateway_thread_map",
        "gateway_inbound_seen",
        "gateway_card_action",
        "gateway_worker_lock",
    } <= names


def test_binding_active_partial_unique(session):
    """Inserting two active rows with the same (provider, external_account_id,
    workspace_id) must raise IntegrityError.
    """
    from app.models import GatewayBinding

    a = GatewayBinding(
        provider="feishu",
        external_account_id="ou_1",
        workspace_id="tk_1",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(a)
    session.commit()

    dup = GatewayBinding(
        provider="feishu",
        external_account_id="ou_1",
        workspace_id="tk_1",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(dup)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.commit()


def test_binding_revoked_not_constrained(session):
    """Revoked rows with the same triple are allowed (partial index is WHERE
    status='active' only).
    """
    from app.models import GatewayBinding

    for _ in range(2):
        session.add(
            GatewayBinding(
                provider="feishu",
                external_account_id="ou_2",
                workspace_id="tk_1",
                desk_user="desk_user",
                persona="trader",
                status="revoked",
            )
        )
    # Should not raise
    session.commit()


def test_thread_map_unique_binding_chat(session):
    """(binding_id, chat_id) must be unique in gateway_thread_map."""
    from app.models import GatewayBinding, GatewayThreadMap

    binding = GatewayBinding(
        provider="feishu",
        external_account_id="ou_3",
        workspace_id="tk_1",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(binding)
    session.flush()

    m1 = GatewayThreadMap(binding_id=binding.id, chat_id="chat_A", thread_id=1)
    m2 = GatewayThreadMap(binding_id=binding.id, chat_id="chat_A", thread_id=2)
    session.add(m1)
    session.flush()
    session.add(m2)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()


def test_inbound_seen_unique_triple(session):
    """(connector, workspace_id, provider_event_id) must be unique."""
    from app.models import GatewayInboundSeen

    s1 = GatewayInboundSeen(
        connector="feishu",
        workspace_id="tk_1",
        provider_event_id="ev_001",
        state="processing",
        owner_token="tok_a",
    )
    s2 = GatewayInboundSeen(
        connector="feishu",
        workspace_id="tk_1",
        provider_event_id="ev_001",
        state="done",
        owner_token="tok_b",
    )
    session.add(s1)
    session.flush()
    session.add(s2)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()


def test_card_action_unique_constraint(session):
    """(thread_id, message_id, action_id, decision) must be unique."""
    from app.models import GatewayBinding, GatewayCardAction
    from datetime import datetime, timedelta

    binding = GatewayBinding(
        provider="feishu",
        external_account_id="ou_4",
        workspace_id="tk_1",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(binding)
    session.flush()

    expires = datetime.utcnow() + timedelta(hours=1)
    ca1 = GatewayCardAction(
        token="tok_1",
        out_connector="feishu",
        out_workspace_id="tk_1",
        out_chat_id="chat_1",
        out_message_id="msg_1",
        binding_id=binding.id,
        thread_id=10,
        message_id=20,
        action_id="confirm_trade",
        decision="confirm",
        expires_at=expires,
        status="pending",
    )
    ca2 = GatewayCardAction(
        token="tok_2",
        out_connector="feishu",
        out_workspace_id="tk_1",
        out_chat_id="chat_1",
        out_message_id="msg_1",
        binding_id=binding.id,
        thread_id=10,
        message_id=20,
        action_id="confirm_trade",
        decision="confirm",
        expires_at=expires,
        status="pending",
    )
    session.add(ca1)
    session.flush()
    session.add(ca2)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()


def test_migration_creates_same_schema_as_metadata(tmp_path, monkeypatch):
    """Alembic upgrade to 0032 creates identical tables + constraints as ORM."""
    from alembic.config import Config
    from alembic import command
    from app.config import Settings
    import os

    url = f"sqlite:///{tmp_path}/m.db"
    # env.py calls get_settings(); patch configure_settings to route the
    # migration to our tmp file regardless of any session-fixture override.
    from app import config as _cfg_mod

    monkeypatch.setattr(
        _cfg_mod, "get_settings", lambda: Settings(database_url=url)
    )

    worktree_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cfg = Config(os.path.join(worktree_root, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0037_gateway_tables")

    eng = create_engine(url)
    insp = inspect(eng)
    names = set(insp.get_table_names())
    assert {
        "gateway_binding",
        "gateway_linking_code",
        "gateway_thread_map",
        "gateway_inbound_seen",
        "gateway_card_action",
        "gateway_worker_lock",
    } <= names

    # Partial unique index on gateway_binding must be present
    assert any(
        ix["name"] == "uq_gateway_binding_active"
        for ix in insp.get_indexes("gateway_binding")
    )

    # FK from gateway_card_action → gateway_binding must be present
    fks = insp.get_foreign_keys("gateway_card_action")
    assert any(fk["referred_table"] == "gateway_binding" for fk in fks)

    # FK from gateway_thread_map → gateway_binding must be present
    fks_tm = insp.get_foreign_keys("gateway_thread_map")
    assert any(fk["referred_table"] == "gateway_binding" for fk in fks_tm)
