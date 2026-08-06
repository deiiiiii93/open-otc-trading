"""The report generation pipeline.

Order (spec §5.5): load template -> resolve comparison run -> resolve every
declared block deterministically -> narrate each section that asks for it ->
grounding-guard the prose -> assemble -> persist.

The narrator is INJECTED rather than constructed here, so the pipeline is fully
testable without a model and the LLM binding lives at the tool/router edge.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import ReportJob, ReportStatus
from app.services.pnl import load_run_pair

from .contracts import BlockContext, BlockResult
from .document import build_document, narrator_brief
from .grounding import check_grounding
from .registry import resolve_block
from .template_spec import parse_spec
from .templates import get_template

# narrate(persona, brief) -> prose
Narrator = Callable[[str, dict[str, Any]], str]


class TemplateNotFound(Exception):
    """Raised when a report is generated from a slug that does not exist."""


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """Yield a session, committing only the one we own.

    ``generate_report`` persists a ReportJob, so a self-owned session must
    commit rather than flush-and-discard. See the same note in
    ``templates.py``.
    """
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess
        sess.commit()


def _resolve_comparison(ctx: BlockContext, session: Session) -> int | None:
    """Resolve the prior run to compare against, or None when there is none."""
    before, _after = load_run_pair(
        portfolio_id=ctx.portfolio_id,
        risk_run_id=ctx.risk_run_id,
        compare_to_run_id=ctx.compare_to_run_id,
        session=session,
    )
    return before.id if before is not None else None


def generate_document(
    *,
    template_slug: str,
    portfolio_id: int,
    compare_to: int | None = None,
    narrate: Narrator | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Build a ReportDocument. Does not persist."""
    with _session_scope(session) as sess:
        row = get_template(slug=template_slug, session=sess)
        if row is None:
            raise TemplateNotFound(f"no report template with slug {template_slug!r}")

        spec_yaml = row.spec
        spec = parse_spec(spec_yaml)

        base_ctx = BlockContext(
            portfolio_id=portfolio_id, compare_to_run_id=compare_to
        )
        compare_to_run_id = _resolve_comparison(base_ctx, sess)

        # Resolve every distinct key ONCE, even when several sections reuse it.
        results: dict[str, BlockResult] = {}
        provenance: dict[str, Any] = {}
        for section in spec.sections:
            for ref in section.blocks:
                if ref.key in results:
                    continue
                ctx = BlockContext(
                    portfolio_id=portfolio_id,
                    compare_to_run_id=compare_to_run_id,
                    params=dict(ref.params),
                )
                result = resolve_block(ref.key, ctx)
                results[ref.key] = result
                for key, value in (result.provenance or {}).items():
                    provenance.setdefault(key, value)

        sections: list[dict[str, Any]] = []
        for section in spec.sections:
            block_entries = [
                {
                    "key": ref.key,
                    "render": ref.render,
                    "fields": ref.fields,
                    "result": results[ref.key].model_dump(),
                }
                for ref in section.blocks
                if ref.key in results
            ]

            narrative: str | None = None
            narrative_error: str | None = None
            grounding: dict[str, Any] = {"checked": False, "flags": [],
                                         "grounded_count": 0}

            instruction = (section.narrative or "").strip()
            if instruction and narrate is not None:
                brief = narrator_brief(section, results)
                try:
                    narrative = (narrate(spec.meta.persona, brief) or "").strip() or None
                except Exception as exc:  # noqa: BLE001 - degrade one section only
                    narrative_error = str(exc)
                if narrative:
                    grounding = check_grounding(
                        narrative,
                        [entry["result"]["data"] for entry in block_entries],
                    )

            sections.append(
                {
                    "id": section.id,
                    "title": section.title,
                    "blocks": block_entries,
                    "narrative": narrative,
                    "narrative_error": narrative_error,
                    "grounding": grounding,
                }
            )

        return build_document(
            spec=spec,
            spec_yaml=spec_yaml,
            version=row.version,
            params={
                "portfolio_id": portfolio_id,
                "compare_to_run_id": compare_to_run_id,
            },
            sections=sections,
            provenance=provenance,
        )


def _status_from_document(document: dict[str, Any]) -> str:
    """A report whose blocks all failed is not a clean report."""
    statuses = [
        entry["result"]["status"]
        for section in document["sections"]
        for entry in section["blocks"]
    ]
    if statuses and all(status == "unavailable" for status in statuses):
        return ReportStatus.COMPLETED_WITH_ERRORS.value
    if any(section.get("narrative_error") for section in document["sections"]):
        return ReportStatus.COMPLETED_WITH_ERRORS.value
    return ReportStatus.COMPLETED.value


def generate_report(
    *,
    template_slug: str,
    portfolio_id: int,
    compare_to: int | None = None,
    narrate: Narrator | None = None,
    session: Session | None = None,
) -> ReportJob:
    """Generate and persist a report as a ReportJob."""
    with _session_scope(session) as sess:
        document = generate_document(
            template_slug=template_slug,
            portfolio_id=portfolio_id,
            compare_to=compare_to,
            narrate=narrate,
            session=sess,
        )
        job = ReportJob(
            report_type=document["template"]["persona"],
            status=_status_from_document(document),
            request_payload={
                "template_slug": template_slug,
                "portfolio_id": portfolio_id,
                "compare_to": compare_to,
                "title": document["template"]["title"],
            },
            result_payload=document,
            artifact_paths={},
            template_slug=template_slug,
            compare_to_run_id=document["params"]["compare_to_run_id"],
        )
        sess.add(job)
        sess.flush()
        return job


__all__ = [
    "Narrator",
    "TemplateNotFound",
    "generate_document",
    "generate_report",
]
