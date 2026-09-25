"""Reports a contestant creates must not outlive its match.

``report_jobs`` has no portfolio column (the portfolio sits in
``request_payload``), so the portfolio-dependents purge cannot reach a report,
and the seeded-report purge only knows the reserved arena marker type. Measured
2026-09-25: report 8 ("Board — Daily One-Pager", portfolio 9101), minted by a
minimax-m3 high-board match on 2026-08-26, sat in every later high-board match.
Every match re-seeds "Desk Control Book" as 9101, so it read as last quarter's
board report for the fresh book: gpt-6-luna answered its 299.17 instead of the
seeded 211.34 (run #141).
"""
from __future__ import annotations

import json
from datetime import timedelta, timezone

from app import models
from app.services.arena import runner
from app.services.arena.trace_harvest import _extract_report_id, collect_report_ids_created


def _thread(session, source="arena"):
    t = models.AgentThread(title="t", source=source)
    session.add(t)
    session.commit()
    return t


def _report(session, portfolio_id=9101):
    r = models.ReportJob(report_type="high_board", status="completed",
                         request_payload={"portfolio_id": portfolio_id},
                         result_payload={}, artifact_paths={})
    session.add(r)
    session.commit()
    return r


def _span(rid, thread_id, at):
    return {"report_id": rid, "thread_id": thread_id,
            "start_time": at.replace(tzinfo=timezone.utc).isoformat()}


def _sweep(monkeypatch, spans):
    monkeypatch.setattr(runner, "collect_report_creations", lambda store=None: spans)
    return runner._sweep_orphaned_match_reports()


def test_both_report_tools_id_shapes_are_read():
    assert _extract_report_id({"report_job_id": 4, "task_id": 84}) == 4  # create_report
    assert _extract_report_id({"report_id": 8, "template_slug": "x"}) == 8  # generate_report
    assert _extract_report_id({"data": {"report_id": 3}}) == 3
    assert _extract_report_id({"reports": []}) is None


def test_a_report_minted_by_an_arena_thread_is_reclaimed(session, monkeypatch):
    thread = _thread(session)
    leak = _report(session)
    task = models.TaskRun(kind="report", status="succeeded", report_job_id=leak.id)
    session.add(task)
    session.commit()
    leak_id, task_id = leak.id, task.id

    assert _sweep(monkeypatch, [_span(leak_id, thread.id, leak.created_at)]) == [leak_id]

    session.expire_all()
    assert session.get(models.ReportJob, leak_id) is None
    # The task row survives as audit evidence; it just no longer names the report.
    assert session.get(models.TaskRun, task_id).report_job_id is None


def test_a_desk_users_report_is_never_touched(session, monkeypatch):
    real = _report(session, portfolio_id=2)
    chat = _thread(session, source="chat")
    assert _sweep(monkeypatch, [_span(real.id, chat.id, real.created_at)]) == []
    assert session.get(models.ReportJob, real.id) is not None


def test_a_reused_report_id_is_not_mistaken_for_the_leak(session, monkeypatch):
    thread = _thread(session)
    reused = _report(session)
    long_ago = reused.created_at - timedelta(days=3)
    assert _sweep(monkeypatch, [_span(reused.id, thread.id, long_ago)]) == []
    assert session.get(models.ReportJob, reused.id) is not None


def test_the_sweep_never_raises(monkeypatch):
    def boom(store=None):
        raise RuntimeError("trace store down")

    monkeypatch.setattr(runner, "collect_report_creations", boom)
    assert runner._sweep_orphaned_match_reports() == []


class _Store:
    def __init__(self, spans):
        self._spans = spans

    def list_thread_traces(self, thread_id, *, limit=50, offset=0):
        return [{"trace_id": "T"}]

    def get_trace(self, trace_id):
        return self._spans


def _tool_span(name, content):
    out = {"output": f"content='{json.dumps(content)}' name='{name}' tool_call_id='c1'"}
    return {"run_type": "tool", "name": name, "outputs": json.dumps(out)}


def test_match_purge_takes_only_reports_this_thread_minted_above_baseline(session, monkeypatch):
    old_id = _report(session, portfolio_id=2).id   # pre-existing: read, not minted
    mine_id = _report(session).id
    store = _Store([
        _tool_span("generate_report", {"report_id": mine_id}),
        _tool_span("get_report", {"report_id": old_id}),
    ])
    assert collect_report_ids_created(1, store=store) == {mine_id}

    monkeypatch.setattr(runner, "collect_report_ids_created",
                        lambda thread_id, store=None: {mine_id, old_id})
    runner._purge_match_reports(1, report_id_baseline=old_id)
    session.expire_all()
    assert session.get(models.ReportJob, mine_id) is None
    assert session.get(models.ReportJob, old_id) is not None  # <= baseline: spared


def test_run_match_wires_the_report_sweep_and_purge():
    import inspect

    src = inspect.getsource(runner.run_match)
    assert src.index("_sweep_orphaned_match_reports()") < src.index("_purge_seeded_portfolios(session")
    assert "_purge_match_reports(thread_id, report_id_baseline)" in src
