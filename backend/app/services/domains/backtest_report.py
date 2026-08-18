"""Repo-native backtest HTML report renderer.

Reads the shaped JSON results produced by `backtest.run_pipeline` and writes a
self-contained HTML file that shares the application's report visual style
(`report_style.py`) with the scenario-test report. Replaces the QuantArk
dashboard as the backtest report artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.domains.report_style import (
    REPORT_CSS,
    fmt_number as _fmt_number,
    h as _h,
    sign_class as _sign_class,
)

# P&L series keys rendered in the chart, in legend order, with their token color.
_SERIES_COLORS = {
    "total_pnl": "var(--info)",
    "hedge_pnl": "var(--pos)",
    "product_pnl": "var(--warn)",
}
_SERIES_LABELS = {
    "total_pnl": "Total P&L",
    "hedge_pnl": "Hedge P&L",
    "product_pnl": "Product P&L",
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_kpis(portfolio: dict[str, Any]) -> str:
    total_pnl = _num(portfolio.get("total_pnl"))
    hedge_pnl = _num(portfolio.get("hedge_pnl"))
    var_95 = _num(portfolio.get("var_95"))
    cvar_95 = _num(portfolio.get("cvar_95"))
    sharpe = _num(portfolio.get("sharpe"))
    cards = [
        ("Total P&L", _fmt_number(total_pnl), _sign_class(total_pnl)),
        ("Hedge P&L", _fmt_number(hedge_pnl), _sign_class(hedge_pnl)),
        ("Trades", _h(portfolio.get("num_trades", "—")), ""),
        ("Underlyings", _h(portfolio.get("num_underlyings", "—")), ""),
        ("Sharpe", f"{sharpe:.2f}" if sharpe is not None else "—", ""),
        ("Max Drawdown", _fmt_number(portfolio.get("max_drawdown")), ""),
        ("VaR 95", _fmt_number(var_95), _sign_class(var_95)),
        ("CVaR 95", _fmt_number(cvar_95), _sign_class(cvar_95)),
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


def _render_pnl_chart(series: list[dict[str, Any]] | None) -> str:
    """Minimal inline-SVG line chart for the cumulative P&L path (no JS)."""
    rows = [r for r in (series or []) if isinstance(r, dict) and r.get("date") is not None]
    keys = [k for k in _SERIES_COLORS if any(_num(r.get(k)) is not None for r in rows)]
    if not rows or not keys:
        return ""

    width, height = 760.0, 220.0
    pad_l, pad_r, pad_t, pad_b = 70.0, 12.0, 12.0, 28.0
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    values = [_num(r.get(k)) for r in rows for k in keys]
    present = [v for v in values if v is not None]
    lo, hi = min(present), max(present)
    if lo == hi:
        lo -= 1.0
        hi += 1.0

    n = len(rows)

    def x(i: int) -> float:
        return pad_l + inner_w * i / max(n - 1, 1)

    def y(v: float) -> float:
        return pad_t + inner_h * (1.0 - (v - lo) / (hi - lo))

    parts: list[str] = []
    # Horizontal gridlines + y labels at min / mid / max.
    for v in (lo, (lo + hi) / 2.0, hi):
        gy = y(v)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"'
            f' style="stroke:var(--hairline);stroke-dasharray:3 3" />'
        )
        parts.append(
            f'<text x="{pad_l - 6}" y="{gy + 3:.1f}" text-anchor="end"'
            f' style="fill:var(--ink-2);font-size:10px;font-family:var(--font-num)">'
            f'{_h(_fmt_number(v))}</text>'
        )
    # Zero baseline when the range crosses zero.
    if lo < 0 < hi:
        zy = y(0.0)
        parts.append(
            f'<line x1="{pad_l}" y1="{zy:.1f}" x2="{width - pad_r}" y2="{zy:.1f}"'
            f' style="stroke:var(--hairline-2)" />'
        )
    # X labels: first / middle / last date (edge labels anchored inward).
    label_anchors: dict[int, str] = {(n - 1) // 2: "middle"}
    if n > 1:
        label_anchors[0] = "start"
        label_anchors[n - 1] = "end"
    for i, anchor in label_anchors.items():
        parts.append(
            f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="{anchor}"'
            f' style="fill:var(--ink-2);font-size:10px">{_h(rows[i].get("date"))}</text>'
        )
    # Series polylines.
    for k in keys:
        pts = " ".join(
            f"{x(i):.1f},{y(v):.1f}"
            for i, r in enumerate(rows)
            if (v := _num(r.get(k))) is not None
        )
        parts.append(
            f'<polyline points="{pts}" fill="none"'
            f' style="stroke:{_SERIES_COLORS[k]};stroke-width:2" />'
        )

    legend = "".join(
        f'<span><span class="swatch" style="background:{_SERIES_COLORS[k]}"></span>'
        f'{_h(_SERIES_LABELS[k])}</span>'
        for k in keys
    )
    return f"""
    <section>
      <h2>Cumulative P&amp;L</h2>
      <svg class="chart" viewBox="0 0 {width:.0f} {height:.0f}" role="img"
           aria-label="Cumulative P&amp;L chart">
        {''.join(parts)}
      </svg>
      <div class="chart-legend">{legend}</div>
    </section>
    """


def _render_lifecycle_events(events: list[dict[str, Any]] | None) -> str:
    if not events:
        return ""
    rows = "".join(
        f"""
        <tr>
          <td>{_h(ev.get('type'))}</td>
          <td>{_h(ev.get('date'))}</td>
          <td class="num">{_fmt_number(ev.get('cashflow'))}</td>
        </tr>
        """
        for ev in events
    )
    return f"""
    <h5>Lifecycle Events</h5>
    <table class="small-table">
      <thead><tr><th>Type</th><th>Date</th><th>Cashflow</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def _render_underlyings(by_underlying: list[dict[str, Any]] | None) -> str:
    if not by_underlying:
        return ""
    rows = []
    for item in by_underlying:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") or item
        total_pnl = _num(summary.get("total_pnl"))
        hedge_pnl = _num(summary.get("hedge_pnl"))
        rows.append(
            f"""
            <tr>
              <td class="scenario-name">{_h(item.get('underlying'))}</td>
              <td class="num">{_h(item.get('num_products', '—'))}</td>
              <td class="num{_sign_class(total_pnl)}">{_fmt_number(total_pnl)}</td>
              <td class="num{_sign_class(hedge_pnl)}">{_fmt_number(hedge_pnl)}</td>
              <td class="num">{_h(summary.get('num_trades', '—'))}</td>
            </tr>
            """
        )
        events_html = _render_lifecycle_events(item.get("lifecycle_events"))
        if events_html:
            rows.append(
                f"""
            <tr class="detail-row">
              <td colspan="5">{events_html}</td>
            </tr>
                """
            )
    return f"""
    <section>
      <h2>By Underlying</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Underlying</th>
              <th>Products</th>
              <th>Total P&amp;L</th>
              <th>Hedge P&amp;L</th>
              <th>Trades</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def _render_excluded(excluded: list[dict[str, Any]] | None) -> str:
    if not excluded:
        return ""
    return f'<p class="muted">{len(excluded)} position(s) excluded from the backtest.</p>'


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


def render_backtest_report_html(results: dict[str, Any], title: str) -> str:
    """Render shaped backtest results as a self-contained HTML document."""
    portfolio = results.get("portfolio") or {}
    window = results.get("window") or {}
    excluded = results.get("excluded_positions") or []
    notes = results.get("notes") or []

    kpi_cards = _render_kpis(portfolio)
    chart_html = _render_pnl_chart(portfolio.get("pnl_series"))
    underlyings_html = _render_underlyings(results.get("by_underlying"))
    excluded_html = _render_excluded(excluded)
    notes_html = _render_notes(notes)

    meta = " · ".join(
        part
        for part in (
            f"{_h(window.get('start', '?'))} – {_h(window.get('end', '?'))}",
            f"Engine {_h(results.get('engine'))}" if results.get("engine") else "",
            f"Vol {_h(results.get('vol_source'))}" if results.get("vol_source") else "",
        )
        if part
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subtitle = f"{meta} · Generated at {generated_at}" if meta else f"Generated at {generated_at}"

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
      <p class="subtitle">{subtitle}</p>
    </header>

    <section>
      <h2>Summary</h2>
      <div class="kpis">
        {kpi_cards}
      </div>
      {excluded_html}
    </section>

    {chart_html}
    {underlyings_html}
    {notes_html}

    <footer>Open OTC Trading — Backtest Report</footer>
  </div>
</body>
</html>"""
