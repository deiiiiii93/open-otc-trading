"""TDD tests for the gateway identity & enrollment service."""
from __future__ import annotations

import datetime as dt
import pytest

from app.services.gateway import identity
from app.config import Settings


def _settings(**kw) -> Settings:
    base = dict(
        database_url="sqlite+pysqlite:///./data/open_otc.sqlite3",
        artifact_dir="./artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Brief-required tests
# ---------------------------------------------------------------------------


def test_redeem_binds_then_transfer_supersedes(db_session):
    s = _settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    b1 = identity.redeem_code(
        db_session,
        connector="feishu",
        external_account_id="ou_1",
        workspace_id="tk_1",
        code=code,
        settings=s,
    )
    assert b1 is not None
    assert b1.status == "active"

    code2, _ = identity.issue_linking_code(db_session, persona="risk_manager", settings=s)
    b2 = identity.redeem_code(
        db_session,
        connector="feishu",
        external_account_id="ou_1",
        workspace_id="tk_1",
        code=code2,
        settings=s,
    )
    db_session.refresh(b1)
    assert b2 is not None
    assert b2.status == "active"
    assert b2.supersedes_binding_id == b1.id
    assert b1.status == "revoked"
    assert (
        identity.active_binding(
            db_session,
            connector="feishu",
            external_account_id="ou_1",
            workspace_id="tk_1",
        ).id
        == b2.id
    )


def test_expired_code_rejected(db_session, monkeypatch):
    s = _settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    # force expiry
    from app.models import GatewayLinkingCode

    row = db_session.query(GatewayLinkingCode).filter_by(code=code).one()
    row.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
    db_session.commit()
    assert (
        identity.redeem_code(
            db_session,
            connector="feishu",
            external_account_id="ou_2",
            workspace_id="tk_1",
            code=code,
            settings=s,
        )
        is None
    )


def test_invalid_persona_rejected(db_session):
    with pytest.raises(ValueError):
        identity.issue_linking_code(
            db_session, persona="nope", settings=_settings()
        )


# ---------------------------------------------------------------------------
# Extra coverage
# ---------------------------------------------------------------------------


def test_revoke_binding_idempotent(db_session):
    """revoke_binding returns 'revoked' even when called twice."""
    s = _settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    binding = identity.redeem_code(
        db_session,
        connector="feishu",
        external_account_id="ou_revoke",
        workspace_id="tk_r",
        code=code,
        settings=s,
    )
    assert binding is not None
    result1 = identity.revoke_binding(db_session, binding_id=binding.id)
    result2 = identity.revoke_binding(db_session, binding_id=binding.id)
    assert result1 == "revoked"
    assert result2 == "revoked"


def test_reused_code_rejected(db_session):
    """A code already redeemed cannot be used again (rebound-no-op)."""
    s = _settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    b1 = identity.redeem_code(
        db_session,
        connector="feishu",
        external_account_id="ou_reuse",
        workspace_id="tk_reuse",
        code=code,
        settings=s,
    )
    assert b1 is not None
    # Second redemption with same code must return None
    b2 = identity.redeem_code(
        db_session,
        connector="feishu",
        external_account_id="ou_reuse2",
        workspace_id="tk_reuse",
        code=code,
        settings=s,
    )
    assert b2 is None


def test_is_code_shaped(db_session):
    s = _settings()
    code, _ = identity.issue_linking_code(db_session, persona="trader", settings=s)
    assert identity.is_code_shaped(code)
    assert not identity.is_code_shaped("too-short")
    assert not identity.is_code_shaped("")


def test_known_personas_contains_all_three():
    assert "trader" in identity.KNOWN_PERSONAS
    assert "risk_manager" in identity.KNOWN_PERSONAS
    assert "high_board" in identity.KNOWN_PERSONAS


def test_active_binding_none_when_absent(db_session):
    result = identity.active_binding(
        db_session,
        connector="feishu",
        external_account_id="nobody",
        workspace_id="none",
    )
    assert result is None


def test_issue_linking_code_returns_code_and_expiry(db_session):
    s = _settings()
    code, expires_at = identity.issue_linking_code(
        db_session, persona="high_board", settings=s
    )
    assert isinstance(code, str)
    assert len(code) >= 26  # base32 of 16 bytes → 26 chars (without padding)
    assert isinstance(expires_at, dt.datetime)
    assert expires_at > dt.datetime.utcnow()
