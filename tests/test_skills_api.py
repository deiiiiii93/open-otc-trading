"""Skills CRUD API tests against a temp skills tree and a stub agent service."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.skills import build_skills_router

VALID_SKILL = """---
name: fetch-market-data
description: Fetch market snapshots for desk workflows when a user names underlyings.
domain: market-data
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context:
  - underlyings
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - snapshots returned
routing:
  - request: "Fetch current market data"
    persona: trader
---

## When to use

- Always, in tests.

## Example

User: fetch.
Assistant: fetched.
"""

VALID_META = """---
name: clarification-policy
description: Test policy fragment.
policy_type: runtime_policy
applies_to:
  - trader
---

## Clarification

Ask before guessing.
"""

VALID_REFERENCE = """---
name: conventions
description: Test reference doc.
reference_type: market_data
---

## Conventions

Symbols use exchange suffixes.
"""


class StubAgentService:
    def __init__(self, fail: bool = False) -> None:
        self.rebuild_calls = 0
        self.fail = fail

    def rebuild_orchestrator(self) -> bool:
        self.rebuild_calls += 1
        if self.fail:
            raise RuntimeError("rebuild boom")
        return True


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    skill_dir = root / "workflows" / "market-data" / "fetch-market-data"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    (root / "workflows" / "risk").mkdir(parents=True)  # empty domain
    (root / "meta").mkdir()
    (root / "meta" / "clarification-policy.md").write_text(VALID_META, encoding="utf-8")
    ref_dir = root / "references" / "market-data"
    ref_dir.mkdir(parents=True)
    (ref_dir / "conventions.md").write_text(VALID_REFERENCE, encoding="utf-8")
    return root


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "orchestrator.md").write_text(
        "Route fetch-market-data requests to trader.", encoding="utf-8"
    )
    (prompts / "trader.md").write_text("Trader identity.", encoding="utf-8")
    (prompts / "risk_manager.md").write_text("Risk identity.", encoding="utf-8")
    (prompts / "high_board.md").write_text("Board identity.", encoding="utf-8")
    return prompts


@pytest.fixture
def service() -> StubAgentService:
    return StubAgentService()


@pytest.fixture
def api(skills_root: Path, prompts_dir: Path, service: StubAgentService) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_skills_router(service, skills_root=skills_root, prompts_dir=prompts_dir)
    )
    return TestClient(app)


# --- catalog + read --------------------------------------------------------


def test_catalog_lists_all_tiers_and_domains(api: TestClient) -> None:
    data = api.get("/api/skills/catalog").json()
    assert data["domains"] == ["market-data", "risk"]
    assert [e["name"] for e in data["workflows"]] == ["fetch-market-data"]
    assert [e["name"] for e in data["meta"]] == ["clarification-policy"]
    assert [e["name"] for e in data["references"]] == ["conventions"]
    workflow = data["workflows"][0]
    assert workflow["tier"] == "workflows"
    assert workflow["path"] == "market-data/fetch-market-data/SKILL.md"
    assert workflow["domain"] == "market-data"
    assert workflow["body_tokens"] is not None
    assert all(issue["severity"] != "error" for issue in workflow["lint"])


def test_get_workflow_file_returns_parsed_parts(api: TestClient) -> None:
    data = api.get(
        "/api/skills/workflows/market-data/fetch-market-data/SKILL.md"
    ).json()
    assert data["frontmatter"]["name"] == "fetch-market-data"
    assert data["frontmatter"]["routing"][0]["persona"] == "trader"
    assert data["body"].startswith("## When to use")
    assert data["content"].startswith("---\n")


def test_catalog_survives_malformed_raw_file(
    api: TestClient, skills_root: Path
) -> None:
    """One broken meta/reference file (e.g. hand-edited YAML) must surface as
    a lint error on its entry — never a 500 that takes down the whole catalog
    and with it the editor that could repair the file."""
    (skills_root / "meta" / "broken.md").write_text(
        "---\nname: broken\ndescription: [unclosed\n---\nbody\n",
        encoding="utf-8",
    )
    response = api.get("/api/skills/catalog")
    assert response.status_code == 200
    broken = next(e for e in response.json()["meta"] if e["name"] == "broken")
    assert any(issue["severity"] == "error" for issue in broken["lint"])


def test_get_meta_file(api: TestClient) -> None:
    data = api.get("/api/skills/meta/clarification-policy.md").json()
    assert data["tier"] == "meta"
    assert data["content"].startswith("---\n")


def test_get_unknown_file_is_404(api: TestClient) -> None:
    assert api.get("/api/skills/meta/missing.md").status_code == 404


def test_path_traversal_is_rejected(api: TestClient) -> None:
    response = api.get("/api/skills/meta/..%2F..%2Fsecrets.md")
    assert response.status_code == 400


def test_non_markdown_is_rejected(api: TestClient) -> None:
    assert api.get("/api/skills/meta/notes.txt").status_code == 400


# --- validate + PUT ---------------------------------------------------------

WORKFLOW_PUT_URL = "/api/skills/workflows/market-data/fetch-market-data/SKILL.md"


def _valid_frontmatter() -> dict:
    return {
        "name": "fetch-market-data",
        "description": "Fetch market snapshots for desk workflows on demand.",
        "domain": "market-data",
        "workflow_type": "read",
        "allowed_envelopes": ["desk_workflow"],
        "may_escalate_to": [],
        "required_context": ["underlyings"],
        "optional_context": [],
        "write_actions": False,
        "confirmation_required": False,
        "success_criteria": ["snapshots returned"],
        "routing": [{"request": "Fetch current market data", "persona": "trader"}],
    }


VALID_BODY = "## When to use\n\n- Updated.\n\n## Example\n\nUser: go.\nAssistant: done.\n"


def test_put_workflow_validates_writes_and_rebuilds(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    response = api.put(
        WORKFLOW_PUT_URL,
        json={"frontmatter": _valid_frontmatter(), "body": VALID_BODY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["saved"] is True and data["reloaded"] is True
    assert service.rebuild_calls == 1
    on_disk = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert on_disk.startswith("---\nname: fetch-market-data\n")
    assert "- Updated." in on_disk
    assert on_disk.endswith("Assistant: done.\n")


def test_put_with_lint_error_blocks_and_leaves_disk_untouched(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    target = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    )
    before = target.read_text(encoding="utf-8")
    bad = _valid_frontmatter()
    bad["allowed_envelopes"] = ["mars_rover"]
    response = api.put(WORKFLOW_PUT_URL, json={"frontmatter": bad, "body": VALID_BODY})
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["detail"]}
    assert "invalid_allowed_envelope" in codes
    assert target.read_text(encoding="utf-8") == before
    assert service.rebuild_calls == 0


def test_put_workflow_name_must_match_directory(api: TestClient) -> None:
    renamed = _valid_frontmatter()
    renamed["name"] = "other-name"
    response = api.put(
        WORKFLOW_PUT_URL, json={"frontmatter": renamed, "body": VALID_BODY}
    )
    assert response.status_code == 422


def test_put_workflow_domain_must_match_directory(api: TestClient) -> None:
    """Domain is path-derived on create; a PUT may not drift it, or routing
    lint would approve a persona that cannot load the physically-scoped skill."""
    drifted = _valid_frontmatter()
    drifted["domain"] = "risk"
    response = api.put(
        WORKFLOW_PUT_URL, json={"frontmatter": drifted, "body": VALID_BODY}
    )
    assert response.status_code == 422
    messages = [issue["message"] for issue in response.json()["detail"]]
    assert any("domain must equal 'market-data'" in m for m in messages)


def test_put_meta_raw_content(api: TestClient, skills_root: Path) -> None:
    content = VALID_META.replace("Ask before guessing.", "Ask, always.")
    response = api.put(
        "/api/skills/meta/clarification-policy.md", json={"content": content}
    )
    assert response.status_code == 200
    assert "Ask, always." in (skills_root / "meta" / "clarification-policy.md").read_text(
        encoding="utf-8"
    )


def test_put_meta_invalid_is_blocked(api: TestClient) -> None:
    response = api.put(
        "/api/skills/meta/clarification-policy.md",
        json={"content": "no frontmatter at all"},
    )
    assert response.status_code == 422


def test_put_reference_requires_valid_frontmatter(api: TestClient) -> None:
    response = api.put(
        "/api/skills/references/market-data/conventions.md",
        json={"content": "missing frontmatter"},
    )
    assert response.status_code == 422


def test_validate_never_writes(api: TestClient, skills_root: Path) -> None:
    target = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    )
    before = target.read_text(encoding="utf-8")
    response = api.post(
        "/api/skills/validate",
        json={
            "tier": "workflows",
            "frontmatter": _valid_frontmatter(),
            "body": VALID_BODY,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["blocking"] is False
    assert data["body_tokens"] is not None
    assert target.read_text(encoding="utf-8") == before


def test_validate_reports_blocking_errors(api: TestClient) -> None:
    bad = _valid_frontmatter()
    del bad["success_criteria"]
    response = api.post(
        "/api/skills/validate",
        json={"tier": "workflows", "frontmatter": bad, "body": VALID_BODY},
    )
    data = response.json()
    assert data["blocking"] is True
    assert any(i["code"] == "missing_frontmatter_field" for i in data["issues"])


def test_rebuild_failure_still_saves(
    skills_root: Path, prompts_dir: Path
) -> None:
    failing = StubAgentService(fail=True)
    app = FastAPI()
    app.include_router(
        build_skills_router(failing, skills_root=skills_root, prompts_dir=prompts_dir)
    )
    client = TestClient(app)
    response = client.put(
        WORKFLOW_PUT_URL,
        json={"frontmatter": _valid_frontmatter(), "body": VALID_BODY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["saved"] is True
    assert data["reloaded"] is False
    assert "rebuild boom" in data["reload_error"]


# --- create / delete / reload ----------------------------------------------


def _create_payload(name: str = "stress-scan", domain: str = "risk") -> dict:
    return {
        "domain": domain,
        "name": name,
        "frontmatter": {
            "name": name,
            "description": "Scan stress results for limit breaches on demand.",
            "domain": domain,
            "workflow_type": "read",
            "allowed_envelopes": ["desk_workflow"],
            "may_escalate_to": [],
            "required_context": [],
            "optional_context": [],
            "write_actions": False,
            "confirmation_required": False,
            "success_criteria": ["breaches listed"],
            "routing": [{"request": "Scan stress breaches", "persona": "risk_manager"}],
        },
        "body": "## When to use\n\n- On demand.\n\n## Example\n\nUser: scan.\nAssistant: scanned.\n",
    }


def test_create_workflow_skill(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    response = api.post("/api/skills/workflows", json=_create_payload())
    assert response.status_code == 201
    target = skills_root / "workflows" / "risk" / "stress-scan" / "SKILL.md"
    assert target.is_file()
    assert service.rebuild_calls == 1
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\nname: stress-scan\n")


def test_create_duplicate_is_409(api: TestClient) -> None:
    payload = _create_payload(name="fetch-market-data", domain="market-data")
    assert api.post("/api/skills/workflows", json=payload).status_code == 409


def test_create_unknown_domain_is_400(api: TestClient) -> None:
    assert (
        api.post("/api/skills/workflows", json=_create_payload(domain="astrology"))
        .status_code
        == 400
    )


def test_create_bad_name_is_400(api: TestClient) -> None:
    assert (
        api.post("/api/skills/workflows", json=_create_payload(name="Bad Name"))
        .status_code
        == 400
    )


def test_create_routing_visibility_is_blocked(api: TestClient) -> None:
    payload = _create_payload()
    payload["frontmatter"]["routing"] = [
        {"request": "Scan stress breaches", "persona": "high_board"}
    ]
    response = api.post("/api/skills/workflows", json=payload)
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["detail"]}
    assert "routing_persona_visibility" in codes


def test_delete_workflow_skill_prunes_and_warns(
    api: TestClient, skills_root: Path, service: StubAgentService
) -> None:
    # fetch-market-data is referenced in the stub prompts_dir orchestrator.md.
    response = api.delete("/api/skills/workflows/market-data/fetch-market-data")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert any("orchestrator.md" in warning for warning in data["warnings"])
    assert service.rebuild_calls == 1
    assert not (skills_root / "workflows" / "market-data" / "fetch-market-data").exists()
    assert (skills_root / "workflows" / "market-data").is_dir()  # domain dir stays


def test_delete_missing_is_404(api: TestClient) -> None:
    assert api.delete("/api/skills/workflows/risk/missing").status_code == 404


def test_reload_endpoint(api: TestClient, service: StubAgentService) -> None:
    response = api.post("/api/skills/reload")
    assert response.status_code == 200
    assert response.json() == {"reloaded": True, "error": None}
    assert service.rebuild_calls == 1


# --- write gate --------------------------------------------------------------


@pytest.fixture
def writes_disabled():
    from app.config import Settings, configure_settings

    configure_settings(Settings(feature_skills_write_api=False))
    try:
        yield
    finally:
        configure_settings(None)


def test_write_gate_blocks_put_create_delete(
    api: TestClient, skills_root: Path, service: StubAgentService, writes_disabled
) -> None:
    """OPEN_OTC_FEATURE_SKILLS_WRITE_API=false turns the API read-only: every
    mutating endpoint 403s before touching disk or rebuilding."""
    target = (
        skills_root / "workflows" / "market-data" / "fetch-market-data" / "SKILL.md"
    )
    before = target.read_text(encoding="utf-8")

    put = api.put(
        WORKFLOW_PUT_URL,
        json={"frontmatter": _valid_frontmatter(), "body": VALID_BODY},
    )
    create = api.post(
        "/api/skills/workflows",
        json={
            "domain": "risk",
            "name": "new-skill",
            "frontmatter": _valid_frontmatter(),
            "body": VALID_BODY,
        },
    )
    delete = api.delete("/api/skills/workflows/market-data/fetch-market-data")
    # Reload mutates the live agent graph, so the read-only gate covers it.
    reload = api.post("/api/skills/reload")

    assert (
        put.status_code,
        create.status_code,
        delete.status_code,
        reload.status_code,
    ) == (403, 403, 403, 403)
    assert target.read_text(encoding="utf-8") == before
    assert service.rebuild_calls == 0


def test_write_gate_leaves_reads_open(api: TestClient, writes_disabled) -> None:
    assert api.get("/api/skills/catalog").status_code == 200
    assert api.get(WORKFLOW_PUT_URL).status_code == 200


# --- app wiring (READ-ONLY against the real skills tree) --------------------


def test_app_serves_skills_catalog(client) -> None:
    """`client` is the repo conftest fixture for the full app. Never write
    through it — this app instance points at the REAL backend/app/skills."""
    response = client.get("/api/skills/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "market-data" in data["domains"]
    names = {entry["name"] for entry in data["workflows"]}
    assert "fetch-market-data" in names
