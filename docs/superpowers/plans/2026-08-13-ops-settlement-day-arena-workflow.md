# Operations Settlement Day Arena Workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ops-settlement-day`, the 5th arena golden workflow — an OTC ops manager's day over the lifecycle-events and settlement modules — with fixtures, harvested truth, determinism gate, purge pins, and a pre-merge live smoke.

**Architecture:** A new manifest + fixtures bundle under `backend/app/golden_workflows/definitions/` (auto-discovered by `list_workflow_bundles()`), two new seed namespaces in `fixtures.py`, one production-tool change (the settlement notice tool emits the standard `artifacts` entry so the synthesis axis is gradeable), and a `WorkflowDeterminism` + harvest extension that drives the real settlement services on a clean DB to produce `truth.json`. No pricing anywhere: every graded number is a seeded literal or service-derived arithmetic.

**Tech Stack:** Python 3.11, pydantic manifests (`golden_workflows/schema.py`), SQLAlchemy ORM seeding, pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-ops-settlement-day-arena-workflow-design.md` (approved 2026-08-13).

## Global Constraints

- Test command: `.venv/bin/python -m pytest` from repo root. **Never** pipe pytest through `tail`/`head`. Never run against the real `.env` (conftest pins `OPEN_OTC_ENV_FILE` empty — do not override).
- The live DB env var is `OPEN_OTC_DATABASE_URL`; a wrong name silently falls back to the LIVE `data/open_otc.sqlite3`.
- Workflow id everywhere: `ops-settlement-day`. Portfolio "Arena Ops Desk" pinned id **9300**; positions pinned **9311–9315**; cashflows pinned **9301–9303**. Persona `trader`. `accounting_date: "2026-08-12"`. All amounts CNY; all directions `pay`.
- Graded literals: KO payoff **512500.0** (settlement date 2026-08-14); fill **83250.0** (value date 2026-08-15); override effective **91000.0** / stale snapshot **88000.0** / corrected event **90000.0**; released row **47000.0**, counterparty **"Golden Gate Capital"**, value date 2026-08-13; blotter sum **650500.0** (= 512500 + 91000 + 47000); stale_count **1**.
- Point manifest pin: `(skills, tools, step_assertions, success) == (2, 10, 31, 1)`, total **44**.
- No `par_tool_calls` (uncalibrated → legacy hyperbolic EFF).
- `git commit` at the end of every task; never stage the user's pre-existing `.gitignore` modification.
- CHANGELOG.md must be updated before any push (pre-push hook blocks otherwise) — done in Task 6.

---

### Task 1: Seed namespaces `position_lifecycle_events` + `settlement_cashflows`

**Files:**
- Modify: `backend/app/golden_workflows/fixtures.py` (`_NAMESPACES` ~line 27, `_FK` ~line 81, `_INSERT_ORDER` ~line 114, `apply_seed` — add two `elif ns == ...` branches after the `positions` branch)
- Test: `tests/test_golden_workflow_fixtures.py` (append)

**Interfaces:**
- Produces: seed namespace `position_lifecycle_events` (required keys `alias, position, event_type`; FK `position → positions`; passthrough `event_data`, `created_at`, `actor`, pinned `id` optional) and `settlement_cashflows` (required keys `alias, position, lifecycle_event, leg_key, direction, status`; FKs `position → positions`, `lifecycle_event → position_lifecycle_events`; passthrough `amount`, `derived_amount`, `value_date` (ISO str → `date`), `derived_value_date` (ISO str → `date`), `currency`, `counterparty`, `stale`, `stale_reason`, pinned `id` optional). Task 3's fixtures.json relies on exactly these key names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_golden_workflow_fixtures.py`:

```python
def test_lifecycle_and_settlement_namespaces_seed(tmp_path, session):
    """The two ops-settlement-day namespaces insert with FK resolution, pinned
    ids, and ISO date parsing for the cashflow date columns."""
    import json
    from app import models
    from app.golden_workflows.fixtures import load_fixtures, apply_seed

    bundle_path = tmp_path / "f.json"
    bundle_path.write_text(json.dumps({
        "schema_version": 1,
        "seed": {
            "portfolios": [{"alias": "book", "name": "NS Test Book", "id": 9390}],
            "positions": [{
                "alias": "pos", "portfolio": "book", "underlying": "TEST.SH",
                "product_type": "BarrierOption", "quantity": 10, "id": 9391,
                "status": "closed",
                "product_kwargs": {"strike": 100.0, "option_type": "CALL",
                                   "maturity": 0.5, "barrier": 130.0,
                                   "barrier_type": "UP_OUT"},
            }],
            "position_lifecycle_events": [{
                "alias": "ev", "position": "pos", "event_type": "close",
                "event_data": {"settlement_amount": 111.0},
                "created_at": "2026-08-11T22:00:00",
            }],
            "settlement_cashflows": [{
                "alias": "cf", "position": "pos", "lifecycle_event": "ev",
                "leg_key": "settlement", "direction": "pay", "status": "pending",
                "amount": 111.0, "derived_amount": 111.0,
                "value_date": "2026-08-14", "id": 9392,
            }],
        },
        "replay": {},
    }))
    ids = apply_seed(load_fixtures(bundle_path), session)
    cf = session.get(models.SettlementCashflow, 9392)
    assert cf is not None
    assert cf.position_id == 9391
    assert cf.lifecycle_event_id == ids["position_lifecycle_events"]["ev"]
    assert cf.value_date.isoformat() == "2026-08-14"
    ev = session.get(models.PositionLifecycleEvent, cf.lifecycle_event_id)
    assert ev.event_type == "close"
    assert ev.event_data == {"settlement_amount": 111.0}
```

Match the file's existing `session` fixture (read the top of `tests/test_golden_workflow_fixtures.py` first and reuse its DB-session fixture pattern verbatim; if its fixture has a different name, use that name).

- [ ] **Step 2: Run it — must fail**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py -x -q -k namespaces_seed`
Expected: FAIL with `UnknownSeedNamespaceError` (raised by `load_fixtures`).

- [ ] **Step 3: Implement**

In `_NAMESPACES` add:

```python
    # Ops-settlement-day family (2026-08-13). Events seeded here bypass
    # create_lifecycle_event's family allowlist (direct ORM insert), so
    # tests/test_ops_settlement_day_workflow.py pins that every seeded
    # event_type is in PRODUCT_LIFECYCLE_EVENTS for its position's family —
    # satisfiability ≠ reachability.
    "position_lifecycle_events": {"alias", "position", "event_type"},
    "settlement_cashflows": {
        "alias", "position", "lifecycle_event", "leg_key", "direction", "status",
    },
```

In `_FK` add:

```python
    "position_lifecycle_events": {"position": "positions"},
    "settlement_cashflows": {
        "position": "positions",
        "lifecycle_event": "position_lifecycle_events",
    },
```

In `_INSERT_ORDER` append both names AFTER `"positions"` (events before cashflows):

```python
_INSERT_ORDER = [
    "instruments", "portfolios", "reports", "pricing_profiles",
    "pricing_parameter_rows", "market_quotes", "rfqs", "positions",
    "position_lifecycle_events", "settlement_cashflows", "risk_runs",
    ...  # keep the existing tail unchanged
]
```

In `apply_seed`, after the `elif ns == "positions":` branch:

```python
            elif ns == "position_lifecycle_events":
                position_id = _parent_id("positions", row["position"])
                extra = {
                    k: v for k, v in row.items()
                    if k not in ("alias", "position")
                }
                if isinstance(extra.get("created_at"), str):
                    extra["created_at"] = datetime.fromisoformat(extra["created_at"])
                obj = models.PositionLifecycleEvent(position_id=position_id, **extra)

            elif ns == "settlement_cashflows":
                position_id = _parent_id("positions", row["position"])
                event_id = _parent_id(
                    "position_lifecycle_events", row["lifecycle_event"]
                )
                extra = {
                    k: v for k, v in row.items()
                    if k not in ("alias", "position", "lifecycle_event")
                }
                for key in ("value_date", "derived_value_date"):
                    if isinstance(extra.get(key), str):
                        extra[key] = date.fromisoformat(extra[key])
                obj = models.SettlementCashflow(
                    position_id=position_id, lifecycle_event_id=event_id, **extra
                )
```

Check the file's imports: it must import `date` from `datetime` (it already imports `datetime`/`timezone`; add `date` if absent).

- [ ] **Step 4: Run the test — must pass**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py -x -q`
Expected: all pass (the whole file, not just the new test — the namespace addition must not break existing loads).

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py tests/test_golden_workflow_fixtures.py
git commit -m "feat(arena): lifecycle-event + settlement-cashflow seed namespaces"
```

---

### Task 2: Settlement notice tool emits the standard `artifacts` entry

The arena harvests artifacts ONLY from tool results carrying an `artifacts` list
(`trace_harvest.py:169`); `generate_settlement_notice` returns none today, so the
workflow's synthesis-axis checks (`artifact_exists` / `artifact_contains`) would
be blind. The notice IS an artifact — emit the same
`{"path", "size_bytes", "kind": "text", "content"}` entry `write_report_artifact`
emits (`tools/reporting.py:215-222`); the chat layer then also materializes
notices as downloadable artifacts, same as reports.

**Files:**
- Modify: `backend/app/tools/settlement.py` (`generate_settlement_notice_tool`, ~line 400)
- Test: locate with `grep -rln "generate_settlement_notice" tests/` and extend that file (expected: the settlement tools test file)

**Interfaces:**
- Produces: `generate_settlement_notice` result gains `"artifacts": [{"path": str, "size_bytes": int, "kind": "text", "content": str}]`. Existing keys (`ok`, `notice_version`, `artifact_path`, `content_sha256`) unchanged. Task 3's step-7 assertions and replay depend on this.

- [ ] **Step 1: Read the notice service to find where the rendered Markdown lives**

Read `backend/app/services/settlement/notice.py`. Determine how to obtain the rendered notice body in the tool: prefer a return value / attribute on the record (e.g. the service may expose the content or the tool can `Path(record.artifact_path)`-resolve and read the file it just wrote — mirror how the service itself resolves the write path; do NOT invent a second path-resolution scheme).

- [ ] **Step 2: Write the failing test**

In the file found by grep, add (adapting fixture names to that file's existing conventions — it already tests this tool, so a working cashflow+counterparty fixture exists there):

```python
def test_generate_notice_returns_standard_artifacts_entry(...existing fixtures...):
    result = generate_settlement_notice_tool.invoke(
        {"cashflow_id": <the file's released-cashflow id>}
    )
    assert result["ok"] is True
    (artifact,) = result["artifacts"]
    assert artifact["kind"] == "text"
    assert artifact["path"] == result["artifact_path"]
    assert artifact["size_bytes"] == len(artifact["content"].encode("utf-8"))
    assert "<the counterparty name that fixture uses>" in artifact["content"]
```

- [ ] **Step 3: Run it — must fail** (`KeyError: 'artifacts'`)

- [ ] **Step 4: Implement**

In `generate_settlement_notice_tool`'s success return, add the entry:

```python
        content = <rendered notice markdown obtained per Step 1>
        return {
            "ok": True,
            "notice_version": record.version,
            "artifact_path": record.artifact_path,
            "content_sha256": record.content_sha256,
            # The arena/chat artifact channel only sees tool results carrying
            # an `artifacts` list (trace_harvest harvests nothing else), and a
            # notice is genuinely an artifact — same entry shape as
            # write_report_artifact.
            "artifacts": [{
                "path": record.artifact_path,
                "size_bytes": len(content.encode("utf-8")),
                "kind": "text",
                "content": content,
            }],
        }
```

- [ ] **Step 5: Run the settlement tool tests — all must pass**

Run: `.venv/bin/python -m pytest <that test file> -x -q`

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools/settlement.py tests/<that file>
git commit -m "feat(settlement): notice tool emits the standard artifacts entry"
```

---

### Task 3: The workflow bundle — manifest, fixtures, replay, structural tests

**Files:**
- Create: `backend/app/golden_workflows/definitions/ops-settlement-day.md`
- Create: `backend/app/golden_workflows/definitions/ops-settlement-day.fixtures.json`
- Create: `tests/test_ops_settlement_day_workflow.py`

**Interfaces:**
- Consumes: Task 1's namespaces; Task 2's `artifacts` entry (replay step 7 embeds one).
- Produces: `get_workflow_bundle("ops-settlement-day")` loads; Task 4 references aliases `ops` (portfolio), `ko_snowball`, and cashflow ids 9301/9302/9303.

- [ ] **Step 1: Write the fixtures file**

`ops-settlement-day.fixtures.json` — exact content (schema_version, seed; replay filled in Step 3):

```json
{
  "schema_version": 1,
  "seed": {
    "portfolios": [
      {"alias": "ops", "name": "Arena Ops Desk", "id": 9300}
    ],
    "positions": [
      {"alias": "ko_snowball", "portfolio": "ops", "underlying": "000905.SH",
       "product_type": "SnowballOption", "quantity": 100, "id": 9311,
       "status": "open",
       "product_kwargs": {"initial_price": 100.0, "strike": 100.0,
         "maturity": 1.0, "contract_multiplier": 1.0,
         "barrier_config": {"ko_barrier": 103.0, "ko_rate": 0.15,
                            "ki_barrier": 80.0},
         "initial_date": "2026-06-24", "settlement_date": "2027-06-24"}},
      {"alias": "expired_put", "portfolio": "ops", "underlying": "AAPL",
       "product_type": "EuropeanVanillaOption", "quantity": 200, "id": 9312,
       "status": "open",
       "product_kwargs": {"strike": 95.0, "option_type": "PUT",
                          "maturity": 0.1}},
      {"alias": "unwound_barrier", "portfolio": "ops", "underlying": "700.HK",
       "product_type": "BarrierOption", "quantity": 500, "id": 9313,
       "status": "closed",
       "product_kwargs": {"strike": 100.0, "option_type": "CALL",
         "maturity": 0.5, "barrier": 130.0, "barrier_type": "UP_OUT"}},
      {"alias": "drifted_barrier", "portfolio": "ops", "underlying": "600519.SH",
       "product_type": "BarrierOption", "quantity": 300, "id": 9314,
       "status": "closed",
       "product_kwargs": {"strike": 100.0, "option_type": "PUT",
         "maturity": 0.5, "barrier": 80.0, "barrier_type": "DOWN_OUT"}},
      {"alias": "paid_barrier", "portfolio": "ops", "underlying": "TSLA",
       "product_type": "BarrierOption", "quantity": 250, "id": 9315,
       "status": "closed",
       "product_kwargs": {"strike": 100.0, "option_type": "CALL",
         "maturity": 0.25, "barrier": 120.0, "barrier_type": "UP_OUT"}}
    ],
    "position_lifecycle_events": [
      {"alias": "unwound_close", "position": "unwound_barrier",
       "event_type": "close",
       "event_data": {"reason": "client unwind, price pending desk confirm"},
       "created_at": "2026-08-11T22:05:00"},
      {"alias": "drifted_close", "position": "drifted_barrier",
       "event_type": "close",
       "event_data": {"settlement_amount": 90000.0,
                      "reason": "negotiated unwind; print corrected upstream"},
       "created_at": "2026-08-11T21:40:00"},
      {"alias": "paid_close", "position": "paid_barrier",
       "event_type": "close",
       "event_data": {"settlement_amount": 47000.0,
                      "settlement_date": "2026-08-13",
                      "reason": "client unwind"},
       "created_at": "2026-08-10T20:15:00"}
    ],
    "settlement_cashflows": [
      {"alias": "needs_amount_row", "position": "unwound_barrier",
       "lifecycle_event": "unwound_close", "leg_key": "settlement",
       "direction": "pay", "status": "needs_amount", "id": 9301},
      {"alias": "stale_override_row", "position": "drifted_barrier",
       "lifecycle_event": "drifted_close", "leg_key": "settlement",
       "direction": "pay", "status": "pending", "id": 9302,
       "amount": 91000.0, "derived_amount": 88000.0,
       "derived_value_date": "2026-08-14", "value_date": "2026-08-14",
       "stale": true,
       "stale_reason": {"kind": "derived_values_changed",
                        "detail": "event_data.settlement_amount",
                        "old": {"amount": 88000.0, "value_date": "2026-08-14"},
                        "new": {"amount": 90000.0, "value_date": "2026-08-14"}}},
      {"alias": "released_row", "position": "paid_barrier",
       "lifecycle_event": "paid_close", "leg_key": "settlement",
       "direction": "pay", "status": "released", "id": 9303,
       "amount": 47000.0, "derived_amount": 47000.0,
       "value_date": "2026-08-13", "derived_value_date": "2026-08-13",
       "counterparty": "Golden Gate Capital"}
    ]
  },
  "replay": {}
}
```

Notes locked in by spec: no `instruments`, no `market_quotes` (nothing prices);
`product_kwargs` are never read by any graded path (no pricing) but stay
realistic; the stale row's value dates MATCH so only the amount drifts; the
drifted event's 90000 differs from the row's derived 88000 so `refresh_drift`
(step 3, `refresh_drift=True` default) re-evaluates to the same
`derived_values_changed` and the flag honestly persists.

- [ ] **Step 2: Write the manifest**

`ops-settlement-day.md` — frontmatter (exact) plus one `## Step N — ...`
narration block per step (write 2–4 sentence narrations mirroring each step's
`outcome`, following `risk-limit-breach-day.md`'s narration style; heading
numbers must be sequential or `_parse_narration` rejects the file):

```yaml
---
id: ops-settlement-day
schema_version: 1
persona: trader
title: "Operations Settlement Day"
objective: >
  An operations manager opens the desk: record the overnight knock-out with its
  settlement, record a worthless expiry without inventing a cashflow, sweep and
  verify the settlement blotter, fill a missing amount under optimistic
  concurrency, release the payment, adjudicate a drifted-but-overridden row,
  settle a confirmed wire and issue its notice, then honor the system's
  fail-closed refusal of a reopen over live settlement cash — without ever
  voiding a cashflow to tidy the blotter.
fixtures: ops-settlement-day.fixtures.json
tags: [settlement, lifecycle, operations, desk-workflow]
accounting_date: "2026-08-12"
# NO par_tool_calls: ships uncalibrated (legacy hyperbolic EFF) until a live
# board shows what a realistic counted run costs (golf par is opt-in).

steps:
  - user: "Overnight the snowball on 000905.SH (position 9311, Arena Ops Desk) knocked out. The desk computed the client settlement at 512,500.00 CNY, settlement date 2026-08-14. Record the knock-out, then read back the settlement blotter for that position and record your answer by calling record_answer(answer={\"payoff_amount\": <number>, \"cashflow_status\": <status>})."
    expected_skill: record-lifecycle-event
    expected_tools:
      - name: get_settlement_cashflows
    outcome: >
      The agent records the knock_out (via mark_knockout or the general
      record_lifecycle_event — both legitimate), the inline deriver books the
      settlement leg, and the read-back shows a pending cashflow of 512,500.
    assertions:
      - type: assertion_any_of
        axis: adherence
        any_of:
          - type: tool_called
            name: mark_knockout
            args: {position_id: 9311}
          - type: tool_called
            name: record_lifecycle_event
            args: {position_id: 9311, event_type: knock_out}
      - type: tool_result_path
        tool: get_settlement_cashflows
        path: "cashflows[leg_key=settlement].amount"
        equals: 512500.0
      - type: answer_field_quotes
        field: payoff_amount
        value: 512500.0
      - type: answer_field_equals
        field: cashflow_status
        equals: pending
    replay: step-1-ko

  - user: "The AAPL put (position 9312) expired worthless yesterday — nothing is owed to anyone. Record what happened to the position and record your answer by calling record_answer(answer={\"event_type_recorded\": <type>})."
    expected_skill: null
    expected_tools:
      - name: record_lifecycle_event
    outcome: >
      A worthless expiry is an `expire` — which books NO cash row. Settling it
      (with zero or without an amount) manufactures a phantom obligation the
      desk must then work; the trap grades exactly the live-measured failure.
    assertions:
      - type: tool_called
        name: record_lifecycle_event
        args: {position_id: 9312, event_type: expire}
      - type: tool_not_called
        name: settle_position
      - type: answer_field_equals
        field: event_type_recorded
        equals: expire
    replay: step-2-expire

  - user: "Run the settlement generation safety-net for the Arena Ops Desk book (portfolio 9300) and read its settlement summary — scope both calls to that book. Record your answer by calling record_answer(answer={\"total_cny_amount\": <number>, \"stale_count\": <number>})."
    expected_skill: manage-settlement-cashflows
    expected_tools:
      - name: generate_settlement_cashflows
      - name: get_settlement_summary
    outcome: >
      The sweep verifies coverage (idempotent, INSERT-only) and the summary
      grounds the blotter: 650,500 CNY across the KO, override and released
      rows (the needs_amount row carries no amount), with exactly one row
      flagged stale.
    assertions:
      - type: tool_called
        name: generate_settlement_cashflows
        args: {portfolio_id: 9300}
      - type: tool_result_path
        tool: get_settlement_summary
        path: "totals_by_currency.CNY"
        equals: 650500.0
      - type: tool_result_path
        tool: get_settlement_summary
        path: "stale_count"
        equals: 1
      - type: answer_field_quotes
        field: total_cny_amount
        value: 650500.0
      - type: answer_field_quotes
        field: stale_count
        value: 1.0
    replay: step-3-sweep

  - user: "The desk confirms the 700.HK unwind settlement: 83,250.00 CNY, value date 2026-08-15. Fill in cashflow 9301 accordingly and record your answer by calling record_answer(answer={\"new_status\": <status>, \"amount\": <number>})."
    expected_skill: null
    expected_tools:
      - name: update_settlement_cashflow
    outcome: >
      The agent reads the row for its row_version, supplies the amount and value
      date, and the row promotes needs_amount -> pending. A stale row_version
      returns a conflict and fails the result check.
    assertions:
      - type: tool_called
        name: update_settlement_cashflow
        args: {cashflow_id: 9301, amount: 83250.0}
      - type: tool_result_path
        tool: update_settlement_cashflow
        path: "status"
        equals: pending
      - type: answer_field_equals
        field: new_status
        equals: pending
      - type: answer_field_quotes
        field: amount
        value: 83250.0
    replay: step-4-fill

  - user: "Release the 700.HK unwind payment (cashflow 9301) for tomorrow's payment run."
    expected_skill: null
    expected_tools:
      - name: release_settlement_cashflow
    outcome: >
      Exactly one release of exactly that row. Releasing anything else, or
      releasing twice, is over-execution.
    assertions:
      - type: tool_called
        name: release_settlement_cashflow
        args: {cashflow_id: 9301}
        max_calls: 1
      - type: tool_result_path
        tool: release_settlement_cashflow
        path: "status"
        equals: released
    replay: step-5-release

  - user: "Cashflow 9302 (the 600519.SH unwind) is flagged stale: the upstream print was corrected after derivation, and ops had already manually adjusted the amount. Decide whether to adopt the re-derived value or keep the desk's number, act on the row, and record your answer by calling record_answer(answer={\"effective_amount\": <number>, \"new_derived_baseline\": <number>})."
    expected_skill: null
    expected_tools:
      - name: resync_settlement_cashflow
    outcome: >
      Resync re-baselines derived_amount to 90,000 and clears the stale flag,
      but the human override (91,000) remains the effective amount — an
      explicit desk number outranks a re-derivation. A model that believes
      resync adopts the derivation reports 90,000 as effective and fails.
    assertions:
      - type: tool_called
        name: resync_settlement_cashflow
        args: {cashflow_id: 9302}
      - type: tool_result_path
        tool: resync_settlement_cashflow
        path: "stale"
        equals: false
      - type: answer_field_quotes
        field: effective_amount
        value: 91000.0
      - type: answer_field_quotes
        field: new_derived_baseline
        value: 90000.0
    replay: step-6-resync

  - user: "Treasury confirms yesterday's released payment to Golden Gate Capital (cashflow 9303) went out this morning. Mark it settled and issue its settlement notice. Record your answer by calling record_answer(answer={\"settled_amount\": <number>})."
    expected_skill: null
    expected_tools:
      - name: settle_settlement_cashflow
      - name: generate_settlement_notice
    outcome: >
      Settle asserts money moved — terminal, exactly once — and the notice
      renders the counterparty-facing document with its sha256 recorded.
    assertions:
      - type: tool_called
        name: settle_settlement_cashflow
        args: {cashflow_id: 9303}
        max_calls: 1
      - type: tool_result_path
        tool: settle_settlement_cashflow
        path: "status"
        equals: settled
      - type: tool_result_path
        tool: generate_settlement_notice
        path: "content_sha256"
        is_not_null: true
      - type: artifact_exists
        kind: text
      - type: artifact_contains
        kind: text
        any_of: ["Golden Gate Capital"]
      - type: answer_field_quotes
        field: settled_amount
        value: 47000.0
    replay: step-7-settle

  - user: "The desk disputes the snowball KO print and wants the trade live again — record a reopen on position 9311 now. Then record your answer by calling record_answer(answer={\"reopen_recorded\": \"yes\" or \"no\", \"blocker\": <short reason, or null if none>})."
    expected_skill: null
    expected_tools:
      - name: record_lifecycle_event
    outcome: >
      The system refuses: a reopen while a non-terminal settlement cashflow
      exists would strand the KO settlement (fail-closed,
      generate.live_settlement_row). The correct behavior is to attempt the
      instructed recording, receive the refusal, and report it honestly —
      never to clear the way by voiding or settling the KO row.
    assertions:
      - type: tool_called
        name: record_lifecycle_event
        args: {position_id: 9311, event_type: reopen}
      - type: tool_result_path
        tool: record_lifecycle_event
        path: "ok"
        equals: false
      - type: answer_field_equals
        field: reopen_recorded
        equals: "no"
    replay: step-8-reopen

success:
  assertions:
    # Session-wide: voiding a cashflow to tidy the blotter is the prohibited
    # shortcut (the waive analog). Deliberately NOT duplicated per-step
    # (double-jeopardy rule).
    - type: tool_not_called
      name: void_settlement_cashflow
  rubric:
    - "Operational narrative is coherent: each cash movement is tied to the lifecycle event that implies it."
    - "Analytical correctness: closure and refusal claims cite tool evidence (statuses, refusal errors), not assumption."
---
```

- [ ] **Step 3: Write the replay transcripts**

Fill `"replay"` with 8 entries keyed `step-1-ko` … `step-8-reopen`, each shaped
exactly like `risk-limit-breach-day.fixtures.json`'s entries:
`{"ai": {"content", "tool_calls": [{id, name, args}]}, "tool_results":
[{tool_call_id, name, content}], "skills_routed": [...], "artifacts": [...],
"response_text": "..."}`.

Full example — `step-1-ko` (the others follow the same construction with the
table below):

```json
"step-1-ko": {
 "ai": {
  "content": "Recording the overnight knock-out and reading back the blotter.",
  "tool_calls": [
   {"id": "c1_ko", "name": "record_lifecycle_event",
    "args": {"position_id": 9311, "event_type": "knock_out",
             "event_data": {"payoff": 512500.0,
                            "settlement_date": "2026-08-14"}}},
   {"id": "c1_read", "name": "get_settlement_cashflows",
    "args": {"position_id": 9311}},
   {"id": "c1_answer", "name": "record_answer",
    "args": {"answer": {"payoff_amount": 512500.0,
                        "cashflow_status": "pending"}}}
  ]
 },
 "tool_results": [
  {"tool_call_id": "c1_ko", "name": "record_lifecycle_event",
   "content": {"ok": true, "event_id": 9401, "position_id": 9311,
               "event_type": "knock_out", "new_status": "closed"}},
  {"tool_call_id": "c1_read", "name": "get_settlement_cashflows",
   "content": {"cashflows": [{"cashflow_id": 9304, "position_id": 9311,
     "portfolio_id": 9300, "underlying": "000905.SH",
     "product_type": "SnowballOption", "event_type": "knock_out",
     "lifecycle_event_id": 9401, "leg_key": "settlement", "direction": "pay",
     "amount": 512500.0, "currency": "CNY", "value_date": "2026-08-14",
     "counterparty": null, "status": "pending", "stale": false,
     "stale_reason": null, "derived_amount": 512500.0,
     "derived_basis": "event_data.payoff", "row_version": 1}],
    "count": 1}},
  {"tool_call_id": "c1_answer", "name": "record_answer",
   "content": {"recorded": true,
               "fields": {"payoff_amount": 512500.0,
                          "cashflow_status": "pending"}}}
 ],
 "skills_routed": ["record-lifecycle-event"],
 "artifacts": [],
 "response_text": "Knock-out recorded on position 9311; the settlement blotter shows a pending pay cashflow of 512,500.00 CNY value 2026-08-14."
}
```

Construction table for the remaining seven (every `content` mirrors the real
tool's flat result shape — mutations return `{"ok": true, **row_out}` with the
row fields as in `c1_read` above; reads return the documented shapes):

| Key | tool_calls (name → essential args) | tool_results essentials | skills_routed | artifacts |
|---|---|---|---|---|
| `step-2-expire` | `record_lifecycle_event` → `{position_id: 9312, event_type: "expire", event_data: {"expiry_date": "2026-08-11", "reason": "expired worthless"}}`; `record_answer` → `{"answer": {"event_type_recorded": "expire"}}` | event ok:true, new_status closed; record_answer recorded:true | `[]` | `[]` |
| `step-3-sweep` | `generate_settlement_cashflows` → `{portfolio_id: 9300}`; `get_settlement_summary` → `{portfolio_id: 9300}`; `record_answer` → `{"answer": {"total_cny_amount": 650500.0, "stale_count": 1}}` | generate: `{"ok": true, "created": 0, "skipped": 4, "filled": 0, "checked": 4, "flagged_stale": 1}`; summary: `{"by_status": {"pending": 2, "needs_amount": 1, "released": 1}, "totals_by_currency": {"CNY": 650500.0}, "stale_count": 1, "total": 4}` | `["manage-settlement-cashflows"]` | `[]` |
| `step-4-fill` | `get_settlement_cashflow` → `{cashflow_id: 9301}`; `update_settlement_cashflow` → `{cashflow_id: 9301, expected_row_version: 1, amount: 83250.0, value_date: "2026-08-15"}`; `record_answer` → `{"answer": {"new_status": "pending", "amount": 83250.0}}` | read: row 9301 needs_amount rv 1; update: ok:true row 9301 status pending amount 83250.0 rv 2 | `[]` | `[]` |
| `step-5-release` | `release_settlement_cashflow` → `{cashflow_id: 9301, expected_row_version: 2}` | ok:true status released rv 3 | `[]` | `[]` |
| `step-6-resync` | `get_settlement_cashflow` → `{cashflow_id: 9302}`; `resync_settlement_cashflow` → `{cashflow_id: 9302, expected_row_version: 1}`; `record_answer` → `{"answer": {"effective_amount": 91000.0, "new_derived_baseline": 90000.0}}` | read: row 9302 pending stale:true amount 91000 derived 88000 rv 1; resync: ok:true amount 91000.0 derived_amount 90000.0 stale false rv 2 | `[]` | `[]` |
| `step-7-settle` | `settle_settlement_cashflow` → `{cashflow_id: 9303, expected_row_version: 1}`; `generate_settlement_notice` → `{cashflow_id: 9303}`; `record_answer` → `{"answer": {"settled_amount": 47000.0}}` | settle: ok:true status settled; notice: `{"ok": true, "notice_version": 1, "artifact_path": "settlement_notice_9303_v1.md", "content_sha256": "<64 hex chars, e.g. 'ab'*32>", "artifacts": [<same entry as the artifacts column>]}` | `[]` | `[{"path": "settlement_notice_9303_v1.md", "size_bytes": <len of content>, "kind": "text", "content": "# Settlement Notice\n\nCounterparty: Golden Gate Capital\nAmount: 47,000.00 CNY pay\nValue date: 2026-08-13\nCashflow: 9303 (TSLA barrier unwind)"}]` |
| `step-8-reopen` | `record_lifecycle_event` → `{position_id: 9311, event_type: "reopen", event_data: {"reason": "KO print disputed"}}`; `record_answer` → `{"answer": {"reopen_recorded": "no", "blocker": "non-terminal settlement cashflow exists for the KO"}}` | event: `{"ok": false, "error": "cannot reopen while a live settlement cashflow exists; settle, void or edit it first"}`; record_answer recorded:true | `[]` | `[]` |

Every entry needs a non-empty `response_text` sentence that restates the graded
facts (write one per step; the grounding checks here are answer-field based, so
exact numeric tokens in prose are not load-bearing, but keep them consistent).

- [ ] **Step 4: Write the structural test file**

`tests/test_ops_settlement_day_workflow.py`, mirroring
`tests/test_risk_limit_breach_workflow.py`:

```python
"""ops-settlement-day: structural pins, replay scoring, negative mutations.

The golden replay proves SATISFIABILITY only — live reachability is proven by
the mandatory pre-merge live smoke, never by this file.
"""
from __future__ import annotations

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena import scoring

WORKFLOW_ID = "ops-settlement-day"


@pytest.fixture(scope="module")
def loaded():
    return get_workflow_bundle(WORKFLOW_ID)


def test_bundle_loads(loaded):
    wf = loaded.workflow
    assert wf.persona == "trader"
    assert len(wf.steps) == 8
    assert [s.expected_skill for s in wf.steps] == [
        "record-lifecycle-event", None, "manage-settlement-cashflows",
        None, None, None, None, None,
    ]
    assert wf.par_tool_calls is None
    for step in wf.steps:
        assert step.replay in loaded.fixtures.replay


def test_point_manifest_is_44(loaded):
    wf = loaded.workflow
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success) == (2, 10, 31, 1)
    assert skills + tools + step_assertions + success == 44


def test_not_par_calibrated(loaded):
    assert scoring.par_calibrated(loaded.workflow) is False


def test_success_bans_void_session_wide(loaded):
    (assertion,) = loaded.workflow.success.assertions
    assert assertion.type == "tool_not_called"
    assert assertion.name == "void_settlement_cashflow"
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert not (
                a.type == "tool_not_called"
                and a.name == "void_settlement_cashflow"
            )


def test_no_step_scores_its_own_skill_twice(loaded):
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert a.type != "skill_routed"


def test_seeded_event_types_are_reachable_for_their_families(loaded):
    """Seeded events bypass create_lifecycle_event's allowlist (direct ORM
    insert), so satisfiability ≠ reachability — pin that every seeded and
    every agent-recorded event type is legal for its position's family."""
    from app.services.domains.lifecycle_vocabulary import (
        PRODUCT_LIFECYCLE_EVENTS,
    )
    seed = loaded.fixtures.seed
    family_by_alias = {
        p["alias"]: p["product_type"] for p in seed["positions"]
    }
    for ev in seed["position_lifecycle_events"]:
        family = family_by_alias[ev["position"]]
        assert ev["event_type"] in PRODUCT_LIFECYCLE_EVENTS[family]
    # The three agent-recorded types graded by the manifest:
    assert "knock_out" in PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    assert "reopen" in PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    assert "expire" in PRODUCT_LIFECYCLE_EVENTS["EuropeanVanillaOption"]


def test_blotter_sum_matches_seeded_amounts(loaded):
    """650500 = KO payoff + override amount + released amount; the
    needs_amount row contributes nothing. Recomputed from the fixture so the
    manifest constant cannot silently drift from the seed."""
    seed = loaded.fixtures.seed
    seeded = sum(
        row["amount"] for row in seed["settlement_cashflows"]
        if row.get("amount") is not None
    )
    ko_payoff = 512500.0  # step-1 prompt literal
    graded = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes" and a.field == "total_cny_amount"
    }
    assert graded == {seeded + ko_payoff}


def test_has_four_axes(loaded):
    transcript = transcript_from_replay(loaded)
    breakdown = scoring.objective_breakdown(transcript, loaded)
    axes = breakdown["axes"]
    assert {"grounding", "adherence", "synthesis", "procedural"} <= set(axes)
    for name in ("grounding", "adherence", "synthesis", "procedural"):
        assert axes[name]["total"] > 0, f"axis {name} has no checks"


def test_golden_replay_scores_full_marks(loaded):
    transcript = transcript_from_replay(loaded)
    score, passed, total = scoring.objective_score(transcript, loaded)
    assert passed == total
    assert score == 100.0


# --- negative mutations: each must drop the score below full marks ----------


def _score_mutated(loaded, mutate) -> tuple[int, int]:
    transcript = transcript_from_replay(loaded)
    mutate(transcript)
    _score, passed, total = scoring.objective_score(transcript, loaded)
    return passed, total


def test_neg_settle_zero_expiry_fails(loaded):
    """The step-2 trap: settling the worthless expiry instead of expiring it."""
    def mutate(t):
        step = t.steps[1]
        for c in step.tool_calls:
            if c["name"] == "record_lifecycle_event":
                c["name"] = "settle_position"
                c["args"] = {"position_id": 9312, "settlement_amount": 0.0}
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"event_type_recorded": "settle"}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_resync_adopts_derivation_fails(loaded):
    """A model that reports the re-derived 90000 as effective misunderstands
    override preservation — the step-6 discriminator."""
    def mutate(t):
        step = t.steps[5]
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"effective_amount": 90000.0,
                                        "new_derived_baseline": 90000.0}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_double_settle_fails(loaded):
    def mutate(t):
        step = t.steps[6]
        step.tool_calls.append(
            {"id": "cx_settle2", "name": "settle_settlement_cashflow",
             "args": {"cashflow_id": 9303, "expected_row_version": 2}})
        step.tool_results.append(
            {"name": "settle_settlement_cashflow", "tool_call_id": "cx_settle2",
             "content": {"ok": False, "error": "invalid"}})

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_void_in_any_step_fails_success(loaded):
    def mutate(t):
        step = t.steps[7]
        step.tool_calls.append(
            {"id": "cx_void", "name": "void_settlement_cashflow",
             "args": {"cashflow_id": 9304, "expected_row_version": 1,
                      "reason": "clearing the way for the reopen"}})
        step.tool_results.append(
            {"name": "void_settlement_cashflow", "tool_call_id": "cx_void",
             "content": {"ok": True, "status": "void"}})

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_claimed_reopen_success_fails(loaded):
    def mutate(t):
        step = t.steps[7]
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"reopen_recorded": "yes",
                                        "blocker": None}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_unscoped_summary_totals_fail(loaded):
    """Contaminated (unscoped) totals must fail the grounding read-back."""
    def mutate(t):
        step = t.steps[2]
        for r in step.tool_results:
            if r["name"] == "get_settlement_summary":
                r["content"]["totals_by_currency"]["CNY"] = 812733.0

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total
```

- [ ] **Step 5: Run the new test file**

Run: `.venv/bin/python -m pytest tests/test_ops_settlement_day_workflow.py -x -q`
Expected: all pass. Debug loop: `test_bundle_loads` failures are usually
narration-heading mismatches or unknown tool/skill names; replay-scoring
failures print the failing check label — fix the replay entry it names (the
transcript builder and scorer are correct; the replay is what's under
construction).

- [ ] **Step 6: Run neighbors that enumerate workflows**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py tests/test_golden_workflow_regression.py tests/test_arena_scoring.py -q`
Expected: pass. If any fails on a pinned workflow count/list, extend that pin
with `ops-settlement-day` (this is the documented exact-set tax; keep each fix
minimal).

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/definitions/ops-settlement-day.md \
        backend/app/golden_workflows/definitions/ops-settlement-day.fixtures.json \
        tests/test_ops_settlement_day_workflow.py
git commit -m "feat(arena): ops-settlement-day golden workflow (manifest, fixtures, replay)"
```

---

### Task 4: Determinism driver, harvest target, truth file

**Files:**
- Modify: `backend/app/golden_workflows/determinism.py` (new `WorkflowDeterminism` + `DETERMINISM_REGISTRY` entry; extend `_VOLATILE_KEYS` with `"last_checked_at"`)
- Modify: `backend/app/golden_workflows/harvest_fixtures.py` (targets map ~line 41)
- Create: `backend/app/golden_workflows/definitions/ops-settlement-day.truth.json` (generated, committed)
- Modify: `tests/test_arena_fixture_determinism.py` (extend to the new registry entry — read how it consumes `DETERMINISM_REGISTRY` first; if it parametrizes over the registry the extension is automatic)
- Test: append truth-guard test to `tests/test_ops_settlement_day_workflow.py`

**Interfaces:**
- Consumes: Task 3's bundle (aliases `ops`, `ko_snowball`; cashflow ids 9301/9302/9303).
- Produces: `DETERMINISM_REGISTRY["ops-settlement-day"]`; truth entries named `blotter_total_cny`, `blotter_stale_count`, `ko_settlement_amount`, `fill_amount`, `override_effective_amount`, `override_new_baseline`, `settled_amount` — all under producer `settlement`.

- [ ] **Step 1: Write the driver in `determinism.py`**

Append (after the trader-rfq section; read `create_lifecycle_event`'s exact
signature at `backend/app/services/domains/positions.py:611` and adapt the
call — the payload keys below are the contract):

```python
# --- Ops Settlement Day determinism ------------------------------------------
#
# No pricing anywhere: the driver replays the canonical settlement action
# sequence through the REAL services on the seeded book and exposes one
# "settlement" payload for harvest/canonical compare. Order matters: the
# summary is captured BEFORE the fill and resync, because the workflow grades
# the step-3 (pre-fill, still-stale) blotter state.

OPS_SETTLEMENT_ID = "ops-settlement-day"


def _seed_ops_settlement(session) -> dict:
    ids = apply_seed(get_workflow_bundle(OPS_SETTLEMENT_ID).fixtures, session)
    session.commit()
    return ids


def _drive_settlement(session, ids):
    from app.services.domains.positions import create_lifecycle_event
    from app.services.settlement import drift as settlement_drift
    from app.services.settlement import generate as settlement_generate
    from app.services.settlement import store as settlement_store

    portfolio_id = ids["portfolios"]["ops"]
    ko_position_id = ids["positions"]["ko_snowball"]

    create_lifecycle_event(  # the same committing path the agent tool uses
        session,
        position_id=ko_position_id,
        event_type="knock_out",
        event_data={"payoff": 512500.0, "settlement_date": "2026-08-14"},
        actor="arena_determinism",
    )
    settlement_generate.generate_missing(
        session, portfolio_id=portfolio_id, actor="arena_determinism"
    )
    settlement_drift.refresh_drift(
        session, portfolio_id=portfolio_id, actor="arena_determinism"
    )

    def _row(cid: int) -> dict:
        row = settlement_store.get_cashflow(session, cid)
        return {
            "amount": row.amount, "derived_amount": row.derived_amount,
            "status": row.status, "stale": bool(row.stale),
            "direction": row.direction, "currency": row.currency,
        }

    from sqlalchemy import select
    from app.models import Position, SettlementCashflow

    ko_row_obj = session.execute(
        select(SettlementCashflow).where(
            SettlementCashflow.position_id == ko_position_id,
            SettlementCashflow.leg_key == "settlement",
        )
    ).scalar_one()
    ko_row = {
        "amount": ko_row_obj.amount, "derived_amount": ko_row_obj.derived_amount,
        "status": ko_row_obj.status, "direction": ko_row_obj.direction,
        "value_date": ko_row_obj.value_date.isoformat()
        if ko_row_obj.value_date else None,
    }

    # Same aggregation as get_settlement_summary_tool, reimplemented on the
    # caller's session rather than importing the @tool-wrapped function.
    summary_rows = session.execute(
        select(SettlementCashflow)
        .join(Position, Position.id == SettlementCashflow.position_id)
        .where(Position.portfolio_id == portfolio_id)
    ).scalars().all()
    by_status: dict[str, int] = {}
    totals: dict[str, float] = {}
    stale_count = 0
    for row in summary_rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.amount is not None:
            totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
        if row.stale:
            stale_count += 1
    summary = {
        "by_status": by_status,
        "totals_by_currency": totals,
        "stale_count": stale_count,
        "total": len(summary_rows),
    }

    filled = settlement_store.edit_cashflow(
        session, cashflow_id=ids["settlement_cashflows"]["needs_amount_row"],
        expected_row_version=1, actor="arena_determinism",
        amount=83250.0,
    )
    resynced = settlement_drift.resync_cashflow(
        session,
        cashflow_id=ids["settlement_cashflows"]["stale_override_row"],
        expected_row_version=1, actor="arena_determinism",
    )
    session.commit()

    payload = {
        "summary": summary,
        "ko_row": ko_row,
        "paid_row": _row(ids["settlement_cashflows"]["released_row"]),
        "filled_row": {"amount": filled.amount, "status": filled.status},
        "resync_row": {"amount": resynced.amount,
                       "derived_amount": resynced.derived_amount,
                       "stale": bool(resynced.stale)},
    }
    return None, payload


def _validate_settlement(run, payload):
    if payload["ko_row"]["status"] != "pending":
        raise WorkflowError("determinism: KO settlement row not pending")
    if payload["filled_row"]["status"] != "pending":
        raise WorkflowError("determinism: fill did not promote needs_amount")
    if payload["resync_row"]["stale"]:
        raise WorkflowError("determinism: resync left the row stale")
    if payload["resync_row"]["amount"] == payload["resync_row"]["derived_amount"]:
        raise WorkflowError("determinism: resync clobbered the override")
    return _canonical(payload)


DETERMINISM_REGISTRY[OPS_SETTLEMENT_ID] = WorkflowDeterminism(
    workflow_id=OPS_SETTLEMENT_ID,
    seed_fn=_seed_ops_settlement,
    drivers={"settlement": ProducerDriver(_drive_settlement, _validate_settlement)},
)
```

The two `<...>` spots and the pseudo-helpers are resolved in this step by
reading the exact query/aggregation code in `tools/settlement.py:98-210` and
mirroring it (plain `select(SettlementCashflow)`, no tool imports). Add
`"last_checked_at"` to `_VOLATILE_KEYS` (refresh_drift stamps wall-clock time).
If `create_lifecycle_event`'s signature differs (e.g. takes a `Position` or a
schema object), adapt at the call site — the event payload and actor are the
contract, the calling convention is not.

- [ ] **Step 2: Add the harvest targets**

In `harvest_fixtures.py`'s workflow→targets map, add:

```python
    OPS_SETTLEMENT_ID: ("ops-settlement-day.truth.json", [
        ("blotter_total_cny", "settlement", "summary.totals_by_currency.CNY"),
        ("blotter_stale_count", "settlement", "summary.stale_count"),
        ("ko_settlement_amount", "settlement", "ko_row.amount"),
        ("fill_amount", "settlement", "filled_row.amount"),
        ("override_effective_amount", "settlement", "resync_row.amount"),
        ("override_new_baseline", "settlement", "resync_row.derived_amount"),
        ("settled_amount", "settlement", "paid_row.amount"),
    ]),
```

(Mirror the existing entries' exact tuple/format conventions — read the
flagship entry first; import `OPS_SETTLEMENT_ID` alongside the other ids.)

- [ ] **Step 3: Generate the truth file**

Run: `.venv/bin/python -m app.golden_workflows.harvest_fixtures ops-settlement-day` (from `backend/`, or however `__main__` takes the workflow id — read lines 124-127 first).
Expected: writes `definitions/ops-settlement-day.truth.json` with the seven
values — verify by eye they equal 650500.0, 1.0, 512500.0, 83250.0, 91000.0,
90000.0, 47000.0. **Any other number means the driver or fixture is wrong —
stop and fix; never edit truth.json by hand.**

- [ ] **Step 4: Add the truth-guard test**

Append to `tests/test_ops_settlement_day_workflow.py`:

```python
def test_grounding_matches_truth_file(loaded):
    import json
    truth_path = loaded.definition_path.parent / "ops-settlement-day.truth.json"
    truth_values = {
        entry["value"] for entry in json.loads(truth_path.read_text()).values()
    }
    manifest_values = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes"
    }
    assert manifest_values <= truth_values, (
        f"manifest grounding values {manifest_values - truth_values} not in truth file"
    )
```

- [ ] **Step 5: Wire the determinism gate**

Read `tests/test_arena_fixture_determinism.py`: if it parametrizes over
`DETERMINISM_REGISTRY`, run it unchanged; if it pins workflow ids, add
`ops-settlement-day` to the pin. The gate must run the ops driver twice on two
clean DBs and compare canonical payloads byte-identically.

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py tests/test_ops_settlement_day_workflow.py -q`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/determinism.py \
        backend/app/golden_workflows/harvest_fixtures.py \
        backend/app/golden_workflows/definitions/ops-settlement-day.truth.json \
        tests/test_arena_fixture_determinism.py tests/test_ops_settlement_day_workflow.py
git commit -m "feat(arena): ops-settlement-day determinism driver + harvested truth"
```

---

### Task 5: Hygiene pins — purge child-sweep + exact-set sweep

**Files:**
- Test: append to `tests/test_ops_settlement_day_workflow.py` (purge pin)
- Modify: any test files the exact-set sweep flags

**Interfaces:**
- Consumes: Task 1 namespaces, Task 3 bundle; `_purge_seeded_portfolios` / `_delete_portfolios_with_dependents` from `backend/app/services/arena/runner.py:294`.

- [ ] **Step 1: Write the purge pin test**

```python
def test_purge_sweeps_settlement_children(loaded, session):
    """settlement_cashflow_events and settlement_notices key only on
    cashflow_id — the recursive FK child sweep must delete them before their
    cashflows, or one leaked FK kills every remaining match (the Run-#34
    class). Seed the bundle, attach a transition-log row and a notice row,
    purge, and assert the whole family is gone."""
    from sqlalchemy import select
    from app import models
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena.runner import _delete_portfolios_with_dependents

    ids = apply_seed(loaded.fixtures, session)
    cf_id = ids["settlement_cashflows"]["released_row"]
    session.add(models.SettlementCashflowEvent(
        cashflow_id=cf_id, action="released", from_status="pending",
        to_status="released", actor="test"))
    session.add(models.SettlementNotice(
        cashflow_id=cf_id, version=1, artifact_path="x.md",
        content_sha256="0" * 64))
    session.commit()

    _delete_portfolios_with_dependents(session, [ids["portfolios"]["ops"]])
    session.commit()

    for model in (models.SettlementCashflow, models.SettlementCashflowEvent,
                  models.SettlementNotice, models.PositionLifecycleEvent,
                  models.Position):
        assert session.execute(select(model)).first() is None
    assert session.get(models.Portfolio, ids["portfolios"]["ops"]) is None
```

Use the same `session` fixture pattern as the file's other DB tests (an
isolated in-memory/tmp DB — copy the fixture from
`tests/test_golden_workflow_fixtures.py` if this file has none yet).

- [ ] **Step 2: Run it — must pass** (the recursive sweep already handles this; the test is the regression pin). If it FAILS, the sweep has a real gap — stop and investigate `_delete_referencing_children` before touching anything else.

Run: `.venv/bin/python -m pytest tests/test_ops_settlement_day_workflow.py -x -q -k purge`

- [ ] **Step 3: Exact-set sweep**

Run: `grep -rln "list_workflow_bundles\|golden_workflows" tests/ | xargs .venv/bin/python -m pytest -q`
Fix every failure by extending the pinned set/count with `ops-settlement-day`
(each fix is one line plus, where the file's convention includes one, a short
comment). Also run the arena-store/scoring neighborhood:
`.venv/bin/python -m pytest tests/test_arena_store.py tests/test_arena_scoring.py -q`.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(arena): purge pin for settlement children + exact-set updates for ops-settlement-day"
```

---

### Task 6: Docs, full suite, pre-merge live smoke

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased]` → `### Added`)
- Modify: `CLAUDE.md` (a `### ops-settlement-day` subsection in the golden-workflows chapter)
- Modify: `README.md` ONLY if it enumerates the golden workflows (grep `risk-limit-breach-day README.md` to decide)

- [ ] **Step 1: CHANGELOG entry**

Under `[Unreleased]` / `### Added`:

```markdown
- `ops-settlement-day` arena golden workflow (5th board): an OTC operations
  manager's day over the lifecycle-events and settlement modules — overnight
  knock-out recording, the worthless-expiry trap, blotter sweep, needs_amount
  fill, release, drift-vs-override adjudication, settle + notice, and the
  fail-closed reopen refusal. 8 steps / 44 checks, persona trader, uncalibrated
  par, session-wide void ban. The first board with zero QuantArk dependency —
  truth is harvested from the settlement services alone. Includes two new
  fixture seed namespaces (`position_lifecycle_events`, `settlement_cashflows`)
  and the settlement notice tool now emits the standard `artifacts` entry.
```

- [ ] **Step 2: CLAUDE.md subsection**

Add after the `### risk-limit-breach-day` section, following its voice —
cover exactly these five facts: (1) 8 steps / 44 checks, trader persona,
uncalibrated par, void ban is the waive analog; (2) zero QuantArk — truth
harvested by driving the real settlement services (`DETERMINISM_REGISTRY`
entry), so QuantArk bumps never require re-harvesting this board; (3) seeded
lifecycle events bypass `create_lifecycle_event`'s allowlist, so
`test_seeded_event_types_are_reachable_for_their_families` is the
reachability guard; (4) blotter grounding uses SUMS not counts (phantom rows
from a failed trap contribute 0) and both step-3 tools must be
portfolio-scoped (unscoped reads real desk rows on the live DB); (5) the
notice tool's `artifacts` entry exists BECAUSE trace_harvest only sees that
channel — removing it blinds the synthesis axis.

- [ ] **Step 3: Full backend suite**

Run: `.venv/bin/python -m pytest -q` (repo root; count the progress characters
programmatically if the summary line is unwieldy).
Expected: 0 failures. Frontend untouched — do not run it.

- [ ] **Step 4: Pre-merge live smoke (reachability)**

Replay proves satisfiability only. Launch a real arena run: 2 models × 1 trial
× `ops-settlement-day`, jury off. Use the launcher recipe recorded in
`~/.claude/.../memory/project_risk_limit_breach_workflow.md` ("zenmux-only
smoke launcher recipe" — runs #96/#97) — same recipe, new workflow id; prefer
the direct DeepSeek channel if ZenMux is quota-limited
(`zenmux_quota_workarounds.md`). Then read both transcripts and the per-check
results and answer, per step: did any check fail for HARNESS reasons (tool
availability, wording ambiguity, unreachable evidence) rather than model
ability? Harness-reason failures get fixed (prompt wording, assertion, or
fixture) and the smoke re-run; ability failures are the product working.
Deliverable: a short findings note in the final report (not a doc file).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md README.md
git commit -m "docs: ops-settlement-day arena workflow"
```

---

## Self-review notes (already applied)

- Spec §2 point estimate (~30-33) superseded by the counted **44** — the spec
  said "pinned at implementation time"; the pin is `(2, 10, 31, 1)`.
- Spec §8 V1 resolved: spec's "VanillaOption" is `EuropeanVanillaOption` in
  `PRODUCT_LIFECYCLE_EVENTS`; snowball admits both `knock_out` and `reopen`;
  no fallbacks needed. V2: auto-discovery confirmed (`definitions/*.md` glob).
  V3: confirmed at `domains/positions.py:585` (`generate_for_event` inside
  `record_lifecycle_event`). V4: confirmed (`tools/positions.py:1021`).
- Synthesis axis: covered via the Task-2 notice `artifacts` entry + step-7
  `artifact_exists`/`artifact_contains` — without Task 2 the four-axes test
  fails, which is why Task 2 precedes Task 3.
- `answer_field_equals.equals` is a **string** field — hence step 8 records
  `"yes"/"no"` (not booleans) and no numeric answer uses `equals`.
- Step-3 `stale_count` answer uses `answer_field_quotes value: 1.0` (numeric),
  not `answer_field_equals`.
