"""Content-addressed backend for DeepAgents large tool result blobs."""
from __future__ import annotations

import hashlib
import json
import re
from fnmatch import fnmatch
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from sqlalchemy.orm import Session

from deepagents.backends.protocol import (
    BackendProtocol,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from langchain_core.runnables import RunnableConfig

from ... import database
from ...models import (
    ArtifactEvidenceRef,
    ContextPackPayload,
    SessionArtifact,
    utcnow,
)
from .ledger import LedgerWriter
from .payload_registry import LOAD_BEARING_EVIDENCE_KINDS
from .workflow_state import ensure_thread_workflow_state

_DESK_EXECUTION_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "desk_execution_context",
    default=None,
)

ARTIFACT_REFERENCE_PROVENANCE = "server_capture_v1"


@dataclass(frozen=True)
class _ResolvedContext:
    workflow_id: int
    session_id: int
    task_id: int | None
    context_pack_id: int | None
    tool_name: str | None
    origin: str


@dataclass(frozen=True)
class CasGcResult:
    evicted_artifact_ids: list[int]
    removed_orphan_hashes: list[str]
    retained_artifact_ids: list[int]


@dataclass(frozen=True)
class _GcDecision:
    artifact: SessionArtifact
    blob_hash: str
    tier: str
    evict: bool


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: int
    kind: str
    content_hash: str
    tool_name: str | None
    tool_call_id: str
    generated_at: str
    observed_at: str
    data_as_of: str | None
    locator: str
    byte_size: int
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "generated_at": self.generated_at,
            "observed_at": self.observed_at,
            "data_as_of": self.data_as_of,
            "locator": self.locator,
            "byte_size": self.byte_size,
            "summary": self.summary,
        }


@contextmanager
def desk_execution_context(values: dict[str, Any]) -> Iterator[None]:
    token = _DESK_EXECUTION_CONTEXT.set(dict(values))
    try:
        yield
    finally:
        _DESK_EXECUTION_CONTEXT.reset(token)


class ContentAddressedFilesystemBackend(BackendProtocol):
    """Store routed ``/large_tool_results`` blobs outside LangGraph checkpoints."""

    def __init__(
        self,
        *,
        root_dir: str | Path | None = None,
        virtual_prefix: str = "/large_tool_results",
    ) -> None:
        self.root_dir = Path(root_dir) if root_dir is not None else _default_blob_root()
        self.virtual_prefix = virtual_prefix.rstrip("/")

    def write(
        self,
        file_path: str,
        content: str,
        config: RunnableConfig | None = None,
    ) -> WriteResult:
        try:
            virtual_path = self._virtual_path(file_path)
            self.capture_tool_result(
                tool_call_id=_tool_call_id_from_path(virtual_path),
                tool_name=None,
                content=content,
                tool_args=None,
                config=config,
                classification="filesystem_eviction",
                virtual_path=virtual_path,
            )
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=f"CAS write failed for {file_path}: {exc}")

    def capture_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str | None,
        content: str,
        tool_args: dict[str, Any] | None,
        config: RunnableConfig | None,
        classification: str,
        virtual_path: str | None = None,
    ) -> dict[str, Any]:
        """Persist an exact tool result and return its compact reference.

        Capture is idempotent for ``(workflow_id, tool_call_id, content_hash)`` so
        proactive ground-truth capture and DeepAgents' later large-result eviction
        share one immutable ledger row.
        """
        call_id = str(tool_call_id)
        if not call_id:
            raise ValueError("tool_call_id must not be empty")
        safe_call_id = _safe_tool_call_id(call_id)
        path = virtual_path or self._virtual_path(safe_call_id)
        content_bytes = content.encode("utf-8")
        blob_hash = hashlib.sha256(content_bytes).hexdigest()
        blob_path = self._blob_path(blob_hash)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if blob_path.exists():
            if blob_path.read_bytes() != content_bytes:
                raise ValueError(f"CAS blob hash mismatch for {blob_hash}")
        else:
            blob_path.write_bytes(content_bytes)

        context = self._resolve_context(config)
        resolved_tool_name = tool_name or context.tool_name
        now = _utc_timestamp()
        parsed = _parse_json_value(content)
        data_as_of = _extract_data_as_of(parsed if isinstance(parsed, dict) else None)
        summary = _structural_summary(parsed)
        input_hash = _canonical_input_hash(tool_args)

        with database.SessionLocal() as session:
            candidates = (
                session.query(SessionArtifact)
                .filter(
                    SessionArtifact.workflow_id == context.workflow_id,
                    SessionArtifact.tool_call_id == call_id,
                    SessionArtifact.kind == "tool_result",
                )
                .order_by(SessionArtifact.id.desc())
                .all()
            )
            artifact = next(
                (
                    row
                    for row in candidates
                    if (row.payload or {}).get("blob_hash") == blob_hash
                ),
                None,
            )
            if artifact is None:
                artifact = LedgerWriter(session).write_artifact(
                    workflow_id=context.workflow_id,
                    session_id=context.session_id,
                    task_id=context.task_id,
                    context_pack_id=context.context_pack_id,
                    kind="tool_result",
                    title=f"Tool result {call_id}",
                    payload={
                        "blob_hash": blob_hash,
                        "content_hash": f"sha256:{blob_hash}",
                        "size": len(content_bytes),
                        "byte_size": len(content_bytes),
                        "media_type": (
                            "application/json" if parsed is not None else "text/plain"
                        ),
                        "tool_call_id": call_id,
                        "tool_name": resolved_tool_name,
                        "input_hash": input_hash,
                        "generated_at": now,
                        "observed_at": now,
                        "data_as_of": data_as_of,
                        "summary": summary,
                        "summary_provenance": "deterministic",
                        "classification": classification,
                        "blob_state": "live",
                        "origin": context.origin,
                    },
                    rendered_path=path,
                    tool_call_id=call_id,
                    tool_name=resolved_tool_name,
                )
                session.commit()
            payload = dict(artifact.payload or {})
            reference = ArtifactReference(
                artifact_id=artifact.id,
                kind=artifact.kind,
                content_hash=str(
                    payload.get("content_hash") or f"sha256:{blob_hash}"
                ),
                tool_name=artifact.tool_name or resolved_tool_name,
                tool_call_id=call_id,
                generated_at=str(payload.get("generated_at") or now),
                observed_at=str(payload.get("observed_at") or now),
                data_as_of=(
                    str(payload["data_as_of"])
                    if payload.get("data_as_of") is not None
                    else None
                ),
                locator=path,
                byte_size=int(payload.get("byte_size") or len(content_bytes)),
                summary=(
                    dict(payload.get("summary") or {})
                    if isinstance(payload.get("summary"), dict)
                    else {}
                ),
            )
            return reference.as_dict()

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        virtual_path = self._virtual_path(file_path)
        artifact = self._latest_artifact(virtual_path)
        if artifact is None:
            return self._legacy_state_read(virtual_path, offset=offset, limit=limit)
        blob_hash = str((artifact.payload or {}).get("blob_hash") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", blob_hash):
            return ReadResult(error=f"Blob for '{virtual_path}' has an invalid hash")
        blob_path = self._blob_path(blob_hash)
        if not blob_hash or not blob_path.exists():
            return ReadResult(error=f"Blob for '{virtual_path}' not found")
        content_bytes = blob_path.read_bytes()
        if hashlib.sha256(content_bytes).hexdigest() != blob_hash:
            return ReadResult(error=f"Blob for '{virtual_path}' failed hash verification")
        content = content_bytes.decode("utf-8")
        return ReadResult(
            file_data={
                "content": _slice_lines(content, offset=offset, limit=limit),
                "encoding": "utf-8",
            }
        )

    def ls(self, path: str) -> LsResult:
        prefix = self._virtual_dir(path)
        entries: list[FileInfo] = []
        seen: set[str] = set()
        with database.SessionLocal() as session:
            rows = (
                session.query(SessionArtifact)
                .filter(
                    SessionArtifact.kind == "tool_result",
                    SessionArtifact.rendered_path.like(f"{prefix}%"),
                )
                .order_by(SessionArtifact.rendered_path)
                .all()
            )
            for row in rows:
                rendered = row.rendered_path or ""
                relative = rendered[len(self.virtual_prefix) :].lstrip("/")
                if not relative:
                    continue
                path_value = "/" + relative
                if path_value in seen:
                    continue
                seen.add(path_value)
                entries.append(
                    {
                        "path": path_value,
                        "is_dir": False,
                        "size": int((row.payload or {}).get("size") or 0),
                        "modified_at": row.created_at.isoformat(),
                    }
                )
        return LsResult(entries=entries)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        # The large-result store is flat for now; glob support starts as the
        # ls-visible file set, which is enough for DeepAgents path discovery.
        entries = self.ls(path).entries or []
        return GlobResult(matches=entries)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Search content-addressed blobs for literal text.

        DeepAgents' CompositeBackend delegates /large_tool_results grep calls to
        this backend. Returning a structured GrepResult keeps tool failures
        recoverable instead of letting BackendProtocol raise NotImplementedError.
        """
        try:
            matches = []
            for virtual_path, content in self._iter_searchable_blobs(path or "/", glob):
                backend_path = self._backend_path(virtual_path)
                lines = content.splitlines() or [content]
                for line_number, line in enumerate(lines, start=1):
                    if pattern not in line:
                        continue
                    matches.append(
                        {
                            "path": backend_path,
                            "line": line_number,
                            "text": line,
                        }
                    )
            return GrepResult(matches=matches)
        except Exception as exc:
            return GrepResult(error=f"CAS grep failed for {path or '/'}: {exc}")

    def _virtual_path(self, file_path: str) -> str:
        raw = file_path if file_path.startswith("/") else f"/{file_path}"
        path = PurePosixPath(raw)
        if ".." in path.parts or "~" in path.parts or str(path) == "/":
            raise ValueError(f"invalid CAS path: {file_path}")
        if str(path).startswith(f"{self.virtual_prefix}/"):
            return str(path)
        return f"{self.virtual_prefix}{path}"

    def _virtual_dir(self, path: str) -> str:
        virtual = self._virtual_path(path) if path != "/" else self.virtual_prefix
        return virtual if virtual.endswith("/") else f"{virtual}/"

    def _blob_path(self, blob_hash: str) -> Path:
        if not blob_hash:
            return self.root_dir / "__missing__"
        return self.root_dir / blob_hash[:2] / f"{blob_hash}.json"

    def _latest_artifact(self, virtual_path: str) -> SessionArtifact | None:
        with database.SessionLocal() as session:
            row = (
                session.query(SessionArtifact)
                .filter(
                    SessionArtifact.kind == "tool_result",
                    SessionArtifact.rendered_path == virtual_path,
                )
                .order_by(SessionArtifact.id.desc())
                .first()
            )
            if row is None:
                return None
            session.expunge(row)
            return row

    def _iter_searchable_blobs(
        self,
        path: str,
        glob_filter: str | None,
    ) -> Iterator[tuple[str, str]]:
        virtual_path = (
            self.virtual_prefix
            if path in {"", "/"}
            else self._virtual_path(path)
        )
        exact = self._latest_artifact(virtual_path)
        if exact is not None:
            artifacts = [exact]
        else:
            prefix = (
                self._virtual_dir(path)
                if path not in {"", "/"}
                else f"{self.virtual_prefix}/"
            )
            artifacts = self._latest_artifacts_under(prefix)
        for artifact in artifacts:
            rendered = artifact.rendered_path or ""
            backend_path = self._backend_path(rendered).lstrip("/")
            if glob_filter and not fnmatch(backend_path, glob_filter):
                continue
            blob_hash = str((artifact.payload or {}).get("blob_hash") or "")
            blob_path = self._blob_path(blob_hash)
            if not blob_hash or not blob_path.exists():
                continue
            yield rendered, blob_path.read_text(encoding="utf-8")

    def _latest_artifacts_under(self, virtual_prefix: str) -> list[SessionArtifact]:
        with database.SessionLocal() as session:
            rows = (
                session.query(SessionArtifact)
                .filter(
                    SessionArtifact.kind == "tool_result",
                    SessionArtifact.rendered_path.like(f"{virtual_prefix}%"),
                )
                .order_by(SessionArtifact.id.desc())
                .all()
            )
            out: list[SessionArtifact] = []
            seen: set[str] = set()
            for row in rows:
                rendered = row.rendered_path or ""
                if rendered in seen:
                    continue
                seen.add(rendered)
                session.expunge(row)
                out.append(row)
            return sorted(out, key=lambda item: item.rendered_path or "")

    def _backend_path(self, virtual_path: str) -> str:
        if virtual_path.startswith(f"{self.virtual_prefix}/"):
            return "/" + virtual_path[len(self.virtual_prefix) :].lstrip("/")
        return virtual_path

    def _resolve_context(
        self,
        config: RunnableConfig | None,
    ) -> _ResolvedContext:
        values = _configurable(config) or _configurable(_active_graph_config())
        if not values:
            values = _DESK_EXECUTION_CONTEXT.get() or {}
        if values.get("workflow_id") and values.get("session_id"):
            return _ResolvedContext(
                workflow_id=int(values["workflow_id"]),
                session_id=int(values["session_id"]),
                task_id=_optional_int(values.get("task_id")),
                context_pack_id=_optional_int(values.get("context_pack_id")),
                tool_name=(
                    str(values["tool_name"]) if values.get("tool_name") else None
                ),
                origin="task_config",
            )
        thread_id = _thread_id_from_config(values)
        if thread_id is not None:
            with database.SessionLocal() as session:
                state = ensure_thread_workflow_state(session, thread_id)
                session.commit()
                return _ResolvedContext(
                    workflow_id=state.meta_workflow_id,
                    session_id=state.router_session_id,
                    task_id=None,
                    context_pack_id=state.context_pack_id,
                    tool_name=(
                        str(values["tool_name"]) if values.get("tool_name") else None
                    ),
                    origin="legacy_unflagged",
                )
        raise ValueError("missing workflow/session context for CAS write")

    def _legacy_state_read(
        self,
        virtual_path: str,
        *,
        offset: int,
        limit: int,
    ) -> ReadResult:
        try:
            from deepagents.backends import StateBackend

            return StateBackend().read(virtual_path, offset=offset, limit=limit)
        except Exception:
            return ReadResult(error=f"File '{virtual_path}' not found")


def sweep_cas_blobs(
    *,
    root_dir: str | Path | None = None,
    now: datetime | None = None,
    provisional_ttl_days: int = 180,
    superseded_ttl_days: int = 90,
    orphan_ttl_hours: int = 24,
    dry_run: bool = False,
) -> CasGcResult:
    """Apply audit-safe retention to content-addressed artifact blobs."""
    root = Path(root_dir) if root_dir is not None else _default_blob_root()
    anchor = now or utcnow()
    provisional_ttl = timedelta(days=provisional_ttl_days)
    superseded_ttl = timedelta(days=superseded_ttl_days)
    orphan_ttl = timedelta(hours=orphan_ttl_hours)

    evicted_ids: list[int] = []
    retained_ids: list[int] = []
    removed_orphans: list[str] = []
    retained_hashes: set[str] = set()
    decisions: list[_GcDecision] = []

    with database.SessionLocal() as session:
        for artifact in session.query(SessionArtifact).order_by(SessionArtifact.id).all():
            payload = dict(artifact.payload or {})
            blob_hash = str(payload.get("blob_hash") or "")
            if not blob_hash or payload.get("blob_state") == "gc_evicted":
                continue
            tier = _retention_tier(session, artifact)
            evict = _eligible_for_eviction(
                artifact,
                tier=tier,
                now=anchor,
                provisional_ttl=provisional_ttl,
                superseded_ttl=superseded_ttl,
            )
            decisions.append(
                _GcDecision(
                    artifact=artifact,
                    blob_hash=blob_hash,
                    tier=tier,
                    evict=evict,
                )
            )
            if evict:
                evicted_ids.append(artifact.id)
            else:
                retained_ids.append(artifact.id)
                retained_hashes.add(blob_hash)

        writer = LedgerWriter(session)
        for decision in decisions:
            if not decision.evict:
                continue
            payload = dict(decision.artifact.payload or {})
            size = payload.get("size")
            if decision.blob_hash not in retained_hashes and not dry_run:
                _blob_path(root, decision.blob_hash).unlink(missing_ok=True)
            if dry_run:
                continue
            payload.update(
                {
                    "blob_state": "gc_evicted",
                    "gc_tier": decision.tier,
                    "gc_evicted_at": anchor.isoformat(),
                }
            )
            decision.artifact.payload = payload
            writer.emit_event(
                workflow_id=decision.artifact.workflow_id,
                session_id=decision.artifact.session_id,
                task_id=decision.artifact.task_id,
                artifact_id=decision.artifact.id,
                kind="artifact_gc'd",
                payload={
                    "artifact_id": decision.artifact.id,
                    "blob_hash": decision.blob_hash,
                    "size": size,
                    "tier": decision.tier,
                },
                actor="system",
            )

        if not dry_run:
            session.commit()

    if root.exists():
        for path in sorted(root.glob("*/*.json")):
            blob_hash = path.stem
            if blob_hash in retained_hashes:
                continue
            modified_at = datetime.utcfromtimestamp(path.stat().st_mtime)
            if anchor - modified_at < orphan_ttl:
                continue
            removed_orphans.append(blob_hash)
            if not dry_run:
                path.unlink(missing_ok=True)

    return CasGcResult(
        evicted_artifact_ids=evicted_ids,
        removed_orphan_hashes=removed_orphans,
        retained_artifact_ids=retained_ids,
    )


def _retention_tier(session: Session, artifact: SessionArtifact) -> str:
    if artifact.pinned or artifact.kind in {"persisted_run", "report", "tool_result"}:
        return "load-bearing"

    evidence_kinds = {
        row[0]
        for row in session.query(ArtifactEvidenceRef.evidence_kind)
        .filter(ArtifactEvidenceRef.artifact_id == artifact.id)
        .all()
    }
    if evidence_kinds & LOAD_BEARING_EVIDENCE_KINDS:
        return "load-bearing"

    if artifact.superseded_by is not None and not artifact.pinned:
        superseding = session.get(SessionArtifact, artifact.superseded_by)
        if superseding is not None:
            superseding_payload = superseding.payload or {}
            if superseding_payload.get("blob_state") != "gc_evicted":
                return "superseded"

    if (
        artifact.kind in {"claim", "finding", "plan"}
        and evidence_kinds <= {"agent_attestation", "context_pack"}
        and not _artifact_is_cited(session, artifact.id)
    ):
        return "provisional"

    return "retained"


def _eligible_for_eviction(
    artifact: SessionArtifact,
    *,
    tier: str,
    now: datetime,
    provisional_ttl: timedelta,
    superseded_ttl: timedelta,
) -> bool:
    if tier == "provisional":
        return _older_than(artifact.created_at, now, provisional_ttl)
    if tier == "superseded":
        return _older_than(artifact.created_at, now, superseded_ttl)
    return False


def _older_than(created_at: datetime | None, now: datetime, ttl: timedelta) -> bool:
    if created_at is None:
        return False
    return now - created_at >= ttl


def _artifact_is_cited(session: Session, artifact_id: int) -> bool:
    if (
        session.query(SessionArtifact.id)
        .filter(SessionArtifact.superseded_by == artifact_id)
        .first()
        is not None
    ):
        return True

    for row in session.query(SessionArtifact.payload).all():
        if _contains_artifact_id(row[0], artifact_id):
            return True
    for row in session.query(ContextPackPayload.stable_payload).all():
        if _contains_artifact_id(row[0], artifact_id):
            return True
    return False


def _contains_artifact_id(payload: Any, artifact_id: int) -> bool:
    if not isinstance(payload, dict):
        return False
    cited = payload.get("cited_artifact_ids")
    return isinstance(cited, list) and artifact_id in {
        item for item in cited if isinstance(item, int)
    }


def _default_blob_root() -> Path:
    try:
        return Path(database.settings.artifact_dir) / "artifact_blobs"
    except Exception:
        return Path("data/artifact_blobs")


def _configurable(config: RunnableConfig | None) -> dict[str, Any] | None:
    if not config:
        return None
    values = config.get("configurable") or {}
    return dict(values)


def _active_graph_config() -> RunnableConfig | None:
    try:
        from langgraph.config import get_config

        return get_config()
    except Exception:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _thread_id_from_config(values: dict[str, Any]) -> int | None:
    raw = values.get("parent_thread_id") or values.get("thread_id")
    try:
        text = str(raw)
        return int(text.split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def _tool_call_id_from_path(virtual_path: str) -> str:
    name = PurePosixPath(virtual_path).name
    return name.rsplit(".", 1)[0] if "." in name else name


def _safe_tool_call_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    if not safe:
        raise ValueError("tool_call_id does not contain a safe path component")
    return safe[:120]


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_json_value(content: str) -> Any | None:
    try:
        return json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _extract_data_as_of(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("valuation_as_of", "data_as_of", "as_of"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _structural_summary(payload: Any | None) -> dict[str, Any]:
    if payload is None:
        return {"format": "text"}
    if isinstance(payload, list):
        return {"format": "json", "top_level_type": "list", "count": len(payload)}
    if not isinstance(payload, dict):
        return {"format": "json", "top_level_type": type(payload).__name__}
    summary: dict[str, Any] = {
        "format": "json",
        "top_level_keys": sorted(str(key)[:120] for key in payload)[:40],
    }
    status = payload.get("status")
    if isinstance(status, (bool, int, float)):
        summary["status"] = status
    elif isinstance(status, str):
        summary["status"] = status[:120]
    ids = {
        str(key)[:120]: value if isinstance(value, int) else value[:120]
        for key, value in payload.items()
        if str(key).endswith("_id") and isinstance(value, (int, str))
    }
    if ids:
        summary["ids"] = ids
    counts = {
        str(key): len(value)
        for key, value in payload.items()
        if isinstance(value, (list, dict))
    }
    if counts:
        summary["counts"] = counts
    return summary


def _canonical_input_hash(tool_args: dict[str, Any] | None) -> str:
    body = json.dumps(
        tool_args or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _slice_lines(content: str, *, offset: int, limit: int) -> str:
    if limit <= 0:
        return ""
    lines = content.splitlines()
    if not lines:
        return content
    return "\n".join(lines[offset : offset + limit])


def _blob_path(root_dir: Path, blob_hash: str) -> Path:
    return root_dir / blob_hash[:2] / f"{blob_hash}.json"
