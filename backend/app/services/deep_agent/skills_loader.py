"""Skill assembly helpers.

Two responsibilities:

1. Load policy fragments from disk and concatenate them. Used to build each
   persona's `system_prompt` at agent build time.
2. Compose a persona prompt from an identity body + selected policy fragments.

Workflow skills do NOT flow through this module. They are surfaced to personas
by `EnvelopeSkillsMiddleware`, which filters workflow metadata by runtime
envelope before injecting the skill list into each subagent prompt.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .skills_paths import POLICY_DIR


META_POLICY_REQUIRED_FIELDS = {"name", "description", "policy_type", "applies_to"}
VALID_META_POLICY_TYPES = {
    "runtime_policy",
    "envelope_contract",
    "escalation_policy",
    "context_contract",
}


@dataclass(frozen=True)
class MetaPolicyFragment:
    path: Path
    frontmatter: dict[str, Any]
    body: str


def parse_policy_fragment(path: Path) -> MetaPolicyFragment:
    text = Path(path).read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, path=Path(path))
    return MetaPolicyFragment(
        path=Path(path),
        frontmatter=frontmatter,
        body=body.strip(),
    )


def validate_meta_policy_tree(root: Path = POLICY_DIR) -> list[MetaPolicyFragment]:
    return [validate_meta_policy_file(path) for path in sorted(Path(root).glob("*.md"))]


def validate_meta_policy_file(path: Path) -> MetaPolicyFragment:
    fragment = parse_policy_fragment(path)
    missing = sorted(META_POLICY_REQUIRED_FIELDS - set(fragment.frontmatter))
    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    name = fragment.frontmatter.get("name")
    if name != fragment.path.stem:
        errors.append(f"name must match filename stem {fragment.path.stem!r}")

    description = fragment.frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
    elif len(description) > 200:
        errors.append("description must be <=200 characters")

    policy_type = fragment.frontmatter.get("policy_type")
    if policy_type not in VALID_META_POLICY_TYPES:
        errors.append(f"policy_type must be one of {sorted(VALID_META_POLICY_TYPES)}")

    applies_to = fragment.frontmatter.get("applies_to")
    if not isinstance(applies_to, list) or not applies_to:
        errors.append("applies_to must be a non-empty list")
    elif not all(isinstance(item, str) and item for item in applies_to):
        errors.append("applies_to entries must be non-empty strings")

    if not fragment.body.startswith("## "):
        errors.append("body must start with a markdown h2 heading")

    if errors:
        raise ValueError(f"Invalid meta policy {fragment.path}: {'; '.join(errors)}")
    return fragment


def _split_frontmatter(text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"Meta policy missing frontmatter: {path}")
    try:
        _, rest = text.split("---\n", 1)
        raw_frontmatter, body = rest.split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"Meta policy has malformed frontmatter fences: {path}") from exc
    loaded = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Meta policy frontmatter must be a mapping: {path}")
    return loaded, body


def load_policy_fragments(names: Sequence[str]) -> str:
    """Read named policy fragments from `POLICY_DIR` and concatenate them.

    Args:
        names: Policy fragment basenames (without `.md`), in the desired order.

    Returns:
        Concatenated fragments separated by a blank line, each individually
        stripped of trailing whitespace. Empty string if `names` is empty.

    Raises:
        FileNotFoundError: A named fragment file does not exist. The error
            message includes the resolved path so the failure is debuggable
            at agent build time.
    """
    parts: list[str] = []
    for name in names:
        path = POLICY_DIR / f"{name}.md"
        if not path.is_file():
            raise FileNotFoundError(f"Policy fragment not found: {path}")
        parts.append(validate_meta_policy_file(path).body)
    return "\n\n".join(parts)


def compose_persona_prompt(
    *,
    identity_prompt: str,
    policy_fragment_names: Sequence[str],
) -> str:
    """Compose a persona system prompt: identity body followed by policy fragments.

    Args:
        identity_prompt: The persona's identity + output style + routing-from-skills
            directive, loaded from `prompts/<persona>.md`.
        policy_fragment_names: Policy fragment basenames composed in order.

    Returns:
        `<identity_prompt.rstrip()>\\n\\n<concatenated fragments>`. When
        `policy_fragment_names` is empty, returns `identity_prompt.rstrip()`
        with no trailing newlines — the join separator is suppressed so the
        degenerate case doesn't produce a dangling blank line.
    """
    fragments = load_policy_fragments(policy_fragment_names)
    if not fragments:
        return identity_prompt.rstrip()
    return identity_prompt.rstrip() + "\n\n" + fragments
