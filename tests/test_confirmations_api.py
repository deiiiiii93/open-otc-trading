"""REST surface over the trade-confirmation pipeline (Task 5).

`dispatch_parse` normally hands off to a ThreadPoolExecutor
(services.task_runner.submit_async_task), which would make these tests racy.
Determinism here comes from monkeypatching `service.dispatch_parse` (the
module attribute the router calls through `confirmations.dispatch_parse`,
where `confirmations` IS the `app.services.confirmations.service` module
object) to instead run `run_parse_batch` synchronously and in-request with a
FakeClient, mirroring tests/test_confirmations_service.py's fixtures.

`run_parse_batch` opens its OWN `database.SessionLocal()`, separate from the
router's per-request session. Because that per-request session has
`expire_on_commit=False` and already holds the freshly created (pre-parse)
`ConfirmationDocument` rows in its identity map from `create_batch`, the
*upload* response can legitimately still show stale (pending, trade-less)
documents even though parsing has already committed underneath it — the
follow-up `GET` is a brand-new request/session with an empty identity map,
so it reads the committed "parsed" state cleanly. Assertions below are
written to respect that: the POST response is checked only for document
count and task_id; parsed status and trades are checked via GET.
"""
import json

import pytest

from app.models import AuditEvent, ConfirmationBatch, Portfolio, Position, TaskRun
from app.services.confirmations import service as confirmations_service
from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.domains.booking import book_position as _real_book_position

# initial_price (S0) is required by every product_builders family — see
# tests/test_product_builders.py's canonical minimal vanilla term set.
VANILLA_TERMS = {
    "option_type": "call", "strike": 150.0, "maturity_years": 1.0,
    "initial_price": 148.0,
}


class FakeClient:
    """Pops canned stage-1/stage-2 JSON responses in call order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


def _seg_json(family: str = "EuropeanVanillaOption") -> str:
    return json.dumps({"trades": [{"family": family, "pages": [1], "anchor": "BUY 100"}]})


def _trade_json(external_trade_id: str, strike: float = 150.0) -> str:
    return json.dumps({
        "terms": {**VANILLA_TERMS, "strike": strike}, "underlying": "AAPL",
        "quantity": 100, "entry_price": 12.5, "currency": "USD",
        "counterparty": "Big Bank", "trade_date": "2026-08-01",
        "external_trade_id": external_trade_id, "confidence": 0.9,
        "evidence": {"strike": {"quote": f"strike {strike}", "page": 1}},
    })


def _sync_dispatch(fake_client):
    """Replacement for service.dispatch_parse: run the parse in-request."""
    def _dispatch(batch_id, task_id):
        confirmations_service.run_parse_batch(batch_id, task_id, client=fake_client)
    return _dispatch


@pytest.fixture(autouse=True)
def _aapl_is_bookable(registered_underlying):
    """The confirmations in this file trade AAPL, and validation now requires
    the underlying to be an ACTIVE instrument tagged "underlying"."""
    registered_underlying("AAPL")


@pytest.fixture(autouse=True)
def _fake_extract_document(monkeypatch):
    monkeypatch.setattr(
        confirmations_service, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="BUY 100 AAPL call")],
            page_count=1, extract_mode="text"),
    )


@pytest.fixture
def container_portfolio(session):
    p = Portfolio(name="Conf API Test Book", kind="container")
    session.add(p)
    session.flush()
    session.commit()
    return p


def _upload(client, monkeypatch, *, fake_client, portfolio_id=None, filenames=("c.pdf",)):
    monkeypatch.setattr(confirmations_service, "dispatch_parse", _sync_dispatch(fake_client))
    data = {}
    if portfolio_id is not None:
        data["portfolio_id"] = str(portfolio_id)
    files = [
        ("files", (name, b"%PDF-1.4 fake " + name.encode(), "application/pdf"))
        for name in filenames
    ]
    resp = client.post("/api/confirmations", files=files, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _upload_and_get_trade(client, monkeypatch, *, external_trade_id, portfolio_id=None):
    fake_client = FakeClient(_seg_json(), _trade_json(external_trade_id))
    body = _upload(client, monkeypatch, fake_client=fake_client, portfolio_id=portfolio_id)
    detail = client.get(f"/api/confirmations/{body['id']}").json()
    trade = detail["documents"][0]["trades"][0]
    return body["id"], trade


# (a) POST two files -> batch with 2 documents + task_id set, then GET shows
# parsed docs with trades.
def test_upload_two_files_then_get_shows_parsed_trades(client, monkeypatch):
    fake_client = FakeClient(
        _seg_json(), _trade_json("TC-A"), _seg_json(), _trade_json("TC-B"),
    )
    body = _upload(
        client, monkeypatch, fake_client=fake_client, filenames=("a.pdf", "b.pdf"),
    )
    assert len(body["documents"]) == 2
    assert body["task_id"] is not None

    detail = client.get(f"/api/confirmations/{body['id']}").json()
    assert detail["id"] == body["id"]
    assert {d["status"] for d in detail["documents"]} == {"parsed"}
    all_trades = [t for d in detail["documents"] for t in d["trades"]]
    assert len(all_trades) == 2
    assert {t["external_trade_id"] for t in all_trades} == {"TC-A", "TC-B"}
    for t in all_trades:
        assert t["validation_status"] == "valid"
        assert t["status"] == "extracted"


def test_list_batches_includes_uploaded_batch(client, monkeypatch):
    fake_client = FakeClient(_seg_json(), _trade_json("TC-LIST"))
    body = _upload(client, monkeypatch, fake_client=fake_client, filenames=("l.pdf",))
    listing = client.get("/api/confirmations").json()
    assert any(b["id"] == body["id"] for b in listing)


def test_get_unknown_batch_404s(client):
    resp = client.get("/api/confirmations/999999")
    assert resp.status_code == 404


# (b) PUT trade edit -> revalidates.
def test_put_trade_edit_revalidates(client, monkeypatch):
    _, trade = _upload_and_get_trade(client, monkeypatch, external_trade_id="TC-EDIT")
    resp = client.put(
        f"/api/confirmations/trades/{trade['id']}",
        json={"terms": {**VANILLA_TERMS, "strike": 155.0}},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["terms"]["strike"] == 155.0
    assert updated["validation_status"] == "valid"
    # original extraction is preserved immutably alongside the edit
    assert updated["extracted_terms"]["strike"] == 150.0


def test_put_unknown_trade_409s(client):
    resp = client.put(
        "/api/confirmations/trades/999999",
        json={"terms": VANILLA_TERMS},
    )
    assert resp.status_code == 409


# (c) book -> Position created + audit row asserted; (e) book again -> already_booked.
def test_book_trade_creates_position_and_audit_row_then_rebook_fails(
    client, session, monkeypatch, container_portfolio,
):
    _, trade = _upload_and_get_trade(
        client, monkeypatch, external_trade_id="TC-BOOK",
        portfolio_id=container_portfolio.id,
    )

    resp = client.post(f"/api/confirmations/trades/{trade['id']}/book", json={})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["ok"] is True
    position_id = result["position_id"]

    position = session.get(Position, position_id)
    assert position is not None
    assert position.portfolio_id == container_portfolio.id
    assert position.source_trade_id == "TC-BOOK"

    audit_rows = (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "confirmations.trade_booked")
        .all()
    )
    assert any(
        row.subject_type == "extracted_trade"
        and row.subject_id == str(trade["id"])
        and row.payload.get("position_id") == position_id
        for row in audit_rows
    )

    # (e) booking the same trade again is refused, not double-booked.
    again = client.post(f"/api/confirmations/trades/{trade['id']}/book", json={})
    assert again.status_code == 200
    again_body = again.json()
    assert again_body["ok"] is False
    assert again_body["error"] == "already_booked"
    assert again_body["position_id"] == position_id


def test_book_trade_without_target_portfolio_fails_honestly(client, monkeypatch):
    _, trade = _upload_and_get_trade(client, monkeypatch, external_trade_id="TC-NOPORT")
    resp = client.post(f"/api/confirmations/trades/{trade['id']}/book", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "no_target_portfolio"


def test_book_trade_booking_failed_rolls_back(
    client, session, monkeypatch, container_portfolio,
):
    """book_position raising ValueError -> {"ok": False, "error": "booking_failed"}
    -> router `session.rollback()`. The wrapper calls the REAL book_position first
    (so a Position row is genuinely flushed, exercising an actual partial write)
    and only then raises, so this proves the rollback discards that flushed-but-
    uncommitted row rather than merely proving a pre-flush guard never wrote
    anything in the first place.
    """
    batch_id, trade = _upload_and_get_trade(
        client, monkeypatch, external_trade_id="TC-BOOKFAIL",
        portfolio_id=container_portfolio.id,
    )

    def _flush_then_fail(sess, req):
        _real_book_position(sess, req)
        raise ValueError("forced booking failure")

    monkeypatch.setattr(confirmations_service, "book_position", _flush_then_fail)

    resp = client.post(f"/api/confirmations/trades/{trade['id']}/book", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "booking_failed"

    # Fresh GET (new session) proves the trade was never mutated to "booked".
    detail = client.get(f"/api/confirmations/{batch_id}").json()
    refreshed = detail["documents"][0]["trades"][0]
    assert refreshed["status"] == "extracted"
    assert refreshed["booked_position_id"] is None

    # Fresh session query proves no orphan Position row survived the rollback.
    count = (
        session.query(Position)
        .filter(Position.source_trade_id == "TC-BOOKFAIL")
        .count()
    )
    assert count == 0


# (d) reject -> status rejected.
def test_reject_trade_marks_rejected_with_reason(client, monkeypatch):
    _, trade = _upload_and_get_trade(client, monkeypatch, external_trade_id="TC-REJECT")
    resp = client.post(
        f"/api/confirmations/trades/{trade['id']}/reject",
        json={"reason": "duplicate confirmation"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "duplicate confirmation"


def test_reject_trade_writes_audit_row(client, session, monkeypatch):
    _, trade = _upload_and_get_trade(client, monkeypatch, external_trade_id="TC-REJECT-AUDIT")
    resp = client.post(
        f"/api/confirmations/trades/{trade['id']}/reject",
        json={"reason": "bad scan"},
    )
    assert resp.status_code == 200, resp.text

    audit_rows = (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "confirmations.trade_rejected")
        .all()
    )
    assert any(
        row.subject_type == "extracted_trade"
        and row.subject_id == str(trade["id"])
        and row.payload.get("reason") == "bad scan"
        for row in audit_rows
    )


def test_reject_unknown_trade_409s(client):
    resp = client.post("/api/confirmations/trades/999999/reject", json={})
    assert resp.status_code == 409


def test_reject_booked_trade_409s(client, monkeypatch, container_portfolio):
    _, trade = _upload_and_get_trade(
        client, monkeypatch, external_trade_id="TC-REJECT-BOOKED",
        portfolio_id=container_portfolio.id,
    )
    book_resp = client.post(f"/api/confirmations/trades/{trade['id']}/book", json={})
    assert book_resp.json()["ok"] is True

    resp = client.post(f"/api/confirmations/trades/{trade['id']}/reject", json={})
    assert resp.status_code == 409


def test_upload_writes_upload_audit_row(client, session, monkeypatch):
    fake_client = FakeClient(_seg_json(), _trade_json("TC-AUDIT-UPLOAD"))
    body = _upload(client, monkeypatch, fake_client=fake_client, filenames=("u.pdf",))
    audit_rows = (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "confirmations.uploaded")
        .all()
    )
    assert any(
        row.subject_type == "confirmation_batch" and row.subject_id == str(body["id"])
        for row in audit_rows
    )


def test_upload_dispatch_failure_returns_500_and_marks_task_failed(
    client, session, monkeypatch,
):
    """dispatch_parse raising -> router catches, marks the TaskRun "failed", commits
    that, then raises HTTPException(500). The batch + document rows created before
    dispatch was ever called must survive (only dispatch failed, not the upload)."""
    def _boom(batch_id, task_id):
        raise RuntimeError("dispatch boom")

    monkeypatch.setattr(confirmations_service, "dispatch_parse", _boom)

    resp = client.post(
        "/api/confirmations",
        files=[("files", ("d.pdf", b"%PDF-1.4 fake d", "application/pdf"))],
    )
    assert resp.status_code == 500

    # Fresh queries (this session never touched these rows before) prove the
    # upload's own writes were committed prior to dispatch, independent of the
    # 500 raised afterward.
    batch = (
        session.query(ConfirmationBatch)
        .order_by(ConfirmationBatch.id.desc())
        .first()
    )
    assert batch is not None
    assert len(batch.documents) == 1

    task = session.get(TaskRun, batch.task_id)
    assert task is not None
    assert task.status == "failed"
    assert "dispatch boom" in (task.error or "")
