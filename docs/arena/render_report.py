#!/usr/bin/env python3
"""Render the run-8 arena markdown into a styled, self-contained HTML report.

The three ASCII bar charts in the markdown are swapped for real CSS bar charts so
the HTML/PDF read better than monospace blocks. Output is a single .html file with
all CSS inlined (no external assets) so the PDF render is deterministic.
"""
import json
import re
import subprocess
import sys
from pathlib import Path
import markdown

# Default to the run-8 report living next to this script; override with argv[1].
HERE = Path(__file__).resolve().parent
SRC = (Path(sys.argv[1]) if len(sys.argv) > 1
       else HERE / "2026-06-27-run8-otc-desk-agent-arena.md").resolve()
OUT_HTML = SRC.with_suffix(".html")
OUT_PDF = SRC.with_suffix(".pdf")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Each report carries the same three ASCII charts; for HTML/PDF we swap them for
# real CSS bar charts. The chart data is per-report: a sibling `<src>.charts.json`
# (a list of [caption, axis_max, unit, rows] specs, rows = [label, value, cls,
# vlabel]) overrides the built-in run-8 defaults below, so every new run is a
# drop-in — no code edit, just author the report + its charts sidecar.
RUN_LABEL = next((m.group(0).replace("run", "Run #")
                  for m in [re.search(r"run\d+", SRC.name)] if m), "Run")

# ---------------------------------------------------------------- chart data ---
# (label, value, css-class)  — class drives the bar colour
LEADERBOARD = ("Mean total score (5 trials each)", 70, "pt", [
    ("🥇 Claude Opus 4.8", 66.4, "gold",   "66.4"),
    ("🥈 GPT-5.5",         66.3, "silver", "66.3"),
    ("🥉 Claude Sonnet 4.6",59.1,"bronze", "59.1"),
    ("Kimi 2.7",          56.9, "mid",    "56.9"),
    ("MiMo V2.5 Pro",     55.3, "mid",    "55.3"),
    ("GLM 5.2 ⚠️",        53.3, "mid",    "53.3 · n=2"),
    ("DeepSeek V4 Pro",   52.0, "mid",    "52.0"),
    ("Gemini 3.1 Pro",    48.7, "mid",    "48.7"),
    ("MiniMax M3",         0.6, "floor",  "0.6"),
    ("Qwen 3.7 Max",       0.0, "floor",  "0.0"),
])

RELIABILITY = ("Std-dev across 5 trials — shorter is steadier", 15, "σ", [
    ("Claude Sonnet 4.6", 2.7, "good", "2.7 · steadiest"),
    ("GLM 5.2",           2.3, "good", "2.3 · n=2"),
    ("MiMo V2.5 Pro",     5.1, "neutral", "5.1"),
    ("Gemini 3.1 Pro",    6.4, "neutral", "6.4"),
    ("Claude Opus 4.8",   8.1, "neutral", "8.1"),
    ("GPT-5.5",           8.2, "neutral", "8.2"),
    ("Kimi 2.7",         10.4, "warn", "10.4"),
    ("DeepSeek V4 Pro",  13.7, "bad",  "13.7 · most volatile"),
])

PPD = ("Score per dollar (est.) — cost-efficiency inverts the quality ranking", 70, "pts/$", [
    ("Kimi 2.7",          63, "good",  "≈63 · champion"),
    ("MiMo V2.5 Pro",     61, "good",  "≈61"),
    ("GLM 5.2",           59, "good",  "≈59 · n=2"),
    ("DeepSeek V4 Pro",   58, "good",  "≈58"),
    ("Gemini 3.1 Pro",    11, "neutral","≈11"),
    ("Claude Sonnet 4.6",  9, "frontier","≈9"),
    ("Claude Opus 4.8",    6, "frontier","≈6 · best quality"),
    ("GPT-5.5",            6, "frontier","≈6"),
    ("MiniMax M3",         2, "floor", "≈2"),
    ("Qwen 3.7 Max",       0, "floor", "0"),
])

_SIDECAR = SRC.with_suffix(".charts.json")
if _SIDECAR.exists():
    # Sidecar: [[caption, axis_max, unit, [[label, value, cls, vlabel], ...]], x3]
    _specs = [(s[0], s[1], s[2], [tuple(r) for r in s[3]])
              for s in json.loads(_SIDECAR.read_text())]
    LEADERBOARD, RELIABILITY, PPD = _specs


def chart_html(spec):
    caption, axis_max, unit, rows = spec
    out = ['<figure class="chart">']
    for label, value, cls, vlabel in rows:
        pct = max(value / axis_max * 100, 0.4)  # floor so 0-bars still show a sliver
        out.append(
            '<div class="row">'
            f'<span class="lbl">{label}</span>'
            f'<span class="track"><span class="bar {cls}" style="width:{pct:.1f}%"></span></span>'
            f'<span class="val">{vlabel}</span>'
            '</div>'
        )
    out.append(f'<figcaption>{caption} · axis 0–{axis_max} {unit}</figcaption>')
    out.append('</figure>')
    return "\n".join(out)

# --------------------------------------------------- swap ASCII blocks -> charts
md_text = SRC.read_text()
charts = iter([chart_html(LEADERBOARD), chart_html(RELIABILITY), chart_html(PPD)])
sentinels = []

def _sub(_m):
    tok = f"@@CHART_{len(sentinels)}@@"
    sentinels.append(next(charts))
    return tok

# fenced blocks that contain a full-block char are our ASCII charts
md_text = re.sub(r"```[^\n]*\n.*?█.*?```", _sub, md_text, flags=re.DOTALL)
assert len(sentinels) == 3, f"expected 3 ascii charts, found {len(sentinels)}"

body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "attr_list", "sane_lists", "md_in_html"],
)

# python-markdown wraps a lone sentinel paragraph in <p>…</p>
for i, html in enumerate(sentinels):
    body = body.replace(f"<p>@@CHART_{i}@@</p>", html).replace(f"@@CHART_{i}@@", html)

CSS = """
:root{
  --ink:#1a1d21; --muted:#5b6470; --line:#e3e6eb; --bg:#ffffff;
  --accent:#2f5d8a; --soft:#f4f6f9;
  --gold:#caa12e; --silver:#9aa0a6; --bronze:#b06a3b; --mid:#3b6fb0; --floor:#c5c9d1;
  --good:#2e8b57; --neutral:#6b7280; --warn:#d98a2b; --bad:#c0392b; --frontier:#3b6fb0;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{
  font-family:"Charter","Georgia",Cambria,"Times New Roman",serif;
  color:var(--ink); background:var(--bg); line-height:1.62;
  max-width:820px; margin:0 auto; padding:56px 40px 80px; font-size:16.5px;
}
h1,h2,h3,h4{font-family:-apple-system,"Inter","Segoe UI",Helvetica,Arial,sans-serif;
  line-height:1.22; letter-spacing:-.01em; color:var(--ink)}
h1{font-size:2.05rem; margin:.2em 0 .1em; font-weight:800}
h2{font-size:1.42rem; margin:2.1em 0 .55em; padding-bottom:.28em;
  border-bottom:2px solid var(--line); font-weight:750}
h3{font-size:1.12rem; margin:1.7em 0 .5em; font-weight:700; color:#2b3038}
h1+p em, body>p:first-of-type em{color:var(--muted)}
p,li{font-size:1rem}
a{color:var(--accent); text-decoration:none}
hr{border:none; border-top:1px solid var(--line); margin:2.2em 0}
code{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace; font-size:.86em;
  background:var(--soft); padding:.1em .35em; border-radius:4px}
blockquote{margin:1.1em 0; padding:.7em 1.1em; background:var(--soft);
  border-left:4px solid var(--accent); border-radius:0 6px 6px 0; color:#2b3038}
blockquote p{margin:.3em 0}
strong{font-weight:700}

/* tables */
table{border-collapse:collapse; width:100%; margin:1.1em 0; font-size:.9rem;
  font-family:-apple-system,"Inter","Segoe UI",sans-serif}
th,td{padding:.5em .7em; border-bottom:1px solid var(--line); text-align:left}
thead th{background:#f7f9fc; border-bottom:2px solid #d6dbe3; font-weight:700;
  font-size:.82rem; letter-spacing:.01em; color:#39414c}
tbody tr:nth-child(even){background:#fbfcfe}
td:first-child,th:first-child{white-space:nowrap}

/* charts */
.chart{margin:1.3em 0 1.6em; padding:0}
.chart .row{display:flex; align-items:center; gap:12px; margin:5px 0;
  font-family:-apple-system,"Inter","Segoe UI",sans-serif}
.chart .lbl{flex:0 0 168px; text-align:right; font-size:.82rem; color:#39414c; font-weight:600}
.chart .track{flex:1; height:22px; background:#eef1f5; border-radius:5px; overflow:hidden}
.chart .bar{display:block; height:100%; border-radius:5px}
.chart .val{flex:0 0 150px; font-size:.78rem; color:var(--muted); font-variant-numeric:tabular-nums}
.chart figcaption{margin-top:.7em; font-size:.76rem; color:var(--muted);
  font-family:-apple-system,"Inter",sans-serif; text-align:right}
.bar.gold{background:linear-gradient(90deg,#d9b13f,#caa12e)}
.bar.silver{background:linear-gradient(90deg,#aab0b6,#9aa0a6)}
.bar.bronze{background:linear-gradient(90deg,#c07c4a,#b06a3b)}
.bar.mid{background:linear-gradient(90deg,#4d80bf,#3b6fb0)}
.bar.frontier{background:linear-gradient(90deg,#4d80bf,#3b6fb0)}
.bar.floor{background:#c5c9d1}
.bar.good{background:linear-gradient(90deg,#37a169,#2e8b57)}
.bar.neutral{background:#8a929e}
.bar.warn{background:linear-gradient(90deg,#e09a3e,#d98a2b)}
.bar.bad{background:linear-gradient(90deg,#d04b3c,#c0392b)}

/* report header band */
.masthead{border-bottom:3px solid var(--ink); padding-bottom:.6em; margin-bottom:1.4em}
.footer-note{margin-top:3em; padding-top:1em; border-top:1px solid var(--line);
  font-size:.8rem; color:var(--muted); font-family:-apple-system,"Inter",sans-serif}

@page{size:A4; margin:16mm 15mm}
@media print{
  body{max-width:none; margin:0; padding:0; font-size:10.6pt}
  h2{margin-top:1.4em}
  h2,h3,h4{break-after:avoid}
  table,.chart,blockquote,figure{break-inside:avoid}
  tr{break-inside:avoid}
  a{color:var(--ink)}
}
"""

doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTC Desk Agent Arena — {RUN_LABEL}</title>
<style>{CSS}</style>
</head><body>
{body}
<p class="footer-note">Rendered from
<code>docs/arena/{SRC.name}</code> ·
OTC Desk Agent Arena · {RUN_LABEL}.</p>
</body></html>
"""

OUT_HTML.write_text(doc)
print(f"wrote {OUT_HTML}  ({len(doc):,} bytes)")

# ------------------------------------------------------- render PDF via Chrome
# Headless Chrome is the highest-fidelity HTML->PDF path: it honours @page,
# @media print, and print-color-adjust (so the chart colours survive).
if Path(CHROME).exists():
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw",
         f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()],
        check=True, capture_output=True,
    )
    print(f"wrote {OUT_PDF}")
else:
    print(f"skipped PDF (Chrome not found at {CHROME})")
