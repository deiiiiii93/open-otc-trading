from types import SimpleNamespace

from app.services.domains import scenario_test


class _FakeScenarioResult:
    def __init__(self, name, pnl):
        self.scenario = SimpleNamespace(name=name)
        self.portfolio_value = 1000.0 + pnl
        self.portfolio_pnl = pnl
        self.portfolio_pnl_pct = pnl / 10.0
        self.greeks = {"delta": 1.0}
        self.underlying_results = {"AAPL": {"num_positions": 1, "total_value": 1.0, "greeks": None}}
        self.position_results = []
        self.execution_time = 0.01


class _FakeResults:
    baseline_value = 1000.0
    baseline_greeks = {"delta": 2.0}
    total_execution_time = 0.5
    def __init__(self):
        self.scenario_results = [_FakeScenarioResult("Crash", -200.0),
                                 _FakeScenarioResult("Rally", 150.0)]
    def get_worst_scenario(self):
        return min(self.scenario_results, key=lambda r: r.portfolio_pnl)
    def get_best_scenario(self):
        return max(self.scenario_results, key=lambda r: r.portfolio_pnl)


def test_shape_results_picks_worst_best_and_varcvar(monkeypatch):
    monkeypatch.setattr(scenario_test, "_result_aggregator", lambda: SimpleNamespace(
        get_risk_summary=lambda r: {"avg_pnl": -25.0, "max_drawdown_pct": -20.0},
        calculate_var_cvar=lambda r, confidence_level=0.95: {"var": -200.0, "cvar": -200.0},
    ))
    shaped = scenario_test.shape_results(_FakeResults())
    assert shaped["worst_scenario"] == "Crash"
    assert shaped["best_scenario"] == "Rally"
    assert shaped["var_cvar"]["var"] == -200.0
    assert shaped["var_cvar"]["confidence"] == 0.95
    assert len(shaped["scenarios"]) == 2
    assert shaped["scenarios"][0]["name"] == "Crash"
    assert shaped["baseline_value"] == 1000.0


def test_shape_results_coerces_non_json_scalars(monkeypatch):
    monkeypatch.setattr(scenario_test, "_result_aggregator", lambda: SimpleNamespace(
        get_risk_summary=lambda r: {"avg_pnl": 0.0},
        calculate_var_cvar=lambda r, confidence_level=0.95: {"var": 0.0, "cvar": 0.0},
    ))

    class _FakeNpFloat:
        def __init__(self, v): self._v = v
        def item(self): return self._v

    res = _FakeResults()
    res.scenario_results[0].greeks = {"delta": _FakeNpFloat(1.5)}
    res.baseline_greeks = {"delta": _FakeNpFloat(2.5)}
    shaped = scenario_test.shape_results(res)
    import json
    json.dumps(shaped)  # must not raise
    assert shaped["scenarios"][0]["greeks"]["delta"] == 1.5
    assert shaped["baseline_greeks"]["delta"] == 2.5


def test_write_artifacts_is_graceful_when_libs_missing(tmp_path, monkeypatch):
    # Force exporter and report renderer to fail -> artifacts records notes, never raises.
    def _boom():
        raise ImportError("no pyarrow")
    monkeypatch.setattr(scenario_test, "_result_exporter", _boom)
    monkeypatch.setattr(
        scenario_test.scenario_test_report,
        "render_scenario_test_report_html",
        lambda _results, _title: (_ for _ in ()).throw(RuntimeError("render failed")),
    )
    artifacts = scenario_test.write_artifacts(
        results={"baseline_value": 1000.0, "scenarios": []},
        run_id=7, formats=["parquet"], base_dir=str(tmp_path))
    assert "notes" in artifacts
    assert artifacts["export_paths"] == []
    assert artifacts["report_html_path"] is None


def test_write_artifacts_renders_repo_html_report(tmp_path):
    results = {
        "baseline_value": 1000.0,
        "scenarios": [
            {"name": "Crash", "portfolio_value": 800.0, "pnl": -200.0, "pnl_pct": -20.0,
             "greeks": {"delta": 0.5}, "underlying_results": {}, "position_results": [],
             "execution_time": 0.1},
            {"name": "Rally", "portfolio_value": 1100.0, "pnl": 100.0, "pnl_pct": 10.0,
             "greeks": {"delta": 0.6}, "underlying_results": {}, "position_results": [],
             "execution_time": 0.1},
        ],
        "worst_scenario": "Crash",
        "best_scenario": "Rally",
        "risk_summary": {"avg_pnl": -50.0},
        "var_cvar": {"var": -200.0, "cvar": -200.0, "confidence": 0.95},
        "num_scenarios": 2,
        "execution_time": 0.2,
        "pricing_warnings": [{"position_id": 5, "reason": "fallback"}],
    }
    artifacts = scenario_test.write_artifacts(
        results=results,
        excluded_positions=[{"position_id": 1, "reason": "closed"}],
        run_id=8, formats=[], base_dir=str(tmp_path))
    assert artifacts["report_html_path"] is not None
    report_path = artifacts["report_html_path"]
    content = open(report_path, encoding="utf-8").read()
    assert "Scenario Test #8" in content
    assert "Crash" in content
    assert "Rally" in content
    assert "Pricing Warnings" in content
    assert "fallback" in content
    assert "excluded" in content.lower()


def test_render_report_html_escapes_values():
    from app.services.domains.scenario_test_report import render_scenario_test_report_html
    results = {
        "baseline_value": 1000.0,
        "scenarios": [
            {"name": "<script>alert(1)</script>", "portfolio_value": 900.0, "pnl": -100.0,
             "pnl_pct": -10.0, "greeks": None, "underlying_results": {},
             "position_results": [], "execution_time": 0.1},
        ],
        "num_scenarios": 1,
    }
    html_out = render_scenario_test_report_html(results, title="Test <title>")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
    assert "&lt;title&gt;" in html_out
    assert "Test &lt;title&gt;" in html_out

