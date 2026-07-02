"""Reference-document validation for the Phase 3 skill catalog."""
from __future__ import annotations

import re
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

    quantark_classes = doc.frontmatter.get("quantark_classes")
    if quantark_classes is not None and (
        not isinstance(quantark_classes, list)
        or not quantark_classes
        or not all(isinstance(c, str) and c.strip() for c in quantark_classes)
    ):
        errors.append("quantark_classes must be a non-empty list of class names")

    region = doc.frontmatter.get("region")
    if region is not None and (not isinstance(region, str) or not region.strip()):
        errors.append("region must be a non-empty string")

    extends = doc.frontmatter.get("extends")
    if extends is not None and (not isinstance(extends, str) or not extends.strip()):
        errors.append("extends must be a doc name string")

    if not doc.body.startswith("## "):
        errors.append("body must start with a markdown h2 heading")

    if errors:
        raise ValueError(f"Invalid reference doc {doc.path}: {'; '.join(errors)}")
    return doc


def validate_product_reference_tree(root: Path = REFERENCES_DIR) -> dict[str, ReferenceDoc]:
    """Validate product docs as a set: claims unique, extends resolvable
    (depth <=1), no neutral doc extending a region-marked base."""
    docs = validate_reference_doc_tree(root)
    products = {
        d.frontmatter["name"]: d for d in docs if d.frontmatter["reference_type"] == "product"
    }

    claims: dict[str, ReferenceDoc] = {}
    for doc in products.values():
        base_name = doc.frontmatter.get("extends")
        if base_name is not None:
            base = products.get(base_name)
            if base is None:
                raise ValueError(f"{doc.path.name}: unknown extends target {base_name!r}")
            if base.frontmatter.get("extends") is not None:
                raise ValueError(f"{doc.path.name}: extends chain deeper than one level")
            base_region = base.frontmatter.get("region")
            if base_region is not None and doc.frontmatter.get("region") != base_region:
                raise ValueError(
                    f"{doc.path.name}: cannot extend region-marked base {base_name!r} "
                    "without declaring the same region"
                )
        for cls in doc.frontmatter.get("quantark_classes") or []:
            if cls in claims:
                raise ValueError(f"{cls} claimed by more than one product doc")
            claims[cls] = doc
    return claims


@dataclass(frozen=True)
class ResolvedReferenceDoc:
    quantark_class: str
    content: str
    source_paths: tuple[Path, ...]


def resolve_product_reference(
    quantark_class: str, *, region: str | None = None, root: Path = REFERENCES_DIR
) -> ResolvedReferenceDoc:
    claims = validate_product_reference_tree(root)
    claiming = claims.get(quantark_class)
    if claiming is None:
        raise KeyError(f"No product reference doc claims {quantark_class!r}")

    docs = validate_reference_doc_tree(root)
    products = {
        d.frontmatter["name"]: d for d in docs if d.frontmatter["reference_type"] == "product"
    }

    parts: list[ReferenceDoc] = []
    base_name = claiming.frontmatter.get("extends")
    if base_name is not None:
        parts.append(products[base_name])
    parts.append(claiming)

    # Per-heading merge: base sections first, child text appended UNDER THE
    # SAME H2 heading; child-only headings appended after. A flat body concat
    # would emit duplicate "## Pricing Inputs" headings and the coherence
    # net's section regex would only see the base's text.
    merged: dict[str, list[str]] = {}
    for part in parts:
        for heading, text in _h2_sections(part.body):
            merged.setdefault(heading, []).append(text)
    content = "\n\n".join(
        f"## {heading}\n\n" + "\n\n".join(texts) for heading, texts in merged.items()
    )
    paths = [p.path for p in parts]

    if region is not None:
        # An overlay attaches if it extends ANY doc in the resolved chain —
        # inherited families (claiming doc extends a base) must still pick up
        # the base's regional overlay, or CN conventions silently vanish for
        # KO-reset/Phoenix while plain snowballs keep them.
        chain_names = {p.frontmatter["name"] for p in parts}
        for doc in products.values():
            if (
                doc.frontmatter.get("region") == region
                and doc.frontmatter.get("extends") in chain_names
            ):
                content += f"\n\n## Regional Conventions ({region})\n\n{doc.body}"
                paths.append(doc.path)
    return ResolvedReferenceDoc(
        quantark_class=quantark_class, content=content, source_paths=tuple(paths)
    )


def _h2_sections(body: str) -> list[tuple[str, str]]:
    """Split a doc body into (h2-heading, section-text) pairs, in order."""
    sections: list[tuple[str, str]] = []
    # NOTE: heading capture must be [^\n]+, not .+ — under re.S a greedy .+
    # spans newlines and swallows the whole body into one mega-heading.
    for match in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", body, re.M | re.S):
        sections.append((match.group(1).strip(), match.group(2).strip()))
    return sections


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
