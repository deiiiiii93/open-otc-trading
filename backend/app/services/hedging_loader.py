# backend/app/services/hedging_loader.py
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..models import HedgeMapEntry, Instrument, Position, TaskKind, TaskRun, TaskStatus, Underlying
from .hedging_universe import ENUMERATORS, EnumeratedContract, FamilySpec, resolve_families
from .quotes import record_quote
from .task_runner import (
    ACTIVE_TASK_STATUSES,
    mark_task_finished,
    mark_task_running,
    update_task_progress,
)

logger = logging.getLogger(__name__)


def _like_prefix(contract_code: str) -> str:
    """LIKE pattern matching ``<contract_code>.<anything>`` with wildcards in the
    code escaped (option codes may legitimately contain ``_``). Pairs with
    ``ESCAPE '\\'`` (SQLAlchemy ``.like(..., escape='\\')``)."""
    escaped = (
        (contract_code or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"{escaped}.%"


# Hedge families that route a registry underlying to its derivatives. For these
# the contract's contractual underlier IS the registry row, so parent_id =
# underlying_id (e.g. IC2606's underlier is 000905.SH; an ETF/index option's
# underlier is the index/ETF registry row).
_PARENT_IS_UNDERLYING_FAMILIES = {"index_future", "index_option", "etf_option"}


def _instrument_kind(contract: EnumeratedContract) -> str:
    """Map an enumerated contract to an Instrument.kind.

    The catalog only ever carries listed contracts; option_type present ⇒ a
    listed option, otherwise a future. (Equivalent to the old instrument_type
    'option'/'future' split, but keyed off option_type which is authoritative.)
    """
    return "listed_option" if contract.option_type else "futures"


def _resolve_parent_id(
    session: Session, contract: EnumeratedContract, underlying_id: int
) -> int | None:
    """Contractual underlier (parent_id) for a freshly loaded contract.

    * index futures / index options / ETF options → the registry underlying row
      (the contract's underlier IS that row).
    * commodity FUTURES → None (the underlier is a physical, not a registry row).
    * commodity OPTIONS → the matching futures Instrument: same series_root,
      kind='futures', whose contract_code is the YYMM-month prefix of the option
      code. Commodity option codes start with the future code (e.g. future
      'LH2609' → option 'LH2609-C-13000' / 'LH2609C13000'), so we match the
      future whose code the option code startswith. Not found → None.
    """
    if contract.family in _PARENT_IS_UNDERLYING_FAMILIES:
        return underlying_id
    if contract.option_type is None:
        # commodity future (or any future family not routed to the underlying)
        return None
    # commodity option: find the sibling future it derives from.
    option_code = (contract.contract_code or "").upper()
    candidates = (
        session.query(Instrument.id, Instrument.contract_code)
        .filter(
            Instrument.kind == "futures",
            Instrument.series_root == contract.series_root,
            Instrument.contract_code.isnot(None),
        )
        .all()
    )
    best: tuple[int, int] | None = None  # (len(code), id) of longest matching prefix
    for fid, fcode in candidates:
        fc = (fcode or "").upper()
        if fc and option_code.startswith(fc):
            if best is None or len(fc) > best[0] or (len(fc) == best[0] and fid < best[1]):
                best = (len(fc), fid)
    return best[1] if best else None


def _upsert_catalog(
    session: Session, contracts: list[EnumeratedContract], underlying_id: int
) -> set[tuple[str, str]]:
    """Insert/refresh Instrument catalog rows; return the (exchange, code) keys
    seen so ``_expire_missing`` can flag the rest.

    Identity is EXCHANGE-AGNOSTIC: a contract is matched to an existing
    instrument by ``symbol LIKE contract_code || '.%'`` (the contract code embeds
    series+expiry; the exchange suffix style drifts — a registry row may carry
    ``IC2606.CFE`` while the feed reports exchange ``CFFEX``). A match MERGES:
    feed terms are authoritative and overwrite kind/contract terms + flip status
    to 'active', but the existing ``symbol`` (position-referenced, load-bearing)
    and the curated r/q/vol/currency/notes columns are left untouched. No match
    INSERTs under the canonical ``symbol = f"{contract_code}.{exchange}"``. If the
    prefix is ambiguous (>1 instrument), we log and SKIP the contract rather than
    crash — per-family isolation conventions. Prices flow into the market_quotes
    store via record_quote; no last_price column is written.
    """
    seen: set[tuple[str, str]] = set()
    now = datetime.utcnow()
    # Pre-fetch existing rows for this batch by contract-code prefix in one query
    # (full option chains can be hundreds of contracts), grouped by code.
    codes = [c.contract_code for c in contracts if c.contract_code]
    by_code: dict[str, list[Instrument]] = {}
    if codes:
        clauses = [Instrument.symbol.like(_like_prefix(code), escape="\\") for code in set(codes)]
        for row in session.query(Instrument).filter(or_(*clauses)):
            # the code is the symbol up to the first '.'; group on it.
            row_code = (row.symbol or "").split(".", 1)[0]
            by_code.setdefault(row_code, []).append(row)
    for c in contracts:
        canonical = f"{c.contract_code}.{c.exchange}"
        seen.add((c.exchange, c.contract_code))
        kind = _instrument_kind(c)
        parent_id = _resolve_parent_id(session, c, underlying_id)
        candidates = by_code.get(c.contract_code, [])
        if len(candidates) > 1:
            logger.warning(
                "hedge load: contract_code %r matched %d instruments (%s); "
                "skipping ambiguous merge",
                c.contract_code,
                len(candidates),
                ", ".join(sorted(i.symbol for i in candidates)),
            )
            continue
        row = candidates[0] if candidates else None
        if row is None:
            # No registry/legacy row carried this code → insert under the
            # canonical symbol. Register it so a repeated code in the same batch
            # merges onto it rather than double-inserting.
            row = Instrument(
                symbol=canonical,
                kind=kind,
                contract_code=c.contract_code,
                series_root=c.series_root,
                exchange=c.exchange,
                expiry=c.expiry,
                multiplier=c.multiplier,
                strike=c.strike,
                option_type=c.option_type,
                parent_id=parent_id,
                akshare_symbol=c.akshare_symbol,
                status="active",
                source="hedge_load",
                loaded_at=now,
            )
            session.add(row)
            session.flush()  # assign id for record_quote
            by_code.setdefault(c.contract_code, []).append(row)
        else:
            # MERGE: feed terms are authoritative. The existing ``symbol`` is
            # KEPT (registry style is position-referenced); do NOT overwrite the
            # curated currency/notes/rate/dividend_yield/volatility either.
            row.kind = kind
            row.contract_code = c.contract_code
            row.series_root = c.series_root
            row.exchange = c.exchange
            row.expiry = c.expiry
            row.multiplier = c.multiplier
            row.strike = c.strike
            row.option_type = c.option_type
            if parent_id is not None:
                row.parent_id = parent_id
            if c.akshare_symbol is not None:
                row.akshare_symbol = c.akshare_symbol
            row.status = "active"
            row.loaded_at = now
        if c.last_price is not None:
            record_quote(
                session,
                instrument_id=row.id,
                price=c.last_price,
                as_of=now,
                source="hedge_load",
            )
    return seen


def _family_kind(family: str) -> str:
    """The Instrument.kind a hedge ``family`` maps to.

    Instrument has no ``family`` column; family encoded (series_root, kind).
    Option families (*_option) → 'listed_option'; everything else → 'futures'.
    This reproduces the old per-family scoping (a series_root is enumerated by
    exactly one future family and/or one option family).
    """
    return "listed_option" if family.endswith("_option") else "futures"


def _expire_missing(
    session: Session,
    family: str,
    seen: set[tuple[str, str]],
    series_root: str,
) -> None:
    """Flag active Instrument rows of this enumerated slice not seen as expired.

    Family scoping equivalence: the old HedgeInstrument carried a ``family``
    column; Instrument does not. A family maps deterministically to a
    (series_root, kind) pair — ``_family_kind`` decodes the kind — which is the
    exact slice the enumerator owns, so filtering on (series_root, kind) is
    behaviour-equivalent to the old (family, series_root) filter.

    Call ONLY when the family enumeration confirmably succeeded.
    """
    q = session.query(Instrument).filter(
        Instrument.kind == _family_kind(family),
        Instrument.status == "active",
        Instrument.series_root == series_root,
    )
    for row in q.all():
        if (row.exchange, row.contract_code) not in seen:
            row.status = "expired"


def reconcile_map(session: Session) -> None:
    """Recompute every map entry's reconcile_status against active catalog rows.

    Prefers the durable ``instrument_id`` link when set; otherwise falls back to
    matching on the (exchange, contract_code) display columns (legacy entries /
    rows whose instrument link was never backfilled).
    """
    active_ids = {
        r[0]
        for r in session.query(Instrument.id).filter(Instrument.status == "active")
    }
    active_keys = {
        (r.exchange, r.contract_code)
        for r in session.query(
            Instrument.exchange, Instrument.contract_code
        ).filter(Instrument.status == "active")
    }
    for entry in session.query(HedgeMapEntry).all():
        if entry.instrument_id is not None:
            is_active = entry.instrument_id in active_ids
        else:
            is_active = (entry.exchange, entry.contract_code) in active_keys
        entry.reconcile_status = "active" if is_active else "stale"


class HedgeLoadInProgress(Exception):
    def __init__(self, task_id: int):
        super().__init__(f"Hedge load already in progress: {task_id}")
        self.task_id = task_id


def list_in_scope_underlyings(session: Session) -> list[Underlying]:
    open_ids = {
        row[0]
        for row in session.query(Position.underlying_id)
        .filter(
            Position.status == "open",
            Position.position_kind == "otc",
            Position.underlying_id.isnot(None),
        )
        .distinct()
    }
    mapped_ids = {
        row[0] for row in session.query(HedgeMapEntry.underlying_id).distinct()
    }
    ids = open_ids | mapped_ids
    if not ids:
        return []
    return (
        session.query(Underlying)
        .filter(Underlying.id.in_(ids))
        .order_by(Underlying.symbol)
        .all()
    )


def queue_hedge_load(session: Session) -> TaskRun:
    existing = (
        session.query(TaskRun)
        .filter(
            TaskRun.kind == TaskKind.HEDGE_LOAD.value,
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
        )
        .first()
    )
    if existing is not None:
        raise HedgeLoadInProgress(existing.id)
    task = TaskRun(
        kind=TaskKind.HEDGE_LOAD.value,
        status=TaskStatus.QUEUED.value,
        progress_current=0,
        progress_total=0,
        message="Queued hedge instrument load",
    )
    session.add(task)
    session.flush()
    return task


def execute_hedge_load_task(
    task_id: int, session_factory: sessionmaker | None = None
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute(session, task_id)
    finally:
        session.close()


def _execute(session: Session, task_id: int) -> None:
    """Run the load, marking the task FAILED if anything unexpected escapes.

    The per-family loop isolates enumerator failures; this outer guard catches
    anything else (e.g. a DB error that poisons the session) so the task never
    gets stuck in RUNNING. Mirrors batch_pricing._execute_batch_pricing_task.
    """
    try:
        _execute_inner(session, task_id)
    except Exception as exc:  # noqa: BLE001 — last-resort guard, must not leak
        session.rollback()
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.FAILED.value,
            message="Hedge instrument load failed",
            error=str(exc),
        )
        session.commit()


def _execute_inner(session: Session, task_id: int) -> None:
    underlyings = list_in_scope_underlyings(session)
    work: list[tuple[Underlying, FamilySpec]] = []
    unresolvable: list[str] = []
    for u in underlyings:
        specs = resolve_families(u.symbol, u.asset_class)
        if not specs:
            unresolvable.append(u.symbol)
            continue
        for spec in specs:
            work.append((u, spec))

    mark_task_running(
        session, task_id, message="Loading hedge instruments", total=len(work)
    )
    session.commit()

    summary: dict = {"families": [], "unresolvable": unresolvable, "errors": []}
    done = 0
    for u, spec in work:
        enumerator = ENUMERATORS.get(spec.enumerator_key)
        if enumerator is None:
            # Config drift: a resolved family has no registered enumerator.
            # Record an error and skip — never expire on a missing enumerator.
            summary["errors"].append({
                "underlying": u.symbol,
                "family": spec.family,
                "error": f"No enumerator registered for key '{spec.enumerator_key}'",
            })
        else:
            try:
                contracts = enumerator(spec.series_root)
                seen = _upsert_catalog(session, contracts, u.id)
                _expire_missing(session, spec.family, seen, spec.series_root)
                summary["families"].append({
                    "underlying": u.symbol,
                    "family": spec.family,
                    "count": len(contracts),
                })
            except Exception as exc:  # noqa: BLE001 — per-family isolation by design
                summary["errors"].append(
                    {"underlying": u.symbol, "family": spec.family, "error": str(exc)}
                )
        done += 1
        update_task_progress(
            session, task_id, current=done,
            message=f"{u.symbol} · {spec.family}",
        )
        session.commit()

    reconcile_map(session)
    task = session.get(TaskRun, task_id)
    task.result_payload = summary
    status = (
        TaskStatus.COMPLETED_WITH_ERRORS.value
        if summary["errors"]
        else TaskStatus.COMPLETED.value
    )
    mark_task_finished(
        session, task_id, status=status, message="Hedge instrument load complete"
    )
    session.commit()
