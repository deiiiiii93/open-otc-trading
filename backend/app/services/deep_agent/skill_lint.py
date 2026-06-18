"""Skill-catalog lint helpers (warn and CI modes) for Phase 3 quality gates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.services.deep_agent.persona_domains import PERSONA_WORKFLOW_DOMAINS
from app.services.deep_agent.skills_paths import SKILLS_ROOT


SKILL_LINT_REQUIRED_FRONTMATTER = {
    "name",
    "description",
    "domain",
    "workflow_type",
    "allowed_envelopes",
    "may_escalate_to",
    "required_context",
    "optional_context",
    "write_actions",
    "confirmation_required",
    "success_criteria",
}

SkillLintMode = Literal["warn", "ci"]
SkillLintSeverity = Literal["warning", "error"]

CI_ERROR_CODES = {
    "invalid_frontmatter",
    "missing_frontmatter_field",
    "invalid_workflow_type",
    "invalid_allowed_envelope",
    "invalid_routing",
    "routing_persona_visibility",
    "description_length",
    "description_prefix",
    "missing_example",
    "archaeology_marker",
    "body_length",
}

VALID_WORKFLOW_TYPES = {"diagnostic", "action", "read", "compound"}
VALID_ENVELOPES = {"pet_page", "pet_diagnostic", "desk_workflow", "desk_async"}
VALID_ROUTING_PERSONAS = frozenset(PERSONA_WORKFLOW_DOMAINS)
DESCRIPTION_MAX_CHARS = 1024
BODY_MAX_TOKENS = 500
DESCRIPTION_BAD_PREFIXES = ("this skill", "skill for", "documentation")
FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<frontmatter>.*?)(?:\r?\n---[ \t]*(?:\r?\n|\Z))"
    r"(?P<body>.*)\Z",
    re.DOTALL,
)
OPENING_FRONTMATTER_PATTERN = re.compile(r"\A---[ \t]*(?:\r?\n|\Z)")

ARCHAEOLOGY_PATTERNS = (
    re.compile(r"\bcommit\s+`?[0-9a-f]{6,}`?", re.IGNORECASE),
    re.compile(r"\bv[0-9]+(?:\s+[a-z0-9][a-z0-9_-]*){1,3}", re.IGNORECASE),
    re.compile(r"\bfixed this mistake\b", re.IGNORECASE),
    re.compile(r"\bgrandfathered\b", re.IGNORECASE),
    re.compile(r"[\u2014-]v[0-9]+\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ParsedSkill:
    path: Path
    frontmatter: dict[str, Any]
    body: str
    frontmatter_error: str | None = None


@dataclass(frozen=True)
class SkillLintWarning:
    path: Path
    code: str
    message: str
    detail: str = ""
    severity: SkillLintSeverity = "warning"

    def with_severity(self, severity: SkillLintSeverity) -> "SkillLintWarning":
        return SkillLintWarning(
            path=self.path,
            code=self.code,
            message=self.message,
            detail=self.detail,
            severity=severity,
        )


def iter_skill_files(root: Path = SKILLS_ROOT) -> list[Path]:
    """Return skill files in deterministic order under `root`."""
    return sorted(Path(root).glob("**/SKILL.md"))


@lru_cache(maxsize=1)
def _token_encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_body_tokens(body: str) -> int:
    """Count skill body tokens using the Phase 3 lint tokenizer."""
    return len(_token_encoder().encode(body))


def parse_skill_text(text: str, path: Path) -> ParsedSkill:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        if not OPENING_FRONTMATTER_PATTERN.match(text):
            return ParsedSkill(path=Path(path), frontmatter={}, body=text)
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=OPENING_FRONTMATTER_PATTERN.sub("", text, count=1),
            frontmatter_error="missing closing frontmatter fence",
        )

    raw_frontmatter = match.group("frontmatter")
    body = match.group("body")
    try:
        loaded = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error=str(exc),
        )

    if not isinstance(loaded, dict):
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error="frontmatter root must be a mapping",
        )

    return ParsedSkill(path=Path(path), frontmatter=loaded, body=body)


def parse_skill_file(path: Path) -> ParsedSkill:
    return parse_skill_text(Path(path).read_text(encoding="utf-8"), Path(path))


def lint_skill_tree(
    root: Path = SKILLS_ROOT,
    *,
    mode: SkillLintMode = "warn",
) -> list[SkillLintWarning]:
    root = Path(root)
    warnings: list[SkillLintWarning] = []
    for skill_path in iter_skill_files(root):
        warnings.extend(lint_skill_file(skill_path))
    return _apply_lint_mode(warnings, root=root, mode=mode)


def lint_skill_file(
    path: Path,
    *,
    mode: SkillLintMode = "warn",
    root: Path | None = None,
) -> list[SkillLintWarning]:
    parsed = parse_skill_file(path)
    warnings = _lint_parsed(parsed)
    if mode == "warn":
        return warnings
    return _apply_lint_mode(
        warnings,
        root=Path(root) if root is not None else _infer_lint_root(path),
        mode=mode,
    )


def lint_skill_text(
    text: str,
    *,
    path: Path | str = Path("<unsaved>"),
    mode: SkillLintMode = "warn",
) -> list[SkillLintWarning]:
    """Lint skill content that may not exist on disk (API validation path)."""
    parsed = parse_skill_text(text, Path(path))
    warnings = _lint_parsed(parsed)
    if mode == "warn":
        return warnings
    return _apply_lint_mode(warnings, root=SKILLS_ROOT, mode=mode)


def _lint_parsed(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    if parsed.frontmatter_error:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="invalid_frontmatter",
                message="Skill frontmatter could not be parsed as a YAML mapping.",
                detail=parsed.frontmatter_error,
            )
        )
    warnings.extend(_lint_frontmatter(parsed))
    warnings.extend(_lint_routing(parsed))
    warnings.extend(_lint_content(parsed))
    return warnings


def assert_no_skill_lint_errors(root: Path = SKILLS_ROOT) -> list[SkillLintWarning]:
    warnings = lint_skill_tree(root, mode="ci")
    errors = [warning for warning in warnings if warning.severity == "error"]
    if errors:
        raise AssertionError(format_lint_errors(warnings))
    return warnings


def format_lint_errors(warnings: list[SkillLintWarning]) -> str:
    errors = [warning for warning in warnings if warning.severity == "error"]
    if not errors:
        return "Skill lint errors: none"

    lines = ["Skill lint errors:"]
    for warning in errors:
        detail = f" ({warning.detail})" if warning.detail else ""
        lines.append(f"- {warning.path}: {warning.code}{detail}: {warning.message}")
    return "\n".join(lines)


def _apply_lint_mode(
    warnings: list[SkillLintWarning],
    *,
    root: Path,
    mode: SkillLintMode,
) -> list[SkillLintWarning]:
    if mode == "warn":
        return warnings
    root = Path(root)
    return [
        warning.with_severity(_ci_severity(warning, root=root))
        for warning in warnings
    ]


def _ci_severity(warning: SkillLintWarning, *, root: Path) -> SkillLintSeverity:
    if warning.code not in CI_ERROR_CODES:
        return "warning"
    return "error"


def _infer_lint_root(path: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(SKILLS_ROOT.resolve())
    except ValueError:
        pass
    else:
        return SKILLS_ROOT
    return resolved.parent


def _lint_frontmatter(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    frontmatter = parsed.frontmatter

    for field in sorted(SKILL_LINT_REQUIRED_FRONTMATTER - set(frontmatter)):
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="missing_frontmatter_field",
                message=f"Skill frontmatter is missing required field '{field}'.",
                detail=field,
            )
        )

    description = frontmatter.get("description")
    if isinstance(description, str):
        if len(description) > DESCRIPTION_MAX_CHARS:
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="description_length",
                    message="Skill description exceeds 1024 characters.",
                    detail=str(len(description)),
                )
            )
        lowered = description.strip().lower()
        if lowered.startswith(DESCRIPTION_BAD_PREFIXES):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="description_prefix",
                    message="Skill description starts with a generic prefix.",
                    detail=description.split(maxsplit=3)[0].lower(),
                )
            )

    workflow_type = frontmatter.get("workflow_type")
    if workflow_type is not None and workflow_type not in VALID_WORKFLOW_TYPES:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="invalid_workflow_type",
                message="Skill workflow_type is not in the allowed Phase 3 set.",
                detail=str(workflow_type),
            )
        )

    allowed_envelopes = frontmatter.get("allowed_envelopes")
    if allowed_envelopes is not None:
        if not isinstance(allowed_envelopes, list):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="invalid_allowed_envelope",
                    message="Skill allowed_envelopes must be a list.",
                    detail=type(allowed_envelopes).__name__,
                )
            )
        else:
            for envelope in allowed_envelopes:
                if envelope not in VALID_ENVELOPES:
                    warnings.append(
                        SkillLintWarning(
                            path=parsed.path,
                            code="invalid_allowed_envelope",
                            message=(
                                "Skill allowed_envelopes contains an unknown "
                                "envelope."
                            ),
                            detail=str(envelope),
                        )
                    )

    return warnings


def _lint_routing(parsed: ParsedSkill) -> list[SkillLintWarning]:
    routing = parsed.frontmatter.get("routing")
    if routing is None:
        return []

    def invalid(message: str, detail: str = "") -> SkillLintWarning:
        return SkillLintWarning(
            path=parsed.path,
            code="invalid_routing",
            message=message,
            detail=detail,
        )

    if not isinstance(routing, list) or not routing:
        return [
            invalid(
                "Skill routing must be a non-empty list of "
                "{request, persona} mappings.",
                type(routing).__name__,
            )
        ]

    warnings: list[SkillLintWarning] = []
    domain = parsed.frontmatter.get("domain")
    for index, entry in enumerate(routing):
        if not isinstance(entry, dict) or set(entry) != {"request", "persona"}:
            warnings.append(
                invalid(
                    "Routing entries must be {request, persona} mappings.",
                    f"entry {index}",
                )
            )
            continue
        request = entry["request"]
        persona = entry["persona"]
        if not isinstance(request, str) or not request.strip():
            warnings.append(
                invalid("Routing request must be a non-empty string.", f"entry {index}")
            )
        elif "|" in request:
            warnings.append(
                invalid(
                    "Routing request must not contain '|' "
                    "(reserved by the generated markdown table).",
                    f"entry {index}",
                )
            )
        elif "\n" in request or "\r" in request:
            # A line break would escape the table cell and inject the rest of
            # the request as arbitrary orchestrator prompt text.
            warnings.append(
                invalid(
                    "Routing request must be a single line "
                    "(rendered as a markdown table cell).",
                    f"entry {index}",
                )
            )
        if not isinstance(persona, str):
            warnings.append(
                invalid(
                    "Routing persona must be a string.",
                    f"entry {index}: {persona!r}",
                )
            )
        elif persona not in VALID_ROUTING_PERSONAS:
            warnings.append(
                invalid(
                    "Routing persona is not a known persona.",
                    f"entry {index}: {persona!r}",
                )
            )
        elif (
            isinstance(domain, str)
            and domain not in PERSONA_WORKFLOW_DOMAINS[persona]
        ):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="routing_persona_visibility",
                    message=(
                        "Routing persona cannot see this skill's workflow domain."
                    ),
                    detail=f"{persona} cannot see domain {domain!r}",
                )
            )
    return warnings


def _lint_content(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    if "## Example" not in parsed.body and "## Examples" not in parsed.body:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="missing_example",
                message=(
                    "Skill body is missing a '## Example' or '## Examples' "
                    "section."
                ),
            )
        )

    body_tokens = count_body_tokens(parsed.body)
    if body_tokens > BODY_MAX_TOKENS:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="body_length",
                message="Skill body exceeds 500 tokens.",
                detail=str(body_tokens),
            )
        )

    for content in _iter_archaeology_sources(parsed):
        for pattern in ARCHAEOLOGY_PATTERNS:
            match = pattern.search(content)
            if match:
                warnings.append(
                    SkillLintWarning(
                        path=parsed.path,
                        code="archaeology_marker",
                        message=(
                            "Skill content contains implementation-history "
                            "archaeology."
                        ),
                        detail=match.group(0),
                    )
                )

    return warnings


def _iter_archaeology_sources(parsed: ParsedSkill) -> list[str]:
    sources = [parsed.body]
    sources.extend(_iter_frontmatter_strings(parsed.frontmatter))
    return sources


def _iter_frontmatter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for child in value.values():
            strings.extend(_iter_frontmatter_strings(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_iter_frontmatter_strings(child))
        return strings
    return []
