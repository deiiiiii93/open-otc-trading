"""What a match cost: token totals and a list-price USD estimate.

Every LLM call's metered usage is harvested into the transcript (see
``trace_harvest._llm_usage``). This module folds it into one block per match and
prices it against the vendored ``config/model_pricing.json`` — ZenMux's list
prices, refreshed by ``scripts/refresh_model_pricing.py``.

Priced at match time and stored with the snapshot's ``fetched_at`` and
``sha256``: a later price change must not re-cost a finished board.

Three honesty rules:

- **A list price is an estimate.** The billed amount comes from the gateway's
  billing API, keyed by the ``generation_id`` each call records;
  ``generation_ids`` says how many calls can be reconciled that way.
- **An ambiguous rate is a range, not a guess.** When the list carries
  unconditioned entries that disagree (deepseek-v4.1-flash: 0.075 and 0.15 per
  1M prompt tokens — a discount window the list does not describe), the block
  reports ``usd_low``/``usd_high`` and leaves ``usd`` null.
- **Unpriced is not free.** A call whose model is missing from the snapshot is
  counted in ``unpriced_calls`` and excluded from the dollar figures, which are
  then a floor; ``usd`` is null so nobody reads a partial sum as the cost.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# parents: [0] arena, [1] services, [2] app, [3] backend, [4] repo root
_SNAPSHOT = Path(__file__).resolve().parents[4] / "config" / "model_pricing.json"
_TOKEN_KEYS = ("input", "cache_read", "cache_write", "output", "reasoning")


@lru_cache(maxsize=1)
def pricing_snapshot() -> dict[str, Any]:
    try:
        raw = _SNAPSHOT.read_bytes()
    except OSError:
        return {"models": {}, "fetched_at": None, "sha256": None}
    data = json.loads(raw)
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    return data


def _rate(tiers: list[dict] | None, prompt_tokens: int) -> tuple[float, float] | None:
    """(low, high) USD per 1M tokens for a request of *prompt_tokens*.

    The highest tier whose threshold the prompt reaches applies to the whole
    request. Several entries at that tier with different values → the range.
    """
    if not tiers:
        return None
    reached = [t for t in tiers if prompt_tokens >= t["min_prompt_ktok"] * 1000]
    if not reached:
        return None
    top = max(t["min_prompt_ktok"] for t in reached)
    values = [t["usd_per_mtok"] for t in reached if t["min_prompt_ktok"] == top]
    return min(values), max(values)


def price_call(call: dict, rates: dict) -> tuple[float, float] | None:
    """(low, high) USD for one call, or None if the model has no usable rates."""
    n_in = int(call.get("input") or 0)
    prompt = _rate(rates.get("prompt"), n_in)
    completion = _rate(rates.get("completion"), n_in)
    if prompt is None or completion is None:
        return None
    # A route that lists no cache rate bills cached tokens as ordinary prompt.
    cache_read = _rate(rates.get("input_cache_read"), n_in) or prompt
    cache_write = _rate(rates.get("input_cache_write"), n_in) or prompt
    cached = int(call.get("cache_read") or 0)
    written = int(call.get("cache_write") or 0)
    uncached = max(n_in - cached - written, 0)
    out = int(call.get("output") or 0)
    return tuple(  # type: ignore[return-value]
        (uncached * prompt[i] + cached * cache_read[i] + written * cache_write[i]
         + out * completion[i]) / 1_000_000
        for i in (0, 1)
    )


def _model_key(name: str | None) -> str | None:
    # The gateway reports the bare id; a ":provider" pin is not part of the price.
    return name.split(":", 1)[0] if name else None


def usage_block(transcript) -> dict[str, Any] | None:
    """Token totals and list-price cost for one match, or None if unmetered.

    ``None`` means NOT MEASURED — a transcript from before metering, or a replay
    fixture — and must never be read as a free match.
    """
    calls = [c for s in transcript.steps for c in (getattr(s, "usage", None) or [])]
    if not calls:
        return None
    snap = pricing_snapshot()
    models: dict = snap.get("models") or {}
    totals = {k: sum(int(c.get(k) or 0) for c in calls) for k in _TOKEN_KEYS}
    by_model: dict[str, dict] = {}
    low = high = 0.0
    unpriced = 0
    for c in calls:
        key = _model_key(c.get("model")) or "unknown"
        row = by_model.setdefault(
            key, {"calls": 0, **{k: 0 for k in _TOKEN_KEYS},
                  "usd_low": 0.0, "usd_high": 0.0, "priced": key in models})
        row["calls"] += 1
        for k in _TOKEN_KEYS:
            row[k] += int(c.get(k) or 0)
        priced = price_call(c, models[key]) if key in models else None
        if priced is None:
            unpriced += 1
            row["priced"] = False
            continue
        row["usd_low"] += priced[0]
        row["usd_high"] += priced[1]
        low += priced[0]
        high += priced[1]
    for row in by_model.values():
        row["usd_low"] = round(row["usd_low"], 6)
        row["usd_high"] = round(row["usd_high"], 6)
    low, high = round(low, 6), round(high, 6)
    return {
        "calls": len(calls),
        **totals,
        "total": totals["input"] + totals["output"],
        "generation_ids": sum(1 for c in calls if c.get("generation_id")),
        "usd": low if (low == high and not unpriced) else None,
        "usd_low": low,
        "usd_high": high,
        "unpriced_calls": unpriced,
        "by_model": by_model,
        "pricing": {"source": snap.get("source"), "fetched_at": snap.get("fetched_at"),
                    "sha256": snap.get("sha256")},
    }


def fold_usage(blocks: list[dict | None]) -> dict[str, Any] | None:
    """Sum per-trial usage blocks; None if no trial was metered.

    Summed, like truncation: the reader wants what the whole cell cost.
    ``trials_measured`` against ``trials_total`` shows partial coverage, and the
    dollar figures cover only the measured trials.
    """
    measured = [b for b in blocks if isinstance(b, dict)]
    if not measured:
        return None
    keys = ("calls", *_TOKEN_KEYS, "total", "generation_ids", "unpriced_calls")
    out: dict[str, Any] = {k: sum(int(b.get(k) or 0) for b in measured) for k in keys}
    out["usd_low"] = round(sum(float(b.get("usd_low") or 0) for b in measured), 6)
    out["usd_high"] = round(sum(float(b.get("usd_high") or 0) for b in measured), 6)
    exact = all(b.get("usd") is not None for b in measured)
    out["usd"] = out["usd_low"] if exact else None
    out["trials_measured"] = len(measured)
    out["trials_total"] = len(blocks)
    out["pricing"] = measured[0].get("pricing")
    return out
