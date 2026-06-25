# Task 10 Report: Fail-Closed Approval-Card Builder

## Real payload keys found in hitl.py + tool schemas

`AgentActionProposal.payload` = raw args dict from `ActionRequest["args"]`.
The brief's logical-key map uses aliases that don't match real parameter names.
Mapping from brief's logical → actual tool parameter name:

| Tool | Brief logical keys | Actual payload keys used |
|---|---|---|
| book_position | instrument, side, notional, terms, portfolio, preview | product (dict with family/underlying/terms), quantity, portfolio_id |
| book_hedge | instrument, side, notional, linked_ref, preview | portfolio_id, underlying, strategy, spot, legs |
| quote_rfq | rfq_id, underlying, structure, size, level, preview | rfq_id, quote_mode, created_by, product |
| submit_rfq_for_approval | rfq_id, summary, approver_step | rfq_id, actor |
| approve_rfq | rfq_id, summary, state | rfq_id, approver |
| reject_rfq | rfq_id, summary, state | rfq_id, approver |
| release_rfq | rfq_id, counterparty, final_terms | rfq_id, actor |
| __cost_preview__ | tool, estimated_cost, scope | estimated_cost, scope |

REQUIRED_FIELDS uses real keys (dropping the "tool" field since it maps to tool_name on AgentActionProposal, not the payload dict).

## TDD RED → GREEN

- RED: `ModuleNotFoundError: No module named 'app.services.gateway.cards'` — confirmed.
- GREEN: all 30 tests pass after implementing cards.py.
- One test fix needed: `Settings` is a frozen dataclass, not a pydantic model — used `dataclasses.replace()` instead of `model_dump()`.

## Files

- Created: `backend/app/services/gateway/cards.py`
- Created: `tests/gateway/test_cards.py`

## Test results

30 tests in test_cards.py, all passing. Full gateway suite: 101 passed, 0 failed.

## Concerns / Notes

1. `REQUIRED_FIELDS["quote_rfq"]` uses `["rfq_id", "quote_mode", "created_by"]` — the brief had more elaborate keys (structure, size, level) that don't exist as actual tool parameters. The real QuoteRfqInput is thin; main quoting parameters come via nested `product` dict. REQUIRED_FIELDS only enforces `rfq_id, quote_mode, created_by` as a minimum viable content check.

2. `IRREVERSIBLE` in hitl.py marks `reject_rfq` as "irreversible" but the brief spec has it NOT in IRREVERSIBLE. Kept the brief's IRREVERSIBLE set (book_position, book_hedge, approve_rfq, release_rfq) — this is the gateway's opinion, which can differ from the HITL risk classification.

3. Oversized truncation threshold set to 200 chars per field. This is a reasonable default and keeps cards readable in IM clients with character limits.

4. The brief's `__cost_preview__` key is kept for forward compatibility but no tools currently produce it.

---

## Fix pass (review feedback)

### Important: fail-OPEN hole — None/empty values treated as present
The missing-field guard used `f not in payload`, so a payload with
`quantity: None` or `quantity: ""` passed the check and made an IRREVERSIBLE
`book_position` APPROVABLE with an undefined notional. Fixed by adding
`_is_missing(payload, field)`: a required field counts as missing when absent,
`None`, an empty/whitespace-only string, OR an empty dict (a `product: {}`
carries no decision-relevant content). `0`/`False`/empty list are NOT treated
as missing — a legitimate zero must not silently void approval.
Guard is now `missing = [f for f in required if _is_missing(payload, f)]`.

New tests (class `TestBuildApprovalCardEmptyValue`):
- `quantity=None` → non-approvable (0 actions)
- `quantity=""` → non-approvable
- `quantity="   "` (whitespace) → non-approvable
- `product={}` → non-approvable
- present non-empty value → approvable (2 actions)
- `quantity=0` → still approvable (zero is legitimate)
- IRREVERSIBLE `approve_rfq` with `rfq_id=None` → non-approvable

### Minor: book_hedge omitted risk_run_id
Added `"risk_run_id"` to `REQUIRED_FIELDS["book_hedge"]` (decision-relevant
source risk run for an irreversible hedge). Full payload already carried it.
New tests (class `TestBuildApprovalCardBookHedgeRiskRunId`): present in
REQUIRED_FIELDS, full payload approvable, missing risk_run_id → non-approvable,
risk_run_id=None → non-approvable.

### Not changed
`IRREVERSIBLE` left exactly as the brief defined (reject_rfq excluded), per
coordinator instruction.

### Test command + result
    python -m pytest tests/gateway/test_cards.py -q
    => 41 passed in 1.24s   (was 30; +11 new tests)
Full gateway suite: `python -m pytest tests/gateway/` => 112 passed.
