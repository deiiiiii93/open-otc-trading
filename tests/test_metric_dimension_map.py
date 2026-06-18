from app.services.quantark import RISK_GREEK_KEYS
from app.services.risk_currency import (
    MONEY_METRIC_KEYS,
    SHARED_METRIC_KEYS,
    metric_dimension,
)

ALL_TOTAL_KEYS = {
    "market_value", "delta_proxy", "gross_notional", "pnl", "one_day_var_proxy",
    *RISK_GREEK_KEYS,
}


def test_money_and_shared_partition_all_total_keys():
    assert MONEY_METRIC_KEYS.isdisjoint(SHARED_METRIC_KEYS)
    assert MONEY_METRIC_KEYS | SHARED_METRIC_KEYS == ALL_TOTAL_KEYS


def test_known_tags():
    assert metric_dimension("market_value") == "money"
    assert metric_dimension("vega") == "money"
    assert metric_dimension("delta_cash") == "money"
    assert metric_dimension("one_day_var_proxy") == "money"
    assert metric_dimension("delta") == "shared"
    assert metric_dimension("gamma") == "shared"
    assert metric_dimension("delta_proxy") == "shared"
