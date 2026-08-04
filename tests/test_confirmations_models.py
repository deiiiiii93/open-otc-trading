from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade


def test_confirmation_models_round_trip(session):
    batch = ConfirmationBatch(source="web", default_portfolio_id=None)
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="conf.pdf", stored_path="/tmp/conf.pdf",
        sha256="a" * 64, byte_len=10, mime="application/pdf",
    )
    session.add(doc)
    session.flush()
    trade = ExtractedTrade(
        document_id=doc.id, seq=1, family="EuropeanVanillaOption",
        extracted_terms={"strike": 100.0}, terms={"strike": 100.0},
        underlying="AAPL", evidence={}, validation_errors=[],
    )
    session.add(trade)
    session.flush()
    assert doc.status == "pending"
    assert trade.status == "extracted"
    assert trade.validation_status == "invalid"
    assert batch.documents[0].trades[0].id == trade.id


def test_batch_delete_cascades(session):
    batch = ConfirmationBatch(source="agent")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="x.docx", stored_path="/tmp/x.docx",
        sha256="b" * 64, byte_len=5, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    session.add(doc)
    session.flush()
    session.delete(batch)
    session.flush()
    assert session.get(ConfirmationDocument, doc.id) is None
