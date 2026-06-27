from __future__ import annotations
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import yaml
from app.golden_workflows.schema import (
    GoldenWorkflow, parse_workflow, normalize_tool_name, normalize_skill,
    WorkflowError, NarrationMismatchError, MissingReplayError, FixturePathError,
    UnknownToolError, SkillNameCollisionError, ToolNameCollisionError,
    DuplicateWorkflowError,
)
from app.golden_workflows.fixtures import load_fixtures, FixtureBundle
from app.golden_workflows.assertions import resolve_seed_refs

_DEFS = Path(__file__).parent / "definitions"
# parents[0] = golden_workflows/, parents[1] = app/
_SKILLS = Path(__file__).resolve().parents[1] / "skills" / "workflows"
_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_HEADING = re.compile(r"^##\s+Step\s+(\d+)\s+[—-]\s+.*$", re.M)


@lru_cache(maxsize=1)
def skill_names() -> set[str]:
    names: dict[str, Path] = {}
    for sk in _SKILLS.rglob("SKILL.md"):
        fm = _FM.match(sk.read_text())
        if not fm:
            continue
        meta = yaml.safe_load(fm.group(1)) or {}
        n = normalize_skill(str(meta.get("name", "")))
        if n in names:
            raise SkillNameCollisionError(f"{n}: {names[n]} vs {sk}")
        names[n] = sk
    return set(names)


@lru_cache(maxsize=1)
def agent_tool_names() -> set[str]:
    from app.tools import all_agent_tools
    seen: dict[str, str] = {}
    for t in all_agent_tools():
        n = normalize_tool_name(t.name)
        if n in seen and seen[n] != t.name:
            raise ToolNameCollisionError(f"{seen[n]} and {t.name} both normalize to {n}")
        seen[n] = t.name
    return set(seen)


def _parse_narration(body: str, n_steps: int) -> list[str]:
    headings = list(_HEADING.finditer(body))
    if len(headings) != n_steps:
        raise NarrationMismatchError(f"{len(headings)} narration blocks for {n_steps} steps")
    for i, h in enumerate(headings, start=1):
        if int(h.group(1)) != i:
            raise NarrationMismatchError(f"step heading {h.group(1)} out of order (want {i})")
    blocks = []
    for i, h in enumerate(headings):
        start = h.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        blocks.append(body[start:end].strip())
    return blocks


@dataclass
class LoadedWorkflow:
    workflow: GoldenWorkflow
    fixtures: FixtureBundle
    definition_path: Path


# $seed refs are resolved on the RAW frontmatter dict BEFORE parse_workflow,
# so typed assertion fields (gte/lte: float, equals, args) receive real values
# and Pydantic validation never sees a literal "$seed..." string.

def load_workflow_bundle(md_path: Path) -> LoadedWorkflow:
    md_path = Path(md_path)
    fm = _FM.match(md_path.read_text())
    if not fm:
        raise WorkflowError(f"{md_path}: missing frontmatter")
    data = yaml.safe_load(fm.group(1)) or {}
    if data.get("id") != md_path.stem:
        raise WorkflowError(f"id {data.get('id')} != filename {md_path.stem}")
    fixtures_ref = data.get("fixtures", "")
    if "/" in fixtures_ref or ".." in fixtures_ref:
        raise FixturePathError(fixtures_ref)
    bundle = load_fixtures(md_path.parent / fixtures_ref)
    # Resolve $seed refs BEFORE parse so typed fields get real values
    data = resolve_seed_refs(data, bundle.seed_map)
    wf = parse_workflow(data)
    for step in wf.steps:
        if step.replay not in bundle.replay:
            raise MissingReplayError(step.replay)
        if normalize_skill(step.expected_skill) not in skill_names():
            raise WorkflowError(f"unknown skill {step.expected_skill}")
        for te in step.expected_tools:
            if normalize_tool_name(te.name) not in agent_tool_names():
                raise UnknownToolError(te.name)
    wf.narration = _parse_narration(fm.group(2), len(wf.steps))
    return LoadedWorkflow(workflow=wf, fixtures=bundle, definition_path=md_path)


def list_workflow_bundles() -> list[LoadedWorkflow]:
    seen: dict[str, Path] = {}
    out = []
    for md in sorted(_DEFS.glob("*.md")):
        if md.stem in seen:
            raise DuplicateWorkflowError(md.stem)
        seen[md.stem] = md
        out.append(load_workflow_bundle(md))
    return out


def get_workflow_bundle(wf_id: str) -> LoadedWorkflow:
    return load_workflow_bundle(_DEFS / f"{wf_id}.md")


# Convenience wrappers (workflow only)
def load_workflow(md_path: Path) -> GoldenWorkflow:
    return load_workflow_bundle(md_path).workflow


def get_workflow(wf_id: str) -> GoldenWorkflow:
    return get_workflow_bundle(wf_id).workflow


def list_workflows() -> list[GoldenWorkflow]:
    return [b.workflow for b in list_workflow_bundles()]
