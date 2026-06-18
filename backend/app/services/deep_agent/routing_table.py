"""Generate the orchestrator "Known single-persona skills" table.

The table rows come from the optional `routing` frontmatter list on workflow
skills (`[{request, persona}, ...]`). Skills without `routing` are deliberate
sub-workflows reached via persona catalogs and never appear here. The
orchestrator prompt file carries a sentinel comment that `_orchestrator_prompt`
replaces at agent build time, so UI edits become routable after a rebuild.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .skill_lint import iter_skill_files, parse_skill_file
from .skills_paths import WORKFLOWS_DIR

KNOWN_SKILLS_SENTINEL = "<!-- KNOWN_SKILLS_TABLE -->"
_HEADER = ("Request shape", "Persona", "Suggested skill")


@dataclass(frozen=True)
class RoutingRow:
    domain: str
    skill: str
    request: str
    persona: str


def collect_routing_rows(root: Path = WORKFLOWS_DIR) -> list[RoutingRow]:
    """Routing rows from every workflow skill, sorted (domain, skill, request).

    Malformed entries are skipped here — they are CI-blocked by skill lint
    (`invalid_routing`), and the generator must not crash agent builds on a
    file lint already rejects.
    """
    rows: list[RoutingRow] = []
    for path in iter_skill_files(Path(root)):
        parsed = parse_skill_file(path)
        routing = parsed.frontmatter.get("routing")
        if not isinstance(routing, list):
            continue
        skill = parsed.frontmatter.get("name") or path.parent.name
        domain = parsed.frontmatter.get("domain") or path.parent.parent.name
        for entry in routing:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("request"), str)
                and isinstance(entry.get("persona"), str)
            ):
                rows.append(
                    RoutingRow(
                        domain=str(domain),
                        skill=str(skill),
                        request=entry["request"],
                        persona=entry["persona"],
                    )
                )
    rows.sort(key=lambda row: (row.domain, row.skill, row.request))
    return rows


def render_known_skills_table(rows: Sequence[RoutingRow]) -> str:
    cells = [(row.request, row.persona, row.skill) for row in rows]
    widths = [
        max(len(_HEADER[i]), *(len(cell[i]) for cell in cells))
        if cells
        else len(_HEADER[i])
        for i in range(3)
    ]

    def fmt(values: tuple[str, str, str]) -> str:
        return (
            "| "
            + " | ".join(value.ljust(widths[i]) for i, value in enumerate(values))
            + " |"
        )

    separator = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
    return "\n".join([fmt(_HEADER), separator, *(fmt(cell) for cell in cells)])


def inject_known_skills_table(prompt: str, root: Path = WORKFLOWS_DIR) -> str:
    if KNOWN_SKILLS_SENTINEL not in prompt:
        raise ValueError(
            "orchestrator prompt is missing the KNOWN_SKILLS_TABLE sentinel; "
            "the routing table cannot be injected"
        )
    if prompt.count(KNOWN_SKILLS_SENTINEL) > 1:
        raise ValueError(
            "orchestrator prompt contains multiple KNOWN_SKILLS_TABLE sentinels"
        )
    table = render_known_skills_table(collect_routing_rows(root))
    return prompt.replace(KNOWN_SKILLS_SENTINEL, table)


__all__ = [
    "KNOWN_SKILLS_SENTINEL",
    "RoutingRow",
    "collect_routing_rows",
    "inject_known_skills_table",
    "render_known_skills_table",
]
