"""Live smoke: the merged confirmation family cross-check against real Jev.

The golden confirmation set (backend/app/golden_workflows/documents). Jev is
never shown the extractor's family — its state is only the anchor and the
segment's text layer — so ONE live answer per trade serves both arms:
  correct:   the extractor picked the true family  -> want "agree"
  near_miss: the extractor picked a plausible wrong family -> want "disagree"
decide() is the shipped decision table, applied to the same answer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.confirmations.extract import extract_document
from app.services.confirmations.family_check import check_family, decide
from app.services.confirmations.llm import TradeSegment
from app.tools.product_term_schema import _SCHEMA_FAMILIES

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
REPO = Path(__file__).resolve().parents[4]
D = REPO / "backend/app/golden_workflows/documents"

# (doc, pages, anchor, true_family, near_miss_family, what the near miss means)
TRADES = [
    ("conf-01-american-call-aapl.pdf", [], "American call on Apple Inc. (AAPL)",
     "AmericanOption", "EuropeanVanillaOption", "exercise style misread"),
    ("conf-02-european-put-msft.docx", [], "European put on Microsoft (MSFT)",
     "EuropeanVanillaOption", "AmericanOption", "exercise style misread"),
    ("conf-03-multi-trade-sablefish.pdf", [2], "TRANSACTION 1 OF 3 - Ref. No: ARD-EQO-2026-04610",
     "EuropeanVanillaOption", "AmericanOption", "exercise style misread"),
    ("conf-03-multi-trade-sablefish.pdf", [3], "TRANSACTION 2 OF 3 - Ref. No: ARD-EQO-2026-04611",
     "EuropeanVanillaOption", "BarrierOption", "neighbouring trade's barrier bled in"),
    ("conf-03-multi-trade-sablefish.pdf", [4], "TRANSACTION 3 OF 3 - Ref. No: ARD-EQO-2026-04612",
     "BarrierOption", "EuropeanVanillaOption", "barrier missed"),
    ("conf-05-knockout-barrier-tsla.pdf", [], "Up-and-out knock-out call on Tesla (TSLA)",
     "BarrierOption", "EuropeanVanillaOption", "barrier missed"),
    ("conf-06-asian-average-spy.pdf", [], "Average-price call on SPY",
     "AsianOption", "EuropeanVanillaOption", "averaging missed"),
    ("conf-07-missing-initial-price-meta.pdf", [], "European call on Meta Platforms (META)",
     "EuropeanVanillaOption", "AmericanOption", "exercise style misread"),
]
# Scanned or mixed: no text layer for the whole segment -> never sent to Jev.
STRUCTURAL = [
    ("conf-04-scanned-call-googl.pdf", [], "Call on Alphabet (GOOGL)", "EuropeanVanillaOption"),
    ("conf-08-mixed-text-and-scan-amd.pdf", [1, 2], "Call on AMD", "EuropeanVanillaOption"),
    ("conf-09-amended-strike-nvda.pdf", [], "Call on NVIDIA (NVDA)", "EuropeanVanillaOption"),
    ("conf-10-ticked-barrier-amzn.pdf", [], "Barrier option on Amazon (AMZN)", "BarrierOption"),
    ("conf-11-faint-notional-orcl.pdf", [], "Call on Oracle (ORCL)", "EuropeanVanillaOption"),
]


def main():
    families = sorted(_SCHEMA_FAMILIES)
    out = []
    for rep in range(REPEATS):
        for doc, pages, anchor, truth, near, meaning in TRADES:
            content = extract_document(D / doc)
            fc = check_family(content, TradeSegment(family=truth, pages=pages, anchor=anchor),
                              schema_families=families)
            near_status = (decide(near, fc.jev_family, fc.confidence, families)[0]
                           if fc.jev_family is not None else fc.status)
            r = {"rep": rep, "doc": doc, "pages": pages, "truth": truth, "near_miss": near,
                 "meaning": meaning, "jev_family": fc.jev_family, "confidence": fc.confidence,
                 "top": fc.top, "status_correct": fc.status, "status_near_miss": near_status,
                 "reason": fc.reason}
            out.append(r)
            print(f"r{rep} {doc[:30]:30} p{pages or 'all'} truth={truth:22} jev={fc.jev_family} "
                  f"({fc.confidence}) correct->{fc.status:9} near({near})->{near_status} "
                  f"top={fc.top} {fc.reason or ''}", flush=True)
    for doc, pages, anchor, truth in STRUCTURAL:
        fc = check_family(extract_document(D / doc), TradeSegment(family=truth, pages=pages, anchor=anchor),
                          schema_families=families)
        out.append({"rep": 0, "doc": doc, "pages": pages, "truth": truth, "status_correct": fc.status,
                    "reason": fc.reason, "jev_family": fc.jev_family})
        print(f"   {doc[:30]:30} -> {fc.status} ({fc.reason})")
    with open("family_results.json", "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
