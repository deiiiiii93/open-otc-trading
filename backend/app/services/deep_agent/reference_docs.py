"""Reference-document validation for the Phase 3 skill catalog."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .skills_paths import REFERENCES_DIR


REFERENCE_DOC_REQUIRED_FIELDS = {
    "name",
    "description",
    "reference_type",
}
VALID_REFERENCE_TYPES = {
    "product",
    "pricing",
    "market_data",
    "portfolio",
    "rfq",
    "hedging",
    "risk",
}


@dataclass(frozen=True)
class ReferenceDoc:
    path: Path
    frontmatter: dict[str, Any]
    body: str


def parse_reference_doc(path: Path) -> ReferenceDoc:
    text = Path(path).read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, path=Path(path))
    return ReferenceDoc(
        path=Path(path),
        frontmatter=frontmatter,
        body=body.strip(),
    )


def validate_reference_doc_tree(root: Path = REFERENCES_DIR) -> list[ReferenceDoc]:
    return [validate_reference_doc_file(path) for path in sorted(Path(root).rglob("*.md"))]


def validate_reference_doc_file(path: Path) -> ReferenceDoc:
    doc = parse_reference_doc(path)
    missing = sorted(REFERENCE_DOC_REQUIRED_FIELDS - set(doc.frontmatter))
    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    name = doc.frontmatter.get("name")
    if name != doc.path.stem:
        errors.append(f"name must match filename stem {doc.path.stem!r}")

    description = doc.frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
    elif len(description) > 200:
        errors.append("description must be <=200 characters")

    reference_type = doc.frontmatter.get("reference_type")
    if reference_type not in VALID_REFERENCE_TYPES:
        errors.append(f"reference_type must be one of {sorted(VALID_REFERENCE_TYPES)}")

    if not doc.body.startswith("## "):
        errors.append("body must start with a markdown h2 heading")

    if errors:
        raise ValueError(f"Invalid reference doc {doc.path}: {'; '.join(errors)}")
    return doc


def _split_frontmatter(text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"Reference doc missing frontmatter: {path}")
    try:
        _, rest = text.split("---\n", 1)
        raw_frontmatter, body = rest.split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"Reference doc has malformed frontmatter fences: {path}") from exc
    loaded = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Reference doc frontmatter must be a mapping: {path}")
    return loaded, body
