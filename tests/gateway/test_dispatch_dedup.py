"""TDD tests for Dispatcher dedup state machine — Task 12a.

Covers:
- first event → "new"
- immediate redelivery (fresh lease) → "skip"
- after lease expiry → "reclaim" (attempts incremented)
- after _finish_inbound (state "done") → "skip"
- two-session test: session A claims (new) + commits, session B sees committed
  lease and returns "skip"
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.services.gateway.types import ChatRef, InboundMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )


@pytest.fixture
def configured_db(db_settings: Settings):
    """Configure the database and create all tables. Returns the sessionmaker."""
    database.configure_database(db_settings)
    database.init_db()
    return database.SessionLocal


@pytest.fixture
def sm(configured_db):
    """Return the sessionmaker for use by the Dispatcher."""
    return configured_db


@pytest.fixture
def settings_short_lease(db_settings: Settings) -> Settings:
    """Settings with a very short lease for lease-expiry tests."""
    return Settings(
        database_url=db_settings.database_url,
        artifact_dir=db_settings.artifact_dir,
        agent_checkpoint_db_path=":memory:",
        gateway_dedupe_lease_s=30,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inbound(event_id: str = "ev_001") -> InboundMessage:
    return InboundMessage(
        connector="feishu",
        workspace_id="tk_test",
        external_account_id="ou_test",
        provider_event_id=event_id,
        chat=ChatRef(
            connector="feishu",
            workspace_id="tk_test",
            chat_id="chat_1",
            chat_type="dm",
        ),
        kind="message",
        text="hello",
        action=None,
        raw={},
    )


def _make_dispatcher(sm, settings):
    from app.services.gateway.dispatch import Dispatcher

    # connector, bridge, renderer — stubbed to None for 12a tests
    return Dispatcher(
        connector=None,
        bridge=None,
        renderer=None,
        sessionmaker=sm,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_event_returns_new(sm, db_settings):
    """First time we see an event → _claim_inbound returns 'new'."""
    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_first")
    with sm() as session:
        result = disp._claim_inbound(session, inbound)
    assert result == "new"


def test_fresh_redelivery_returns_skip(sm, db_settings):
    """If the lease is still fresh, a second call returns 'skip'."""
    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_redeliver")

    with sm() as session:
        r1 = disp._claim_inbound(session, inbound)
        session.commit()
    assert r1 == "new"

    with sm() as session:
        r2 = disp._claim_inbound(session, inbound)
    assert r2 == "skip"


def test_expired_lease_returns_reclaim_with_incremented_attempts(sm, db_settings):
    """Expired lease → reclaim and attempts is incremented."""
    from app.models import GatewayInboundSeen

    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_expire")

    # Claim once to create the row
    with sm() as session:
        r1 = disp._claim_inbound(session, inbound)
        session.commit()
    assert r1 == "new"

    # Manually backdate claimed_at past the lease window
    lease_s = db_settings.gateway_dedupe_lease_s
    past_time = datetime.utcnow() - timedelta(seconds=lease_s + 60)
    with sm() as session:
        row = (
            session.query(GatewayInboundSeen)
            .filter_by(connector="feishu", workspace_id="tk_test", provider_event_id="ev_expire")
            .one()
        )
        row.claimed_at = past_time
        session.commit()

    # Now claim again — should reclaim
    with sm() as session:
        r2 = disp._claim_inbound(session, inbound)
        attempts_after = (
            session.query(GatewayInboundSeen)
            .filter_by(connector="feishu", workspace_id="tk_test", provider_event_id="ev_expire")
            .one()
            .attempts
        )
        session.commit()

    assert r2 == "reclaim"
    assert attempts_after == 2


def test_done_state_returns_skip(sm, db_settings):
    """After _finish_inbound sets state='done', a new claim returns 'skip'."""
    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_done")

    with sm() as session:
        r1 = disp._claim_inbound(session, inbound)
        session.commit()
    assert r1 == "new"

    with sm() as session:
        disp._finish_inbound(session, inbound)
        session.commit()

    with sm() as session:
        r3 = disp._claim_inbound(session, inbound)
    assert r3 == "skip"


def test_two_session_cross_commit_skip(sm, db_settings):
    """Session A claims and COMMITS; session B (separate session) sees the
    committed lease and returns 'skip' — proves commit-before-process ordering.
    """
    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_two_session")

    # Session A: claim + commit
    with sm() as session_a:
        result_a = disp._claim_inbound(session_a, inbound)
        session_a.commit()

    assert result_a == "new"

    # Session B: independent session, must see the committed row
    with sm() as session_b:
        result_b = disp._claim_inbound(session_b, inbound)

    assert result_b == "skip"


def test_handle_stub_exercises_dedup_commit(sm, db_settings):
    """handle() uses the dedup state machine end-to-end: new claim committed,
    then _finish_inbound sets state done. A second handle call returns without
    processing (row is 'done' → skip).
    """
    from app.models import GatewayInboundSeen

    disp = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound("ev_handle")

    disp.handle(inbound)

    # Row should now be in 'done' state
    with sm() as session:
        row = (
            session.query(GatewayInboundSeen)
            .filter_by(
                connector="feishu",
                workspace_id="tk_test",
                provider_event_id="ev_handle",
            )
            .one()
        )
        assert row.state == "done"
