from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools.rfq import (
    approve_rfq_tool,
    book_rfq_to_position_tool,
    create_or_update_rfq_draft_tool,
    get_rfq_catalog_tool,
    mark_rfq_client_accepted_tool,
    quote_rfq_tool,
    reject_rfq_tool,
    release_rfq_tool,
    solve_rfq_tool,
    submit_rfq_for_approval_tool,
    validate_rfq_terms_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    """Per-test SQLite fixture — every tool opens its own SessionLocal."""
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


# ----- helpers ---------------------------------------------------------------


def _make_rfq_stub(**overrides):
    """Build a minimal duck-typed RFQ for shape_rfq to consume."""
    base = {
        "id": 1,
        "status": "draft",
        "client_name": "Demo Client",
        "channel": "agent",
        "request_payload": {"client_name": "Demo Client"},
        "quote_payload": {},
        "approved_response": None,
        "quote_versions": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _draft_payload(**overrides) -> dict:
    payload = {
        "client_name": "Demo Client",
        "side": "buy",
        "quantity": 1.0,
        "product": {
            "underlying": "AAPL",
            "quantark_class": "EuropeanVanillaOption",
            "terms": {"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        },
        "engine_spec": {"engine_name": "BlackScholesEngine"},
    }
    payload.update(overrides)
    return payload


# ----- read-only / pure tools -------------------------------------------------


def test_solve_rfq_tool(monkeypatch):
    """solve_rfq dispatches to services.quantark.solve_rfq via the facade."""
    fake = SimpleNamespace(ok=True, data={"price": 1.23}, error=None)
    monkeypatch.setattr("app.services.domains.rfq.solve_rfq", lambda draft: fake)
    result = solve_rfq_tool.invoke(_draft_payload())
    assert result == {"ok": True, "quote": {"price": 1.23}, "error": None}


def test_get_rfq_catalog_tool(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.rfq.get_rfq_catalog",
        lambda: {"product_types": [], "engine_options": []},
    )
    result = get_rfq_catalog_tool.invoke({})
    assert result["product_types"] == []
    assert result["engine_options"] == []


def test_validate_rfq_terms_tool(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.rfq.validate_rfq_terms",
        lambda terms, mode: {"valid": True, "errors": [], "missing_fields": []},
    )
    result = validate_rfq_terms_tool.invoke(
        {"terms": _draft_payload(), "quote_mode": "solve"}
    )
    assert result == {"valid": True, "errors": [], "missing_fields": []}


# ----- write / lifecycle tools (mock the underlying service) ------------------


def test_create_or_update_rfq_draft_tool_create(monkeypatch):
    """rfq_id=None -> create_rfq_draft path."""
    rfq = _make_rfq_stub(id=42)
    captured: dict = {}

    def fake_create(session, payload, *, channel, actor):
        captured["channel"] = channel
        captured["actor"] = actor
        return rfq

    monkeypatch.setattr("app.services.domains.rfq.create_rfq_draft", fake_create)
    monkeypatch.setattr(
        "app.services.domains.rfq.update_rfq_draft",
        lambda *a, **k: pytest.fail("update should not be called"),
    )
    result = create_or_update_rfq_draft_tool.invoke(
        {"draft": _draft_payload(), "channel": "desk", "actor": "alice"}
    )
    assert result["rfq_id"] == 42
    assert captured == {"channel": "desk", "actor": "alice"}


def test_create_or_update_rfq_draft_tool_update(monkeypatch):
    """rfq_id given -> update_rfq_draft path."""
    rfq = _make_rfq_stub(id=7, status="draft")
    captured: dict = {}

    def fake_update(session, rfq_id, payload, *, actor):
        captured["rfq_id"] = rfq_id
        captured["actor"] = actor
        return rfq

    monkeypatch.setattr("app.services.domains.rfq.update_rfq_draft", fake_update)
    monkeypatch.setattr(
        "app.services.domains.rfq.create_rfq_draft",
        lambda *a, **k: pytest.fail("create should not be called"),
    )
    result = create_or_update_rfq_draft_tool.invoke(
        {"rfq_id": 7, "draft": _draft_payload(), "actor": "alice"}
    )
    assert result["rfq_id"] == 7
    assert captured == {"rfq_id": 7, "actor": "alice"}


def test_quote_rfq_tool(monkeypatch):
    rfq = _make_rfq_stub(id=11, status="pending_approval")
    monkeypatch.setattr(
        "app.services.domains.rfq.quote_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = quote_rfq_tool.invoke({"rfq_id": 11, "quote_mode": "solve"})
    assert result["rfq_id"] == 11
    assert result["status"] == "pending_approval"


def test_submit_rfq_for_approval_tool(monkeypatch):
    rfq = _make_rfq_stub(id=3, status="submitted")
    monkeypatch.setattr(
        "app.services.domains.rfq.submit_rfq_for_approval",
        lambda session, rfq_id, *, actor: rfq,
    )
    result = submit_rfq_for_approval_tool.invoke({"rfq_id": 3})
    assert result["status"] == "submitted"


def test_approve_rfq_tool(monkeypatch):
    rfq = _make_rfq_stub(id=5, status="approved", approved_response="OK")
    monkeypatch.setattr(
        "app.services.domains.rfq.approve_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = approve_rfq_tool.invoke({"rfq_id": 5, "approver": "alice"})
    assert result["status"] == "approved"
    assert result["approved_response"] == "OK"


def test_reject_rfq_tool(monkeypatch):
    rfq = _make_rfq_stub(id=6, status="rejected")
    monkeypatch.setattr(
        "app.services.domains.rfq.reject_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = reject_rfq_tool.invoke({"rfq_id": 6, "approver": "alice"})
    assert result["status"] == "rejected"


def test_release_rfq_tool(monkeypatch):
    rfq = _make_rfq_stub(id=8, status="released")
    monkeypatch.setattr(
        "app.services.domains.rfq.release_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = release_rfq_tool.invoke({"rfq_id": 8, "actor": "trader"})
    assert result["status"] == "released"


def test_mark_rfq_client_accepted_tool(monkeypatch):
    rfq = _make_rfq_stub(id=9, status="client_accepted")
    monkeypatch.setattr(
        "app.services.domains.rfq.mark_client_accepted",
        lambda session, rfq_id, payload: rfq,
    )
    result = mark_rfq_client_accepted_tool.invoke({"rfq_id": 9, "actor": "client"})
    assert result["status"] == "client_accepted"


def test_book_rfq_to_position_tool(monkeypatch):
    position = SimpleNamespace(
        id=100,
        portfolio_id=1,
        underlying="AAPL",
        product_type="EuropeanVanillaOption",
        quantity=1.0,
        entry_price=10.5,
        status="open",
        rfq_id=12,
        rfq_quote_version_id=34,
    )
    monkeypatch.setattr(
        "app.services.domains.rfq.book_rfq_to_position",
        lambda session, rfq_id, payload: position,
    )
    result = book_rfq_to_position_tool.invoke(
        {"rfq_id": 12, "portfolio_id": 1, "actor": "trader"}
    )
    assert result["position_id"] == 100
    assert result["rfq_id"] == 12
    assert result["rfq_quote_version_id"] == 34
