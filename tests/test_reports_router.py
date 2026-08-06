"""REST surface for report templates and generation.

Uses the shared `client` fixture (isolated tmp SQLite via `settings`/`session`),
then seeds the shipped templates through the real store so the endpoints are
exercised against the same rows migration 0054 installs in production.
"""
import pytest


@pytest.fixture
def seeded(session):
    from app.services.reporting import templates as templates_svc
    from app.services.reporting.seeds import load_seed_specs

    for slug, spec_yaml in load_seed_specs().items():
        templates_svc.save_template(
            slug=slug, spec_yaml=spec_yaml, source="seed", session=session
        )
    session.commit()
    return load_seed_specs()


def test_blocks_endpoint_returns_the_catalog(client):
    response = client.get("/api/reports/blocks")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 18
    assert any(block["key"] == "pnl.explain" for block in body["blocks"])


def test_templates_endpoint_lists_the_seeded_four(client, seeded):
    response = client.get("/api/reports/templates")
    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert {"trader-daily", "risk-manager-daily", "high-board-daily",
            "portfolio-snapshot"} <= slugs


def test_templates_endpoint_filters_by_persona(client, seeded):
    response = client.get("/api/reports/templates", params={"persona": "trader"})
    assert response.status_code == 200
    body = response.json()
    assert body
    assert all(row["persona"] == "trader" for row in body)


def test_get_one_template_includes_its_spec(client, seeded):
    response = client.get("/api/reports/templates/portfolio-snapshot")
    assert response.status_code == 200
    assert "sections:" in response.json()["spec"]


def test_get_unknown_template_is_404(client):
    assert client.get("/api/reports/templates/nope").status_code == 404


def test_validate_accepts_a_good_spec(client, seeded):
    spec = client.get("/api/reports/templates/portfolio-snapshot").json()["spec"]
    response = client.post("/api/reports/templates/validate", json={"spec_yaml": spec})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": []}


def test_validate_reports_errors_without_saving(client):
    bad = """
meta:
  slug: bad-one
  title: Bad
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.nope, render: metric_row }
"""
    response = client.post("/api/reports/templates/validate", json={"spec_yaml": bad})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("risk.nope" in message for message in body["errors"])
    assert client.get("/api/reports/templates/bad-one").status_code == 404


def test_put_an_invalid_template_is_422_and_persists_nothing(client):
    bad = """
meta:
  slug: bad-two
  title: Bad
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.totals, render: waterfall }
"""
    response = client.put("/api/reports/templates/bad-two", json={"spec_yaml": bad})
    assert response.status_code == 422
    assert any("waterfall" in message for message in response.json()["detail"]["errors"])
    assert client.get("/api/reports/templates/bad-two").status_code == 404


def test_put_a_valid_template_creates_it(client):
    good = """
meta:
  slug: api-made
  title: API Made
  persona: trader
sections:
  - id: totals
    title: Totals
    blocks:
      - { key: risk.totals, render: metric_row }
"""
    response = client.put("/api/reports/templates/api-made", json={"spec_yaml": good})
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert client.get("/api/reports/templates/api-made").status_code == 200


def test_deleting_a_seeded_template_is_409(client, seeded):
    response = client.delete("/api/reports/templates/portfolio-snapshot")
    assert response.status_code == 409


def test_generate_persists_a_report_job(client, seeded):
    response = client.post(
        "/api/reports/generate",
        json={"template_slug": "portfolio-snapshot", "portfolio_id": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["template_slug"] == "portfolio-snapshot"
    assert body["id"] > 0
