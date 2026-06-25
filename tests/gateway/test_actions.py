"""Tests for gateway card-action token minting and atomic claim (Task 9)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import GatewayBinding
from app.services.gateway.types import MessageRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_binding(session) -> GatewayBinding:
    b = GatewayBinding(
        provider="feishu",
        external_account_id="ou_test",
        workspace_id="tk_test",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(b)
    session.flush()
    return b


def _make_out_ref() -> MessageRef:
    return MessageRef(
        connector="feishu",
        workspace_id="tk_test",
        chat_id="chat_001",
        message_id="msg_card_001",
    )


def _mint_defaults(session, binding, settings, *, out_ref=None):
    """Convenience wrapper with fixed logical key."""
    from app.services.gateway.actions import mint_card_action
    return mint_card_action(
        session,
        binding=binding,
        thread_id=1,
        message_id=10,
        action_id="confirm_trade",
        decision="confirm",
        out_ref=out_ref or _make_out_ref(),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMintCardAction:
    def test_returns_token_string(self, db_session, db_settings):
        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)
        assert isinstance(token, str)
        assert len(token) >= 22  # >=128-bit encoded is always longer

    def test_token_high_entropy(self, db_session, db_settings):
        """Two independent mints for DIFFERENT keys must produce different tokens."""
        b = _make_binding(db_session)
        from app.services.gateway.actions import mint_card_action
        t1 = mint_card_action(
            db_session, binding=b, thread_id=1, message_id=10,
            action_id="confirm_trade", decision="confirm",
            out_ref=_make_out_ref(), settings=db_settings,
        )
        t2 = mint_card_action(
            db_session, binding=b, thread_id=1, message_id=10,
            action_id="confirm_trade", decision="dismiss",  # different decision
            out_ref=_make_out_ref(), settings=db_settings,
        )
        assert t1 != t2

    def test_idempotent_same_key_returns_same_token(self, db_session, db_settings):
        """Minting twice with the same (thread_id, message_id, action_id, decision)
        must return the EXACT same token and leave exactly ONE row in the DB.
        """
        from app.models import GatewayCardAction
        from sqlalchemy import select

        b = _make_binding(db_session)
        t1 = _mint_defaults(db_session, b, db_settings)
        t2 = _mint_defaults(db_session, b, db_settings)

        assert t1 == t2

        rows = db_session.execute(
            select(GatewayCardAction).where(
                GatewayCardAction.thread_id == 1,
                GatewayCardAction.message_id == 10,
                GatewayCardAction.action_id == "confirm_trade",
                GatewayCardAction.decision == "confirm",
            )
        ).scalars().all()
        assert len(rows) == 1

    def test_stores_out_ref_fields(self, db_session, db_settings):
        from app.models import GatewayCardAction
        from sqlalchemy import select

        b = _make_binding(db_session)
        out_ref = _make_out_ref()
        _mint_defaults(db_session, b, db_settings, out_ref=out_ref)

        row = db_session.execute(
            select(GatewayCardAction).where(GatewayCardAction.thread_id == 1)
        ).scalar_one()

        assert row.out_connector == out_ref.connector
        assert row.out_workspace_id == out_ref.workspace_id
        assert row.out_chat_id == out_ref.chat_id
        assert row.out_message_id == out_ref.message_id

    def test_stores_binding_id(self, db_session, db_settings):
        from app.models import GatewayCardAction
        from sqlalchemy import select

        b = _make_binding(db_session)
        _mint_defaults(db_session, b, db_settings)

        row = db_session.execute(
            select(GatewayCardAction).where(GatewayCardAction.thread_id == 1)
        ).scalar_one()
        assert row.binding_id == b.id

    def test_expires_at_is_future(self, db_session, db_settings):
        from app.models import GatewayCardAction
        from sqlalchemy import select

        b = _make_binding(db_session)
        _mint_defaults(db_session, b, db_settings)

        row = db_session.execute(
            select(GatewayCardAction).where(GatewayCardAction.thread_id == 1)
        ).scalar_one()
        assert row.expires_at > datetime.utcnow()

    def test_status_is_pending_after_mint(self, db_session, db_settings):
        from app.models import GatewayCardAction
        from sqlalchemy import select

        b = _make_binding(db_session)
        _mint_defaults(db_session, b, db_settings)

        row = db_session.execute(
            select(GatewayCardAction).where(GatewayCardAction.thread_id == 1)
        ).scalar_one()
        assert row.status == "pending"


class TestVerifyAndClaim:
    def test_winning_claim_returns_row(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        result = verify_and_claim(
            db_session, token=token, source_message_ref=_make_out_ref()
        )
        assert not isinstance(result, ClaimError)
        assert result.token == token
        assert result.status == "resolving"

    def test_second_claim_returns_already_resolved(self, db_session, db_settings):
        """Calling verify_and_claim twice: first wins, second returns already_resolved."""
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)
        out_ref = _make_out_ref()

        first = verify_and_claim(db_session, token=token, source_message_ref=out_ref)
        assert not isinstance(first, ClaimError)

        second = verify_and_claim(db_session, token=token, source_message_ref=out_ref)
        assert second is ClaimError.already_resolved

    def test_bad_token_rejected(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        result = verify_and_claim(
            db_session,
            token="definitely-not-a-real-token-abc123xyz",
            source_message_ref=_make_out_ref(),
        )
        assert result is ClaimError.bad_token

    def test_expired_token_rejected(self, db_session, db_settings):
        from app.models import GatewayCardAction
        from app.services.gateway.actions import verify_and_claim, ClaimError
        from sqlalchemy import select

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        # Force the row to be expired
        row = db_session.execute(
            select(GatewayCardAction).where(GatewayCardAction.token == token)
        ).scalar_one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db_session.flush()

        result = verify_and_claim(
            db_session, token=token, source_message_ref=_make_out_ref()
        )
        assert result is ClaimError.expired

    def test_source_mismatch_connector_rejected(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        wrong_ref = MessageRef(
            connector="lark",  # wrong connector
            workspace_id="tk_test",
            chat_id="chat_001",
            message_id="msg_card_001",
        )
        result = verify_and_claim(db_session, token=token, source_message_ref=wrong_ref)
        assert result is ClaimError.source_mismatch

    def test_source_mismatch_workspace_rejected(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        wrong_ref = MessageRef(
            connector="feishu",
            workspace_id="WRONG_WORKSPACE",
            chat_id="chat_001",
            message_id="msg_card_001",
        )
        result = verify_and_claim(db_session, token=token, source_message_ref=wrong_ref)
        assert result is ClaimError.source_mismatch

    def test_source_mismatch_chat_rejected(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        wrong_ref = MessageRef(
            connector="feishu",
            workspace_id="tk_test",
            chat_id="WRONG_CHAT",
            message_id="msg_card_001",
        )
        result = verify_and_claim(db_session, token=token, source_message_ref=wrong_ref)
        assert result is ClaimError.source_mismatch

    def test_source_mismatch_message_id_rejected(self, db_session, db_settings):
        from app.services.gateway.actions import verify_and_claim, ClaimError

        b = _make_binding(db_session)
        token = _mint_defaults(db_session, b, db_settings)

        wrong_ref = MessageRef(
            connector="feishu",
            workspace_id="tk_test",
            chat_id="chat_001",
            message_id="WRONG_MSG_ID",
        )
        result = verify_and_claim(db_session, token=token, source_message_ref=wrong_ref)
        assert result is ClaimError.source_mismatch


class TestMarkHelpers:
    def _claim(self, session, settings):
        from app.services.gateway.actions import verify_and_claim
        b = _make_binding(session)
        token = _mint_defaults(session, b, settings)
        row = verify_and_claim(session, token=token, source_message_ref=_make_out_ref())
        return row, b

    def test_mark_resolved(self, db_session, db_settings):
        from app.services.gateway.actions import mark_resolved

        row, b = self._claim(db_session, db_settings)
        mark_resolved(db_session, row, resolved_by_binding_id=b.id)
        db_session.flush()

        assert row.status == "resolved"
        assert row.resolved_by_binding_id == b.id

    def test_mark_failed(self, db_session, db_settings):
        from app.services.gateway.actions import mark_failed

        row, _ = self._claim(db_session, db_settings)
        mark_failed(db_session, row)
        db_session.flush()

        assert row.status == "failed"

    def test_mark_unknown(self, db_session, db_settings):
        from app.services.gateway.actions import mark_unknown

        row, _ = self._claim(db_session, db_settings)
        mark_unknown(db_session, row)
        db_session.flush()

        assert row.status == "unknown"
