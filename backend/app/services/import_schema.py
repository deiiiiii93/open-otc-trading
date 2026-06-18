"""Single source of truth for the standardized English import templates.

Both the file *parsers* (``position_adapter``, ``market_input_workbooks``) and the
template *generator* (``import_templates``) import from this module, so the blank
template the user downloads can never drift from the columns the adapter reads.

Two templates are described here:

* **Positions** – the OTC trade book imported on the Positions page.
* **Pricing Parameters** – the per-trade r / q / vol / spot sheet imported on the
  Pricing Parameter page.

The column headers and the cell *enum vocabulary* (Buy/Sell, Call/Put, structure
names, Yes/No, ...) are both defined here. The adapter compares raw cell text
against the constants below, so translating a header without translating the enum
it carries would silently break parsing — keeping both in one module makes that
mistake impossible to miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Positions template — column headers
# ---------------------------------------------------------------------------

class PositionCol:
    """English column headers for the Positions import template."""

    STRUCTURE_TYPE = "Structure Type"
    OPTION_TYPE = "Option Type"
    DIRECTION = "Direction"
    UNDERLYING_CODE = "Underlying Code"
    TRADE_ID = "Trade ID"
    TRADE_STATUS = "Trade Status"
    START_DATE = "Start Date"
    FINAL_OBSERVATION_DATE = "Final Observation Date"
    MATURITY_DATE = "Maturity Date"
    SETTLEMENT_DATE = "Settlement Date"
    INITIAL_NOTIONAL = "Initial Notional"
    NOTIONAL = "Notional"
    NOTIONAL_UNIT = "Notional Unit"
    INITIAL_PRICE = "Initial Price"
    STRIKE_PRICE = "Strike Price"
    PARTICIPATION_RATE = "Participation Rate"
    COUPON_RATE = "Coupon Rate"
    KNOCK_OUT_BARRIER = "Knock-Out Barrier"
    KNOCK_OUT_BARRIER_2 = "Knock-Out Barrier 2"
    KNOCK_OUT_COUPON = "Knock-Out Coupon"
    KNOCK_OUT_OBSERVATION_DATES = "Knock-Out Observation Dates"
    KNOCK_OUT_DAY_COUNTS = "Knock-Out Day Counts"
    KNOCK_IN_BARRIER = "Knock-In Barrier"
    NO_KNOCK_IN_COUPON = "No-Knock-In Coupon"
    ALREADY_KNOCKED_IN = "Already Knocked In"
    CUSTOM_STRUCTURE = "Custom Structure"
    DIVIDEND_COUPON = "Dividend Coupon"
    KI_MIN_RETURN_RATE = "Knock-In Min Return Rate"
    ANNUALIZED = "Annualized"
    KI_ANNUALIZED = "Knock-In Annualized"
    KO_COUPON_OBSERVATION_DATES = "Knock-Out/Coupon Observation Dates"
    DAY_COUNT_FACTORS = "Day-Count Factors"
    COUPON_BARRIER = "Coupon Barrier"
    COUPON_BARRIER_RATE = "Coupon Barrier Rate"
    CURRENCY = "Currency"


POSITIONS_SHEET_NAME = "Positions"


# ---------------------------------------------------------------------------
# Positions template — cell enum vocabulary
# ---------------------------------------------------------------------------

class Direction:
    BUY = "Buy"
    SELL = "Sell"


class OptionType:
    CALL = "Call"
    PUT = "Put"


class TradeStatus:
    OPEN = "Open"
    KNOCKED_OUT = "Knocked Out"
    KNOCKED_IN = "Knocked In"
    SETTLED = "Settled"
    CLOSED = "Closed"


#: Trade statuses that mean the position is no longer live.
CLOSED_STATUSES = {TradeStatus.KNOCKED_OUT, TradeStatus.SETTLED, TradeStatus.CLOSED}


class YesNo:
    YES = "Yes"
    NO = "No"


# ``Custom Structure`` free-text tags the adapter searches for as substrings.
CUSTOM_NO_KNOCK_IN = "No Knock-In"
CUSTOM_EUROPEAN = "European"

# Substrings searched inside the structure-type label.
PARTIAL_PROTECTION_TAG = "Partial Protection"
PHOENIX_TAG = "Phoenix"


#: Structure-type label -> internal mapper id (consumed by ``position_adapter``).
STRUCTURE_TYPE_VANILLA_EUROPEAN = "European Vanilla"
STRUCTURE_TYPE_VANILLA_AMERICAN = "American Vanilla"
STRUCTURE_TYPE_DIGITAL = "European Digital"
STRUCTURE_TYPE_BARRIER_KNOCK_IN = "Barrier Knock-In"
STRUCTURE_TYPE_SINGLE_SHARKFIN = "Single Sharkfin"
STRUCTURE_TYPE_DOUBLE_SHARKFIN = "Double Sharkfin"
STRUCTURE_TYPE_SNOWBALL = "Snowball (No Protection)"
STRUCTURE_TYPE_SNOWBALL_PARTIAL = "Snowball (Partial Protection)"
STRUCTURE_TYPE_PHOENIX = "Phoenix (No Protection)"
STRUCTURE_TYPE_PHOENIX_PARTIAL = "Phoenix (Partial Protection)"

SUPPORTED_STRUCTURE_TYPES = [
    STRUCTURE_TYPE_VANILLA_EUROPEAN,
    STRUCTURE_TYPE_VANILLA_AMERICAN,
    STRUCTURE_TYPE_DIGITAL,
    STRUCTURE_TYPE_BARRIER_KNOCK_IN,
    STRUCTURE_TYPE_SINGLE_SHARKFIN,
    STRUCTURE_TYPE_DOUBLE_SHARKFIN,
    STRUCTURE_TYPE_SNOWBALL,
    STRUCTURE_TYPE_SNOWBALL_PARTIAL,
    STRUCTURE_TYPE_PHOENIX,
    STRUCTURE_TYPE_PHOENIX_PARTIAL,
]


# ---------------------------------------------------------------------------
# Pricing Parameters template
# ---------------------------------------------------------------------------

class PricingCol:
    """English column headers for the Pricing Parameters import template."""

    TRADE_ID = "Trade ID"
    UNDERLYING_CODE = "Underlying Code"
    UNDERLYING_PRICE = "Underlying Price"
    VOLATILITY = "Volatility"
    RISK_FREE_RATE = "Risk-Free Rate"
    DIVIDEND_BORROW_YIELD = "Dividend/Borrow Yield"


PRICING_SHEET_NAME = "Pricing Parameters"


# ---------------------------------------------------------------------------
# Column metadata — drives the downloadable template (Instructions sheet)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnSpec:
    """Documentation + example data for one template column."""

    name: str
    required: bool
    dtype: str
    notes: str
    allowed: list[str] = field(default_factory=list)
    examples: list[object] = field(default_factory=list)


# Two illustrative example rows accompany the positions template: a plain
# European vanilla and a (more complex) snowball, so a user can see how list-style
# cells (comma-separated barriers / dates) are formatted.
POSITION_COLUMNS: list[ColumnSpec] = [
    ColumnSpec(
        PositionCol.STRUCTURE_TYPE, True, "enum",
        "Product family for the trade.",
        allowed=SUPPORTED_STRUCTURE_TYPES,
        examples=[STRUCTURE_TYPE_VANILLA_EUROPEAN, STRUCTURE_TYPE_SNOWBALL],
    ),
    ColumnSpec(
        PositionCol.OPTION_TYPE, False, "enum",
        "Call or Put. Leave blank for autocallables (snowball/phoenix).",
        allowed=[OptionType.CALL, OptionType.PUT],
        examples=[OptionType.CALL, None],
    ),
    ColumnSpec(
        PositionCol.DIRECTION, True, "enum",
        "Desk side of the trade. Sell books a short (negative) quantity.",
        allowed=[Direction.BUY, Direction.SELL],
        examples=[Direction.SELL, Direction.SELL],
    ),
    ColumnSpec(
        PositionCol.UNDERLYING_CODE, True, "text",
        "Exchange ticker, e.g. 000852.SH. A trailing ' - name' suffix is stripped.",
        examples=["000852.SH", "000852.SH"],
    ),
    ColumnSpec(
        PositionCol.TRADE_ID, True, "text",
        "Unique trade identifier. Used as the join key to the pricing-parameter sheet.",
        examples=["OTC-VANILLA-01", "OTC-SNOWBALL-01"],
    ),
    ColumnSpec(
        PositionCol.TRADE_STATUS, True, "enum",
        "Lifecycle state. Knocked Out / Settled / Closed mark the position closed.",
        allowed=[TradeStatus.OPEN, TradeStatus.KNOCKED_OUT, TradeStatus.KNOCKED_IN,
                 TradeStatus.SETTLED, TradeStatus.CLOSED],
        examples=[TradeStatus.OPEN, TradeStatus.OPEN],
    ),
    ColumnSpec(
        PositionCol.START_DATE, False, "date",
        "Trade inception date (YYYY-MM-DD). Required for autocallable KI scheduling.",
        examples=["2026-01-01", "2026-01-01"],
    ),
    ColumnSpec(
        PositionCol.FINAL_OBSERVATION_DATE, True, "date",
        "Final observation date (YYYY-MM-DD). Falls back to Maturity Date if blank.",
        examples=["2026-12-31", "2026-12-31"],
    ),
    ColumnSpec(
        PositionCol.MATURITY_DATE, False, "date",
        "Expiry date (YYYY-MM-DD). Used when Final Observation Date is blank.",
        examples=["2026-12-31", "2026-12-31"],
    ),
    ColumnSpec(
        PositionCol.SETTLEMENT_DATE, True, "date",
        "Cash settlement date (YYYY-MM-DD).",
        examples=["2027-01-04", "2027-01-04"],
    ),
    ColumnSpec(
        PositionCol.INITIAL_NOTIONAL, True, "number",
        "Notional at inception. Used if Notional is blank.",
        examples=[1_000_000, 1_000_000],
    ),
    ColumnSpec(
        PositionCol.NOTIONAL, True, "number",
        "Current notional. Divided by Initial Price to derive the contract multiplier.",
        examples=[1_000_000, 1_000_000],
    ),
    ColumnSpec(
        PositionCol.NOTIONAL_UNIT, False, "text",
        "Free-text unit label for the notional, e.g. CNY.",
        examples=["CNY", "CNY"],
    ),
    ColumnSpec(
        PositionCol.INITIAL_PRICE, True, "number",
        "Underlying price at inception (S0). Required for every family.",
        examples=[100.0, 100.0],
    ),
    ColumnSpec(
        PositionCol.STRIKE_PRICE, True, "number",
        "Strike level.",
        examples=[100.0, 100.0],
    ),
    ColumnSpec(
        PositionCol.PARTICIPATION_RATE, False, "number",
        "Participation rate (1.0 = 100%). Defaults to 1.0.",
        examples=[1.0, 1.0],
    ),
    ColumnSpec(
        PositionCol.COUPON_RATE, False, "rate",
        "No-hit / payout coupon rate. Accepts 0.5% or 0.005.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.KNOCK_OUT_BARRIER, False, "number|list",
        "Knock-out barrier(s). Comma-separate for stepped autocallables: 105,100.",
        examples=[None, "105,100"],
    ),
    ColumnSpec(
        PositionCol.KNOCK_OUT_BARRIER_2, False, "number|list",
        "Second knock-out barrier, double sharkfin only.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.KNOCK_OUT_COUPON, False, "rate|list",
        "Knock-out coupon rate(s), aligned with the barriers: 10%,10%.",
        examples=[None, "10%,10%"],
    ),
    ColumnSpec(
        PositionCol.KNOCK_OUT_OBSERVATION_DATES, False, "date list",
        "Comma-separated knock-out observation dates: 2026-06-30,2026-12-31.",
        examples=[None, "2026-06-30,2026-12-31"],
    ),
    ColumnSpec(
        PositionCol.KNOCK_OUT_DAY_COUNTS, False, "number list",
        "Snowball accrual day counts per observation: 183,365.",
        examples=[None, "183,365"],
    ),
    ColumnSpec(
        PositionCol.KNOCK_IN_BARRIER, False, "number",
        "Knock-in barrier level.",
        examples=[None, 70.0],
    ),
    ColumnSpec(
        PositionCol.NO_KNOCK_IN_COUPON, False, "rate",
        "Coupon paid when the option never knocks in.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.ALREADY_KNOCKED_IN, False, "enum",
        "Whether the position has already knocked in.",
        allowed=[YesNo.YES, YesNo.NO],
        examples=[None, YesNo.NO],
    ),
    ColumnSpec(
        PositionCol.CUSTOM_STRUCTURE, False, "text",
        f"KI-observation convention tag. '{CUSTOM_NO_KNOCK_IN}' = no KI; "
        f"'{CUSTOM_EUROPEAN}' = European KI; blank = daily KI.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.DIVIDEND_COUPON, False, "rate",
        "Rebate / dividend coupon for autocallables.",
        examples=[None, 0.03],
    ),
    ColumnSpec(
        PositionCol.KI_MIN_RETURN_RATE, False, "rate",
        "Minimum guaranteed return for partially-protected structures.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.ANNUALIZED, False, "enum",
        "Whether knock-out / rebate coupons are quoted annualized.",
        allowed=[YesNo.YES, YesNo.NO],
        examples=[YesNo.NO, YesNo.YES],
    ),
    ColumnSpec(
        PositionCol.KI_ANNUALIZED, False, "enum",
        "Whether the knock-in leg coupon is quoted annualized.",
        allowed=[YesNo.YES, YesNo.NO],
        examples=[None, YesNo.NO],
    ),
    ColumnSpec(
        PositionCol.KO_COUPON_OBSERVATION_DATES, False, "date list",
        "Phoenix knock-out / coupon observation dates.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.DAY_COUNT_FACTORS, False, "factor list",
        "Phoenix accrual factors, e.g. 30/360,30/360.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.COUPON_BARRIER, False, "number|list",
        "Phoenix coupon barrier(s).",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.COUPON_BARRIER_RATE, False, "rate|list",
        "Phoenix coupon rate(s) at the coupon barrier.",
        examples=[None, None],
    ),
    ColumnSpec(
        PositionCol.CURRENCY, False, "text",
        "ISO-4217 code, e.g. CNY. Defaults to CNY when blank.",
        examples=["CNY", "CNY"],
    ),
]


PRICING_COLUMNS: list[ColumnSpec] = [
    ColumnSpec(
        PricingCol.TRADE_ID, True, "text",
        "Trade identifier matching the Trade ID on the Positions sheet.",
        examples=["OTC-VANILLA-01", "OTC-SNOWBALL-01"],
    ),
    ColumnSpec(
        PricingCol.UNDERLYING_CODE, True, "text",
        "Exchange ticker. A trailing ' - name' suffix is stripped.",
        examples=["000852.SH", "000852.SH"],
    ),
    ColumnSpec(
        PricingCol.UNDERLYING_PRICE, False, "number",
        "Spot/observation price. Stored as a market quote, not an assumption.",
        examples=[6400.0, 6400.0],
    ),
    ColumnSpec(
        PricingCol.VOLATILITY, True, "rate",
        "Implied volatility. Accepts 22% or 0.22.",
        examples=[0.22, 0.22],
    ),
    ColumnSpec(
        PricingCol.RISK_FREE_RATE, True, "rate",
        "Risk-free rate. Accepts 2% or 0.02.",
        examples=[0.02, 0.02],
    ),
    ColumnSpec(
        PricingCol.DIVIDEND_BORROW_YIELD, True, "rate",
        "Dividend / borrow yield (q). Accepts 3% or 0.03.",
        examples=[0.03, 0.03],
    ),
]


#: Required header sets used by the parsers to validate an uploaded workbook.
POSITION_REQUIRED_HEADERS = {spec.name for spec in POSITION_COLUMNS if spec.required}
PRICING_REQUIRED_HEADERS = {spec.name for spec in PRICING_COLUMNS if spec.required}


# ---------------------------------------------------------------------------
# Legacy stored-payload compatibility
# ---------------------------------------------------------------------------
# Uploads are now English-only, but ``Position.source_payload`` is a historical
# record: the live database holds rows imported under the old Chinese headers,
# and brand-masking does not translate the field vocabulary. Readers of stored
# payloads therefore accept BOTH the English schema and the legacy Chinese keys
# so existing positions keep resolving their trade status / currency.

LEGACY_TRADE_STATUS_KEY = "交易状态"
LEGACY_NOTIONAL_UNIT_KEY = "交易规模单位"
LEGACY_KNOCKED_OUT = "敲出"
LEGACY_CLOSED_STATUSES = {"敲出", "结算", "平仓"}

#: Trade statuses (English + legacy) that mean the position is no longer live.
TERMINAL_STATUSES = CLOSED_STATUSES | LEGACY_CLOSED_STATUSES
#: Statuses (English + legacy) that mean a knock-out occurred.
KNOCKED_OUT_STATUSES = {TradeStatus.KNOCKED_OUT, LEGACY_KNOCKED_OUT}


def read_trade_status(row: object) -> str:
    """Trade status from a stored payload row, English header or legacy Chinese."""
    if not isinstance(row, dict):
        return ""
    raw = row.get(PositionCol.TRADE_STATUS) or row.get(LEGACY_TRADE_STATUS_KEY)
    return str(raw).strip() if raw is not None else ""


def read_notional_unit(row: object) -> str:
    """Notional-unit cell (the legacy currency fallback), English or Chinese."""
    if not isinstance(row, dict):
        return ""
    raw = row.get(PositionCol.NOTIONAL_UNIT) or row.get(LEGACY_NOTIONAL_UNIT_KEY)
    return str(raw).strip() if raw is not None else ""


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_knocked_out(status: str) -> bool:
    return status in KNOCKED_OUT_STATUSES
