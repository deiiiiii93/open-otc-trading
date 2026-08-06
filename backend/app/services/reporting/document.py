"""ReportDocument shape and assembly.

A document embeds the full template spec plus its sha256, so a report is a
self-contained reproducible artifact: editing a template later cannot silently
change what an old report claims to have been generated from, and no stored
hash points at a row that may since have moved.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .contracts import BlockResult
from .template_spec import SectionSpec, TemplateSpec


def spec_sha256(spec_yaml: str) -> str:
    digest = hashlib.sha256(spec_yaml.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def narrator_brief(
    section: SectionSpec,
    results: dict[str, BlockResult],
    upstream: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What the agent is shown for one section.

    Each block carries its status and reason, not just its data. A narrator that
    cannot see ``status == "unavailable"`` will write that the book is within
    limits when the limit check never ran.

    ``upstream`` carries the already-resolved sections and is supplied only to a
    SYNTHESIS section — one declaring no blocks of its own, whose job is to sum
    up what came before. Without it such a section is handed an empty brief and
    honestly reports that it was given nothing: a live run of the board
    one-pager produced exactly that, an executive summary stating "the evidence
    base is empty" while all five sections above it had resolved fine. It is
    withheld from ordinary sections so a brief stays focused on its own
    evidence.
    """
    brief: dict[str, Any] = {
        "section_id": section.id,
        "section_title": section.title,
        "instruction": (section.narrative or "").strip(),
        "blocks": [
            {
                "key": ref.key,
                "status": results[ref.key].status,
                "reason": results[ref.key].reason,
                "data": results[ref.key].data,
            }
            for ref in section.blocks
            if ref.key in results
        ],
    }
    if upstream:
        brief["report_so_far"] = upstream
    return brief


def build_document(
    *,
    spec: TemplateSpec,
    spec_yaml: str,
    version: int,
    params: dict[str, Any],
    sections: list[dict[str, Any]],
    provenance: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "template": {
            "slug": spec.meta.slug,
            "title": spec.meta.title,
            "persona": spec.meta.persona,
            "version": version,
            "spec": spec_yaml,
            "spec_sha256": spec_sha256(spec_yaml),
        },
        "params": params,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sections": sections,
        "provenance": provenance,
    }


__all__ = ["spec_sha256", "narrator_brief", "build_document"]
