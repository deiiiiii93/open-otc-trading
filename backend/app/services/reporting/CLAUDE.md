# Report module (templated reports)

Declarative YAML templates with a server-owned block registry; the agent writes prose only.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

---

## Report module (templated reports)

Reports are generated from **declarative YAML templates**. A server-owned block
registry resolves every number deterministically; the agent's only output is prose.

**Package:** `backend/app/services/reporting/` — `contracts.py` (tri-state `BlockResult`),
`registry.py` (`@report_block`), `blocks/{risk,pnl,limits,desk}.py` (18 producers),
`renderers.py` (renderer↔shape map), `template_spec.py` (parse + validate-all-errors),
`templates.py` (validate-then-commit store), `seeds/*.yaml` (4 shipped templates),
`grounding.py`, `document.py`, `generate.py`, `narrator.py`. Deterministic P&L lives in
`backend/app/services/pnl/` (`snapshot_diff`, `explain`, `entry_price`). REST:
`routers/reports.py`. Tools: `tools/report_templates.py`. Skills:
`skills/workflows/reporting/{generate-templated-report,author-report-template}`.
Migrations `0053` (tables) + `0054` (seeds).

### Gotchas

- **`empty` vs `unavailable` is the whole point — never collapse them.** `empty` = the
  check RAN and found nothing ("no limit is in breach"); `unavailable` = the check DID NOT
  RUN. A report that renders them alike claims a clean book nobody verified. Limit
  evaluations with status `unknown`/`incomplete_scope` are a third case, counted as
  `indeterminate` — filtering them out (the obvious implementation) produces an empty
  breach list and a report that reads clean when the truth is "we couldn't tell".
- **`create_report` keeps its name forever.** It is a graded `tool_not_called` prohibition
  in three golden workflows; deleting the tool makes four checks trivially always-pass.
  The implementation was deleted, the name routes through `portfolio-snapshot`.
  `tests/test_reporting_legacy_retirement.py` pins this.
- **Seeded templates need BOTH install paths.** Migration `0054` seeds them, but a DB
  created by `init_db()`'s ORM bootstrap never runs it and then `create_report` has no
  template to resolve. `database.ensure_seeded_report_templates()` covers that, called
  from `create_app` — deliberately **not** from `init_db()`, which every block producer's
  `_session_scope` calls on the hot path, including from worker threads.
- **Never hold a write transaction across block resolution.** Each producer's
  `_session_scope` calls `database.init_db()`, which issues `create_all` + schema DDL;
  with an open write transaction in the same worker thread SQLite deadlocks and the task
  hangs to its poll timeout. `_complete_report_job` commits the `RUNNING` marker first.
- **A write service must not copy `domains/risk.py`'s `_session_scope`.** That one is
  READ-only: it flushes and never commits, so a self-owned session silently discards the
  write. The template store did exactly this and `PUT` answered 200 while nothing
  persisted — caught only by an HTTP test, because every unit test injected its own
  session.
- **Reports embed their own template spec + sha256.** Editing a template never rewrites
  what an old report claims to have been generated from, and no stored hash can dangle.
- **`scenario.latest_grid` reads `unavailable` while `scenario_test_runs` is empty.**
  That part is honest, not a bug: the risk template shows the desk that its report says
  nothing about tail risk. But **a producer that returns `empty` from a failed key lookup
  is indistinguishable, at the type level, from one reporting a genuine absence** — and
  `.get()` hands you the reassuring branch by default. This block read `results["rows"]`
  while the runner persists `shape_results(...)` under **`"scenarios"`**, so a populated
  stress grid would have rendered as "we checked, nothing to report". A producer must
  return **`unavailable` for a payload it cannot parse** and reserve `empty` for a payload
  it read successfully that contained nothing. Test the POPULATED path against the real
  producer's keys — a fixture that invents its own payload shape shares the bug.
- The grounding guard reuses the arena scorer's `_scan_numeric_tokens`. That tokenizer
  emits TWO readings at one offset for a `%` token (`34.0` and `0.34`), so tokens are
  grouped by offset and grounded if EITHER matches — otherwise "34%" is flagged whenever
  the data stored `0.34`. Its `k|m|mm|bn|b` suffix has **no word boundary after it**, so
  it reads "5 basis points" as `5e9` and "3 month" as `3e6`, and emits only the scaled
  reading. **Never fix that in `assertions.py`:** `_quote_value_report` matches on ANY
  reading, so an extra reading makes arena grading strictly more lenient across every
  stored board. `grounding.py::_misparsed_suffix_readings` corrects it locally and
  **overwrites** the reading (there is no suffix, so the scaled value is a misparse, not
  a second interpretation). Decide on the WHOLE trailing word — a per-character rule
  backtracks `1.2bn` to a `b` suffix followed by the "letter" `n` and corrupts a genuine
  magnitude.
- Flags are **non-blocking**: a false positive should degrade the report's confidence
  signal, not destroy the report.
- **A section with no blocks is a SYNTHESIS section and must be shown the report
  above it.** `narrator_brief` passes only that section's own blocks, so a
  `blocks: []` section — the board one-pager's `executive_summary` — was handed an
  empty brief and honestly wrote "the evidence base is empty" while all five
  sections above it had resolved fine. It now receives `report_so_far` (resolved
  upstream sections) and is grounded against that same evidence, or every figure
  it correctly carries forward would be flagged as invented. Ordinary sections are
  deliberately NOT given it, so a brief stays focused on its own evidence.
- **The grounding guard needed three live-found corrections**, all false positives
  that would have trained readers to ignore it: ISO timestamps are strings, so
  "23 June 2026" was ungrounded until date components are mined from them; a
  sha256 quoted verbatim from the data was shredded into 17 fabricated "numbers"
  until strings reproduced verbatim are blanked before tokenizing; and relative
  tolerance alone rejects prose rounding at small magnitudes ("0.06" for 0.0634 is
  5.4% off), so a token also grounds when it equals a data value rounded to any
  precision. **Only a live model run surfaces these** — a hand-written fixture
  narrative quotes numbers the way the test author would, not the way a model does.
