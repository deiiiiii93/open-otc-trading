"""Envelope-aware SkillsMiddleware for workflow catalog exposure."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import yaml
from deepagents.middleware.skills import (
    SkillsMiddleware,
    SkillsStateUpdate,
    _alist_skills_with_errors,
    _list_skills_with_errors,
)
from langchain_core.runnables import RunnableConfig

from .envelopes import Envelope

logger = logging.getLogger(__name__)

WORKFLOW_SKILL_SOURCES: tuple[str, ...] = (
    "/skills/workflows/positions/",
    "/skills/workflows/try-solve/",
    "/skills/workflows/pricing/",
    "/skills/workflows/market-data/",
    "/skills/workflows/portfolios/",
    "/skills/workflows/rfq/",
    "/skills/workflows/risk/",
    "/skills/workflows/reporting/",
    "/skills/workflows/snowballs/",
)


def _envelope_from_config(config: dict | None, default: Envelope) -> Envelope:
    configurable = (config or {}).get("configurable", {})
    raw = configurable.get("envelope")
    try:
        return Envelope(raw)
    except (TypeError, ValueError):
        return default


def _frontmatter_from_text(text: str, *, path: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        raise ValueError(f"skill missing frontmatter: {path}")
    try:
        _, rest = text.split("---\n", 1)
        raw_frontmatter, _body = rest.split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"skill has malformed frontmatter fences: {path}") from exc
    loaded = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"skill frontmatter must be a mapping: {path}")
    return loaded


def _frontmatter_for_skill(backend: Any, path: str) -> dict[str, Any]:
    response = backend.download_files([path])[0]
    if getattr(response, "error", None):
        raise ValueError(f"cannot read {path}: {response.error}")
    content = getattr(response, "content", None)
    if content is None:
        raise ValueError(f"skill file has no content: {path}")
    return _frontmatter_from_text(content.decode("utf-8"), path=path)


async def _afrontmatter_for_skill(backend: Any, path: str) -> dict[str, Any]:
    response = (await backend.adownload_files([path]))[0]
    if getattr(response, "error", None):
        raise ValueError(f"cannot read {path}: {response.error}")
    content = getattr(response, "content", None)
    if content is None:
        raise ValueError(f"skill file has no content: {path}")
    return _frontmatter_from_text(content.decode("utf-8"), path=path)


def _skill_allowed(frontmatter: dict[str, Any], envelope: Envelope) -> bool:
    allowed = frontmatter.get("allowed_envelopes")
    if not isinstance(allowed, list):
        return False
    if envelope.value in allowed:
        return True
    if envelope is Envelope.DESK_ASYNC:
        may_escalate_to = frontmatter.get("may_escalate_to")
        if isinstance(may_escalate_to, list) and Envelope.DESK_ASYNC.value in may_escalate_to:
            return True
        return Envelope.DESK_WORKFLOW.value in allowed
    return False


def load_envelope_filtered_skills(
    backend: Any,
    sources: Sequence[str],
    envelope: Envelope,
) -> list[dict[str, Any]]:
    skills, errors = load_envelope_filtered_skills_with_errors(
        backend,
        sources,
        envelope,
    )
    if errors:
        logger.warning("Envelope skill load errors: %s", errors)
    return skills


def load_envelope_filtered_skills_with_errors(
    backend: Any,
    sources: Sequence[str],
    envelope: Envelope,
) -> tuple[list[dict[str, Any]], list[str]]:
    all_skills: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for source_path in sources:
        source_skills, source_error = _list_skills_with_errors(backend, source_path)
        if source_error is not None:
            errors.append(source_error)
        for skill in source_skills:
            path = skill["path"]
            try:
                frontmatter = _frontmatter_for_skill(backend, path)
            except Exception as exc:  # pragma: no cover - defensive logging path
                errors.append(f"Cannot inspect skill envelope at {path}: {exc}")
                continue
            if _skill_allowed(frontmatter, envelope):
                all_skills[skill["name"]] = skill

    return list(all_skills.values()), errors


async def aload_envelope_filtered_skills_with_errors(
    backend: Any,
    sources: Sequence[str],
    envelope: Envelope,
) -> tuple[list[dict[str, Any]], list[str]]:
    all_skills: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for source_path in sources:
        source_skills, source_error = await _alist_skills_with_errors(
            backend,
            source_path,
        )
        if source_error is not None:
            errors.append(source_error)
        for skill in source_skills:
            path = skill["path"]
            try:
                frontmatter = await _afrontmatter_for_skill(backend, path)
            except Exception as exc:  # pragma: no cover - defensive logging path
                errors.append(f"Cannot inspect skill envelope at {path}: {exc}")
                continue
            if _skill_allowed(frontmatter, envelope):
                all_skills[skill["name"]] = skill

    return list(all_skills.values()), errors


class EnvelopeSkillsMiddleware(SkillsMiddleware):
    """Expose only workflow skills valid for the current runtime envelope."""

    def __init__(
        self,
        *,
        backend: Any,
        sources: Sequence[str],
        default_envelope: Envelope = Envelope.PET_PAGE,
    ) -> None:
        super().__init__(backend=backend, sources=sources)
        self.default_envelope = default_envelope

    def before_agent(
        self,
        state: Any,
        runtime: Any,
        config: RunnableConfig,
    ) -> SkillsStateUpdate:
        backend = self._get_backend(state, runtime, config)
        envelope = _envelope_from_config(config, self.default_envelope)
        skills, errors = load_envelope_filtered_skills_with_errors(
            backend,
            self.sources,
            envelope,
        )
        update = SkillsStateUpdate(skills_metadata=skills)
        if errors:
            logger.warning("Skills load errors: %s", errors)
            update["skills_load_errors"] = errors
        return update

    async def abefore_agent(
        self,
        state: Any,
        runtime: Any,
        config: RunnableConfig,
    ) -> SkillsStateUpdate:
        backend = self._get_backend(state, runtime, config)
        envelope = _envelope_from_config(config, self.default_envelope)
        skills, errors = await aload_envelope_filtered_skills_with_errors(
            backend,
            self.sources,
            envelope,
        )
        update = SkillsStateUpdate(skills_metadata=skills)
        if errors:
            logger.warning("Skills load errors: %s", errors)
            update["skills_load_errors"] = errors
        return update


__all__ = [
    "EnvelopeSkillsMiddleware",
    "WORKFLOW_SKILL_SOURCES",
    "load_envelope_filtered_skills",
    "load_envelope_filtered_skills_with_errors",
]
