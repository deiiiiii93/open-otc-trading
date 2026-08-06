"""Report template spec: the YAML document a template stores.

A spec declares which deterministic blocks each section shows and, optionally,
a narrative brief for the agent. It cannot express execution — that is why an
agent may author one without a sandbox.

``validate_spec`` collects every error rather than stopping at the first, so a
template author sees the whole list in one 422 instead of fixing errors one
round-trip at a time.
"""
from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from .registry import UnknownBlockError, require_block
from .renderers import known_renderers, renderer_accepts

VALID_PERSONAS: tuple[str, ...] = ("trader", "risk_manager", "high_board")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECTION_ID_RE = re.compile(r"^[a-z0-9_]+$")


class TemplateSpecError(Exception):
    """Raised when a spec is malformed or references something unresolvable."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class BlockRef(BaseModel):
    key: str
    render: str
    fields: list[str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SectionSpec(BaseModel):
    id: str
    title: str = ""
    blocks: list[BlockRef] = Field(default_factory=list)
    narrative: str | None = None


class TemplateMeta(BaseModel):
    slug: str
    title: str
    persona: str
    description: str = ""
    params: list[dict[str, Any]] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    meta: TemplateMeta
    sections: list[SectionSpec]


def parse_spec(yaml_text: str) -> TemplateSpec:
    """Parse YAML into a TemplateSpec. Raises TemplateSpecError."""
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise TemplateSpecError([f"template spec is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise TemplateSpecError(
            ["template spec must be a YAML mapping with 'meta' and 'sections'"]
        )
    try:
        return TemplateSpec.model_validate(raw)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        ]
        raise TemplateSpecError(errors) from exc


def validate_spec(spec: TemplateSpec) -> None:
    """Check every reference resolves. Raises TemplateSpecError listing all problems."""
    errors: list[str] = []

    if not _SLUG_RE.match(spec.meta.slug):
        errors.append(
            f"meta.slug {spec.meta.slug!r} must be kebab-case (lowercase, digits, hyphens)"
        )
    if spec.meta.persona not in VALID_PERSONAS:
        errors.append(
            f"meta.persona {spec.meta.persona!r} is not a known persona; "
            f"expected one of {list(VALID_PERSONAS)}"
        )
    if not spec.sections:
        errors.append("sections must contain at least one section")

    seen_ids: set[str] = set()
    for index, section in enumerate(spec.sections):
        where = f"sections[{index}]"
        if not _SECTION_ID_RE.match(section.id or ""):
            errors.append(
                f"{where}.id {section.id!r} must be lowercase letters, digits or underscores"
            )
        if section.id in seen_ids:
            errors.append(f"{where}.id {section.id!r} is duplicated")
        seen_ids.add(section.id)

        if not section.blocks and not (section.narrative or "").strip():
            errors.append(
                f"{where} declares neither blocks nor a narrative, so it would render empty"
            )

        for block_index, ref in enumerate(section.blocks):
            block_where = f"{where}.blocks[{block_index}]"
            try:
                block = require_block(ref.key)
            except UnknownBlockError:
                errors.append(
                    f"{block_where}.key {ref.key!r} is not a registered block"
                )
                continue

            if ref.render not in known_renderers():
                errors.append(
                    f"{block_where}.render {ref.render!r} is not a known renderer; "
                    f"expected one of {known_renderers()}"
                )
            elif not renderer_accepts(ref.render, block.shape):
                errors.append(
                    f"{block_where}: renderer {ref.render!r} cannot render block "
                    f"{ref.key!r}, whose shape is {block.shape.value!r}"
                )

    if errors:
        raise TemplateSpecError(errors)


def spec_block_keys(spec: TemplateSpec) -> list[str]:
    """Every distinct block key a spec references, in declaration order."""
    keys: list[str] = []
    for section in spec.sections:
        for ref in section.blocks:
            if ref.key not in keys:
                keys.append(ref.key)
    return keys


__all__ = [
    "VALID_PERSONAS",
    "BlockRef",
    "SectionSpec",
    "TemplateMeta",
    "TemplateSpec",
    "TemplateSpecError",
    "parse_spec",
    "validate_spec",
    "spec_block_keys",
]
