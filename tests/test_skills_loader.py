"""Unit tests for skills_loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent.skills_loader import (
    META_POLICY_REQUIRED_FIELDS,
    POLICY_DIR,
    compose_persona_prompt,
    load_policy_fragments,
    parse_policy_fragment,
    validate_meta_policy_file,
)


def _meta_fragment(name: str, body: str = "## Alpha\nbody A") -> str:
    return f"""---
name: {name}
description: Test policy fragment for loader behavior.
policy_type: runtime_policy
applies_to:
  - trader
---

{body}
"""


def test_policy_dir_resolves_to_meta_inside_package() -> None:
    assert POLICY_DIR.is_dir(), f"{POLICY_DIR} must exist"
    assert POLICY_DIR.name == "meta"
    assert POLICY_DIR.parent.name == "skills"


def test_parse_policy_fragment_splits_frontmatter_and_body(tmp_path: Path) -> None:
    path = tmp_path / "alpha.md"
    path.write_text(_meta_fragment("alpha"), encoding="utf-8")

    fragment = parse_policy_fragment(path)

    assert fragment.frontmatter["name"] == "alpha"
    assert fragment.body == "## Alpha\nbody A"


def test_validate_meta_policy_file_enforces_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "alpha.md"
    path.write_text("---\nname: alpha\n---\n\n## Alpha\nbody A\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        validate_meta_policy_file(path)

    message = str(exc.value)
    for field in META_POLICY_REQUIRED_FIELDS - {"name"}:
        assert field in message


def test_load_policy_fragments_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_concatenates_with_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    (policy_dir / "beta.md").write_text(
        _meta_fragment("beta", body="## Beta\nbody B"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha", "beta"])
    assert result == "## Alpha\nbody A\n\n## Beta\nbody B"


def test_load_policy_fragments_strips_trailing_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(
        _meta_fragment("alpha", body="## Alpha\nbody A\n\n\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_missing_fragment_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    with pytest.raises(FileNotFoundError) as exc:
        load_policy_fragments(["does-not-exist"])
    assert "does-not-exist.md" in str(exc.value)


def test_compose_persona_prompt_identity_plus_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = compose_persona_prompt(
        identity_prompt="You are foo.\n\n## Tools\n- bar\n",
        policy_fragment_names=["alpha"],
    )
    assert result == "You are foo.\n\n## Tools\n- bar\n\n## Alpha\nbody A"


def test_compose_persona_prompt_empty_fragment_list_returns_identity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", tmp_path / "meta")
    result = compose_persona_prompt(
        identity_prompt="You are foo.\n",
        policy_fragment_names=[],
    )
    assert result == "You are foo."
