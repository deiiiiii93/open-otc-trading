"""Skills management API.

File-CRUD over `backend/app/skills/` with server-side validation (reusing
skill_lint / meta / reference validators) and an orchestrator rebuild after
every successful write. Local-dev tool by design: no auth, no concurrency
control — git review of the resulting file diffs is the safety net. The
mutating endpoints (PUT/POST/DELETE) are additionally gated behind
`feature_skills_write_api` (OPEN_OTC_FEATURE_SKILLS_WRITE_API) so a shared
deployment can be made read-only with one env line.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.services.deep_agent.reference_docs import validate_reference_doc_file
from app.services.deep_agent.skill_lint import (
    count_body_tokens,
    lint_skill_file,
    lint_skill_text,
    parse_skill_text,
)
from app.services.deep_agent.skills_loader import validate_meta_policy_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT

logger = logging.getLogger(__name__)

Tier = Literal["workflows", "references", "meta"]
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "services" / "deep_agent" / "prompts"
_PROMPT_FILES = ("orchestrator.md", "trader.md", "risk_manager.md", "high_board.md")
_KEBAB_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_FIELD_ORDER = (
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
    "routing",
)


class SupportsOrchestratorRebuild(Protocol):
    def rebuild_orchestrator(self) -> bool: ...


def _require_write_api() -> None:
    """Request-time gate so configure_settings overrides apply without a rebuild."""
    if not get_settings().feature_skills_write_api:
        raise HTTPException(
            status_code=403,
            detail=(
                "skills write API disabled "
                "(set OPEN_OTC_FEATURE_SKILLS_WRITE_API=true to enable)"
            ),
        )


# --- API models -------------------------------------------------------------

# 422 contract: every 422 raised by this router carries detail as a list of
# SkillLintIssueOut dicts, so clients never type-sniff `detail`. (FastAPI's
# own request-validation 422s keep their native shape.)


class SkillLintIssueOut(BaseModel):
    code: str
    message: str
    detail: str = ""
    severity: Literal["warning", "error"]


class SkillFileSummaryOut(BaseModel):
    tier: Tier
    path: str
    name: str
    domain: str | None = None
    frontmatter: dict[str, Any] | None = None
    frontmatter_error: str | None = None
    lint: list[SkillLintIssueOut] = []
    body_tokens: int | None = None


class SkillCatalogOut(BaseModel):
    domains: list[str]
    workflows: list[SkillFileSummaryOut]
    references: list[SkillFileSummaryOut]
    meta: list[SkillFileSummaryOut]


class SkillFileOut(SkillFileSummaryOut):
    content: str
    body: str | None = None


class SkillWritePayload(BaseModel):
    frontmatter: dict[str, Any] | None = None
    body: str | None = None
    content: str | None = None


class WorkflowSkillCreate(BaseModel):
    domain: str
    name: str
    frontmatter: dict[str, Any]
    body: str


class SkillValidatePayload(SkillWritePayload):
    tier: Tier


class SkillValidateOut(BaseModel):
    issues: list[SkillLintIssueOut]
    body_tokens: int | None = None
    blocking: bool


class SkillSaveOut(BaseModel):
    saved: bool
    reloaded: bool
    reload_error: str | None = None
    lint: list[SkillLintIssueOut] = []


class SkillDeleteOut(BaseModel):
    deleted: bool
    reloaded: bool
    reload_error: str | None = None
    warnings: list[str] = []


class SkillReloadOut(BaseModel):
    reloaded: bool
    error: str | None = None


# --- helpers ----------------------------------------------------------------


def serialize_workflow_skill(frontmatter: dict[str, Any], body: str) -> str:
    """Canonical SKILL.md text: ordered frontmatter, stripped body, one EOF newline."""
    ordered: dict[str, Any] = {}
    cleaned = dict(frontmatter)
    routing = cleaned.get("routing")
    if isinstance(routing, list):
        # Canonical key order inside entries; drop empty routing entirely.
        entries = [
            {"request": entry.get("request"), "persona": entry.get("persona")}
            if isinstance(entry, dict)
            else entry
            for entry in routing
        ]
        if entries:
            cleaned["routing"] = entries
        else:
            cleaned.pop("routing", None)
    for key in _FRONTMATTER_FIELD_ORDER:
        if key in cleaned:
            ordered[key] = cleaned[key]
    for key, value in cleaned.items():
        if key not in ordered:
            ordered[key] = value
    yaml_text = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100_000,
    )
    return f"---\n{yaml_text}---\n\n{body.strip()}\n"


def _issue(warning: Any) -> SkillLintIssueOut:
    return SkillLintIssueOut(
        code=warning.code,
        message=warning.message,
        detail=warning.detail,
        severity=warning.severity,
    )


def _exception_issue(code: str, exc: Exception) -> SkillLintIssueOut:
    return SkillLintIssueOut(code=code, message=str(exc), severity="error")


def _validate_named_file(
    validator: Any, filename: str, content: str, code: str
) -> list[SkillLintIssueOut]:
    """Run a Path-based validator (meta/reference) against unsaved text.

    Both validators require name == filename stem, so the temp file keeps the
    target filename inside a TemporaryDirectory.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidate = Path(tmp_dir) / filename
        candidate.write_text(content, encoding="utf-8")
        try:
            validator(candidate)
        except Exception as exc:  # noqa: BLE001 — validators may raise beyond ValueError; surface as lint issue, never 500
            return [_exception_issue(code, exc)]
    return []


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def build_skills_router(
    agent_service: SupportsOrchestratorRebuild,
    *,
    skills_root: Path = SKILLS_ROOT,
    prompts_dir: Path = _PROMPTS_DIR,
) -> APIRouter:
    router = APIRouter(prefix="/api/skills", tags=["skills"])
    skills_root = Path(skills_root)

    def _tier_root(tier: Tier) -> Path:
        return (skills_root / tier).resolve()

    def _resolve(tier: Tier, rel_path: str) -> Path:
        root = _tier_root(tier)
        target = (root / rel_path).resolve()
        if target != root and root not in target.parents:
            raise HTTPException(status_code=400, detail="path escapes the skills tree")
        if target.suffix != ".md":
            raise HTTPException(status_code=400, detail="only .md files are managed")
        return target

    def _domains() -> list[str]:
        workflows = _tier_root("workflows")
        if not workflows.is_dir():
            return []
        return sorted(d.name for d in workflows.iterdir() if d.is_dir())

    def _rebuild() -> tuple[bool, str | None]:
        try:
            return agent_service.rebuild_orchestrator(), None
        except Exception as exc:
            logger.exception("orchestrator rebuild after skill write failed")
            return False, str(exc)

    def _lint_existing(tier: Tier, path: Path) -> list[SkillLintIssueOut]:
        if tier == "workflows":
            return [
                _issue(w)
                for w in lint_skill_file(path, mode="ci", root=skills_root)
            ]
        if tier == "meta":
            try:
                validate_meta_policy_file(path)
            except Exception as exc:  # noqa: BLE001 — same contract as _validate_named_file: malformed on-disk files (e.g. broken YAML) become lint issues, never a 500 that takes down the catalog
                return [_exception_issue("invalid_meta_policy", exc)]
            return []
        try:
            validate_reference_doc_file(path)
        except Exception as exc:  # noqa: BLE001 — same contract as _validate_named_file
            return [_exception_issue("invalid_reference_doc", exc)]
        return []

    def _summary(tier: Tier, path: Path) -> SkillFileSummaryOut:
        rel = path.relative_to(_tier_root(tier)).as_posix()
        text = path.read_text(encoding="utf-8")
        parsed = parse_skill_text(text, path)
        name = str(parsed.frontmatter.get("name") or path.stem)
        domain = None
        if tier == "workflows":
            domain = str(parsed.frontmatter.get("domain") or path.parent.parent.name)
        return SkillFileSummaryOut(
            tier=tier,
            path=rel,
            name=name,
            domain=domain,
            frontmatter=parsed.frontmatter or None,
            frontmatter_error=parsed.frontmatter_error,
            lint=_lint_existing(tier, path),
            body_tokens=count_body_tokens(parsed.body) if tier == "workflows" else None,
        )

    def _validate_payload(
        tier: Tier, payload: SkillWritePayload, filename: str
    ) -> tuple[str, list[SkillLintIssueOut], int | None]:
        """Returns (file text, ci-mode issues, body token count)."""
        if tier == "workflows":
            if payload.frontmatter is None or payload.body is None:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        SkillLintIssueOut(
                            code="invalid_payload",
                            message="workflow skills require frontmatter and body",
                            severity="error",
                        ).model_dump()
                    ],
                )
            text = serialize_workflow_skill(payload.frontmatter, payload.body)
            issues = [_issue(w) for w in lint_skill_text(text, mode="ci")]
            return text, issues, count_body_tokens(payload.body)
        if payload.content is None:
            raise HTTPException(
                status_code=422,
                detail=[
                    SkillLintIssueOut(
                        code="invalid_payload",
                        message=f"{tier} files are updated as raw content",
                        severity="error",
                    ).model_dump()
                ],
            )
        validator = (
            validate_meta_policy_file if tier == "meta" else validate_reference_doc_file
        )
        code = "invalid_meta_policy" if tier == "meta" else "invalid_reference_doc"
        issues = _validate_named_file(validator, filename, payload.content, code)
        return payload.content, issues, None

    # --- read ---------------------------------------------------------------

    @router.get("/catalog", response_model=SkillCatalogOut)
    def catalog() -> SkillCatalogOut:
        workflows = [
            _summary("workflows", p)
            for p in sorted(_tier_root("workflows").glob("*/*/SKILL.md"))
        ]
        references = [
            _summary("references", p)
            for p in sorted(_tier_root("references").rglob("*.md"))
        ]
        meta = [_summary("meta", p) for p in sorted(_tier_root("meta").glob("*.md"))]
        return SkillCatalogOut(
            domains=_domains(),
            workflows=workflows,
            references=references,
            meta=meta,
        )

    @router.post("/validate", response_model=SkillValidateOut)
    def validate(payload: SkillValidatePayload) -> SkillValidateOut:
        filename = "SKILL.md"
        if payload.tier != "workflows":
            # Raw tiers: name==stem is enforced on PUT against the real
            # filename; for dry-run validation derive a stem from the
            # content's declared name so the validator's name check passes
            # exactly when the declared name is self-consistent.
            parsed = parse_skill_text(payload.content or "", Path("<unsaved>"))
            filename = f"{parsed.frontmatter.get('name', 'unsaved')}.md"
        _text, issues, body_tokens = _validate_payload(payload.tier, payload, filename)
        return SkillValidateOut(
            issues=issues,
            body_tokens=body_tokens,
            blocking=any(issue.severity == "error" for issue in issues),
        )

    @router.get("/{tier}/{rel_path:path}", response_model=SkillFileOut)
    def get_file(tier: Tier, rel_path: str) -> SkillFileOut:
        target = _resolve(tier, rel_path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill file not found")
        summary = _summary(tier, target)
        text = target.read_text(encoding="utf-8")
        parsed = parse_skill_text(text, target)
        return SkillFileOut(
            **summary.model_dump(),
            content=text,
            body=parsed.body.lstrip("\n") if parsed.frontmatter else None,
        )

    # --- write --------------------------------------------------------------

    @router.put("/{tier}/{rel_path:path}", response_model=SkillSaveOut)
    def update_file(tier: Tier, rel_path: str, payload: SkillWritePayload) -> SkillSaveOut:
        _require_write_api()
        target = _resolve(tier, rel_path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill file not found")
        if tier == "workflows":
            if target.name != "SKILL.md":
                raise HTTPException(
                    status_code=400, detail="workflow skills live in SKILL.md files"
                )
            declared = (payload.frontmatter or {}).get("name")
            if declared != target.parent.name:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        SkillLintIssueOut(
                            code="invalid_payload",
                            message=(
                                f"frontmatter name must equal {target.parent.name!r}"
                            ),
                            severity="error",
                        ).model_dump()
                    ],
                )
            # Domain is path-derived on create; a PUT must not drift it, or
            # routing lint would approve a persona that cannot load the skill.
            declared_domain = (payload.frontmatter or {}).get("domain")
            if declared_domain != target.parent.parent.name:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        SkillLintIssueOut(
                            code="invalid_payload",
                            message=(
                                "frontmatter domain must equal "
                                f"{target.parent.parent.name!r}"
                            ),
                            severity="error",
                        ).model_dump()
                    ],
                )
        text, issues, _tokens = _validate_payload(tier, payload, target.name)
        if any(issue.severity == "error" for issue in issues):
            raise HTTPException(
                status_code=422, detail=[issue.model_dump() for issue in issues]
            )
        _atomic_write(target, text)
        reloaded, reload_error = _rebuild()
        return SkillSaveOut(
            saved=True, reloaded=reloaded, reload_error=reload_error, lint=issues
        )

    @router.post("/workflows", response_model=SkillSaveOut, status_code=201)
    def create_workflow_skill(payload: WorkflowSkillCreate) -> SkillSaveOut:
        _require_write_api()
        if payload.domain not in _domains():
            raise HTTPException(
                status_code=400,
                detail=(
                    "unknown workflow domain; new domains require persona "
                    "visibility wiring in code review"
                ),
            )
        if not _KEBAB_NAME.match(payload.name):
            raise HTTPException(status_code=400, detail="name must be kebab-case")
        target = _tier_root("workflows") / payload.domain / payload.name / "SKILL.md"
        if target.exists():
            raise HTTPException(status_code=409, detail="skill already exists")
        frontmatter = dict(payload.frontmatter)
        frontmatter["name"] = payload.name
        frontmatter["domain"] = payload.domain
        text = serialize_workflow_skill(frontmatter, payload.body)
        issues = [_issue(w) for w in lint_skill_text(text, mode="ci")]
        if any(issue.severity == "error" for issue in issues):
            raise HTTPException(
                status_code=422, detail=[issue.model_dump() for issue in issues]
            )
        _atomic_write(target, text)
        reloaded, reload_error = _rebuild()
        return SkillSaveOut(
            saved=True, reloaded=reloaded, reload_error=reload_error, lint=issues
        )

    @router.delete("/workflows/{domain}/{name}", response_model=SkillDeleteOut)
    def delete_workflow_skill(domain: str, name: str) -> SkillDeleteOut:
        _require_write_api()
        target = _resolve("workflows", f"{domain}/{name}/SKILL.md")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="skill not found")
        warnings = []
        for prompt_name in _PROMPT_FILES:
            prompt_path = Path(prompts_dir) / prompt_name
            if prompt_path.is_file() and name in prompt_path.read_text(encoding="utf-8"):
                warnings.append(f"`{name}` is still referenced in prompts/{prompt_name}")
        target.unlink()
        try:
            target.parent.rmdir()  # prune the skill dir; domain dirs stay
        except OSError:
            pass
        reloaded, reload_error = _rebuild()
        return SkillDeleteOut(
            deleted=True,
            reloaded=reloaded,
            reload_error=reload_error,
            warnings=warnings,
        )

    @router.post("/reload", response_model=SkillReloadOut)
    def reload() -> SkillReloadOut:
        # Rebuilding the live agent graph mutates server state, so the
        # read-only deployment gate covers it too.
        _require_write_api()
        reloaded, error = _rebuild()
        return SkillReloadOut(reloaded=reloaded, error=error)

    return router


__all__ = ["build_skills_router", "serialize_workflow_skill"]
