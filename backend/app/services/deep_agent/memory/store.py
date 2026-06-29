"""MemoryStore — the single mutation gateway (spec §store.py, §Apply)."""
from __future__ import annotations

import collections
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.models import MemoryEntry
from .config import MemoryConfig
from .normalize import normalize_content
from .safety import is_memorable

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_CATEGORY = re.compile(r"^[a-z0-9_-]+$")

_VALID_STATUS = {
    "user": {"active", "archived"},
    "book": {"active", "archived"},
    "correction": {"active", "archived"},
    "domain": {"proposed", "approved", "archived"},
}
_ALLOWED_TRANSITIONS = {
    ("proposed", "approved"), ("proposed", "archived"),
    ("approved", "archived"), ("active", "archived"),
}


class MemoryValidationError(ValueError):
    """400 — invalid content/confidence/category/floor/denylist/(scope,status)."""


class MemoryConflictError(ValueError):
    """409 — dedup conflict or illegal status transition."""


class MemoryNotFound(LookupError):
    """404."""


_VALID_SCOPE_TYPES = frozenset(_VALID_STATUS)


def _validate_scope(scope_type: str) -> None:
    if scope_type not in _VALID_SCOPE_TYPES:
        raise MemoryValidationError(f"invalid scope_type: {scope_type!r}")


def _validate_scope_status(scope_type: str, status: str) -> None:
    # Matrix gate: domain is proposed/approved/archived; user/book/correction are
    # active/archived. Rejects a non-domain 'proposed' or a domain 'active'.
    if status not in _VALID_STATUS.get(scope_type, frozenset()):
        raise MemoryValidationError(f"invalid (scope,status): ({scope_type},{status})")


@dataclass(frozen=True)
class Fact:
    id: int
    scope_type: str
    scope_id: str
    content: str
    confidence: float
    status: str
    category: str | None
    source_error: bool
    pinned: bool
    created_at: datetime
    updated_at: datetime
    mutable: bool


@dataclass(frozen=True)
class WriteContext:
    allowed_scopes: list[str]
    book_scope_id: str | None = None
    created_by: str = "extractor"
    meta: dict = field(default_factory=dict)


def _to_fact(row: MemoryEntry) -> Fact:
    return Fact(
        id=row.id, scope_type=row.scope_type, scope_id=row.scope_id,
        content=row.content, confidence=row.confidence, status=row.status,
        category=row.category, source_error=row.source_error, pinned=row.pinned,
        created_at=row.created_at, updated_at=row.updated_at, mutable=not row.pinned,
    )


def _clean_category(category: str | None, max_chars: int) -> str | None:
    if not category:
        return None
    category = category.strip().lower()
    if len(category) > max_chars or not _CATEGORY.match(category):
        return None
    return category


def _normalize_source_error(row: MemoryEntry) -> None:
    """Invariant: source_error is True iff scope_type == 'correction'.
    Called on EVERY mutation path (create/update/set_status/archive/apply_diff)
    so a wrong value is silently corrected, never persisted."""
    row.source_error = (row.scope_type == "correction")


class MemoryStore:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self.counters: dict[str, int] = collections.defaultdict(int)

    # -- reads ------------------------------------------------------------

    def load_injectable(self, session, scopes) -> list[Fact]:
        out: list[Fact] = []
        for scope_type, scope_id in scopes:
            statuses = ("approved",) if scope_type == "domain" else ("active",)
            rows = (session.query(MemoryEntry)
                    .filter(MemoryEntry.scope_type == scope_type,
                            MemoryEntry.scope_id == scope_id,
                            MemoryEntry.status.in_(statuses))
                    .order_by(MemoryEntry.confidence.desc(),
                              MemoryEntry.updated_at.desc(), MemoryEntry.id.asc())
                    .all())
            out.extend(_to_fact(r) for r in rows)
        return out

    def load_existing(self, session, scope_type, scope_id) -> list[Fact]:
        statuses = ("proposed", "approved") if scope_type == "domain" else ("active",)
        rows = (session.query(MemoryEntry)
                .filter(MemoryEntry.scope_type == scope_type,
                        MemoryEntry.scope_id == scope_id,
                        MemoryEntry.status.in_(statuses))
                .order_by(MemoryEntry.confidence.desc(),
                          MemoryEntry.updated_at.desc(), MemoryEntry.id.asc())
                .limit(50).all())
        return [_to_fact(r) for r in rows]

    # -- validation helpers ----------------------------------------------

    def _validate_new(self, scope_type, content, confidence) -> str:
        norm = normalize_content(content)
        if not norm:
            raise MemoryValidationError("empty content after normalize")
        if len(content) > self.config.content_max_chars:
            raise MemoryValidationError("content too long")
        ok, reason = is_memorable(content, self.config.denylist)
        if not ok:
            raise MemoryValidationError(f"denylist: {reason}")
        if confidence < self.config.confidence_floor:
            raise MemoryValidationError("below confidence floor")
        if not 0.0 <= confidence <= 1.0:
            raise MemoryValidationError("confidence out of range")
        return norm

    def _dedup_exists(self, session, scope_type, scope_id, norm, exclude_id=None) -> bool:
        q = (session.query(MemoryEntry)
             .filter(MemoryEntry.scope_type == scope_type,
                     MemoryEntry.scope_id == scope_id,
                     MemoryEntry.normalized_content == norm,
                     MemoryEntry.status != "archived"))
        if exclude_id is not None:
            q = q.filter(MemoryEntry.id != exclude_id)
        return bool(session.query(q.exists()).scalar())

    # -- caps -------------------------------------------------------------

    def _enforce_caps(self, session, scope_type, scope_id) -> None:
        cap = (self.config.max_correction_facts if scope_type == "correction"
               else self.config.max_facts_per_scope)
        rows = (session.query(MemoryEntry)
                .filter(MemoryEntry.scope_type == scope_type,
                        MemoryEntry.scope_id == scope_id,
                        MemoryEntry.status != "archived").all())
        if len(rows) <= cap:
            return
        # Always evict as many NON-pinned rows as needed (lowest-confidence,
        # then oldest created_at, then lowest id; domain prefers proposed over
        # approved). Only flag overflow if STILL over cap after evicting every
        # non-pinned row (i.e. pinned rows alone exceed the cap).
        evictable = sorted(
            (r for r in rows if not r.pinned),
            key=lambda r: (r.scope_type == "domain" and r.status == "approved",
                           r.confidence, r.created_at, r.id))
        to_evict = min(len(rows) - cap, len(evictable))
        for r in evictable[:to_evict]:
            r.status = "archived"
        if len(rows) - to_evict > cap:
            self.counters["memory_cap_pinned_overflow"] += 1
            logger.warning("memory_cap_pinned_overflow %s/%s", scope_type, scope_id)
        if to_evict:
            session.flush()

    # -- writes -----------------------------------------------------------

    def create(self, session, *, scope_type, scope_id, content,
               confidence=1.0, category=None, created_by="api") -> Fact:
        _validate_scope(scope_type)
        norm = self._validate_new(scope_type, content, confidence)
        status = "proposed" if scope_type == "domain" else "active"
        _validate_scope_status(scope_type, status)
        if self._dedup_exists(session, scope_type, scope_id, norm):
            raise MemoryConflictError("duplicate")
        row = MemoryEntry(
            scope_type=scope_type, scope_id=scope_id, content=content,
            normalized_content=norm, confidence=confidence, status=status,
            category=_clean_category(category, self.config.category_max_chars),
            source_error=(scope_type == "correction"),
            created_by=created_by,
            pinned=(created_by == "api" and scope_type != "domain"),
            meta={})
        sp = session.begin_nested()
        session.add(row)
        try:
            session.flush()
            sp.commit()
        except IntegrityError as exc:
            sp.rollback()
            raise MemoryConflictError("duplicate") from exc
        self._enforce_caps(session, scope_type, scope_id)
        return _to_fact(row)

    def _update_row(self, session, row, *, content=None, confidence=None, category=None) -> None:
        new_content = content if content is not None else row.content
        new_conf = confidence if confidence is not None else row.confidence
        norm = self._validate_new(row.scope_type, new_content, new_conf)
        if self._dedup_exists(session, row.scope_type, row.scope_id, norm, exclude_id=row.id):
            raise MemoryConflictError("duplicate")
        row.content = new_content
        row.normalized_content = norm
        row.confidence = new_conf
        if category is not None:
            row.category = _clean_category(category, self.config.category_max_chars)
        _normalize_source_error(row)
        session.flush()

    def update(self, session, fact_id, *, content=None, confidence=None, category=None) -> Fact:
        row = session.get(MemoryEntry, fact_id)
        if row is None:
            raise MemoryNotFound(str(fact_id))
        sp = session.begin_nested()
        try:
            self._update_row(session, row, content=content, confidence=confidence,
                             category=category)
            sp.commit()
        except IntegrityError as exc:
            sp.rollback()
            raise MemoryConflictError("duplicate") from exc
        return _to_fact(row)

    def set_status(self, session, fact_id, new_status) -> Fact:
        row = session.get(MemoryEntry, fact_id)
        if row is None:
            raise MemoryNotFound(str(fact_id))
        if new_status not in _VALID_STATUS.get(row.scope_type, set()):
            raise MemoryConflictError("invalid (scope, status)")
        if (row.status, new_status) not in _ALLOWED_TRANSITIONS:
            raise MemoryConflictError("illegal transition")
        row.status = new_status
        if new_status == "approved":
            row.pinned = True
        _normalize_source_error(row)
        session.flush()
        return _to_fact(row)

    def archive(self, session, fact_id) -> bool:
        row = session.get(MemoryEntry, fact_id)
        if row is None:
            raise MemoryNotFound(str(fact_id))
        if row.status != "archived":
            row.status = "archived"
            _normalize_source_error(row)
            session.flush()
        return True

    # -- apply_diff -------------------------------------------------------

    def _resolve_scope_id(self, scope_type, ctx: WriteContext) -> str | None:
        if scope_type in ("user", "correction"):
            return "desk"
        if scope_type == "domain":
            return "global"
        if scope_type == "book":
            return ctx.book_scope_id
        return None

    def apply_diff(self, session, diff, ctx: WriteContext) -> None:
        with _LOCK:
            with session.begin_nested():   # atomic unit: rolls back ALL on error
                self._apply_diff_inner(session, diff, ctx)

    def _apply_diff_inner(self, session, diff, ctx: WriteContext) -> None:
        touched: set[tuple[str, str]] = set()
        for item in diff.add:
            scope_type = item.get("scope_type")
            if scope_type not in ctx.allowed_scopes:
                continue
            scope_id = self._resolve_scope_id(scope_type, ctx)
            if scope_id is None:
                continue
            try:
                norm = self._validate_new(scope_type, item.get("content", ""),
                                          item.get("confidence", 1.0))
                status = "proposed" if scope_type == "domain" else "active"
                _validate_scope_status(scope_type, status)   # matrix gate (defense in depth)
            except MemoryValidationError:
                continue
            if self._dedup_exists(session, scope_type, scope_id, norm):
                continue
            row = MemoryEntry(
                scope_type=scope_type, scope_id=scope_id, content=item["content"],
                normalized_content=norm, confidence=item.get("confidence", 1.0),
                status=status,
                category=_clean_category(item.get("category"), self.config.category_max_chars),
                source_error=(scope_type == "correction"),
                created_by="extractor", pinned=False, meta=dict(ctx.meta))
            sp = session.begin_nested()
            session.add(row)
            try:
                session.flush()
                sp.commit()
            except IntegrityError:
                sp.rollback()
                continue
            touched.add((scope_type, scope_id))
        for rid in diff.remove:
            row = session.get(MemoryEntry, rid)
            # Guard scope_id too (not just scope_type): a job allowed for book "1"
            # must not archive a fact in book "2" (same scope_type, different id).
            if (row is None or row.pinned or row.status == "archived"
                    or row.scope_type not in ctx.allowed_scopes
                    or row.scope_id != self._resolve_scope_id(row.scope_type, ctx)):
                if row is not None and row.pinned:
                    logger.info("memory extractor remove targeted pinned id=%s (no-op)", rid)
                continue
            row.status = "archived"
            _normalize_source_error(row)
        for upd in diff.update:
            row = session.get(MemoryEntry, upd.get("id"))
            if (row is None or row.scope_type not in ctx.allowed_scopes
                    or row.scope_id != self._resolve_scope_id(row.scope_type, ctx)):
                continue
            if row.pinned:
                logger.info("memory extractor update targeted pinned id=%s (no-op)", row.id)
                continue
            sp = session.begin_nested()
            try:
                self._update_row(session, row, content=upd.get("content"),
                                 confidence=upd.get("confidence"), category=upd.get("category"))
                sp.commit()
            except (MemoryValidationError, MemoryConflictError, IntegrityError):
                sp.rollback()
                continue
        session.flush()
        for scope_type, scope_id in touched:
            self._enforce_caps(session, scope_type, scope_id)
