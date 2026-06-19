from __future__ import annotations

from unittest.mock import MagicMock


def test_pricing_preview_happy_path(client, monkeypatch):
    fake = MagicMock(
        ok=True,
        error=None,
        data={
            "price": 4.25,
            "engine": "BlackScholesEngine",
            "product_type": "EuropeanVanillaOption",
            "greeks": {
                "delta": 0.5, "gamma": 0.01, "vega": 0.2,
                "theta": -0.03, "rho": 0.1, "rho_q": -0.05,
            },
            "greeks_error": None,
        },
    )
    monkeypatch.setattr(
        "app.main.price_product_preview", lambda **kwargs: fake, raising=False
    )
    resp = client.post(
        "/api/pricing/preview",
        json={
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {"strike": 100, "option_type": "CALL"},
            "engine_name": "BlackScholesEngine",
            "market": {"spot": 100, "volatility": 0.2, "rate": 0.03, "dividend_yield": 0.0},
            "compute_greeks": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["price"] == 4.25
    assert body["greeks"]["delta"] == 0.5
    assert body["greeks"]["rho_q"] == -0.05
    assert body["error"] is None


def test_pricing_preview_reports_build_failure(client, monkeypatch):
    fake = MagicMock(
        ok=False,
        error="bad terms",
        data={"price": 0.0, "engine": "BlackScholesEngine",
              "product_type": "X", "greeks": None, "greeks_error": None},
    )
    monkeypatch.setattr(
        "app.main.price_product_preview", lambda **kwargs: fake, raising=False
    )
    resp = client.post(
        "/api/pricing/preview",
        json={"product_type": "X", "product_kwargs": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "bad terms"
    assert body["greeks"] is None
