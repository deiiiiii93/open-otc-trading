# Synthetic confirmation samples

Test documents for the **trade confirmation → book** module. Modelled on the
structure and vocabulary of the ISDA 2002 Equity Derivatives Definitions
"Confirmation of Share Option Transaction" template in
`docs/confirmations/equity-share-option.pdf` — the one file in that directory
still gitignored, because it is third-party.

**This corpus is TRACKED and load-bearing.** It grades the
`confirmation-desk-day` arena board, and a benchmark cannot rest on
per-environment files. Regeneration must reproduce the same page text and
rendered pixels; a diff means a rendering dependency moved, and every graded
constant has to be re-verified before trusting a board built on it. Guarded by
`tests/test_confirmation_documents.py`.

**Everything here is fictional.** The dealer (*Ardsley Global Markets, N.A.*), every
counterparty, every LEI, reference number, and contact detail is invented; each page
is footed `SYNTHETIC TEST DOCUMENT - NOT A REAL TRADE CONFIRMATION`. Only the
underlying tickers are real, because the desk prices against them. These are fixtures —
do not treat any of them as a record of a trade.

Regenerate with the generator described at the bottom of this file.

## The set

| File | Fmt | Mode | Family | Expected | Exercises |
|---|---|---|---|---|---|
| `conf-01-american-call-aapl.pdf` | PDF | text | `AmericanOption` | valid | Happy path; `Option Style: American` must pick the American family |
| `conf-02-european-put-msft.docx` | DOCX | text | `EuropeanVanillaOption` | valid | DOCX path + **table-cell** extraction; PUT |
| `conf-03-multi-trade-sablefish.pdf` | PDF | text | 2× vanilla + `BarrierOption` | valid ×3 | **Segmentation** — three Transactions in one file |
| `conf-04-scanned-call-googl.pdf` | PDF | vision | `EuropeanVanillaOption` | valid | **Vision path** — image-only page, skewed/grainy scan |
| `conf-05-knockout-barrier-tsla.pdf` | PDF | text | `BarrierOption` | valid | `UP_OUT` + rebate + `Option Entitlement: 100` → `contract_multiplier` |
| `conf-06-asian-average-spy.pdf` | PDF | text | `AsianOption` | valid | `maturity_years` (not `exercise_date`) + `MONTHLY` averaging |
| `conf-07-missing-initial-price-meta.pdf` | PDF | text | `EuropeanVanillaOption` | **invalid** (usually — see below) | Template-faithful doc with **no Initial Price** |
| `conf-08-mixed-text-and-scan-amd.pdf` | PDF | mixed | `EuropeanVanillaOption` | valid | **`mixed` mode** — text p1 + scanned p2 carrying the priced terms |

All three `extract_mode` values (`text` / `vision` / `mixed`) are covered.

Reference numbers are unique per trade (`ARD-EQO-2026-0xxxx`) and become
`external_trade_id`, so re-uploading any file exercises the `already_booked`
idempotency pre-check.

## Why `conf-07` has no Initial Price

`initial_price` (the S0 fixing) is **required by every builder family** and is
explicitly "never invented" (`product_builders.py::_initial_price`) — but the ISDA
share-option template has no such line. Real structured-equity confirmations usually
do state an Initial Price, so seven of these fixtures include one; `conf-07` omits it
to exercise the review-first path (`invalid` → operator supplies the value → server
re-validates → book).

## Known behaviours these fixtures surface

Two findings from a live run of the real extractor over this set. Both are module
behaviours, not document defects — the fixtures exist to keep them visible.

1. **`conf-07`: the extractor sometimes substitutes the strike for the missing
   `initial_price`.** In **2 of 6** sampled runs it returned `initial_price = 780.0`
   — the strike — citing `"Strike Price: USD 780.00"` as the evidence quote, so the
   trade validated `valid` on a value that is not in the document. The other 4 runs
   correctly left the field `None`, and the trade landed `invalid` as intended. The
   builder's "never invented" guard only checks *presence*, so a filled field passes
   regardless of where it came from. The per-field evidence quote is what exposes it:
   an `initial_price` grounded in a quote reading `Strike Price:` is self-evidently
   wrong. That mismatch is worth reading in review, and is a candidate for an
   automatic check.

2. **`conf-08`: stage 1's page list is a hard filter on what stage 2 can see.**
   `_content_parts(content, segment.pages)` drops every page not listed. Stage 1
   returned `pages=[1]` instead of `[1, 2]` on **1 of 6** sampled runs; when it does,
   stage 2 never receives the scanned page, returns empty terms at `confidence 0.0`,
   and the trade lands `invalid` with no signal that page *selection* — rather than an
   incomplete document — was the cause. Given `pages=[1, 2]`, stage 2 extracted every
   term correctly 3 times out of 3.

## Regenerating

The generator is a standalone script (no repo dependency beyond `pypdf`, `Pillow`
and `python-docx`, all already required by the module):

```
.venv/bin/python backend/app/golden_workflows/documents/make_confirmations.py
```

Trade economics live in the `C1`…`C8` dicts near the bottom of that file; the
`Pdf` class renders the label/value confirmation grid, and `render_scan` produces the
degraded image pages.
