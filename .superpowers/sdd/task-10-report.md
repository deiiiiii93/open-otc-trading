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
