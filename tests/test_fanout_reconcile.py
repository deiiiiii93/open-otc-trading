"""Task 8: deterministic coverage reconciliation (the real guarantee)."""
from app.services.deep_agent.dynamic_subagents import MAX_PTC_CALLS, reconcile_fanout_coverage


def _rec(pid, **kw):
    return {"position_id": pid, **kw}


def test_every_scoped_id_gets_exactly_one_record():
    out = reconcile_fanout_coverage(
        ["p1", "p2"],
        [_rec("p1", severity="high", commentary="x"), _rec("p2", severity="low", commentary="y")],
    )
    assert {r["position_id"] for r in out["records"]} == {"p1", "p2"}
    assert len(out["records"]) == 2 and out["covered"] == 2 and out["total"] == 2
    assert out["failed_ids"] == []


def test_missing_id_becomes_failed_not_dropped():
    out = reconcile_fanout_coverage(["p1", "p2", "p3"], [_rec("p1", severity="high", commentary="x")])
    by_id = {r["position_id"]: r for r in out["records"]}
    assert set(by_id) == {"p1", "p2", "p3"}  # coverage preserved
    assert by_id["p2"]["status"] == "failed" and by_id["p3"]["status"] == "failed"
    assert set(out["failed_ids"]) == {"p2", "p3"}


def test_explicit_failed_record_is_kept():
    out = reconcile_fanout_coverage(["p1"], [_rec("p1", status="failed")])
    assert out["records"][0]["status"] == "failed" and out["failed_ids"] == ["p1"]


def test_overflow_all_covered_even_beyond_ptc_budget():
    scoped = [f"p{i}" for i in range(MAX_PTC_CALLS + 40)]  # 64 > 24
    records = [_rec(f"p{i}", severity="low", commentary="c") for i in range(MAX_PTC_CALLS)]
    out = reconcile_fanout_coverage(scoped, records)
    assert len(out["records"]) == len(scoped)  # ALL covered
    assert out["covered"] == MAX_PTC_CALLS
    assert len(out["failed_ids"]) == 40  # truncated tail -> failed


def test_duplicate_records_collapse_to_one():
    out = reconcile_fanout_coverage(
        ["p1"], [_rec("p1", severity="low", commentary="a"), _rec("p1", severity="low", commentary="b")]
    )
    assert len([r for r in out["records"] if r["position_id"] == "p1"]) == 1
    assert out["failed_ids"] == []


def test_records_for_unscoped_ids_ignored():
    out = reconcile_fanout_coverage(
        ["p1"], [_rec("p1", severity="low", commentary="a"), _rec("ghost", severity="low", commentary="z")]
    )
    assert {r["position_id"] for r in out["records"]} == {"p1"}


def test_int_and_str_ids_match_after_normalization():
    # scope ids are ints (from the DB); a subagent echoes int 1 and str "2".
    out = reconcile_fanout_coverage(
        [1, 2],
        [{"position_id": 1, "severity": "low", "commentary": "x"},
         {"position_id": "2", "severity": "low", "commentary": "y"}],
    )
    assert out["failed_ids"] == []
    assert out["covered"] == 2 and out["total"] == 2


def test_malformed_or_error_records_become_failed():
    # missing severity/commentary, or an error status -> failed, never silently covered.
    out = reconcile_fanout_coverage(
        ["p1", "p2", "p3"],
        [
            {"position_id": "p1"},  # missing severity+commentary
            {"position_id": "p2", "severity": "high", "status": "error"},  # error status
            _rec("p3", severity="low", commentary="ok"),  # valid success
        ],
    )
    assert set(out["failed_ids"]) == {"p1", "p2"}
    assert out["covered"] == 1  # only p3


# --- server-authoritative scope enumeration ---

def _session_returning(run):
    from types import SimpleNamespace

    class _Result:
        def scalar_one_or_none(self):
            return run

    return SimpleNamespace(execute=lambda *a, **k: _Result())


def test_enumerate_reads_breaches_from_latest_riskrun():
    from types import SimpleNamespace

    from app.services.risk_limits import enumerate_limit_breaches

    run = SimpleNamespace(metrics={"limit_breaches": [{"position_id": 7}, {"position_id": 9}, 9]})
    assert enumerate_limit_breaches(_session_returning(run), "1") == ["7", "9"]


def test_enumerate_empty_when_no_run_or_no_breaches():
    from types import SimpleNamespace

    from app.services.risk_limits import enumerate_limit_breaches

    assert enumerate_limit_breaches(_session_returning(None), "1") == []
    assert enumerate_limit_breaches(_session_returning(SimpleNamespace(metrics={})), "1") == []


def test_enumerate_from_a_persisted_riskrun(session):
    """Integration: against a real persisted RiskRun with breach metrics, the latest
    run's breach ids are returned as strings (and the newest run wins)."""
    from app.models import Portfolio, RiskRun
    from app.services.risk_limits import enumerate_limit_breaches

    pf = Portfolio(name="breach-pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(RiskRun(portfolio_id=pf.id, metrics={"limit_breaches": [{"position_id": 3}]}))
    session.add(
        RiskRun(portfolio_id=pf.id, metrics={"limit_breaches": [{"position_id": 7}, {"position_id": 9}]})
    )
    session.flush()
    assert enumerate_limit_breaches(session, str(pf.id)) == ["7", "9"]  # newest run


def test_newer_queued_run_does_not_erase_completed_breaches(session):
    """A freshly-queued empty run (the workflow's own scope step) must not mask an
    older completed run's breaches — only terminal-status runs are authoritative."""
    from app.models import Portfolio, RiskRun
    from app.services.risk_limits import enumerate_limit_breaches

    pf = Portfolio(name="queued-pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"limit_breaches": [{"position_id": 5}]}))
    session.add(RiskRun(portfolio_id=pf.id, status="queued", metrics={}))  # newer, non-terminal
    session.flush()
    assert enumerate_limit_breaches(session, str(pf.id)) == ["5"]  # completed run wins


def test_tool_uses_server_scope_not_model_records(session, monkeypatch):
    """Model-supplied records cannot shrink coverage: scope is server-derived, so
    omitted breaches surface as failed."""
    import app.tools.assemble_breach_report as t

    scoped = [f"p{i}" for i in range(MAX_PTC_CALLS + 40)]
    monkeypatch.setattr(t, "enumerate_limit_breaches", lambda *a, **k: scoped)
    records = [{"position_id": f"p{i}", "severity": "low", "commentary": "c"} for i in range(MAX_PTC_CALLS)]
    out = t._assemble("ptf-1", records)
    assert len(out["records"]) == len(scoped)
    assert len(out["failed_ids"]) == 40


def test_server_launch_arg_overrides_model_portfolio_id(session, monkeypatch):
    """A hallucinated/injected model portfolio_id cannot redirect the report: the
    server-stamped launch arg wins."""
    import langgraph.config as lgc

    import app.tools.assemble_breach_report as t
    from app.services.deep_agent.dynamic_subagents import FANOUT_LAUNCH_ARGS_KEY

    captured: dict = {}

    def _fake_enum(_session, pid):
        captured["pid"] = pid
        return []

    monkeypatch.setattr(t, "enumerate_limit_breaches", _fake_enum)
    monkeypatch.setattr(
        lgc, "get_config",
        lambda: {"configurable": {FANOUT_LAUNCH_ARGS_KEY: {"portfolio_id": "SERVER-9"}}},
    )
    t._assemble("MODEL-1", [])
    assert captured["pid"] == "SERVER-9"  # server launch arg, not the model's "MODEL-1"
