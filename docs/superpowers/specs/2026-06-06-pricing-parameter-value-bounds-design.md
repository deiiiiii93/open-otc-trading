# Pricing Parameter Value Bounds — Design (small)

**Date:** 2026-06-06
**Status:** Approved (follow-up to 2026-06-05-pricing-parameter-tools-design.md;
bounds question raised by code review, strictness chosen by user: sign/zero only)

## Decision

Agent pricing-parameter writes refuse only outright nonsense — no range
opinions:

- all of rate / dividend_yield / volatility must be **finite**
  (`math.isfinite`; Python's `json.loads` accepts `NaN`/`Infinity`, so
  non-finite values can really arrive from LLM tool args);
- **volatility must be > 0** when provided.

Anything else passes — explicitly including near-zero vol (live case:
0.0009 on 931059.CSI) and negative rates. Tests pin the accepted cases so
the looseness is deliberate, not accidental.

## Shape

- New `backend/app/services/domains/_validation.py`:
  `invalid_param_reason(field: str, value: Any) -> str | None` →
  `"not_finite"` | `"must_be_positive"` (vol ≤ 0) | `None`. Shared because
  bounds are policy (one home, no drift) — unlike the deliberately
  per-module `_session_scope` plumbing.
- `domains/pricing_profiles._validate_row_inputs` (covers `create_profile`
  AND `upsert_rows`): after the `empty_row` check, collect offenders →
  `DomainWriteError("invalid_value", {"rows": [{"row_index": i,
  "field": f, "reason": r}, ...]})`.
- `domains/assumptions.set_instrument_defaults`: check `provided` →
  `DomainWriteError("invalid_value", {"fields": {field: reason}})`.
- `_ROWS_DESCRIPTION` in `tools/pricing_profiles.py` gains
  "volatility must be > 0".
- xlsx importer untouched (human path, out of scope).

## Tests (TDD, existing files)

`tests/test_pricing_profile_writes.py`: vol=0 refused; vol=-0.5 refused;
rate=inf refused (detail names row/field/reason); vol=0.0009 and
rate=-0.02 accepted. Same upsert path. `tests/test_assumptions_domain.py`:
vol=0 / nan refused on `set_instrument_defaults`; refusal precedes
ensure-create (nothing persisted).

## Out of scope

Range bounds (percent-confusion guard) — offered, declined; revisit only
if bad data shows up in runs.
