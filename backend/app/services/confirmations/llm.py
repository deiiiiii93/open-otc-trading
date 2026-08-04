"""Two-stage LLM extraction over extracted confirmation content.

Stage 1 segments a document into trades (family + pages + anchor); stage 2
fills the family's legal term schema (get_product_term_schema) with per-field
evidence. All numbers are re-validated downstream by build_product — nothing
here is bookable on its own. Model routing mirrors the memory extractor:
dedicated tag -> "fast" tag -> registry default.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

# NOTE: app.tools.product_term_schema is imported lazily inside the two
# functions below (segment_document / extract_trade), not at module scope.
# app.tools' package __init__ imports app.tools.confirmations, which needs
# this module's names (service.py -> .llm) fully defined; a module-level
# import here of anything under app.tools would force app.tools' package
# init to re-enter this (still-executing) module and fail on a name not
# defined yet -- reproducible via `pytest tests/test_confirmations_llm.py`
# in isolation before this fix (ImportError: partially initialized module).

CONFIRMATION_EXTRACTOR_TAG = "confirmation_extractor"
_FALLBACK_TAG = "fast"


class ExtractionError(Exception):
    def __init__(self, message: str, raw_response: str | None = None):
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(frozen=True)
class TradeSegment:
    family: str
    pages: list[int]
    anchor: str


@dataclass(frozen=True)
class TradeDraft:
    family: str
    terms: dict
    underlying: str | None = None
    quantity: float | None = None
    entry_price: float | None = None
    currency: str | None = None
    counterparty: str | None = None
    trade_date: str | None = None
    external_trade_id: str | None = None
    confidence: float | None = None
    evidence: dict = field(default_factory=dict)


class ExtractorClient(Protocol):
    def complete(self, content_parts: list[dict]) -> str: ...


def resolve_confirmation_extractor_selection(registry) -> dict:
    for tag in (CONFIRMATION_EXTRACTOR_TAG, _FALLBACK_TAG):
        selection = registry.select_by_tag(tag)
        if selection is not None:
            return selection
    return registry.default_selection()


class RegistryExtractorClient:
    """Multimodal chat call through the channel registry (LangChain content parts)."""

    def __init__(self):
        from app.services.deep_agent.channel_registry import get_registry
        from app.services.deep_agent.model_factory import build_agent_model

        registry = get_registry()
        self.selection = resolve_confirmation_extractor_selection(registry)
        self._model = build_agent_model(registry, self.selection)
        if self._model is None:
            raise RuntimeError("confirmation extractor model unavailable")

    def complete(self, content_parts: list[dict]) -> str:
        message = {"role": "user", "content": content_parts}
        content = self._model.invoke([message]).content
        if not isinstance(content, str):
            raise ExtractionError(
                f"extractor returned non-text content ({type(content).__name__})"
            )
        return content


def build_extractor_client() -> ExtractorClient:
    return RegistryExtractorClient()


def _content_parts(content, pages: list[int] | None = None) -> list[dict]:
    parts: list[dict] = []
    wanted = set(pages) if pages else None
    for page in content.pages:
        if wanted is not None and page.index not in wanted:
            continue
        if page.image_png is not None:
            b64 = base64.b64encode(page.image_png).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        elif page.text:
            parts.append({"type": "text", "text": f"[page {page.index}]\n{page.text}"})
    return parts


def _json_from_response(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"top-level JSON must be an object, got {type(parsed).__name__}"
        )
    return parsed


def _complete_json(client: ExtractorClient, parts: list[dict]) -> dict:
    raw = client.complete(parts)
    try:
        return _json_from_response(raw)
    except (json.JSONDecodeError, AttributeError, ValueError):
        raw2 = client.complete(parts + [{
            "type": "text",
            "text": "Your previous reply was not valid JSON. Reply with ONLY the JSON object.",
        }])
        try:
            return _json_from_response(raw2)
        except (json.JSONDecodeError, AttributeError, ValueError) as exc:
            raise ExtractionError("extractor returned invalid JSON twice", raw2) from exc


_SEGMENT_INSTRUCTIONS = """You are reading an OTC trade confirmation document.
List every distinct trade it confirms. For each trade pick the QuantArk product
family from EXACTLY this list (or "unknown" if none fits):
{families}

Reply with ONLY a JSON object:
{{"trades": [{{"family": "<family>", "pages": [<page numbers>], "anchor": "<short quote locating the trade>"}}]}}
"""


def segment_document(content, client: ExtractorClient) -> list[TradeSegment]:
    from app.tools.product_term_schema import _SCHEMA_FAMILIES

    prompt = _SEGMENT_INSTRUCTIONS.format(families=", ".join(sorted(_SCHEMA_FAMILIES)))
    parts = [{"type": "text", "text": prompt}] + _content_parts(content)
    data = _complete_json(client, parts)
    segments = []
    for row in data.get("trades", []):
        family = str(row.get("family") or "unknown")
        pages = [int(p) for p in (row.get("pages") or [])]
        segments.append(TradeSegment(
            family=family, pages=pages, anchor=str(row.get("anchor") or "")))
    return segments


_EXTRACT_INSTRUCTIONS = """Extract the terms of ONE trade (family: {family}) from this
confirmation. Fill ONLY field names from this legal schema (do not invent fields;
use the exact enum spellings shown):

{schema}

Reply with ONLY a JSON object:
{{"terms": {{<schema field name>: <value>, ...}},
  "underlying": <string or null>, "quantity": <number or null>,
  "entry_price": <number or null, the premium/price per unit>,
  "currency": <ISO code or null>, "counterparty": <string or null>,
  "trade_date": <YYYY-MM-DD or null>, "external_trade_id": <the confirmation's own trade/deal reference or null>,
  "confidence": <0..1>,
  "evidence": {{<field name>: {{"quote": "<verbatim source text>", "page": <page number>}}}}}}
"""


def extract_trade(content, segment: TradeSegment, client: ExtractorClient) -> TradeDraft:
    from app.tools.product_term_schema import get_product_term_schema

    schema = get_product_term_schema.func(quantark_class=segment.family)
    prompt = _EXTRACT_INSTRUCTIONS.format(
        family=segment.family, schema=json.dumps(schema, indent=1, default=str))
    parts = [{"type": "text", "text": prompt}] + _content_parts(
        content, segment.pages or None)
    data = _complete_json(client, parts)

    def _num(key):
        value = data.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    def _text(key):
        value = data.get(key)
        return str(value) if value not in (None, "") else None

    return TradeDraft(
        family=segment.family,
        terms=dict(data.get("terms") or {}),
        underlying=_text("underlying"),
        quantity=_num("quantity"),
        entry_price=_num("entry_price"),
        currency=_text("currency"),
        counterparty=_text("counterparty"),
        trade_date=_text("trade_date"),
        external_trade_id=_text("external_trade_id"),
        confidence=_num("confidence"),
        evidence=dict(data.get("evidence") or {}),
    )
