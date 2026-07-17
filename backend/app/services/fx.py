"""FX rates: reproducible point-in-time conversion. The resolver picks the latest
rate with as_of_date <= valuation_date, deriving identity and inverse pairs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FxRate
from app.services.risk_currency import MONEY_METRIC_KEYS


@dataclass(frozen=True, slots=True)
class FxRateEvidence:
    """Exact point-in-time FX evidence used for one conversion."""

    base_currency: str
    quote_currency: str
    rate: float
    as_of: datetime
    fx_rate_id: int | None
    is_inverse: bool
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "fx_rate_id": self.fx_rate_id,
            "is_inverse": self.is_inverse,
            "rate": self.rate,
            "as_of": self.as_of.isoformat(),
            "source": self.source,
        }


def parse_fx_pair_symbol(symbol: str) -> tuple[str, str] | None:
    cleaned = (symbol or "").strip().upper()
    separator = "/" if "/" in cleaned else None
    if separator is None and len(cleaned) == 6 and cleaned.isalpha():
        return cleaned[:3], cleaned[3:]
    if separator is None:
        return None
    base, quote = [part.strip().upper() for part in cleaned.split(separator, 1)]
    if len(base) != 3 or len(quote) != 3 or not base.isalpha() or not quote.isalpha():
        return None
    return base, quote


def _direct_rate_row(
    session: Session,
    base: str,
    quote: str,
    as_of: datetime,
) -> FxRate | None:
    stmt = (
        select(FxRate)
        .where(
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
            FxRate.as_of_date <= as_of,
        )
        .order_by(FxRate.as_of_date.desc(), FxRate.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def fx_rate_evidence_as_of(
    session: Session,
    base: str,
    quote: str,
    as_of: datetime,
) -> FxRateEvidence | None:
    """Resolve a rate and the exact row/direction used for the conversion."""
    normalized_base = str(base).strip().upper()
    normalized_quote = str(quote).strip().upper()
    if normalized_base == normalized_quote:
        return FxRateEvidence(
            base_currency=normalized_base,
            quote_currency=normalized_quote,
            rate=1.0,
            as_of=as_of,
            fx_rate_id=None,
            is_inverse=False,
            source="identity",
        )
    direct = _direct_rate_row(
        session,
        normalized_base,
        normalized_quote,
        as_of,
    )
    if direct is not None:
        return FxRateEvidence(
            base_currency=normalized_base,
            quote_currency=normalized_quote,
            rate=float(direct.rate),
            as_of=direct.as_of_date,
            fx_rate_id=direct.id,
            is_inverse=False,
            source=direct.source,
        )
    inverse = _direct_rate_row(
        session,
        normalized_quote,
        normalized_base,
        as_of,
    )
    if inverse is None or not float(inverse.rate):
        return None
    return FxRateEvidence(
        base_currency=normalized_base,
        quote_currency=normalized_quote,
        rate=1.0 / float(inverse.rate),
        as_of=inverse.as_of_date,
        fx_rate_id=inverse.id,
        is_inverse=True,
        source=inverse.source,
    )


def fx_rate_as_of(
    session: Session, base: str, quote: str, as_of: datetime
) -> float | None:
    """1 unit of `base` in units of `quote`, as of `as_of` (latest <= as_of).
    Returns 1.0 for identity, 1/rate for the inverse pair, None if unknown."""
    evidence = fx_rate_evidence_as_of(session, base, quote, as_of)
    return evidence.rate if evidence is not None else None


def convert_risk_currency(
    by_currency: dict[str, dict[str, float]],
    target_currency: str,
    rate_lookup: Callable[[str, str], float | None],
) -> dict[str, Any]:
    """Collapse the per-currency money buckets into `target_currency`.

    rate_lookup(base, quote) -> float | None. Pure: no DB here. Currencies whose
    rate is unknown are skipped and reported in `missing`. Money metrics are
    FX-scaled; position_count is summed straight."""
    totals: dict[str, float] = {key: 0.0 for key in MONEY_METRIC_KEYS}
    totals["position_count"] = 0.0
    fx_rates_used: dict[str, float] = {}
    missing: list[str] = []

    for ccy, bucket in by_currency.items():
        rate = rate_lookup(ccy, target_currency)
        if rate is None:
            missing.append(f"{ccy}->{target_currency}")
            continue
        if ccy != target_currency:
            fx_rates_used[f"{ccy}->{target_currency}"] = rate
        for key in MONEY_METRIC_KEYS:
            totals[key] += float(bucket.get(key, 0.0) or 0.0) * rate
        totals["position_count"] += float(bucket.get("position_count", 0) or 0)

    return {
        "totals": totals,
        "fx_rates_used": fx_rates_used,
        "missing": missing,
    }


def list_fx_rates(session: Session) -> list[FxRate]:
    stmt = select(FxRate).order_by(FxRate.as_of_date.desc(), FxRate.id.desc())
    return list(session.execute(stmt).scalars().all())


def create_fx_rate(session: Session, payload) -> FxRate:
    row = FxRate(
        base_currency=payload.base_currency,
        quote_currency=payload.quote_currency,
        rate=float(payload.rate),
        as_of_date=payload.as_of_date,
        source=getattr(payload, "source", "manual"),
        pricing_parameter_profile_id=getattr(payload, "pricing_parameter_profile_id", None),
    )
    session.add(row)
    session.flush()
    return row


def delete_fx_rate(session: Session, fx_rate_id: int) -> None:
    row = session.get(FxRate, fx_rate_id)
    if row is not None:
        session.delete(row)


def _fx_quote_rate(quotes, base: str, quote: str) -> float | None:
    """Resolve `1 base = N quote` from a CFETS spot-quote frame.

    `quotes` is `ak.fx_spot_quote()`-shaped: columns 货币对 (e.g. 'USD/CNY',
    '100JPY/CNY'), 买报价 (bid), 卖报价 (ask). Returns the bid/ask mid, dividing
    out akshare's per-100-unit convention (the '100JPY/...' style), or None if the
    pair is absent or quoted NaN."""
    by_pair = {str(p): row for p, row in zip(quotes["货币对"], quotes.to_dict("records"))}
    for key, scale in ((f"{base}/{quote}", 1.0), (f"100{base}/{quote}", 100.0)):
        row = by_pair.get(key)
        if row is None:
            continue
        bid = row.get("买报价")
        ask = row.get("卖报价")
        try:
            mid = (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            return None
        if mid != mid:  # NaN guard
            return None
        return mid / scale
    return None


def fetch_akshare_fx_rate(base_currency: str, quote_currency: str) -> float:
    """Fetch a spot FX rate via akshare's CFETS feed (`ak.fx_spot_quote()`).

    Returns `1 base_currency = N quote_currency`, using the bid/ask mid. Handles
    the inverse pair (only QUOTE/BASE listed -> 1/rate) and akshare's per-100-unit
    convention for currencies like JPY. Raises ValueError when neither direction
    is available."""
    import akshare as ak

    base = base_currency.strip().upper()
    quote = quote_currency.strip().upper()
    if base == quote:
        return 1.0

    quotes = ak.fx_spot_quote()
    direct = _fx_quote_rate(quotes, base, quote)
    if direct is not None:
        return direct
    inverse = _fx_quote_rate(quotes, quote, base)
    if inverse:
        return 1.0 / inverse
    raise ValueError(f"No akshare FX quote for {base}/{quote}")
