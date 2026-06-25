"""Identity & enrollment service for the IM message gateway.

Provides:
- ``issue_linking_code`` — mint a one-time pairing code for a known persona.
- ``redeem_code`` — bind an IM identity to a desk persona (handles transfer).
- ``active_binding`` — look up the current active binding for an IM identity.
- ``revoke_binding`` — idempotently revoke a binding.
- ``is_code_shaped`` — cheap pre-filter to reject obviously malformed codes.
- ``KNOWN_PERSONAS`` — the set of valid persona names.

Transaction semantics for ``redeem_code``
-----------------------------------------
The partial unique index ``uq_gateway_binding_active`` allows at most ONE
active GatewayBinding per (provider, external_account_id, workspace_id).
To honour this constraint during a transfer we MUST:

  1. SELECT + validate the GatewayLinkingCode (unexpired, unredeemed).
  2. REVOKE the existing active binding for the identity (if any).
  3. INSERT the new active binding (supersedes_binding_id = old id if revoked).
  4. Mark the code redeemed (redeemed_by_binding_id = new binding id).
  5. Write the audit event.

Steps 2–4 happen inside the same transaction; the revoke precedes the insert
so the unique index is never violated.  SQLite does not honour ``FOR UPDATE``
but the single-writer default combined with the conditional update on the code
row is sufficient for the test DB and the current deployment topology.
"""
from __future__ import annotations

import base64
import datetime as dt
import secrets
import re

from sqlalchemy.orm import Session

from app.models import GatewayBinding, GatewayLinkingCode
from app.services.audit import record_audit
from app.services.deep_agent.persona_domains import PERSONA_WORKFLOW_DOMAINS

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: The set of valid persona identifiers — sourced from the canonical domain map.
KNOWN_PERSONAS: frozenset[str] = frozenset(PERSONA_WORKFLOW_DOMAINS.keys())

# Base32 alphabet (RFC 4648, no padding) — used for code shape validation.
# A 16-byte secret_bytes produces ceil(16*8/5) = 26 uppercase base32 chars.
_CODE_RE = re.compile(r"^[A-Z2-7]{26,}$")


# ---------------------------------------------------------------------------
# Code helpers
# ---------------------------------------------------------------------------


def _generate_code() -> str:
    """Return a ≥128-bit base32 code (no padding, uppercase)."""
    raw = secrets.token_bytes(16)  # 128 bits
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def is_code_shaped(text: str) -> bool:
    """Return True when *text* matches the expected code format.

    Used as a cheap pre-filter before a DB lookup (e.g., to reject obviously
    short or malformed strings arriving over IM).
    """
    return bool(_CODE_RE.match(text))


# ---------------------------------------------------------------------------
# Core service functions
# ---------------------------------------------------------------------------


def issue_linking_code(
    session: Session,
    *,
    persona: str,
    settings,
) -> tuple[str, dt.datetime]:
    """Mint and persist a one-time pairing code for *persona*.

    Parameters
    ----------
    session:
        Active SQLAlchemy session (must be within a transaction boundary).
    persona:
        Persona name — must be a member of ``KNOWN_PERSONAS``.
    settings:
        Application settings (``gateway_default_desk_user``,
        ``gateway_linking_code_ttl_s``).

    Returns
    -------
    tuple[str, datetime]
        ``(code, expires_at)`` where ``expires_at`` is a UTC datetime.

    Raises
    ------
    ValueError
        If *persona* is not in ``KNOWN_PERSONAS``.
    """
    if persona not in KNOWN_PERSONAS:
        raise ValueError(
            f"Unknown persona {persona!r}. Valid personas: {sorted(KNOWN_PERSONAS)}"
        )

    desk_user: str = settings.gateway_default_desk_user
    ttl_s: int = settings.gateway_linking_code_ttl_s
    code = _generate_code()
    expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=ttl_s)

    row = GatewayLinkingCode(
        code=code,
        desk_user=desk_user,
        persona=persona,
        expires_at=expires_at,
        issued_by=desk_user,
    )
    session.add(row)
    session.flush()
    return code, expires_at


def redeem_code(
    session: Session,
    *,
    connector: str,
    external_account_id: str,
    workspace_id: str,
    code: str,
    settings,
) -> GatewayBinding | None:
    """Redeem a one-time linking code, creating (or transferring) a binding.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.  The caller is responsible for the outer
        ``session.commit()``; this function only flushes so that the new
        binding obtains its ``id`` before being referenced.
    connector:
        Platform name (e.g. ``"feishu"``).  Mapped to ``GatewayBinding.provider``.
    external_account_id:
        IM-platform user identifier.
    workspace_id:
        IM-platform workspace / tenant identifier.
    code:
        The one-time code string.
    settings:
        Application settings.

    Returns
    -------
    GatewayBinding | None
        The new active binding, or ``None`` if the code is invalid, expired,
        or already redeemed.

    Transaction order (see module docstring for rationale)
    -------------------------------------------------------
    1. SELECT + validate code (unexpired, unredeemed).
    2. REVOKE existing active binding for the identity (if any).
    3. INSERT new active binding (supersedes_binding_id if revoked).
    4. Mark code redeemed.
    5. Write audit event.
    """
    # Step 1 — validate the code row.
    code_row: GatewayLinkingCode | None = (
        session.query(GatewayLinkingCode).filter_by(code=code).first()
    )
    if code_row is None:
        return None
    if code_row.expires_at < dt.datetime.utcnow():
        return None
    if code_row.redeemed_by_binding_id is not None:
        return None

    provider = connector  # same value — rename for clarity

    # Step 2 — revoke existing active binding (if any).
    old_binding: GatewayBinding | None = (
        session.query(GatewayBinding)
        .filter_by(
            provider=provider,
            external_account_id=external_account_id,
            workspace_id=workspace_id,
            status="active",
        )
        .first()
    )
    supersedes_id: int | None = None
    if old_binding is not None:
        old_binding.status = "revoked"
        old_binding.revoked_at = dt.datetime.utcnow()
        supersedes_id = old_binding.id
        session.flush()  # push the revoke before the INSERT

    # Step 3 — insert new active binding.
    desk_user: str = code_row.desk_user
    new_binding = GatewayBinding(
        provider=provider,
        external_account_id=external_account_id,
        workspace_id=workspace_id,
        desk_user=desk_user,
        persona=code_row.persona,
        status="active",
        supersedes_binding_id=supersedes_id,
    )
    session.add(new_binding)
    session.flush()  # assign new_binding.id

    # Step 4 — mark code redeemed.
    code_row.redeemed_by_binding_id = new_binding.id
    session.flush()

    # Step 5 — audit.
    if supersedes_id is None:
        audit_event = "gateway.bound"
    else:
        # Distinguish between re-linking the same persona vs switching personas.
        if old_binding is not None and old_binding.persona != new_binding.persona:
            audit_event = "gateway.transferred"
        else:
            audit_event = "gateway.rebound"

    record_audit(
        session,
        event_type=audit_event,
        actor=desk_user,
        subject_type="gateway_binding",
        subject_id=new_binding.id,
        payload={
            "connector": connector,
            "external_account_id": external_account_id,
            "workspace_id": workspace_id,
            "persona": new_binding.persona,
            "supersedes_binding_id": supersedes_id,
        },
    )

    return new_binding


def active_binding(
    session: Session,
    *,
    connector: str,
    external_account_id: str,
    workspace_id: str,
) -> GatewayBinding | None:
    """Return the current active binding for an IM identity, or ``None``."""
    return (
        session.query(GatewayBinding)
        .filter_by(
            provider=connector,
            external_account_id=external_account_id,
            workspace_id=workspace_id,
            status="active",
        )
        .first()
    )


def revoke_binding(session: Session, *, binding_id: int) -> str:
    """Idempotently revoke a binding by id.

    Always returns ``"revoked"`` regardless of whether the binding was already
    revoked before this call.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.
    binding_id:
        Primary key of the ``GatewayBinding`` to revoke.

    Raises
    ------
    LookupError
        If no binding with the given id exists.
    """
    binding: GatewayBinding | None = session.get(GatewayBinding, binding_id)
    if binding is None:
        raise LookupError(f"GatewayBinding id={binding_id} not found")
    if binding.status != "revoked":
        binding.status = "revoked"
        binding.revoked_at = dt.datetime.utcnow()
        session.flush()
    return "revoked"
