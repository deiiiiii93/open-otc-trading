"""Shared visual style + formatting helpers for repo-native HTML reports.

Both the scenario-test and backtest report renderers build on these tokens so
every standalone report artifact carries the same visual language as the app
(paper/ink palette, hairline borders, numeric font, dark-mode support).
"""
from __future__ import annotations

import html
from typing import Any


def fmt_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f:,.2f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f:,.2f}%"


def h(value: Any) -> str:
    return html.escape(str(value) if value is not None else "")


def sign_class(value: float | None) -> str:
    if value is None:
        return ""
    if value > 0:
        return " pos"
    if value < 0:
        return " neg"
    return ""


REPORT_CSS = """
    :root {
      --paper: #fafaf8;
      --paper-2: #f2f2ef;
      --paper-3: #e8e8e5;
      --hairline: #e0e0dc;
      --hairline-2: #d4d4d0;
      --ink: #1a1a17;
      --ink-2: #6b6b66;
      --pos: #166534;
      --neg: #991b1b;
      --warn: #92400e;
      --info: #1d4ed8;
      --font-ui: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-num: "JetBrains Mono", "SF Mono", "Monaco", "Consolas", monospace;
      --gap-1: 4px;
      --gap-2: 8px;
      --gap-3: 16px;
      --gap-4: 24px;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --paper: #141412;
        --paper-2: #1c1c19;
        --paper-3: #252522;
        --hairline: #2e2e2a;
        --hairline-2: #3a3a35;
        --ink: #f5f5f0;
        --ink-2: #a6a69e;
        --pos: #4ade80;
        --neg: #f87171;
        --warn: #fbbf24;
        --info: #60a5fa;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: var(--gap-3);
      font-family: var(--font-ui);
      background: var(--paper);
      color: var(--ink);
      line-height: 1.5;
    }
    .container {
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: var(--gap-4);
    }
    header {
      border: 1px solid var(--hairline-2);
      border-top: 4px solid var(--ink);
      background: var(--paper-2);
      padding: var(--gap-3);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .subtitle {
      margin: var(--gap-2) 0 0;
      color: var(--ink-2);
      font-size: 13px;
    }
    h2 {
      margin: 0 0 var(--gap-3);
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--ink-2);
    }
    h5 {
      margin: var(--gap-3) 0 var(--gap-2);
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--ink-2);
    }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: var(--gap-2);
    }
    .kpi {
      border: 1px solid var(--hairline-2);
      background: var(--paper-2);
      padding: var(--gap-2) var(--gap-3);
      display: flex;
      flex-direction: column;
      gap: var(--gap-1);
    }
    .kpi-label {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--ink-2);
    }
    .kpi-value {
      font-family: var(--font-num);
      font-size: 18px;
      font-weight: 600;
      color: var(--ink);
    }
    .kpi-value.pos { color: var(--pos); }
    .kpi-value.neg { color: var(--neg); }
    section {
      border: 1px solid var(--hairline-2);
      background: var(--paper);
      padding: var(--gap-3);
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: var(--gap-2) var(--gap-3);
      text-align: left;
      border-bottom: 1px solid var(--hairline);
    }
    th {
      background: var(--paper-2);
      color: var(--ink-2);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      white-space: nowrap;
    }
    tr:last-child td { border-bottom: 0; }
    .num {
      font-family: var(--font-num);
      text-align: right;
      white-space: nowrap;
    }
    .pos { color: var(--pos); }
    .neg { color: var(--neg); }
    .scenario-name { font-weight: 600; }
    .scenario-detail {
      padding: 0 var(--gap-3) var(--gap-3);
      border-bottom: 1px solid var(--hairline);
      background: var(--paper-2);
    }
    .detail-row td {
      padding: 0 var(--gap-3) var(--gap-3);
      background: var(--paper-2);
    }
    .small-table {
      margin-top: var(--gap-2);
      background: var(--paper);
    }
    .small-table th,
    .small-table td {
      padding: var(--gap-1) var(--gap-2);
      font-size: 12px;
    }
    .warning-box {
      border: 1px solid var(--hairline-2);
      background: var(--paper);
      box-shadow: inset 4px 0 0 var(--warn);
      color: var(--warn);
    }
    .warning-box h2 { color: var(--warn); }
    .muted {
      color: var(--ink-2);
      font-size: 13px;
    }
    .notes {
      margin: 0;
      padding-left: var(--gap-3);
      color: var(--ink-2);
      font-size: 13px;
    }
    .chart {
      width: 100%;
      height: auto;
      display: block;
    }
    .chart-legend {
      display: flex;
      gap: var(--gap-3);
      margin-top: var(--gap-2);
      font-size: 12px;
      color: var(--ink-2);
    }
    .chart-legend .swatch {
      display: inline-block;
      width: 16px;
      height: 2px;
      margin-right: var(--gap-1);
      vertical-align: middle;
    }
    footer {
      color: var(--ink-2);
      font-size: 12px;
      text-align: right;
    }
    @media (max-width: 640px) {
      .kpis { grid-template-columns: 1fr; }
      th, td { padding: var(--gap-2); }
    }
"""
