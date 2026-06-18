"""Scenario-test pipeline: build EquityPortfolio, run StressTestEngine, shape results."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services import quantark, scenario_test_bridge
from app.services.domains import scenario_catalog, scenario_test_report


def _result_aggregator():
    quantark.ensure_quantark_path()
    from quantark.stresstest.results.result_aggregator import ResultAggregator
    return ResultAggregator


def _jsonable(value: Any) -> Any:
    """Recursively coerce numpy scalars / non-JSON-native values to plain Python
    so results survive json.dumps (SQLAlchemy JSON column + FastAPI)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    # numpy scalars and other number-likes expose .item(); fall back to float/str
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def shape_results(results: Any) -> dict[str, Any]:
    """Project a QuantArk StressTestResults into a JSON-serializable dict."""
    aggregator = _result_aggregator()
    worst = results.get_worst_scenario()
    best = results.get_best_scenario()
    scenarios = [
        {
            "name": r.scenario.name,
            "portfolio_value": float(r.portfolio_value),
            "pnl": float(r.portfolio_pnl),
            "pnl_pct": float(r.portfolio_pnl_pct),
            "greeks": r.greeks,
            "underlying_results": r.underlying_results,
            "position_results": r.position_results,
            "execution_time": float(r.execution_time),
        }
        for r in results.scenario_results
    ]
    try:
        risk_summary = aggregator.get_risk_summary(results)
    except Exception as exc:  # pragma: no cover - defensive
        risk_summary = {"error": str(exc)}
    try:
        var_cvar = aggregator.calculate_var_cvar(results, confidence_level=0.95)
        var_cvar["confidence"] = 0.95
    except Exception as exc:  # pragma: no cover - defensive
        var_cvar = {"error": str(exc)}
    return _jsonable({
        "baseline_value": float(results.baseline_value),
        "baseline_greeks": results.baseline_greeks,
        "scenarios": scenarios,
        "worst_scenario": worst.scenario.name if worst else None,
        "best_scenario": best.scenario.name if best else None,
        "risk_summary": risk_summary,
        "var_cvar": var_cvar,
        "num_scenarios": len(scenarios),
        "execution_time": float(getattr(results, "total_execution_time", 0.0)),
    })


def run_pipeline(
    session: Session,
    *,
    positions: list[Any],
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    portfolio_name: str,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    valuation_date: datetime | None = None,
) -> tuple[str, dict[str, Any], list[dict], Any | None]:
    """Returns (status, results_dict, excluded, raw). status in {completed, empty}.

    `raw` is the QuantArk StressTestResults object on the completed path (used by
    write_artifacts for exports/report), or None on the empty path.
    """
    from app.services.risk_engine import _pricing_position_context  # reuse risk resolver

    valuation_date = valuation_date or datetime.utcnow()
    position_markets, failures, _diag = _pricing_position_context(
        session, positions,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        valuation_date=valuation_date,
    )
    # When a selected profile lacks rows for some positions, those positions are
    # still priced off fallback/assumption snapshots (parity with risk runs). Do
    # NOT discard that signal — surface it so a partial-coverage profile can't
    # silently produce a materially-wrong baseline.
    pricing_warnings = [
        {
            "position_id": pid,
            "reason": str(
                (info or {}).get("pricing_error")
                or "selected pricing profile did not cover this position; "
                "priced off fallback assumptions"
            ),
        }
        for pid, info in (failures or {}).items()
    ]
    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions,
        position_markets,
        portfolio_name=portfolio_name,
        session=session,
        engine_config_id=engine_config_id,
    )

    if len(portfolio) == 0:
        return (
            "empty",
            {"message": "No includable positions to stress", "scenarios": [],
             "pricing_warnings": pricing_warnings},
            excluded,
            None,
        )

    scenarios = scenario_catalog.resolve_scenarios(scenario_request)

    quantark.ensure_quantark_path()
    from quantark.stresstest import StressTestEngine, StressTestConfig
    engine_config = StressTestConfig(
        calculate_greeks=bool(config.get("calculate_greeks", True)),
        greeks_method=str(config.get("greeks_method", "numerical")),
        export_formats=list(config.get("export_formats", ["json"])),
        save_detailed_results=bool(config.get("save_detailed_results", True)),
        output_dir=str(config.get("output_dir", "outputs/scenario_test")),
    )
    engine = StressTestEngine(engine_config)
    results = engine.run_static_scenarios(portfolio, scenarios)
    results_dict = shape_results(results)
    results_dict["pricing_warnings"] = pricing_warnings
    return "completed", results_dict, excluded, results


def _result_exporter():
    quantark.ensure_quantark_path()
    from quantark.stresstest.results.result_exporter import ResultExporter
    return ResultExporter


def write_artifacts(
    *,
    results: dict[str, Any],
    excluded_positions: list[dict] | None = None,
    run_id: int,
    formats: list[str],
    base_dir: str,
) -> dict:
    """Write exports + repo-native HTML report. Never raises: failures become notes."""
    out_dir = os.path.join(base_dir, str(run_id))
    artifacts: dict[str, Any] = {"export_paths": [], "report_html_path": None, "notes": []}
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as exc:
        # Artifact generation is ancillary; an unwritable output dir must NOT fail
        # an otherwise-completed stress run (this function never raises).
        artifacts["notes"].append(f"artifact directory unavailable: {exc}")
        return artifacts

    # Snapshot all files (recursively) before export to detect what was created.
    # Some formats (csv/parquet) write into a SUBDIRECTORY, so we must record the
    # actual files (not the top-level dir) — the artifact endpoint serves files
    # only (os.path.isfile), so a recorded directory would 404 the download.
    def _all_files(root: str) -> set[str]:
        found: set[str] = set()
        for dirpath, _dirs, filenames in os.walk(root):
            for name in filenames:
                found.add(os.path.join(dirpath, name))
        return found

    try:
        exporter = _result_exporter()
    except Exception as exc:
        artifacts["notes"].append(f"export skipped: {exc}")
        exporter = None
    if exporter is not None:
        before = _all_files(out_dir)
        try:
            exporter.export(results, out_dir, formats=formats, base_name=f"scenario_test_{run_id}")
        except Exception as exc:
            artifacts["notes"].append(f"export partially failed: {exc}")
        finally:
            # Record whatever files landed — even if only some formats succeeded.
            # report.html is written after this block, so it can't pollute the diff.
            artifacts["export_paths"] = sorted(_all_files(out_dir) - before)

    try:
        report_path = os.path.join(out_dir, "report.html")
        report_data = dict(results)
        if excluded_positions:
            report_data["excluded_positions"] = excluded_positions
        html = scenario_test_report.render_scenario_test_report_html(
            report_data, title=f"Scenario Test #{run_id}"
        )
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        if os.path.exists(report_path):
            artifacts["report_html_path"] = report_path
    except Exception as exc:
        artifacts["notes"].append(f"report skipped: {exc}")

    return artifacts
