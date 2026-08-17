"""Repo-native scenario test HTML report renderer.

Reads the shaped JSON results produced by `scenario_test.shape_results` and writes a
self-contained HTML file that matches the application's visual style. Replaces the
QuantArk ReportGenerator dependency for this report type.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.domains.report_style import (
    REPORT_CSS,
    fmt_number as _fmt_number,
    fmt_pct as _fmt_pct,
    h as _h,
    sign_class as _sign_class,
)


def _render_greeks(greeks: dict[str, Any] | None) -> str:
    if not greeks:
        return "<p class=\"muted\">No greeks available.</p>"
    rows = "".join(
        f"<tr><td>{_h(k)}</td><td class=\"num{_sign_class(v)}\">{_fmt_number(v)}</td></tr>"
        for k, v in greeks.items()
    )
    return f"""
    <table class="small-table">
      <thead><tr><th>Greek</th><th>Value</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def _render_underlying_results(underlying_results: dict[str, Any] | None) -> str:
    if not underlying_results:
        return ""
    rows = []
    for name, data in underlying_results.items():
        if isinstance(data, dict):
            total_value = data.get("total_value")
            num_positions = data.get("num_positions")
            cells = f"""
            <td class="num">{_fmt_number(total_value)}</td>
            <td class="num">{_h(num_positions)}</td>
            """
        else:
            cells = f"<td colspan=\"2\" class=\"muted\">{_h(data)}</td>"
        rows.append(f"<tr><td>{_h(name)}</td>{cells}</tr>")
    return f"""
    <h5>By Underlying</h5>
    <table class="small-table">
      <thead><tr><th>Underlying</th><th>Total Value</th><th>Positions</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _render_position_results(position_results: list[dict[str, Any]] | None) -> str:
    if not position_results:
        return ""
    rows = []
    for p in position_results:
        pnl = p.get("pnl")
        rows.append(
            f"""
            <tr>
              <td>{_h(p.get('position_id'))}</td>
              <td>{_h(p.get('underlying'))}</td>
              <td>{_h(p.get('product_type'))}</td>
              <td class="num">{_fmt_number(p.get('quantity'))}</td>
              <td class="num">{_fmt_number(p.get('original_value'))}</td>
              <td class="num">{_fmt_number(p.get('stressed_value'))}</td>
              <td class="num{_sign_class(pnl)}">{_fmt_number(pnl)}</td>
            </tr>
            """
        )
    return f"""
    <h5>By Position</h5>
    <table class="small-table">
      <thead>
        <tr>
          <th>ID</th><th>Underlying</th><th>Product</th><th>Qty</th>
          <th>Original</th><th>Stressed</th><th>P&L</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _render_scenarios(scenarios: list[dict[str, Any]]) -> str:
    rows = []
    for s in scenarios:
        pnl = s.get("pnl")
        pnl_pct = s.get("pnl_pct")
        greeks_section = _render_greeks(s.get("greeks"))
        underlying_section = _render_underlying_results(s.get("underlying_results"))
        position_section = _render_position_results(s.get("position_results"))
        details = ""
        if underlying_section or position_section or greeks_section:
            details = f"""
            <div class="scenario-detail">
              {greeks_section}
              {underlying_section}
              {position_section}
            </div>
            """
        rows.append(
            f"""
            <tr>
              <td class="scenario-name">{_h(s.get('name'))}</td>
              <td class="num">{_fmt_number(s.get('portfolio_value'))}</td>
              <td class="num{_sign_class(pnl)}">{_fmt_number(pnl)}</td>
              <td class="num{_sign_class(pnl_pct)}">{_fmt_pct(pnl_pct)}</td>
              <td class="num">{_fmt_number(s.get('execution_time'))}s</td>
            </tr>
            {details}
            """
        )
    return f"""
    <section>
      <h2>Scenarios</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Portfolio Value</th>
              <th>P&L</th>
              <th>P&L %</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def _render_warnings(pricing_warnings: list[dict[str, Any]] | None) -> str:
    if not pricing_warnings:
        return ""
    items = "".join(
        f"<li>Position {_h(w.get('position_id'))}: {_h(w.get('reason'))}</li>"
        for w in pricing_warnings
    )
    return f"""
    <section class="warning-box">
      <h2>Pricing Warnings</h2>
      <p>Some positions were priced off fallback assumptions.</p>
      <ul>{items}</ul>
    </section>
    """


def _render_excluded(excluded: list[dict[str, Any]] | None) -> str:
    if not excluded:
        return ""
    count = len(excluded)
    return f'<p class="muted">{count} position(s) excluded from pricing.</p>'


def _render_notes(notes: list[str] | None) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{_h(n)}</li>" for n in notes)
    return f"""
    <section>
      <h2>Notes</h2>
      <ul class="notes">{items}</ul>
    </section>
    """


def _render_kpis(results: dict[str, Any]) -> str:
    baseline = results.get("baseline_value")
    worst = results.get("worst_scenario")
    best = results.get("best_scenario")
    var_cvar = results.get("var_cvar") or {}
    var = var_cvar.get("var") if isinstance(var_cvar, dict) else None
    cvar = var_cvar.get("cvar") if isinstance(var_cvar, dict) else None
    confidence = var_cvar.get("confidence") if isinstance(var_cvar, dict) else 0.95
    num = results.get("num_scenarios")

    cards = [
        ("Baseline Value", _fmt_number(baseline), ""),
        ("Worst Scenario", _h(worst) if worst else "—", ""),
        ("Best Scenario", _h(best) if best else "—", ""),
        (
            f"VaR / CVaR ({_fmt_number(confidence * 100).rstrip('0').rstrip('.') if isinstance(confidence, (int, float)) else '95'}%)",
            f"{_fmt_number(var)} / {_fmt_number(cvar)}",
            "",
        ),
        ("Scenarios", _h(num) if num is not None else "—", ""),
        ("Execution Time", f"{_fmt_number(results.get('execution_time'))}s", ""),
    ]
    return "".join(
        f"""
        <div class="kpi">
          <span class="kpi-label">{label}</span>
          <span class="kpi-value{cls}">{value}</span>
        </div>
        """
        for label, value, cls in cards
    )


def render_scenario_test_report_html(results: dict[str, Any], title: str) -> str:
    """Render shaped scenario-test results as a self-contained HTML document."""
    scenarios = results.get("scenarios") or []
    pricing_warnings = results.get("pricing_warnings") or []
    excluded = results.get("excluded_positions") or []
    notes = results.get("notes") or []

    kpi_cards = _render_kpis(results)
    scenarios_html = _render_scenarios(scenarios)
    warnings_html = _render_warnings(pricing_warnings)
    excluded_html = _render_excluded(excluded)
    notes_html = _render_notes(notes)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_h(title)}</title>
  <style>{REPORT_CSS}</style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{_h(title)}</h1>
      <p class="subtitle">Generated at {generated_at}</p>
    </header>

    <section>
      <h2>Summary</h2>
      <div class="kpis">
        {kpi_cards}
      </div>
      {excluded_html}
    </section>

    {scenarios_html}
    {warnings_html}
    {notes_html}

    <footer>Open OTC Trading — Scenario Test Report</footer>
  </div>
</body>
</html>"""
