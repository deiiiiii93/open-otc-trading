# backend/app/tools/confirmations.py
"""Agent tools over the trade-confirmation pipeline (services/confirmations).

parse_trade_confirmation runs the parse synchronously inside the tool (agent
turns are long-running already); booking is HITL-gated and rides the same
service gate as the REST surface.
"""
from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel

from .. import database
from ..config import get_settings
from ..models import ConfirmationBatch
from ..services.confirmations import service as confirmations
# `from ..services.confirmations.llm import build_extractor_client` (a bare
# NAME, not a submodule, imported at module scope) is circular-import-unsafe
# here: services/confirmations/llm.py itself imports app.tools.product_term_
# schema at ITS module scope, which (via this package's own __init__.py
# importing this module) can re-enter llm.py while it is still mid-exec and
# hasn't defined build_extractor_client yet -> ImportError on the partially
# initialized module. Importing the submodule object instead (a name Python's
# import system resolves via sys.modules even mid-import) and looking up the
# attribute at CALL time sidesteps that ordering dependency entirely.
from ..services.confirmations import llm as confirmations_llm
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup


def _batch_out(batch: ConfirmationBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "source": batch.source,
        "default_portfolio_id": batch.default_portfolio_id,
        "documents": [
            {
                "document_id": d.id, "filename": d.filename, "status": d.status,
                "extract_mode": d.extract_mode, "error": d.error,
                "trades": [
                    {
                        "trade_id": t.id, "seq": t.seq, "family": t.family,
                        "underlying": t.underlying, "quantity": t.quantity,
                        "entry_price": t.entry_price, "currency": t.currency,
                        "counterparty": t.counterparty,
                        "external_trade_id": t.external_trade_id,
                        "confidence": t.confidence, "terms": dict(t.terms or {}),
                        "validation_status": t.validation_status,
                        "validation_errors": list(t.validation_errors or []),
                        "status": t.status,
                        "booked_position_id": t.booked_position_id,
                    }
                    for t in d.trades
                ],
            }
            for d in batch.documents
        ],
    }


class ParseTradeConfirmationInput(BaseModel):
    paths: list[str]
    portfolio_id: int | None = None


def _resolve_upload_path(raw: str, uploads_root: Path) -> Path | None:
    """Resolve *raw* to a real file INSIDE ``uploads_root``, or None.

    Containment is unchanged and absolute: whatever we return has been
    ``resolve()``d and re-checked against ``uploads_root``. What this adds is
    tolerance of the spellings a model actually produces.

    Measured on arena run #1 (2026-08-28): told the documents were at
    ``/artifacts/uploads/confirmations/``, gpt-5.6-luna tried
    ``/artifacts/uploads/...``, then ``/uploads/...``, then the bare filenames --
    four reasonable attempts, every one rejected with "path outside uploads dir",
    which names the problem without ever revealing the accepted form. The files
    were staged correctly; only the ADDRESSING was unusable, and the whole match
    cascaded from it.

    ``/artifacts/...`` is not a wrong guess either: it is exactly how the deep
    agent's own filesystem backend mounts ``settings.artifact_dir``. A benchmark
    that punishes a model for using the addressing its own runtime taught it is
    measuring the harness.
    """
    candidates: list[Path] = []

    direct = Path(raw)
    if direct.is_absolute():
        candidates.append(direct)

    # Strip the virtual mount prefixes a model reasonably prepends, then anchor
    # the remainder to the real uploads root.
    rel = raw.lstrip("/")
    for prefix in ("artifacts/uploads/", "artifacts/", "uploads/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    if rel:
        candidates.append(uploads_root / rel)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(uploads_root) and resolved.is_file():
            return resolved

    # Last resort: a BARE filename, matched uniquely anywhere under the uploads
    # root. Unique-only, so an ambiguous name is refused rather than guessed.
    #
    # `glob.escape` because rglob takes a PATTERN: unescaped, `conf-08*.pdf`
    # would resolve a document the caller never named, and a stray `[` would
    # make the lookup behave in ways no caller intended. This is a
    # filename-recovery path, not a search tool -- the model gets the file it
    # asked for by name, or nothing.
    if "/" not in raw:
        matches = [p for p in uploads_root.rglob(glob.escape(raw)) if p.is_file()]
        if len(matches) == 1:
            resolved = matches[0].resolve()
            if resolved.is_relative_to(uploads_root):
                return resolved
    return None


def _extractor_override_from_config(config: RunnableConfig | None) -> dict | None:
    """Read the SERVER-STAMPED extractor override off ``configurable``.

    Deliberately absent from ``ParseTradeConfirmationInput``: the override decides
    WHICH model reads the desk's documents, so letting a model supply it would be
    self-authorization -- the same reason fan-out attribution is stamped rather
    than accepted from tool input.

    Anything that is not a non-empty dict returns None, so a malformed stamp
    degrades to the production tag ladder rather than failing deep inside
    model_factory on a half-built selection.
    """
    from ..services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    configurable = (config or {}).get("configurable") or {}
    override = configurable.get(CONFIRMATION_EXTRACTOR_SELECTION_KEY)
    return override if isinstance(override, dict) and override else None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("parse_trade_confirmation", args_schema=ParseTradeConfirmationInput)
def parse_trade_confirmation(
    paths: list[str],
    portfolio_id: int | None = None,
    config: RunnableConfig = None,  # type: ignore[assignment]
) -> dict:
    """Parse uploaded trade confirmation files (PDF/DOCX, scans supported) into
    reviewable extracted trades. `paths` are the stored paths returned when the
    user attached files to this chat. Returns per-document status plus each
    extracted trade's terms, validation result, and trade_id. Booking is a
    separate confirmed step (book_extracted_trade)."""
    settings = get_settings()
    uploads_root = (settings.artifact_dir / "uploads").resolve()
    resolved: list[Path] = []
    for raw in paths:
        p = _resolve_upload_path(str(raw), uploads_root)
        if p is None:
            # Name the ACCEPTED form. The previous message stated only what was
            # wrong, so a model could retry four times without ever learning
            # what would work (arena run #1).
            return {
                "ok": False,
                "error": (
                    f"no such upload: {raw!r}. Give a path under the uploads "
                    f"directory — e.g. 'confirmations/<file>.pdf', "
                    f"'/artifacts/uploads/confirmations/<file>.pdf', or the bare "
                    f"filename if it is unique."
                ),
            }
        resolved.append(p)
    if not resolved:
        return {"ok": False, "error": "no files given"}
    try:
        client = confirmations_llm.build_extractor_client(
            _extractor_override_from_config(config)
        )
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    database.init_db()
    with database.SessionLocal() as session:
        stored = [
            confirmations.StoredFile(
                filename=p.name, stored_path=str(p),
                sha256=confirmations.sha256_of_file(p),
                byte_len=p.stat().st_size,
                mime="application/pdf" if p.suffix.lower() == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            for p in resolved
        ]
        batch = confirmations.create_batch(
            session, files=stored, source="agent", portfolio_id=portfolio_id)
        session.commit()
        for document in batch.documents:
            confirmations.parse_document(session, document, client=client)
            session.commit()
        session.refresh(batch)
        return {"ok": True, **_batch_out(batch)}


class GetConfirmationBatchInput(BaseModel):
    batch_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_confirmation_batch", args_schema=GetConfirmationBatchInput)
def get_confirmation_batch(batch_id: int) -> dict:
    """Read a confirmation batch: per-document parse status and every extracted
    trade with its validation result and booking status."""
    database.init_db()
    with database.SessionLocal() as session:
        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            return {"ok": False, "error": f"batch {batch_id} not found"}
        return {"ok": True, **_batch_out(batch)}


class BookExtractedTradeInput(BaseModel):
    trade_id: int
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_extracted_trade", args_schema=BookExtractedTradeInput)
def book_extracted_trade(trade_id: int, portfolio_id: int | None = None) -> dict:
    """Book one reviewed extracted trade into a portfolio through the standard
    booking gate. Fails honestly on validation errors, missing target portfolio,
    or an already-booked source trade id. HITL — requires confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        result = confirmations.book_trade(
            session, trade_id, portfolio_id=portfolio_id, actor="desk_agent")
        if result.get("ok"):
            session.commit()
        else:
            session.rollback()
        return result
