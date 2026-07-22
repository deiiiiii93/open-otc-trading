from __future__ import annotations

import pytest

from app.services.term_structure import (
    TENOR_YEARS,
    interpolate_curve,
    tenor_to_years,
    validate_curve,
)


def test_tenor_years_map_has_expected_labels() -> None:
    assert set(TENOR_YEARS) == {
        "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y", "3Y", "5Y",
    }
    assert TENOR_YEARS["1Y"] == pytest.approx(1.0)
    assert TENOR_YEARS["6M"] == pytest.approx(0.5)
    # Strictly increasing in label order used by the UI axis.
    years = [TENOR_YEARS[k] for k in
             ("1W", "1M", "3M", "6M", "1Y", "18M", "2Y", "5Y")]
    assert years == sorted(years)


def test_tenor_to_years_rejects_unknown() -> None:
    assert tenor_to_years("3M") == pytest.approx(0.25)
    with pytest.raises(ValueError):
        tenor_to_years("4M")


def test_interpolate_none_and_empty_return_none() -> None:
    assert interpolate_curve(None, 0.5) is None
    assert interpolate_curve([], 0.5) is None


def test_interpolate_single_point_is_constant() -> None:
    curve = [{"tenor": "6M", "value": 0.2}]
    assert interpolate_curve(curve, 0.1) == pytest.approx(0.2)
    assert interpolate_curve(curve, 5.0) == pytest.approx(0.2)


def test_interpolate_flat_extrapolation_both_ends() -> None:
    curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    assert interpolate_curve(curve, 0.0) == pytest.approx(0.02)   # below first
    assert interpolate_curve(curve, 3.0) == pytest.approx(0.05)   # above last


def test_interpolate_linear_between() -> None:
    # 3M=0.25y -> 0.02, 1Y=1.0y -> 0.05. Midpoint at 0.625y -> 0.035.
    curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    assert interpolate_curve(curve, 0.625) == pytest.approx(0.035)


def test_interpolate_is_order_independent() -> None:
    curve = [{"tenor": "1Y", "value": 0.05}, {"tenor": "3M", "value": 0.02}]
    assert interpolate_curve(curve, 0.625) == pytest.approx(0.035)


def test_validate_curve_dedup_and_labels() -> None:
    cleaned = validate_curve([{"tenor": "3M", "value": 0.02}])
    assert cleaned == [{"tenor": "3M", "value": 0.02}]
    assert validate_curve(None) is None
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "4M", "value": 0.02}])         # unknown label
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": 0.02},
                        {"tenor": "3M", "value": 0.03}])          # duplicate
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": "x"}])          # non-numeric
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": 0.0}], require_positive=True)
