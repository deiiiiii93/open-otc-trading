---
name: python-analysis-policy
description: Use run_python for bounded analytics that reduce large result sets.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
  - async_agent
---

## Scripting for ad-hoc analytics with `run_python`

Use `run_python` whenever:

- The user wants a transformation, aggregation, or visualization that no
  single existing tool produces — bucket positions by underlying, plot a
  PnL distribution, compute a custom statistic.
- **A diagnostic or report would otherwise pull a whole portfolio's worth
  of rows into your context.** This is the canonical large-portfolio
  pattern: page the rows out of the read tools (use the tool's `limit`
  argument up to its maximum), hand them to the sandboxed script as
  `payload`, and let the script reduce them to a small summary + flagged
  subset. Your context only ever holds the post-reduce `result`, never the
  full row set.

Do **not** use `run_python` for Snowball KO/autocall proximity screens such as
"KO % From Spot under 5%" when `query_snowball_ko_from_spot` can answer the
request directly. That tool resolves view portfolios, refreshes structured
Snowball terms, joins stored spot inputs, and returns compact flagged rows.

Use the read-fetch-script-write pattern:

1. **READ** — fetch data through existing tools (`get_positions`,
   `get_position_summaries`, `get_latest_position_valuations`,
   `get_latest_risk_run`). Do not invent or guess data inside the script.
2. **FETCH into payload** — pass the rows you fetched as the `payload` to
   `run_python`. Prefer `get_position_summaries` for book scans; pull raw
   executable terms only through the product-detail tools (`get_product_details`,
   `get_option_core_terms`, `get_barrier_terms`) and strip large/unused fields
   before passing the payload, to keep it under ~5MB. When a prior tool offloaded a large result
   to a file, pass it as a whole-value marker such as
   `{"rows": "@file:/large_tool_results/<id>"}` instead of copying the blob
   into chat; the backend injects the resolved payload into the sandbox without
   requiring host filesystem access inside Pyodide.
3. **SCRIPT** — write the transformation. Inside the script, the dict is
   named `data`; the result must be assigned to `result`.
4. **WRITE** — when the deliverable should become a thread asset, have the
   script write text artifacts under `/trading_desk/<descriptive-name>/...`
   or `ARTIFACT_DIR` (`/sandbox_out/`) and call `run_python` with
   `writes_artifacts=true`. Do NOT dump binary content into the chat; prefer
   Plotly's `to_html()`, matplotlib SVG via `savefig(format="svg")`, or
   CSV/markdown — these are all text.

Set `writes_artifacts=false` (the default) for pure analysis. Set
`writes_artifacts=true` only when the script intentionally writes text artifacts
(HTML chart, CSV, Markdown report) under `/trading_desk/...` or `ARTIFACT_DIR`
(`/sandbox_out/`) for downstream persistence. Artifact-writing scripts trigger
the user approval card; pure analysis scripts run directly. If a script writes
files while `writes_artifacts=false`, the files are dropped.

The sandbox has no host filesystem access and no host network (except the
Pyodide package CDN for `numpy`/`pandas`/`scipy`/`matplotlib`/`plotly`). Always
provide a one-line `description` argument. Do not preview the script in chat
before invoking unless the user asked to review it or the expected runtime
requires a cost preview.

Cost: ~3s Pyodide cold start on the first call per backend session, then
per-script time. Use it directly for bounded analysis; reserve preview/wait
flows for expensive or artifact-writing work.
