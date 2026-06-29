"""Tests for live-created RFQ cleanup: collect_rfq_ids_touched + _purge_arena_rfqs."""
from app.services.arena.trace_harvest import collect_rfq_ids_touched
from app.services.arena import runner
from app import models


def test_collect_rfq_ids_touched_from_spans():
    # One RFQ-tool span whose output carries rfq_id=42.
    spans = [{
        "run_type": "tool",
        "name": "create_or_update_rfq_draft",
        "outputs": {"output": {"name": "create_or_update_rfq_draft",
                               "tool_call_id": "c1", "rfq_id": 42}},
    }]

    class _Store:  # mirrors the real TraceStore reader API used by transcript_from_trace
        def list_thread_traces(self, thread_id, limit=1000):
            return [{"trace_id": "t1", "start_time": "2026-01-01"}]

        def get_trace(self, trace_id):
            return spans

    ids = collect_rfq_ids_touched(thread_id=1, store=_Store())
    assert ids == {42}


def test_collect_ignores_non_rfq_tool_spans():
    spans = [{
        "run_type": "tool",
        "name": "run_batch_pricing",  # not an RFQ tool
        "outputs": {"output": {"name": "run_batch_pricing", "tool_call_id": "c1", "id": 99}},
    }]

    class _Store:
        def list_thread_traces(self, thread_id, limit=1000):
            return [{"trace_id": "t1", "start_time": "2026-01-01"}]

        def get_trace(self, trace_id):
            return spans

    assert collect_rfq_ids_touched(thread_id=1, store=_Store()) == set()


def test_purge_arena_rfqs_cascades(session):
    rfq = models.RFQ(status="submitted", client_name="ARENA")
    session.add(rfq)
    session.flush()
    session.add(models.RFQQuoteVersion(rfq_id=rfq.id, version=1))
    session.commit()
    rid = rfq.id

    runner._purge_arena_rfqs(session, {rid})
    session.commit()

    assert session.get(models.RFQ, rid) is None
    assert session.query(models.RFQQuoteVersion).filter_by(rfq_id=rid).count() == 0
