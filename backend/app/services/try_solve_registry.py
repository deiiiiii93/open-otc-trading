from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal


FieldType = Literal["text", "number", "date", "boolean", "select"]
SolverStatus = Literal["solver_ready", "schema_captured"]


@dataclass(frozen=True)
class TrySolveField:
    key: str
    label: str
    field_type: FieldType = "text"
    excel_aliases: tuple[str, ...] = ()
    required: bool = False
    default: Any = None
    options: tuple[str, ...] = ()
    canonical_path: str | None = None


@dataclass(frozen=True)
class TrySolveQuoteField:
    key: str
    label: str
    excel_header: str
    canonical_path: str
    lower_bound: float = 0.0
    upper_bound: float = 2.0
    initial_guess: float | None = None
    solver_ready: bool = False


@dataclass(frozen=True)
class TrySolveProduct:
    product_key: str
    label: str
    excel_sheet: str
    initial_solver_state: SolverStatus
    fields: dict[str, TrySolveField]
    quote_fields: dict[str, TrySolveQuoteField]
    quantark_product_type: str | None = None
    default_engine_name: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "label": self.label,
            "excel_sheet": self.excel_sheet,
            "initial_solver_state": self.initial_solver_state,
            "fields": [asdict(field) for field in self.fields.values()],
            "quote_fields": [asdict(field) for field in self.quote_fields.values()],
            "quantark_product_type": self.quantark_product_type,
            "default_engine_name": self.default_engine_name,
            "notes": self.notes,
        }


COMMON_FIELDS = {
    "counterparty": TrySolveField(
        "counterparty", "Counterparty", excel_aliases=("交易对手",)
    ),
    "side": TrySolveField(
        "side", "Side", "select", ("客户方向",), True, "buy", ("buy", "sell")
    ),
    "underlying": TrySolveField(
        "underlying", "Underlying", excel_aliases=("标的代码",), required=True
    ),
    "notional": TrySolveField(
        "notional", "Notional", "number", ("名义本金",), True, 1.0
    ),
    "quantity": TrySolveField(
        "quantity", "Quantity", "number", ("数量",), False
    ),
    "initial_price": TrySolveField(
        "initial_price", "Initial Price", "number", ("期初价格", "初始价格"), False
    ),
    "prepay_ratio": TrySolveField(
        "prepay_ratio", "Prepay Ratio", "number", ("预付金比例",), False, 0.0
    ),
    "annualized": TrySolveField(
        "annualized", "Annualized", "boolean", ("是否年化",), False, True
    ),
    "lock_time": TrySolveField("lock_time", "Lock Time", "date", ("锁价时间",)),
    "start_date": TrySolveField(
        "start_date", "Start Date", "date", ("起始日",), True
    ),
    "end_date": TrySolveField(
        "end_date", "End Date", "date", ("到期日", "结束日", "终止日")
    ),
    "option_type": TrySolveField(
        "option_type", "Option Type", "select", ("期权类型",), False, "call"
    ),
    "strike": TrySolveField("strike", "Strike", "number", ("行权价",), False, 1.0),
    "barrier": TrySolveField(
        "barrier", "Barrier", "number", ("障碍价格", "障碍", "敲入价格"), False, 1.2
    ),
    "upper_barrier": TrySolveField(
        "upper_barrier", "Upper Barrier", "number", ("上障碍", "上障碍价格"), False, 1.2
    ),
    "lower_barrier": TrySolveField(
        "lower_barrier", "Lower Barrier", "number", ("下障碍", "下障碍价格"), False, 0.8
    ),
    "payout": TrySolveField(
        "payout", "Payout", "number", ("绝对返息", "收益金额"), False, 0.1
    ),
    "rebate": TrySolveField(
        "rebate", "Rebate", "number", ("绝对返息", "票息收益"), False, 0.1
    ),
    "participation_rate": TrySolveField(
        "participation_rate", "Participation Rate", "number", ("参与率",), False, 1.0
    ),
    "ki_barrier": TrySolveField(
        "ki_barrier", "Knock-In Barrier", "number", ("敲入障碍",), False, 0.75
    ),
    "observation_frequency": TrySolveField(
        "observation_frequency", "Observation Frequency", "select", ("观察频率",),
        False, "MONTHLY", ("MONTHLY", "QUARTERLY", "SEMI_ANNUAL"),
    ),
    "lockup_months": TrySolveField(
        "lockup_months", "Lockup Months", "number", (), False, 0.0,
    ),
    "ko_barrier": TrySolveField(
        "ko_barrier", "Knock-Out Barrier", "number", ("敲出障碍",), False, 1.03
    ),
    "coupon_yield": TrySolveField(
        "coupon_yield", "Coupon Yield", "number", ("派息收益率",), False, 0.1
    ),
    "tenor_months": TrySolveField(
        "tenor_months", "Tenor Months", "number", ("存续时间（月）",)
    ),
    "tenor_days": TrySolveField(
        "tenor_days", "Tenor Days", "number", ("存续时间（天）",)
    ),
    "remarks": TrySolveField("remarks", "Remarks", excel_aliases=("备注",)),
}

QUOTE_FIELDS = {
    "premium_rate": TrySolveQuoteField(
        "premium_rate", "Premium Rate", "期权费率", "premium_rate", -1.0, 1.0, 0.0
    ),
    "fixed_yield": TrySolveQuoteField(
        "fixed_yield", "Fixed Yield", "固定收益率", "fixed_yield", -1.0, 1.0, 0.0
    ),
    "annualized_coupon": TrySolveQuoteField(
        "annualized_coupon",
        "Annualized Coupon",
        "年化返息",
        "barrier_config.ko_rate",
        0.001,
        0.5,
        0.1,
    ),
    "absolute_coupon": TrySolveQuoteField(
        "absolute_coupon",
        "Absolute Coupon",
        "绝对返息",
        "absolute_coupon",
        0.001,
        0.5,
        0.1,
    ),
    "exercise_yield": TrySolveQuoteField(
        "exercise_yield",
        "Exercise Yield",
        "行权收益率",
        "exercise_yield",
        -1.0,
        2.0,
        0.1,
    ),
    "coupon_yield": TrySolveQuoteField(
        "coupon_yield",
        "Coupon Yield",
        "派息收益率",
        "coupon_config.coupon_rate",
        0.001,
        0.5,
        0.1,
    ),
    "ko_barrier": TrySolveQuoteField(
        "ko_barrier",
        "Knock-Out Barrier",
        "敲出障碍",
        "barrier_config.ko_barrier",
        0.01,
        10.0,
        1.03,
    ),
    "strike": TrySolveQuoteField(
        "strike", "Strike", "行权价", "strike", 0.01, 10.0, 1.0
    ),
    "barrier": TrySolveQuoteField(
        "barrier", "Barrier", "障碍价格", "barrier", 0.01, 10.0, 1.2
    ),
    "upper_barrier": TrySolveQuoteField(
        "upper_barrier", "Upper Barrier", "上障碍", "upper_barrier", 0.01, 10.0, 1.2
    ),
    "lower_barrier": TrySolveQuoteField(
        "lower_barrier", "Lower Barrier", "下障碍", "lower_barrier", 0.01, 10.0, 0.8
    ),
    "rebate": TrySolveQuoteField(
        "rebate", "Rebate", "绝对返息", "rebate", 0.0, 10.0, 0.1
    ),
    "payout": TrySolveQuoteField(
        "payout", "Payout", "绝对返息", "payout", 0.0, 10.0, 0.1
    ),
    "basis": TrySolveQuoteField(
        "basis", "Basis", "固定收益率", "basis", -10.0, 10.0, 0.0
    ),
    "participation_rate": TrySolveQuoteField(
        "participation_rate",
        "Participation Rate",
        "参与率",
        "participation_rate",
        0.0,
        5.0,
        1.0,
    ),
    "range_accrual_rate": TrySolveQuoteField(
        "range_accrual_rate",
        "Range Accrual Rate",
        "派息收益率",
        "range_config.accrual_rate",
        0.001,
        0.5,
        0.1,
    ),
}


def _fields(*keys: str) -> dict[str, TrySolveField]:
    return {key: COMMON_FIELDS[key] for key in keys}


def _quote_field(key: str, *, solver_ready: bool = False) -> TrySolveQuoteField:
    source = QUOTE_FIELDS[key]
    if source.solver_ready == solver_ready:
        return source
    return replace(source, solver_ready=solver_ready)


def _quote_fields(
    *keys: str,
    solver_ready: tuple[str, ...] = (),
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TrySolveQuoteField]:
    ready = set(solver_ready)
    overrides = overrides or {}
    return {
        key: replace(
            _quote_field(key, solver_ready=key in ready),
            **overrides.get(key, {}),
        )
        for key in keys
    }


def _product(
    product_key: str,
    label: str,
    *,
    excel_sheet: str | None = None,
    field_keys: tuple[str, ...] = (
        "counterparty",
        "side",
        "underlying",
        "notional",
        "start_date",
        "end_date",
        "tenor_months",
        "remarks",
    ),
    quote_keys: tuple[str, ...] = ("premium_rate",),
    solver_quote_keys: tuple[str, ...] = (),
    quote_field_overrides: dict[str, dict[str, Any]] | None = None,
    quantark_product_type: str | None = None,
    default_engine_name: str | None = None,
    notes: str = "",
) -> TrySolveProduct:
    return TrySolveProduct(
        product_key=product_key,
        label=label,
        excel_sheet=excel_sheet or product_key,
        initial_solver_state="solver_ready" if solver_quote_keys else "schema_captured",
        fields=_fields(*field_keys),
        quote_fields=_quote_fields(
            *quote_keys,
            solver_ready=solver_quote_keys,
            overrides=quote_field_overrides,
        ),
        quantark_product_type=quantark_product_type,
        default_engine_name=default_engine_name,
        notes=notes,
    )


PRODUCTS = (
    _product(
        "autocall",
        "Autocall",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "initial_price",
            "start_date",
            "end_date",
            "observation_frequency",
            "lockup_months",
            "ko_barrier",
            "ki_barrier",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("annualized_coupon", "ko_barrier"),
        solver_quote_keys=("annualized_coupon", "ko_barrier"),
        quantark_product_type="SnowballOption",
        default_engine_name="SnowballQuadEngine",
    ),
    _product(
        "phoenix",
        "Phoenix",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "initial_price",
            "start_date",
            "end_date",
            "observation_frequency",
            "lockup_months",
            "ko_barrier",
            "ki_barrier",
            "coupon_yield",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("annualized_coupon", "coupon_yield", "ko_barrier"),
        solver_quote_keys=("annualized_coupon", "coupon_yield", "ko_barrier"),
        quantark_product_type="PhoenixOption",
        default_engine_name="PhoenixQuadEngine",
    ),
    _product(
        "vanilla",
        "Vanilla",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "option_type",
            "strike",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "strike"),
        solver_quote_keys=("strike",),
        quantark_product_type="EuropeanVanillaOption",
        default_engine_name="BlackScholesEngine",
    ),
    _product(
        "vertical_spread", "Vertical Spread", quote_keys=("premium_rate", "strike")
    ),
    _product(
        "digital",
        "Digital",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "option_type",
            "strike",
            "payout",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "payout"),
        solver_quote_keys=("payout",),
        quantark_product_type="CashOrNothingDigitalOption",
        default_engine_name="DigitalOptionAnalyticalEngine",
    ),
    _product(
        "binary_convex", "Binary Convex", quote_keys=("premium_rate", "strike")
    ),
    _product(
        "single_sf",
        "Single Sharkfin",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "option_type",
            "strike",
            "barrier",
            "participation_rate",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "strike", "barrier", "participation_rate"),
        solver_quote_keys=("strike", "barrier", "participation_rate"),
        quantark_product_type="SingleSharkfinOption",
        default_engine_name="SingleSharkfinOptionAnalyticalEngine",
    ),
    _product(
        "double_sf",
        "Double Sharkfin",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "option_type",
            "strike",
            "upper_barrier",
            "lower_barrier",
            "participation_rate",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "strike", "upper_barrier", "lower_barrier", "participation_rate"),
        solver_quote_keys=("strike", "upper_barrier", "lower_barrier", "participation_rate"),
        quantark_product_type="DoubleSharkfinOption",
        default_engine_name="DoubleSharkfinOptionAnalyticalEngine",
    ),
    _product(
        "airbag",
        "Airbag",
        quote_keys=("premium_rate", "strike"),
        quantark_product_type="AirbagOption",
    ),
    _product(
        "airbag_spread",
        "Airbag Spread",
        quote_keys=("premium_rate", "strike"),
    ),
    _product(
        "asian",
        "Asian",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "option_type",
            "strike",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "strike"),
        solver_quote_keys=("strike",),
        quantark_product_type="AsianOption",
        default_engine_name="AsianOptionAnalyticalEngine",
    ),
    _product(
        "call_put_portfolio",
        "Call Put Portfolio",
        quote_keys=("premium_rate", "strike"),
    ),
    _product("ladder_binary", "Ladder Binary", quote_keys=("premium_rate", "strike")),
    _product(
        "forward",
        "Forward",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("fixed_yield", "basis"),
        solver_quote_keys=("basis",),
        quantark_product_type="Futures",
        default_engine_name="DeltaOneEngine",
    ),
    _product(
        "range_accrual",
        "Range Accrual",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "upper_barrier",
            "lower_barrier",
            "coupon_yield",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("range_accrual_rate", "upper_barrier", "lower_barrier"),
        solver_quote_keys=("range_accrual_rate", "upper_barrier", "lower_barrier"),
        quantark_product_type="RangeAccrualOption",
        default_engine_name="RangeAccrualAnalyticalEngine",
    ),
    _product(
        "one_touch",
        "One Touch",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "barrier",
            "rebate",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "barrier", "rebate"),
        solver_quote_keys=("barrier", "rebate"),
        quantark_product_type="OneTouchOption",
        default_engine_name="OneTouchAnalyticalEngine",
    ),
    _product(
        "double_no_touch",
        "Double No Touch",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "upper_barrier",
            "lower_barrier",
            "rebate",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "upper_barrier", "lower_barrier", "rebate"),
        solver_quote_keys=("upper_barrier", "lower_barrier", "rebate"),
        quantark_product_type="DoubleOneTouchOption",
        default_engine_name="OneTouchAnalyticalEngine",
    ),
    _product(
        "double_one_touch",
        "Double One Touch",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "quantity",
            "initial_price",
            "start_date",
            "end_date",
            "upper_barrier",
            "lower_barrier",
            "rebate",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("premium_rate", "upper_barrier", "lower_barrier", "rebate"),
        solver_quote_keys=("upper_barrier", "lower_barrier", "rebate"),
        quantark_product_type="DoubleOneTouchOption",
        default_engine_name="OneTouchAnalyticalEngine",
    ),
    _product(
        "knock_out_autocall",
        "Knock-Out Autocall",
        field_keys=(
            "counterparty",
            "side",
            "underlying",
            "notional",
            "initial_price",
            "start_date",
            "end_date",
            "observation_frequency",
            "lockup_months",
            "ko_barrier",
            "ki_barrier",
            "tenor_months",
            "remarks",
        ),
        quote_keys=("annualized_coupon", "ko_barrier"),
        solver_quote_keys=("annualized_coupon", "ko_barrier"),
        quantark_product_type="KnockOutResetSnowballOption",
        default_engine_name="KOResetSnowballQuadEngine",
    ),
)

PRODUCT_KEYS = tuple(product.product_key for product in PRODUCTS)


def registry_by_key() -> dict[str, TrySolveProduct]:
    return {product.product_key: product for product in PRODUCTS}


def registry_by_sheet() -> dict[str, TrySolveProduct]:
    return {product.excel_sheet: product for product in PRODUCTS}


def get_try_solve_catalog() -> dict[str, Any]:
    return {
        "products": [product.to_dict() for product in PRODUCTS],
        "status_options": [
            "draft",
            "missing_terms",
            "missing_market",
            "mapping_pending",
            "invalid_target",
            "unsupported_market",
            "unsupported_quote_field",
            "quantark_build_failed",
            "solve_failed",
            "solver_ready",
            "solved",
        ],
    }
