# Currency Validation + Report `by_currency` Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject non-ISO-4217 currency codes at every input boundary (hard 422 / structured tool error), and render the `by_currency` + `shared` risk breakdown in both the HTML and xlsx reports.

**Architecture:** A small `currency_codes.py` module owns the ISO-4217 set + normalize/validate helpers; Pydantic `model_validator(mode="after")` hooks on the FxRate/thread input schemas and a guard in the `convert_currency` tool consume it. The report renderers read the existing `by_currency`/`shared` risk-dict shape directly (no new data plumbing).

**Tech Stack:** Python 3 / Pydantic v2 / FastAPI / openpyxl / pytest.

**Spec:** `docs/superpowers/specs/2026-06-02-currency-validation-and-report-enrichment-design.md`

**Worktree / test invocation:** Work in `/Users/fuxinyao/open-otc-trading-currency` (branch `feature/currency-convention`). Run:
```bash
cd /Users/fuxinyao/open-otc-trading-currency
PYTHONPATH=/Users/fuxinyao/open-otc-trading-currency/backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest <args>
```
End every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. A concurrent agent shares this repo's .git — run `git status --short` before staging and stage only your files; never touch unexpected files (especially under `frontend/`).

---

## File Structure
- **New** `backend/app/services/currency_codes.py` — `ISO_4217_CODES` frozenset + `normalize_currency` + `is_valid_currency`. One focused responsibility.
- **Modify** `backend/app/schemas.py` — `model_validator` on `FxRateCreate`, `FxRateAkshareRequest`, `AgentThreadUpdate`.
- **Modify** `backend/app/tools/risk.py` — guard `convert_currency_tool` target.
- **Modify** `backend/app/services/reports.py` — `_write_html`, `_write_xlsx`.
- **New tests** `tests/test_currency_validation.py`, `tests/test_report_by_currency.py`.

---

## Task 1: ISO 4217 currency-code module

**Files:**
- Create: `backend/app/services/currency_codes.py`
- Test: `tests/test_currency_validation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_currency_validation.py
from app.services.currency_codes import (
    ISO_4217_CODES,
    is_valid_currency,
    normalize_currency,
)


def test_normalize_strips_and_uppercases():
    assert normalize_currency(" usd ") == "USD"
    assert normalize_currency("cny") == "CNY"


def test_is_valid_currency():
    assert is_valid_currency("usd") is True
    assert is_valid_currency("USD") is True
    assert is_valid_currency("CNY") is True
    assert is_valid_currency("XYZ") is False
    assert is_valid_currency("US") is False
    assert is_valid_currency("") is False


def test_common_desk_currencies_present():
    for code in ("CNY", "USD", "EUR", "HKD", "JPY", "GBP", "AUD", "SGD", "CHF", "CAD"):
        assert code in ISO_4217_CODES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.currency_codes'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/currency_codes.py
"""ISO 4217 active alphabetic currency codes + normalize/validate helpers.
Static constant (no pycountry dependency) used to reject typo'd currency codes
at every input boundary."""
from __future__ import annotations

# Active ISO 4217 alphabetic codes (fund codes / metals like XAU intentionally
# included; obsolete codes excluded). Source: ISO 4217 published list.
ISO_4217_CODES: frozenset[str] = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BOV",
    "BRL", "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHE", "CHF",
    "CHW", "CLF", "CLP", "CNY", "COP", "COU", "CRC", "CUP", "CVE", "CZK",
    "DJF", "DKK", "DOP", "DZD", "EGP", "ERN", "ETB", "EUR", "FJD", "FKP",
    "GBP", "GEL", "GHS", "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL",
    "HTG", "HUF", "IDR", "ILS", "INR", "IQD", "IRR", "ISK", "JMD", "JOD",
    "JPY", "KES", "KGS", "KHR", "KMF", "KPW", "KRW", "KWD", "KYD", "KZT",
    "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL", "MGA", "MKD",
    "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MXV", "MYR",
    "MZN", "NAD", "NGN", "NIO", "NOK", "NPR", "NZD", "OMR", "PAB", "PEN",
    "PGK", "PHP", "PKR", "PLN", "PYG", "QAR", "RON", "RSD", "RUB", "RWF",
    "SAR", "SBD", "SCR", "SDG", "SEK", "SGD", "SHP", "SLE", "SOS", "SRD",
    "SSP", "STN", "SVC", "SYP", "SZL", "THB", "TJS", "TMT", "TND", "TOP",
    "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "USN", "UYI", "UYU",
    "UZS", "VED", "VES", "VND", "VUV", "WST", "XAF", "XAG", "XAU", "XCD",
    "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWG",
})


def normalize_currency(code: str) -> str:
    return str(code).strip().upper()


def is_valid_currency(code: str) -> bool:
    return normalize_currency(code) in ISO_4217_CODES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_validation.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading-currency add backend/app/services/currency_codes.py tests/test_currency_validation.py
git -C /Users/fuxinyao/open-otc-trading-currency commit -m "feat(fx): ISO 4217 currency-code set + normalize/validate helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Schema validators (FxRateCreate, FxRateAkshareRequest, AgentThreadUpdate)

**Files:**
- Modify: `backend/app/schemas.py` (`AgentThreadUpdate` ~line 14; `FxRateCreate` ~line 826; `FxRateAkshareRequest` ~line 847)
- Test: `tests/test_currency_validation.py` (append)

`schemas.py` already imports `from pydantic import BaseModel, Field, model_validator` and uses `@model_validator(mode="after")` elsewhere — follow that pattern. `datetime` is imported.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_currency_validation.py
from datetime import datetime

import pytest
from pydantic import ValidationError


def test_fxrate_create_validates_and_normalizes():
    from app.schemas import FxRateCreate

    ok = FxRateCreate(base_currency="usd", quote_currency="cny", rate=7.2,
                      as_of_date=datetime(2026, 6, 2))
    assert ok.base_currency == "USD" and ok.quote_currency == "CNY"

    with pytest.raises(ValidationError):
        FxRateCreate(base_currency="USdD", quote_currency="CNY", rate=7.2,
                     as_of_date=datetime(2026, 6, 2))


def test_fxrate_akshare_request_validates():
    from app.schemas import FxRateAkshareRequest

    ok = FxRateAkshareRequest(base_currency="usd", quote_currency="cny")
    assert ok.base_currency == "USD" and ok.quote_currency == "CNY"
    with pytest.raises(ValidationError):
        FxRateAkshareRequest(base_currency="XYZ", quote_currency="CNY")


def test_thread_update_report_currency_validates():
    from app.schemas import AgentThreadUpdate

    assert AgentThreadUpdate(report_currency="usd").report_currency == "USD"
    assert AgentThreadUpdate(report_currency="by_position").report_currency == "by_position"
    assert AgentThreadUpdate(report_currency=None).report_currency is None
    assert AgentThreadUpdate(title="x").report_currency is None
    with pytest.raises(ValidationError):
        AgentThreadUpdate(report_currency="US")
    with pytest.raises(ValidationError):
        AgentThreadUpdate(report_currency="XYZ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_validation.py -k "create or akshare or thread_update" -v`
Expected: FAIL — `"USdD"`/`"XYZ"`/`"US"` are accepted (no `ValidationError` raised); lowercase not normalized.

- [ ] **Step 3: Implement**

Add the import near the top of `schemas.py` (with the other app imports):
```python
from app.services.currency_codes import ISO_4217_CODES, normalize_currency
```

In `AgentThreadUpdate` (after the two fields):
```python
    @model_validator(mode="after")
    def _validate_report_currency(self) -> "AgentThreadUpdate":
        rc = self.report_currency
        if rc is not None and rc != "by_position":
            norm = normalize_currency(rc)
            if norm not in ISO_4217_CODES:
                raise ValueError(f"Invalid report currency: {rc!r}")
            self.report_currency = norm
        return self
```

In `FxRateCreate` (after its fields):
```python
    @model_validator(mode="after")
    def _validate_currencies(self) -> "FxRateCreate":
        self.base_currency = normalize_currency(self.base_currency)
        self.quote_currency = normalize_currency(self.quote_currency)
        for code in (self.base_currency, self.quote_currency):
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {code!r}")
        return self
```

In `FxRateAkshareRequest` (after its fields):
```python
    @model_validator(mode="after")
    def _validate_currencies(self) -> "FxRateAkshareRequest":
        self.base_currency = normalize_currency(self.base_currency)
        self.quote_currency = normalize_currency(self.quote_currency)
        for code in (self.base_currency, self.quote_currency):
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {code!r}")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_validation.py -v`
Expected: PASS. Then check no regression in FX-API + thread tests:
Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_rates_api.py tests/test_thread_report_currency_api.py -q`
Expected: PASS (existing tests use valid codes: USD/CNY).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading-currency add backend/app/schemas.py tests/test_currency_validation.py
git -C /Users/fuxinyao/open-otc-trading-currency commit -m "feat(api): reject non-ISO currency codes on FxRate + thread report_currency (422)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Guard the `convert_currency` tool target

**Files:**
- Modify: `backend/app/tools/risk.py` (`convert_currency_tool`)
- Test: `tests/test_convert_currency.py` (append — this is the existing tool test file)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_convert_currency.py
def test_convert_currency_tool_rejects_invalid_target(_db):
    from app.tools.risk import convert_currency_tool

    result = convert_currency_tool.invoke({
        "by_currency": {"CNY": {"market_value": 100.0, "position_count": 1}},
        "target_currency": "XYZ",
        "valuation_date": "2026-06-02",
    })
    assert "error" in result
    assert result["totals"] == {}
    assert result["missing"] == []
```
(The `_db` fixture already exists in this file from the earlier tool test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py::test_convert_currency_tool_rejects_invalid_target -v`
Expected: FAIL — no `error` key (the tool currently resolves `XYZ` to all-missing instead of erroring).

- [ ] **Step 3: Implement**

In `backend/app/tools/risk.py`, add the import (with the other `app.services` imports):
```python
from app.services.currency_codes import ISO_4217_CODES, normalize_currency
```
At the top of `convert_currency_tool`'s body, before opening the DB session:
```python
    target = normalize_currency(target_currency)
    if target not in ISO_4217_CODES:
        return {
            "error": f"Invalid target currency: {target_currency!r}",
            "totals": {},
            "fx_rates_used": {},
            "missing": [],
        }
    target_currency = target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py -v`
Expected: PASS (existing tool tests still green; the new one passes).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading-currency add backend/app/tools/risk.py tests/test_convert_currency.py
git -C /Users/fuxinyao/open-otc-trading-currency commit -m "feat(tool): convert_currency rejects invalid target with a structured error

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Enrich the HTML report with `by_currency` + `shared`

**Files:**
- Modify: `backend/app/services/reports.py` (`_write_html` ~line 259-298)
- Test: `tests/test_report_by_currency.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_by_currency.py
from app.services.reports import _write_html


def _mixed_payload():
    return {"risk": {
        "by_currency": {
            "CNY": {"market_value": 100.0, "pnl": 5.0, "gross_notional": 200.0,
                    "one_day_var_proxy": 3.0, "vega": 1.0, "theta": -0.5,
                    "rho": 0.2, "rho_q": 0.1, "delta_cash": 50.0, "gamma_cash": 2.0,
                    "position_count": 2},
            "USD": {"market_value": 10.0, "pnl": 1.0, "gross_notional": 20.0,
                    "one_day_var_proxy": 0.4, "vega": 0.1, "theta": -0.05,
                    "rho": 0.02, "rho_q": 0.01, "delta_cash": 5.0, "gamma_cash": 0.2,
                    "position_count": 1},
        },
        "shared": {"delta": 3.5, "gamma": 0.0, "delta_proxy": 42.0},
        "totals": None, "mixed_currency": True, "currencies": ["CNY", "USD"],
        "positions": [],
    }}


def _single_payload():
    return {"risk": {
        "by_currency": {"CNY": {"market_value": 100.0, "pnl": 5.0,
                                "gross_notional": 200.0, "one_day_var_proxy": 3.0,
                                "vega": 1.0, "theta": -0.5, "rho": 0.2, "rho_q": 0.1,
                                "delta_cash": 50.0, "gamma_cash": 2.0,
                                "position_count": 2}},
        "shared": {"delta": 3.0, "gamma": 0.0, "delta_proxy": 10.0},
        "totals": {"market_value": 100.0, "pnl": 5.0, "delta_proxy": 10.0,
                   "one_day_var_proxy": 3.0, "delta": 3.0},
        "mixed_currency": False, "currencies": ["CNY"], "positions": [],
    }}


def test_html_mixed_currency_breakdown(tmp_path):
    path = tmp_path / "r.html"
    _write_html(path, "Mixed", _mixed_payload())
    html = path.read_text()
    assert "Mixed currency" in html          # the mixed note replaces top cards
    assert "CNY" in html and "USD" in html    # per-currency sections
    assert "Shared" in html                   # shared section
    assert "42.0000" in html                  # shared delta_proxy rendered


def test_html_single_currency_keeps_top_cards(tmp_path):
    path = tmp_path / "r.html"
    _write_html(path, "Single", _single_payload())
    html = path.read_text()
    assert "Market value" in html             # legacy top cards present
    assert "By currency" in html              # plus the breakdown
    assert "Shared" in html


def test_html_missing_by_currency_does_not_crash(tmp_path):
    path = tmp_path / "r.html"
    _write_html(path, "Empty", {"risk": {"positions": []}})
    assert path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_by_currency.py -k html -v`
Expected: FAIL — no "By currency"/"Shared"/"Mixed currency" text in current output.

- [ ] **Step 3: Implement**

Replace the body of `_write_html` (lines 259-298) with:
```python
_MONEY_DISPLAY = [
    ("market_value", "Market value"), ("pnl", "PnL"),
    ("gross_notional", "Gross notional"), ("one_day_var_proxy", "1D VaR proxy"),
    ("vega", "Vega"), ("theta", "Theta"), ("rho", "Rho"), ("rho_q", "Rho (q)"),
    ("delta_cash", "Delta cash"), ("gamma_cash", "Gamma cash"),
    ("position_count", "Positions"),
]
_SHARED_DISPLAY = [("delta", "Delta"), ("gamma", "Gamma"), ("delta_proxy", "Delta proxy")]


def _metric_card(label: str, value: Any) -> str:
    try:
        rendered = f"{float(value):.4f}"
    except (TypeError, ValueError):
        rendered = "0.0000"
    return f'<div class="metric"><div>{label}</div><div class="value">{rendered}</div></div>'


def _write_html(path: Path, title: str, payload: dict[str, Any]) -> None:
    risk = payload.get("risk", {}) or {}
    totals = risk.get("totals") or {}
    by_currency = risk.get("by_currency") or {}
    shared = risk.get("shared") or {}
    positions = risk.get("positions", [])

    if totals:
        top_block = '<div class="grid">' + "".join(
            _metric_card(label, totals.get(key, 0)) for key, label in _MONEY_DISPLAY[:4]
        ) + "</div>"
    else:
        top_block = '<p class="note">Mixed currency — see the per-currency breakdown below.</p>'

    by_currency_html = ""
    if by_currency:
        sections = []
        for ccy in sorted(by_currency):
            bucket = by_currency[ccy]
            cards = "".join(_metric_card(label, bucket.get(key, 0)) for key, label in _MONEY_DISPLAY)
            sections.append(f'<h2>{ccy}</h2><div class="grid">{cards}</div>')
        by_currency_html = "<h2>By currency</h2>" + "".join(sections)

    shared_html = ""
    if shared:
        cards = "".join(_metric_card(label, shared.get(key, 0)) for key, label in _SHARED_DISPLAY)
        shared_html = f'<h2>Shared (currency-invariant)</h2><div class="grid">{cards}</div>'

    rows = "\n".join(
        f"<tr><td>{p.get('position_id')}</td><td>{p.get('underlying')}</td><td>{p.get('product_type')}</td>"
        f"<td>{p.get('quantity')}</td><td>{p.get('market_value'):.4f}</td><td>{p.get('pnl'):.4f}</td></tr>"
        for p in positions
    )
    path.write_text(
        f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Inter, Arial, sans-serif; margin: 32px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 24px; }}
    th, td {{ border-bottom: 1px solid #d7deea; padding: 10px; text-align: left; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 8px; }}
    .metric {{ border: 1px solid #d7deea; border-radius: 8px; padding: 16px; }}
    .value {{ font-size: 24px; font-weight: 700; }}
    .note {{ color: #6b7686; font-style: italic; }}
    h2 {{ margin-top: 28px; font-size: 16px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {top_block}
  {by_currency_html}
  {shared_html}
  <table>
    <thead><tr><th>ID</th><th>Underlying</th><th>Product</th><th>Qty</th><th>MV</th><th>PnL</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
```
Place `_MONEY_DISPLAY`, `_SHARED_DISPLAY`, `_metric_card` at module scope just above `_write_html` (they're reused by Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_by_currency.py -k html -v`
Expected: PASS. Then no-regression: `PYTHONPATH=.../backend .../python -m pytest tests/ -k report -q`.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading-currency add backend/app/services/reports.py tests/test_report_by_currency.py
git -C /Users/fuxinyao/open-otc-trading-currency commit -m "feat(reports): render by_currency + shared breakdown in HTML report

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Enrich the xlsx report with a "By Currency" sheet

**Files:**
- Modify: `backend/app/services/reports.py` (`_write_xlsx` ~line 301-333)
- Test: `tests/test_report_by_currency.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_report_by_currency.py
def test_xlsx_has_by_currency_sheet(tmp_path):
    from openpyxl import load_workbook
    from app.services.reports import _write_xlsx

    path = tmp_path / "r.xlsx"
    _write_xlsx(path, "Mixed", _mixed_payload())
    wb = load_workbook(path)
    assert "By Currency" in wb.sheetnames
    ws = wb["By Currency"]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == ("currency", "metric", "value")
    body = rows[1:]
    # every currency appears, and the shared metrics are tagged "(shared)"
    assert any(r[0] == "CNY" and r[1] == "market_value" and r[2] == 100.0 for r in body)
    assert any(r[0] == "USD" for r in body)
    assert any(r[0] == "(shared)" and r[1] == "delta_proxy" and r[2] == 42.0 for r in body)


def test_xlsx_missing_by_currency_does_not_crash(tmp_path):
    from app.services.reports import _write_xlsx
    path = tmp_path / "r.xlsx"
    _write_xlsx(path, "Empty", {"risk": {"positions": []}})
    assert path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_by_currency.py -k xlsx -v`
Expected: FAIL — no "By Currency" sheet.

- [ ] **Step 3: Implement**

In `_write_xlsx`, after the `Summary` sheet loop (`for key, value in totals.items(): ws.append([key, value])`) and before the `Positions` sheet creation, insert:
```python
    risk = payload.get("risk", {}) or {}
    by_currency = risk.get("by_currency") or {}
    shared = risk.get("shared") or {}
    if by_currency or shared:
        ccy_ws = wb.create_sheet("By Currency")
        ccy_ws.append(["currency", "metric", "value"])
        for ccy in sorted(by_currency):
            bucket = by_currency[ccy]
            for key, _label in _MONEY_DISPLAY:
                if key in bucket:
                    ccy_ws.append([ccy, key, bucket[key]])
        for key, _label in _SHARED_DISPLAY:
            if key in shared:
                ccy_ws.append(["(shared)", key, shared[key]])
```
(`_MONEY_DISPLAY` / `_SHARED_DISPLAY` come from Task 4 at module scope. The existing `totals = payload.get("risk", {}).get("totals") or {}` line stays.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_by_currency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading-currency add backend/app/services/reports.py tests/test_report_by_currency.py
git -C /Users/fuxinyao/open-otc-trading-currency commit -m "feat(reports): add By Currency sheet to xlsx report

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Full-suite verification

- [ ] **Step 1: Run the full backend suite**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/ -q`
Expected: all pass (current baseline 1271 + the new tests). Investigate any failure.

- [ ] **Step 2: Confirm clean tree, no stray files**

Run: `git -C /Users/fuxinyao/open-otc-trading-currency status --short`
Expected: clean (all work committed); no unexpected `frontend/` or other files.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Feature A module → Task 1 ✓; schema validators (FxRateCreate/FxRateAkshareRequest/AgentThreadUpdate) → Task 2 ✓; convert_currency tool guard → Task 3 ✓; normalization+storage → Tasks 2/3 (validators mutate to normalized) ✓.
- Feature B HTML (by_currency + shared + mixed note, keep top cards single-currency) → Task 4 ✓; xlsx By Currency sheet → Task 5 ✓; defensive `or {}` → Tasks 4/5 ✓.
- Tests for both → Tasks 1-5 each TDD; full-suite gate → Task 6 ✓.

**Placeholder scan:** none — every step has concrete code/commands.

**Type/name consistency:** `ISO_4217_CODES`, `normalize_currency`, `is_valid_currency`, `_MONEY_DISPLAY`, `_SHARED_DISPLAY`, `_metric_card` used identically across tasks; `model_validator(mode="after")` matches the schemas.py convention; `convert_currency_tool` error-dict shape (`error/totals/fx_rates_used/missing`) matches the tool's normal return keys.
