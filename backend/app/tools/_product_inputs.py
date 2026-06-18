"""Product-native schemas and adapters for curated agent tools."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas import (
    PricingEnvironmentSnapshot,
    RFQEngineSpecIn,
    RFQRequestDraft,
    RFQTargetIn,
    RFQUnknownSpecIn,
)
from app.services.domains.products import ProductSpec, product_family_for_quantark_class


class ToolProductSpec(BaseModel):
    asset_class: str = "equity"
    product_family: str = "option"
    quantark_class: str | None = "EuropeanVanillaOption"
    underlying: str = "CSI500"
    currency: str = "USD"
    terms: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)
    display_name: str | None = None

    def to_product_spec(self) -> ProductSpec:
        return ProductSpec(
            asset_class=self.asset_class,
            product_family=self.product_family,
            quantark_class=self.quantark_class,
            underlying=self.underlying,
            currency=self.currency,
            terms=dict(self.terms or {}),
            components=list(self.components or []),
            display_name=self.display_name,
        )

    def to_legacy_payload(self) -> dict[str, Any]:
        terms = dict(self.terms or {})
        if self.components:
            terms["components"] = list(self.components)
        return {
            "underlying": self.underlying,
            "product_type": self.quantark_class or self.product_family,
            "product_kwargs": terms,
        }


class ToolPositionSnapshotSpec(BaseModel):
    product: ToolProductSpec = Field(default_factory=ToolProductSpec)
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    quantity: float = 1.0
    entry_price: float = 0.0
    status: str = "open"
    source_trade_id: str | None = None
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: date | datetime | None = None

    def to_legacy_payload(self) -> dict[str, Any]:
        payload = self.product.to_legacy_payload()
        payload.update(
            {
                "engine_name": self.engine_name,
                "engine_kwargs": dict(self.engine_kwargs or {}),
                "quantity": self.quantity,
                "entry_price": self.entry_price,
                "status": self.status,
                "source_trade_id": self.source_trade_id,
                "rfq_id": self.rfq_id,
                "rfq_quote_version_id": self.rfq_quote_version_id,
                "trade_effective_date": self.trade_effective_date,
            }
        )
        return payload


class ToolRFQDraft(BaseModel):
    client_name: str = "Demo Client"
    side: Literal["buy", "sell"] = "buy"
    quantity: float = 1.0
    quote_mode: Literal["solve", "price"] = "solve"
    product: ToolProductSpec = Field(default_factory=ToolProductSpec)
    market: PricingEnvironmentSnapshot = Field(default_factory=PricingEnvironmentSnapshot)
    engine_spec: RFQEngineSpecIn = Field(default_factory=RFQEngineSpecIn)
    unknown: RFQUnknownSpecIn = Field(default_factory=RFQUnknownSpecIn)
    target: RFQTargetIn = Field(default_factory=RFQTargetIn)
    notes: str | None = None

    def to_rfq_request_draft(self) -> RFQRequestDraft:
        legacy = self.product.to_legacy_payload()
        return RFQRequestDraft(
            client_name=self.client_name,
            underlying=legacy["underlying"],
            side=self.side,
            quantity=self.quantity,
            quote_mode=self.quote_mode,
            product_type=legacy["product_type"],
            product_kwargs=legacy["product_kwargs"],
            market=self.market,
            engine_spec=self.engine_spec,
            unknown=self.unknown,
            target=self.target,
            notes=self.notes,
        )


class ProductReferenceInput(BaseModel):
    product_id: int | None = None
    product: ToolProductSpec | None = None

    @model_validator(mode="after")
    def _requires_product_reference(self):
        if self.product_id is None and self.product is None:
            raise ValueError("product_id or product is required")
        return self


def rfq_draft_to_product_payload(draft: RFQRequestDraft | dict[str, Any]) -> dict[str, Any]:
    payload = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else dict(draft)
    product_type = payload.get("product_type")
    product_terms = dict(payload.get("product_kwargs") or {})
    components = list(product_terms.pop("components", []) or [])
    return {
        "client_name": payload.get("client_name"),
        "side": payload.get("side"),
        "quantity": payload.get("quantity"),
        "quote_mode": payload.get("quote_mode"),
        "product": {
            "asset_class": "equity",
            "product_family": payload.get("product_family")
            or product_family_for_quantark_class(product_type, components=components),
            "quantark_class": product_type,
            "underlying": payload.get("underlying"),
            "currency": product_terms.get("currency") or payload.get("currency") or "USD",
            "terms": product_terms,
            "components": components,
            "display_name": payload.get("display_name"),
        },
        "market": payload.get("market"),
        "engine_spec": payload.get("engine_spec"),
        "unknown": payload.get("unknown"),
        "target": payload.get("target"),
        "notes": payload.get("notes"),
    }
