# Trade Confirmation → Book Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse uploaded trade confirmation documents (PDF/DOCX, including scanned) into reviewable extracted trades and book them through the existing `book_position` gate — via a new Confirmations web page and chat attachments to the Desk/Pet agents.

**Architecture:** One shared `backend/app/services/confirmations/` package owns extract → two-stage LLM extraction → deterministic re-validation → review → book. REST (`/api/confirmations`), the Confirmations page, and three agent tools are thin clients. Extraction targets `get_product_term_schema(family)`; booking reuses `BookingRequest`/`book_position` with `source_trade_id` idempotency.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic, LangChain chat models via `channel_registry`/`model_factory`, pypdf + pypdfium2 + python-docx, React 19 + Vite, vitest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-trade-confirmation-to-book-design.md`

## Global Constraints

- Run backend tests from **repo root**: `.venv/bin/python -m pytest tests/<file> -x -q` (never pipe to `tail`).
- Dependencies via `uv` — edit `pyproject.toml`, then `uv sync --extra dev`. QuantArk stays pinned `quantark==0.3.0`; never editable-install.
- Migrations use **migration-local Core tables**, never ORM models (`migrations_no_live_orm_services`).
- Frontend: token-only styling (`var(--token)` only), reuse `wl-` primitives, one co-located `.css` per component; verify `cd frontend && npx tsc --noEmit` and `npm test`.
- Exact-set test pins that MUST be updated in the same commit as the change they pin: `tests/test_capability_assignments.py:34` (`len(QUANT_AGENT_TOOLS) == 112` today), `tests/test_hitl.py` (exact HITL set), skill-catalog assertions (enumerate with `grep -rln "book-position" tests/`).
- `CHANGELOG.md` `[Unreleased]` must be updated before pushing (pre-push hook blocks otherwise); README + root CLAUDE.md get a section for the new subsystem (final task).
- LLM output is never bookable without passing `prepare_booking_product_spec` and human confirmation (HITL / review UI).
- New agent tools need: `QUANT_AGENT_TOOLS` (`backend/app/tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`backend/app/services/agents.py:373`), and — for HITL writes — all three structures in `backend/app/services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES` line 23, `_RISK_LEVEL_BY_TOOL` line 69, `_LABEL_BY_TOOL` line 125).

---

### Task 1: Dependencies, ORM models, migration 0052

**Files:**
- Modify: `pyproject.toml` (add `pypdf>=5.1`, `pypdfium2>=4.30`)
- Modify: `backend/app/models.py` (three new models, after `PositionImportBatch` ~line 1394)
- Create: `backend/alembic/versions/0052_trade_confirmations.py`
- Test: `tests/test_confirmations_models.py`

**Interfaces:**
- Produces: `ConfirmationBatch` (`source`, `default_portfolio_id`, `task_id`, `documents`), `ConfirmationDocument` (`batch_id`, `filename`, `stored_path`, `sha256`, `byte_len`, `mime`, `page_count`, `extract_mode`, `status`, `error`, `model_provenance`, `extraction_debug`, `parsed_at`, `trades`), `ExtractedTrade` (`document_id`, `seq`, `family`, `extracted_terms`, `terms`, `underlying`, `quantity`, `entry_price`, `currency`, `counterparty`, `trade_date`, `external_trade_id`, `confidence`, `evidence`, `validation_status`, `validation_errors`, `status`, `booked_position_id`, `reject_reason`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_confirmations_models.py
from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade


def test_confirmation_models_round_trip(session):
    batch = ConfirmationBatch(source="web", default_portfolio_id=None)
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="conf.pdf", stored_path="/tmp/conf.pdf",
        sha256="a" * 64, byte_len=10, mime="application/pdf",
    )
    session.add(doc)
    session.flush()
    trade = ExtractedTrade(
        document_id=doc.id, seq=1, family="EuropeanVanillaOption",
        extracted_terms={"strike": 100.0}, terms={"strike": 100.0},
        underlying="AAPL", evidence={}, validation_errors=[],
    )
    session.add(trade)
    session.flush()
    assert doc.status == "pending"
    assert trade.status == "extracted"
    assert trade.validation_status == "invalid"
    assert batch.documents[0].trades[0].id == trade.id


def test_batch_delete_cascades(session):
    batch = ConfirmationBatch(source="agent")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="x.docx", stored_path="/tmp/x.docx",
        sha256="b" * 64, byte_len=5, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    session.add(doc)
    session.flush()
    session.delete(batch)
    session.flush()
    assert session.get(ConfirmationDocument, doc.id) is None
```

- [ ] **Step 2: Run it — expect ImportError** — `.venv/bin/python -m pytest tests/test_confirmations_models.py -x -q`

- [ ] **Step 3: Add deps + models + migration**

`pyproject.toml`: add `"pypdf>=5.1"`, `"pypdfium2>=4.30"` next to `"python-docx>=1.1.2"`, then `uv sync --extra dev`.

`backend/app/models.py` (follow neighboring model style — `Mapped`/`mapped_column`, JSON columns default via lambda):

```python
class ConfirmationBatch(Base):
    __tablename__ = "confirmation_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default="web")
    default_portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents: Mapped[list["ConfirmationDocument"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ConfirmationDocument(Base):
    __tablename__ = "confirmation_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("confirmation_batches.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_len: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(120))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extract_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extraction_debug: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    batch: Mapped[ConfirmationBatch] = relationship(back_populates="documents")
    trades: Mapped[list["ExtractedTrade"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ExtractedTrade(Base):
    __tablename__ = "extracted_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("confirmation_documents.id"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=1)
    family: Mapped[str] = mapped_column(String(80))
    extracted_terms: Mapped[dict] = mapped_column(JSON, default=dict)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    underlying: Mapped[str | None] = mapped_column(String(80), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trade_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_trade_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(20), default="invalid")
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="extracted", index=True)
    booked_position_id: Mapped[int | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    document: Mapped[ConfirmationDocument] = relationship(back_populates="trades")
```

`backend/alembic/versions/0052_trade_confirmations.py`: **migration-local `sa.Table`/`op.create_table` definitions only** (mirror `0043`/`0050` style): three `op.create_table` calls with the same columns/FKs/indexes; `down_revision = "0051_pricing_parameter_row_position_id"` (use the actual revision id string from that file's `revision =` attribute, not the filename). Downgrade drops in reverse order.

- [ ] **Step 4: Run test + migration dry-run**

```bash
.venv/bin/python -m pytest tests/test_confirmations_models.py -x -q
.venv/bin/python -m alembic upgrade head   # against the live DB per CLAUDE.md
```

- [ ] **Step 5: Commit** — `feat(confirmations): models + migration 0052 for trade confirmation intake`

---

### Task 2: Document extraction — text, image-page detection, page render

**Files:**
- Create: `backend/app/services/confirmations/__init__.py` (empty)
- Create: `backend/app/services/confirmations/extract.py`
- Test: `tests/test_confirmations_extract.py` (+ fixture builders inside the test file)

**Interfaces:**
- Produces:
  - `PageContent` dataclass: `index: int` (1-based), `text: str`, `image_png: bytes | None`
  - `DocumentContent` dataclass: `pages: list[PageContent]`, `page_count: int`, `extract_mode: str` (`"text" | "vision" | "mixed"`)
  - `extract_document(path: Path) -> DocumentContent` (raises `ValueError` on unsupported extension)
  - `MIN_TEXT_CHARS_PER_PAGE = 40`

- [ ] **Step 1: Write failing tests with generated fixtures**

```python
# tests/test_confirmations_extract.py
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.services.confirmations.extract import (
    MIN_TEXT_CHARS_PER_PAGE, extract_document,
)

_TEXT_PAGE_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 120>>stream
BT /F1 12 Tf 72 720 Td (Trade Confirmation: BUY 100 EuropeanVanillaOption on AAPL strike 150 maturity 1.0 premium 12.5 USD) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>"""


def _write_text_pdf(path: Path) -> None:
    path.write_bytes(_TEXT_PAGE_PDF)


def _write_blank_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with path.open("wb") as fh:
        w.write(fh)


def _write_docx(path: Path, text: str) -> None:
    doc = DocxDocument()
    doc.add_paragraph(text)
    doc.save(str(path))


def test_text_pdf_extracts_text_mode(tmp_path):
    p = tmp_path / "text.pdf"
    _write_text_pdf(p)
    content = extract_document(p)
    assert content.extract_mode == "text"
    assert content.page_count == 1
    assert "EuropeanVanillaOption" in content.pages[0].text
    assert content.pages[0].image_png is None


def test_blank_pdf_page_renders_image(tmp_path):
    p = tmp_path / "scan.pdf"
    _write_blank_pdf(p)
    content = extract_document(p)
    assert content.extract_mode == "vision"
    assert content.pages[0].image_png is not None
    assert content.pages[0].image_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_docx_extracts_paragraph_and_table_text(tmp_path):
    p = tmp_path / "conf.docx"
    doc = DocxDocument()
    doc.add_paragraph("Confirmation of SnowballOption trade")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Underlying"
    table.rows[0].cells[1].text = "700050.SH"
    doc.save(str(p))
    content = extract_document(p)
    assert content.extract_mode == "text"
    assert "SnowballOption" in content.pages[0].text
    assert "700050.SH" in content.pages[0].text


def test_unsupported_extension_raises(tmp_path):
    p = tmp_path / "conf.txt"
    p.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported"):
        extract_document(p)
```

- [ ] **Step 2: Run — expect ModuleNotFoundError** — `.venv/bin/python -m pytest tests/test_confirmations_extract.py -x -q`

- [ ] **Step 3: Implement `extract.py`**

```python
"""PDF/DOCX text + image extraction for trade confirmations.

Text-first: a PDF page yielding fewer than MIN_TEXT_CHARS_PER_PAGE characters is
treated as a scan and rendered to PNG (pypdfium2) for the vision extraction
path. DOCX is page-less: all paragraphs + table cells become one text "page";
if that text is under the threshold and the archive embeds images, those images
are surfaced for vision instead.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

MIN_TEXT_CHARS_PER_PAGE = 40
_RENDER_SCALE = 2.0  # ~144 dpi; keeps scans legible without huge payloads


@dataclass(frozen=True)
class PageContent:
    index: int
    text: str
    image_png: bytes | None = None


@dataclass(frozen=True)
class DocumentContent:
    pages: list[PageContent]
    page_count: int
    extract_mode: str


def extract_document(path: Path) -> DocumentContent:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    raise ValueError(f"Unsupported confirmation file type: {suffix!r} (want .pdf/.docx)")


def _mode(pages: list[PageContent]) -> str:
    has_text = any(p.image_png is None for p in pages)
    has_image = any(p.image_png is not None for p in pages)
    if has_text and has_image:
        return "mixed"
    return "vision" if has_image else "text"


def _extract_pdf(path: Path) -> DocumentContent:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    texts = [(page.extract_text() or "").strip() for page in reader.pages]
    pages: list[PageContent] = []
    image_indexes = [
        i for i, text in enumerate(texts) if len(text) < MIN_TEXT_CHARS_PER_PAGE
    ]
    rendered: dict[int, bytes] = {}
    if image_indexes:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in image_indexes:
                bitmap = pdf[i].render(scale=_RENDER_SCALE)
                pil = bitmap.to_pil()
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                rendered[i] = buf.getvalue()
        finally:
            pdf.close()
    for i, text in enumerate(texts):
        pages.append(PageContent(index=i + 1, text=text, image_png=rendered.get(i)))
    return DocumentContent(pages=pages, page_count=len(pages), extract_mode=_mode(pages))


def _extract_docx(path: Path) -> DocumentContent:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    chunks = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                chunks.append(" | ".join(cells))
    text = "\n".join(chunks).strip()
    if len(text) >= MIN_TEXT_CHARS_PER_PAGE:
        return DocumentContent(
            pages=[PageContent(index=1, text=text)], page_count=1, extract_mode="text"
        )
    # Near-empty text: surface embedded images (a scan pasted into Word).
    images: list[bytes] = []
    with zipfile.ZipFile(str(path)) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/") and name.lower().endswith(
                (".png", ".jpg", ".jpeg")
            ):
                images.append(zf.read(name))
    if not images:
        return DocumentContent(
            pages=[PageContent(index=1, text=text)], page_count=1, extract_mode="text"
        )
    pages = [
        PageContent(index=i + 1, text="", image_png=_ensure_png(data))
        for i, data in enumerate(images)
    ]
    if text:
        pages.insert(0, PageContent(index=0, text=text))
    return DocumentContent(pages=pages, page_count=len(pages), extract_mode=_mode(pages))


def _ensure_png(data: bytes) -> bytes:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    from PIL import Image

    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
```

(`PIL` arrives as pypdfium2's rendering companion — `pillow` is already in the venv via existing deps; if `uv sync` reports it missing, add `"pillow>=10"` to `pyproject.toml`.)

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_confirmations_extract.py -x -q` — PASS
- [ ] **Step 5: Commit** — `feat(confirmations): PDF/DOCX extraction with image-page vision routing`

---

### Task 3: Extractor LLM client + two-stage extraction

**Files:**
- Create: `backend/app/services/confirmations/llm.py`
- Test: `tests/test_confirmations_llm.py`

**Interfaces:**
- Consumes: `DocumentContent`/`PageContent` from Task 2; `get_registry()` + `build_agent_model` (existing); `get_product_term_schema` tool (`app.tools.product_term_schema`, call `.func(quantark_class=...)`); `_SCHEMA_FAMILIES` from the same module.
- Produces:
  - `ExtractionError(Exception)` with `.raw_response: str | None`
  - `TradeSegment` dataclass: `family: str`, `pages: list[int]`, `anchor: str`
  - `TradeDraft` dataclass: `family: str`, `terms: dict`, `underlying: str | None`, `quantity: float | None`, `entry_price: float | None`, `currency: str | None`, `counterparty: str | None`, `trade_date: str | None`, `external_trade_id: str | None`, `confidence: float | None`, `evidence: dict`
  - `ExtractorClient` protocol: `complete(self, content_parts: list[dict]) -> str`
  - `resolve_confirmation_extractor_selection(registry) -> dict` (tag `"confirmation_extractor"` → `"fast"` → default)
  - `build_extractor_client() -> ExtractorClient` (registry-backed; raises `RuntimeError` if model unavailable)
  - `segment_document(content: DocumentContent, client: ExtractorClient) -> list[TradeSegment]`
  - `extract_trade(content: DocumentContent, segment: TradeSegment, client: ExtractorClient) -> TradeDraft`
  - `CONFIRMATION_EXTRACTOR_TAG = "confirmation_extractor"`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_confirmations_llm.py
import json

import pytest

from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.confirmations.llm import (
    ExtractionError, TradeSegment, extract_trade, segment_document,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, content_parts):
        self.calls.append(content_parts)
        return self.responses.pop(0)


def _text_doc(text="BUY 100 vanilla call on AAPL strike 150"):
    return DocumentContent(
        pages=[PageContent(index=1, text=text)], page_count=1, extract_mode="text"
    )


def test_segment_document_parses_trade_list():
    client = FakeClient([json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100 vanilla"}
    ]})])
    segments = segment_document(_text_doc(), client)
    assert segments == [TradeSegment(
        family="EuropeanVanillaOption", pages=[1], anchor="BUY 100 vanilla")]
    # Prompt must constrain the family vocabulary.
    prompt_text = "".join(p.get("text", "") for p in client.calls[0])
    assert "EuropeanVanillaOption" in prompt_text
    assert "SnowballOption" in prompt_text


def test_segment_invalid_json_retries_once_then_raises():
    client = FakeClient(["not json", "still not json"])
    with pytest.raises(ExtractionError):
        segment_document(_text_doc(), client)
    assert len(client.calls) == 2


def test_extract_trade_targets_family_schema_and_parses_draft():
    payload = {
        "terms": {"strike": 150.0, "maturity_years": 1.0, "option_type": "call"},
        "underlying": "AAPL", "quantity": 100, "entry_price": 12.5,
        "currency": "USD", "counterparty": "Big Bank", "trade_date": "2026-08-01",
        "external_trade_id": "TC-1001", "confidence": 0.92,
        "evidence": {"strike": {"quote": "strike 150", "page": 1}},
    }
    client = FakeClient([json.dumps(payload)])
    seg = TradeSegment(family="EuropeanVanillaOption", pages=[1], anchor="BUY")
    draft = extract_trade(_text_doc(), seg, client)
    assert draft.family == "EuropeanVanillaOption"
    assert draft.terms["strike"] == 150.0
    assert draft.external_trade_id == "TC-1001"
    # Stage-2 prompt must embed the family's legal schema field names.
    prompt_text = "".join(p.get("text", "") for p in client.calls[0])
    assert "strike" in prompt_text


def test_image_pages_become_image_parts():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    doc = DocumentContent(
        pages=[PageContent(index=1, text="", image_png=png)],
        page_count=1, extract_mode="vision",
    )
    client = FakeClient([json.dumps({"trades": []})])
    segment_document(doc, client)
    kinds = {p["type"] for p in client.calls[0]}
    assert "image_url" in kinds


def test_resolver_prefers_dedicated_tag():
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    class Reg:
        def select_by_tag(self, tag):
            return {"channel": "zenmux", "provider": "openai", "model": "vision-x"} \
                if tag == "confirmation_extractor" else None

        def default_selection(self):
            return {"channel": "zenmux", "provider": "openai", "model": "default-y"}

    assert resolve_confirmation_extractor_selection(Reg())["model"] == "vision-x"
```

- [ ] **Step 2: Run — expect ImportError** — `.venv/bin/python -m pytest tests/test_confirmations_llm.py -x -q`

- [ ] **Step 3: Implement `llm.py`**

```python
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

from app.tools.product_term_schema import _SCHEMA_FAMILIES, get_product_term_schema

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
    return json.loads(text)


def _complete_json(client: ExtractorClient, parts: list[dict]) -> dict:
    raw = client.complete(parts)
    try:
        return _json_from_response(raw)
    except (json.JSONDecodeError, AttributeError):
        raw2 = client.complete(parts + [{
            "type": "text",
            "text": "Your previous reply was not valid JSON. Reply with ONLY the JSON object.",
        }])
        try:
            return _json_from_response(raw2)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ExtractionError("extractor returned invalid JSON twice", raw2) from exc


_SEGMENT_INSTRUCTIONS = """You are reading an OTC trade confirmation document.
List every distinct trade it confirms. For each trade pick the QuantArk product
family from EXACTLY this list (or "unknown" if none fits):
{families}

Reply with ONLY a JSON object:
{{"trades": [{{"family": "<family>", "pages": [<page numbers>], "anchor": "<short quote locating the trade>"}}]}}
"""


def segment_document(content, client: ExtractorClient) -> list[TradeSegment]:
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
```

Check the actual import path of `get_registry` (`app.services.deep_agent.channel_registry`) and `build_agent_model` before running; mirror `memory/runtime.py`'s imports if they differ.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_confirmations_llm.py -x -q` — PASS
- [ ] **Step 5: Commit** — `feat(confirmations): two-stage schema-targeted LLM extraction with vision parts`

---

### Task 4: Confirmation service — batch, parse, validate, edit, book, reject

**Files:**
- Create: `backend/app/services/confirmations/service.py`
- Test: `tests/test_confirmations_service.py`

**Interfaces:**
- Consumes: Tasks 1–3; `BookingRequest`, `ProductBookingSpec`, `book_position`, `prepare_booking_product_spec` (`app.services.domains.booking`); `product_family_for_quantark_class` (`app.services.domains.products`); `DEFAULT_ENGINE_BY_PRODUCT_TYPE` (`app.services.engine_configs`); `record_audit` (`app.services.audit`); `TaskRun` + `submit_async_task` (`app.services.task_runner`); `get_settings`.
- Produces:
  - `store_confirmation_files(settings, files: list[tuple[str, bytes]]) -> list[StoredFile]` where `StoredFile` = dataclass `filename, stored_path: str, sha256: str, byte_len: int, mime: str`
  - `create_batch(session, *, files: list[StoredFile], source: str, portfolio_id: int | None) -> ConfirmationBatch`
  - `parse_document(session, document, *, client) -> None` (mutates rows; never raises for extraction failures — records `status="failed"`)
  - `run_parse_batch(batch_id: int, task_id: int | None = None, *, client=None) -> None` (opens own sessions; the async-task entrypoint)
  - `queue_parse_task(session, batch) -> TaskRun`
  - `validate_trade_terms(trade) -> tuple[str, list[str]]`
  - `update_trade(session, trade_id, *, updates: dict) -> ExtractedTrade` (raises `ValueError` on unknown id / non-`extracted` status)
  - `book_trade(session, trade_id, *, portfolio_id=None, actor="desk_user") -> dict` — `{"ok": True, "position_id": ...}` | `{"ok": False, "error": "already_booked", "position_id": ...}` | `{"ok": False, "error": ...}`
  - `reject_trade(session, trade_id, *, reason=None) -> ExtractedTrade`
  - `confirmation_source_trade_id(trade, document) -> str`

- [ ] **Step 1: Write failing tests** (uses conftest `session` fixture + a seeded container portfolio; look at any existing test that creates a `Portfolio(kind="container")` — e.g. grep `PortfolioKind.CONTAINER` in `tests/` — and copy that helper)

```python
# tests/test_confirmations_service.py
import json

from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Position
from app.services.confirmations import service as svc
from app.services.confirmations.extract import DocumentContent, PageContent


class FakeClient:
    """Stage-1 then stage-2 responses, one document."""
    def __init__(self, segment_json, trade_jsons):
        self.responses = [segment_json, *trade_jsons]
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


VANILLA_TERMS = {
    "option_type": "call", "strike": 150.0, "maturity_years": 1.0,
}


def _seed_doc(session, tmp_path, text="BUY 100 AAPL call strike 150"):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    stored = tmp_path / "c.pdf"
    stored.write_bytes(b"%PDF-1.4 minimal")
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="c.pdf", stored_path=str(stored),
        sha256="c" * 64, byte_len=16, mime="application/pdf",
    )
    session.add(doc)
    session.flush()
    return batch, doc


def _fake_for_vanilla():
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100"}]})
    trade = json.dumps({
        "terms": VANILLA_TERMS, "underlying": "AAPL", "quantity": 100,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
        "trade_date": "2026-08-01", "external_trade_id": "TC-1001",
        "confidence": 0.9,
        "evidence": {"strike": {"quote": "strike 150", "page": 1}},
    })
    return FakeClient(seg, [trade])


def test_parse_document_produces_valid_trade(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="BUY 100 AAPL call")],
            page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    session.flush()
    assert doc.status == "parsed"
    trade = doc.trades[0]
    assert trade.family == "EuropeanVanillaOption"
    assert trade.validation_status == "valid"
    assert trade.extracted_terms == trade.terms
    assert trade.evidence["strike"]["page"] == 1


def test_parse_failure_isolated_to_document(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: (_ for _ in ()).throw(ValueError("boom")),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    assert doc.status == "failed"
    assert "boom" in (doc.error or "")


def test_invalid_terms_marked_invalid_not_booked(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "a"}]})
    bad = json.dumps({"terms": {"strike": -5}, "underlying": "AAPL",
                      "quantity": 1, "confidence": 0.5, "evidence": {}})
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=FakeClient(seg, [bad]))
    trade = doc.trades[0]
    assert trade.validation_status == "invalid"
    assert trade.validation_errors


def test_unknown_family_unsupported(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    seg = json.dumps({"trades": [{"family": "unknown", "pages": [1], "anchor": "a"}]})
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=FakeClient(seg, []))
    assert doc.trades[0].validation_status == "unsupported"


def test_update_trade_revalidates(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    trade = doc.trades[0]
    updated = svc.update_trade(session, trade.id, updates={"terms": {**VANILLA_TERMS, "strike": 155.0}})
    assert updated.terms["strike"] == 155.0
    assert updated.validation_status == "valid"
    assert updated.extracted_terms["strike"] == 150.0  # immutable original


def test_book_trade_books_and_is_idempotent(session, tmp_path, monkeypatch, container_portfolio):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    trade = doc.trades[0]
    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert result["ok"] is True
    position = session.get(Position, result["position_id"])
    assert position.source_trade_id == "TC-1001"
    assert trade.status == "booked"
    again = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert again["ok"] is False and again["error"] == "already_booked"
```

Add a `container_portfolio` fixture at the top of the test file (or reuse an existing conftest fixture if `grep -rn "container" tests/conftest.py` shows one):

```python
import pytest
from app.models import Portfolio

@pytest.fixture
def container_portfolio(session):
    p = Portfolio(name="Conf Test Book", kind="container")
    session.add(p)
    session.flush()
    return p
```

(Verify `Portfolio`'s required constructor fields against a neighboring test before assuming `name`/`kind` suffice.)

- [ ] **Step 2: Run — expect failures** — `.venv/bin/python -m pytest tests/test_confirmations_service.py -x -q`

- [ ] **Step 3: Implement `service.py`**

```python
"""Trade-confirmation pipeline: store -> parse -> validate -> review -> book.

The single write path for every surface (REST router, agent tools). All
booking goes through domains.booking.book_position; extraction output is never
bookable without prepare_booking_product_spec passing and a human action.
"""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Position, TaskRun,
)
from ..audit import record_audit
from ..domains.booking import (
    BookingRequest, ProductBookingSpec, book_position, prepare_booking_product_spec,
)
from ..domains.products import product_family_for_quantark_class
from ..engine_configs import DEFAULT_ENGINE_BY_PRODUCT_TYPE
from ..task_runner import submit_async_task
from .extract import extract_document
from .llm import (
    ExtractionError, TradeDraft, build_extractor_client, extract_trade,
    segment_document,
)
from app.tools.product_term_schema import _SCHEMA_FAMILIES


@dataclass(frozen=True)
class StoredFile:
    filename: str
    stored_path: str
    sha256: str
    byte_len: int
    mime: str


def store_confirmation_files(settings, files: list[tuple[str, bytes]]) -> list[StoredFile]:
    target_dir = settings.artifact_dir / "uploads" / "confirmations"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored: list[StoredFile] = []
    for filename, data in files:
        name = Path(filename or "confirmation.pdf").name
        digest = hashlib.sha256(data).hexdigest()
        target = target_dir / f"{digest[:12]}-{name}"
        target.write_bytes(data)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        stored.append(StoredFile(
            filename=name, stored_path=str(target), sha256=digest,
            byte_len=len(data), mime=mime,
        ))
    return stored


def create_batch(
    session: Session, *, files: list[StoredFile], source: str,
    portfolio_id: int | None,
) -> ConfirmationBatch:
    batch = ConfirmationBatch(source=source, default_portfolio_id=portfolio_id)
    session.add(batch)
    session.flush()
    for f in files:
        session.add(ConfirmationDocument(
            batch_id=batch.id, filename=f.filename, stored_path=f.stored_path,
            sha256=f.sha256, byte_len=f.byte_len, mime=f.mime,
        ))
    session.flush()
    return batch


def _draft_to_row(document_id: int, seq: int, draft: TradeDraft) -> ExtractedTrade:
    return ExtractedTrade(
        document_id=document_id, seq=seq, family=draft.family,
        extracted_terms=dict(draft.terms), terms=dict(draft.terms),
        underlying=draft.underlying, quantity=draft.quantity,
        entry_price=draft.entry_price, currency=draft.currency,
        counterparty=draft.counterparty, trade_date=draft.trade_date,
        external_trade_id=draft.external_trade_id, confidence=draft.confidence,
        evidence=dict(draft.evidence), validation_errors=[],
    )


def validate_trade_terms(trade: ExtractedTrade) -> tuple[str, list[str]]:
    if trade.family not in _SCHEMA_FAMILIES:
        return "unsupported", [f"unsupported product family {trade.family!r}"]
    engine_name = DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(trade.family)
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=dict(trade.terms or {}),
        components=[],
    )
    try:
        prepare_booking_product_spec(spec, engine_name=engine_name)
    except Exception as exc:  # noqa: BLE001 — validation failures are data here
        return "invalid", [str(exc)]
    if not trade.underlying:
        return "invalid", ["missing underlying"]
    if not trade.quantity:
        return "invalid", ["missing quantity"]
    return "valid", []


def parse_document(session: Session, document: ConfirmationDocument, *, client) -> None:
    document.status = "parsing"
    session.flush()
    debug: dict = {}
    try:
        content = extract_document(Path(document.stored_path))
        document.page_count = content.page_count
        document.extract_mode = content.extract_mode
        segments = segment_document(content, client)
        drafts = []
        for seg in segments:
            if seg.family not in _SCHEMA_FAMILIES:
                drafts.append(TradeDraft(family=seg.family, terms={}))
                continue
            drafts.append(extract_trade(content, seg, client))
        for i, draft in enumerate(drafts, start=1):
            row = _draft_to_row(document.id, i, draft)
            session.add(row)
            session.flush()
            row.validation_status, row.validation_errors = validate_trade_terms(row)
        document.status = "parsed"
        document.parsed_at = datetime.utcnow()
        document.model_provenance = getattr(client, "selection", None)
    except ExtractionError as exc:
        debug["raw_response"] = exc.raw_response
        document.status = "failed"
        document.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — per-document isolation is the contract
        document.status = "failed"
        document.error = str(exc)
    finally:
        if debug:
            document.extraction_debug = debug
        session.flush()


def run_parse_batch(batch_id: int, task_id: int | None = None, *, client=None) -> None:
    """Async-task entrypoint: own session per phase, per-document isolation."""
    from ... import database

    database.init_db()
    resolved_client = client or build_extractor_client()
    with database.SessionLocal() as session:
        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            return
        doc_ids = [d.id for d in batch.documents]
        task = session.get(TaskRun, task_id) if task_id else None
        if task is not None:
            task.status = "running"
            session.commit()
        for doc_id in doc_ids:
            document = session.get(ConfirmationDocument, doc_id)
            parse_document(session, document, client=resolved_client)
            session.commit()
        if task is not None:
            statuses = {d.status for d in session.get(ConfirmationBatch, batch_id).documents}
            task.status = "failed" if statuses == {"failed"} else "completed"
            task.finished_at = datetime.utcnow()
            session.commit()


def queue_parse_task(session: Session, batch: ConfirmationBatch) -> TaskRun:
    task = TaskRun(kind="confirmation_parse", status="queued")
    session.add(task)
    session.flush()
    batch.task_id = task.id
    session.flush()
    return task


def dispatch_parse(batch_id: int, task_id: int) -> None:
    submit_async_task(run_parse_batch, batch_id, task_id)


_EDITABLE_FIELDS = {
    "family", "underlying", "quantity", "entry_price", "currency", "terms",
}


def update_trade(session: Session, trade_id: int, *, updates: dict) -> ExtractedTrade:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        raise ValueError(f"Extracted trade {trade_id} not found")
    if trade.status != "extracted":
        raise ValueError(f"Trade {trade_id} is {trade.status}; only extracted trades are editable")
    for key, value in updates.items():
        if key not in _EDITABLE_FIELDS:
            raise ValueError(f"Field {key!r} is not editable")
        setattr(trade, key, value)
    trade.validation_status, trade.validation_errors = validate_trade_terms(trade)
    session.flush()
    return trade


def confirmation_source_trade_id(trade: ExtractedTrade, document: ConfirmationDocument) -> str:
    if trade.external_trade_id:
        return trade.external_trade_id
    return f"conf:{document.sha256[:12]}:{trade.seq}"


def book_trade(
    session: Session, trade_id: int, *, portfolio_id: int | None = None,
    actor: str = "desk_user",
) -> dict:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        return {"ok": False, "error": "not_found"}
    if trade.status == "booked":
        return {"ok": False, "error": "already_booked",
                "position_id": trade.booked_position_id}
    if trade.status != "extracted":
        return {"ok": False, "error": f"trade is {trade.status}"}
    trade.validation_status, trade.validation_errors = validate_trade_terms(trade)
    if trade.validation_status != "valid":
        return {"ok": False, "error": "validation_failed",
                "detail": trade.validation_errors}
    document = trade.document
    target_portfolio = portfolio_id or document.batch.default_portfolio_id
    if not target_portfolio:
        return {"ok": False, "error": "no_target_portfolio"}
    source_trade_id = confirmation_source_trade_id(trade, document)
    existing = (
        session.query(Position)
        .filter(Position.portfolio_id == target_portfolio,
                Position.source_trade_id == source_trade_id)
        .one_or_none()
    )
    if existing is not None:
        return {"ok": False, "error": "already_booked", "position_id": existing.id}
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=dict(trade.terms or {}),
        components=[],
        source_payload={
            "confirmation_document": document.filename,
            "confirmation_sha256": document.sha256,
            "counterparty": trade.counterparty,
            "trade_date": trade.trade_date,
            "extracted_terms": trade.extracted_terms,
        },
    )
    try:
        position = book_position(session, BookingRequest(
            portfolio_id=target_portfolio,
            product=spec,
            quantity=float(trade.quantity or 0.0),
            entry_price=float(trade.entry_price or 0.0),
            source_trade_id=source_trade_id,
            engine_name=DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(trade.family),
            source="confirmation",
            actor=actor,
            source_payload={
                "confirmation_batch_id": document.batch_id,
                "confirmation_document_id": document.id,
                "extracted_trade_id": trade.id,
                "counterparty": trade.counterparty,
            },
        ))
    except ValueError as exc:
        return {"ok": False, "error": "booking_failed", "detail": str(exc)}
    trade.status = "booked"
    trade.booked_position_id = position.id
    session.flush()
    return {"ok": True, "position_id": position.id}


def reject_trade(session: Session, trade_id: int, *, reason: str | None = None) -> ExtractedTrade:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        raise ValueError(f"Extracted trade {trade_id} not found")
    if trade.status == "booked":
        raise ValueError("booked trades cannot be rejected")
    trade.status = "rejected"
    trade.reject_reason = reason
    session.flush()
    return trade
```

Verify `product_family_for_quantark_class` exists with that exact name (`grep -n "def product_family_for_quantark_class" backend/app/services/domains/products.py`); if the signature differs, adapt at the call sites (both `validate_trade_terms` and `book_trade`).

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_confirmations_service.py -x -q` — PASS (iterate on the vanilla terms if `prepare_booking_product_spec` rejects `maturity_years` vocabulary — fix the TEST terms, never bypass the gate)
- [ ] **Step 5: Commit** — `feat(confirmations): pipeline service — parse, validate, edit, book, reject`

---

### Task 5: REST router + async parse wiring + audit events

**Files:**
- Create: `backend/app/routers/confirmations.py`
- Modify: `backend/app/schemas.py` (Out/In models, near `PositionImportBatchOut`)
- Modify: `backend/app/main.py` (`app.include_router(build_confirmations_router(get_db=get_db))` next to the other `include_router` calls ~line 4140)
- Test: `tests/test_confirmations_api.py`

**Interfaces:**
- Consumes: everything from Task 4.
- Produces REST: `POST /api/confirmations` (multipart `files: list[UploadFile]`, form `portfolio_id: int | None`) → `ConfirmationBatchOut`; `GET /api/confirmations` → `list[ConfirmationBatchOut]`; `GET /api/confirmations/{batch_id}` → `ConfirmationBatchDetailOut` (documents + trades); `PUT /api/confirmations/trades/{trade_id}` (`ExtractedTradeUpdateIn`) → `ExtractedTradeOut`; `POST /api/confirmations/trades/{trade_id}/book` (`BookTradeIn {portfolio_id?}`) → dict; `POST /api/confirmations/trades/{trade_id}/reject` (`RejectTradeIn {reason?}`) → `ExtractedTradeOut`.

Schemas (`schemas.py`):

```python
class ExtractedTradeOut(BaseModel):
    id: int
    document_id: int
    seq: int
    family: str
    extracted_terms: dict[str, Any] = Field(default_factory=dict)
    terms: dict[str, Any] = Field(default_factory=dict)
    underlying: str | None = None
    quantity: float | None = None
    entry_price: float | None = None
    currency: str | None = None
    counterparty: str | None = None
    trade_date: str | None = None
    external_trade_id: str | None = None
    confidence: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    validation_status: str
    validation_errors: list[Any] = Field(default_factory=list)
    status: str
    booked_position_id: int | None = None
    reject_reason: str | None = None
    model_config = {"from_attributes": True}


class ConfirmationDocumentOut(BaseModel):
    id: int
    filename: str
    sha256: str
    byte_len: int
    mime: str
    page_count: int | None = None
    extract_mode: str | None = None
    status: str
    error: str | None = None
    model_provenance: dict[str, Any] | None = None
    parsed_at: datetime | None = None
    trades: list[ExtractedTradeOut] = Field(default_factory=list)
    model_config = {"from_attributes": True}


class ConfirmationBatchOut(BaseModel):
    id: int
    source: str
    default_portfolio_id: int | None = None
    task_id: int | None = None
    created_at: datetime
    documents: list[ConfirmationDocumentOut] = Field(default_factory=list)
    model_config = {"from_attributes": True}


class ExtractedTradeUpdateIn(BaseModel):
    family: str | None = None
    underlying: str | None = None
    quantity: float | None = None
    entry_price: float | None = None
    currency: str | None = None
    terms: dict[str, Any] | None = None


class BookTradeIn(BaseModel):
    portfolio_id: int | None = None


class RejectTradeIn(BaseModel):
    reason: str | None = None
```

Router skeleton (mirror `routers/limits.py`'s `build_..._router(get_db=...)` factory shape):

```python
# backend/app/routers/confirmations.py
"""REST surface over services/confirmations — upload, review, book."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..schemas import (
    BookTradeIn, ConfirmationBatchOut, ExtractedTradeOut, ExtractedTradeUpdateIn,
    RejectTradeIn,
)
from ..services.audit import record_audit
from ..services.confirmations import service as confirmations


def build_confirmations_router(*, get_db) -> APIRouter:
    router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])

    @router.post("", response_model=ConfirmationBatchOut)
    def upload_confirmations(
        files: list[UploadFile] = File(...),
        portfolio_id: int | None = Form(None),
        session: Session = Depends(get_db),
    ):
        payload = [(f.filename or "confirmation.pdf", f.file.read()) for f in files]
        stored = confirmations.store_confirmation_files(get_settings(), payload)
        batch = confirmations.create_batch(
            session, files=stored, source="web", portfolio_id=portfolio_id)
        task = confirmations.queue_parse_task(session, batch)
        record_audit(
            session, event_type="confirmations.uploaded", actor="desk_user",
            subject_type="confirmation_batch", subject_id=batch.id,
            payload={"files": [s.filename for s in stored], "task_id": task.id},
        )
        session.commit()
        try:
            confirmations.dispatch_parse(batch.id, task.id)
        except Exception as exc:  # noqa: BLE001 — mirror limits router dispatch dance
            task.status = "failed"
            task.error = str(exc)
            session.commit()
            raise HTTPException(status_code=500, detail="parse dispatch failed") from exc
        session.refresh(batch)
        return batch

    @router.get("", response_model=list[ConfirmationBatchOut])
    def list_batches(session: Session = Depends(get_db)):
        from ..models import ConfirmationBatch

        return (
            session.query(ConfirmationBatch)
            .order_by(ConfirmationBatch.id.desc())
            .limit(100).all()
        )

    @router.get("/{batch_id}", response_model=ConfirmationBatchOut)
    def get_batch(batch_id: int, session: Session = Depends(get_db)):
        from ..models import ConfirmationBatch

        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="batch not found")
        return batch

    @router.put("/trades/{trade_id}", response_model=ExtractedTradeOut)
    def update_trade(
        trade_id: int, payload: ExtractedTradeUpdateIn,
        session: Session = Depends(get_db),
    ):
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        try:
            trade = confirmations.update_trade(session, trade_id, updates=updates)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        return trade

    @router.post("/trades/{trade_id}/book")
    def book_trade(
        trade_id: int, payload: BookTradeIn, session: Session = Depends(get_db),
    ):
        result = confirmations.book_trade(
            session, trade_id, portfolio_id=payload.portfolio_id)
        if result.get("ok"):
            record_audit(
                session, event_type="confirmations.trade_booked", actor="desk_user",
                subject_type="extracted_trade", subject_id=trade_id,
                payload={"position_id": result["position_id"]},
            )
            session.commit()
        else:
            session.rollback()
        return result

    @router.post("/trades/{trade_id}/reject", response_model=ExtractedTradeOut)
    def reject_trade(
        trade_id: int, payload: RejectTradeIn, session: Session = Depends(get_db),
    ):
        try:
            trade = confirmations.reject_trade(session, trade_id, reason=payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record_audit(
            session, event_type="confirmations.trade_rejected", actor="desk_user",
            subject_type="extracted_trade", subject_id=trade_id,
            payload={"reason": payload.reason},
        )
        session.commit()
        return trade

    return router
```

- [ ] **Step 1: Write failing API tests** — `tests/test_confirmations_api.py` using the conftest `client` fixture. Monkeypatch `app.services.confirmations.service.build_extractor_client` to return the Task-4 `FakeClient`, and monkeypatch `service.extract_document` the same way as Task 4. Tests: (a) POST two files → 200, batch has 2 documents, `task_id` set; poll `GET /api/confirmations/{id}` until documents `parsed` (the executor runs on a thread — poll with a short deadline loop, or monkeypatch `confirmations.dispatch_parse` to call `run_parse_batch` synchronously for determinism — prefer the synchronous monkeypatch); (b) PUT trade edit → validation rerun; (c) book → position created + audit row (`query(AgentActionAudit)`? No — REST audit rows are `AuditLog`; check the model `record_audit` writes and assert on that); (d) reject → status rejected; (e) book again → `already_booked`.
- [ ] **Step 2: Run — expect 404s** — `.venv/bin/python -m pytest tests/test_confirmations_api.py -x -q`
- [ ] **Step 3: Implement schemas + router + `main.py` wiring** (code above)
- [ ] **Step 4: Run tests** — PASS
- [ ] **Step 5: Commit** — `feat(confirmations): REST surface + async parse dispatch + audit events`

---### Task 6: Agent tools + registrations (HITL, capability, pins)

**Files:**
- Create: `backend/app/tools/confirmations.py`
- Modify: `backend/app/tools/__init__.py` (imports + `QUANT_AGENT_TOOLS` entries)
- Modify: `backend/app/services/agents.py` (`DEEP_AGENT_TOOL_NAMES` — add the three names)
- Modify: `backend/app/services/deep_agent/hitl.py` (add `book_extracted_trade` to all three structures: `INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL` → `"write"`, `_LABEL_BY_TOOL` → `"Book extracted trade"`)
- Modify: `tests/test_capability_assignments.py:34` (112 → 115) and the `test_hitl.py` exact-set assertion
- Test: `tests/test_confirmations_tools.py`

**Interfaces:**
- Consumes: Task 4 service; `capability_gated` + `ToolGroup` (`app.services.deep_agent.capability_gate` / `envelopes`); `database.init_db()` + `database.SessionLocal` (tool-session pattern from `tools/limits.py`).
- Produces tools: `parse_trade_confirmation(paths: list[str], portfolio_id: int | None = None)` (DOMAIN_WRITE, no HITL), `get_confirmation_batch(batch_id: int)` (DOMAIN_READ), `book_extracted_trade(trade_id: int, portfolio_id: int | None = None)` (DOMAIN_WRITE + HITL `"write"`).

Tool file:

```python
# backend/app/tools/confirmations.py
"""Agent tools over the trade-confirmation pipeline (services/confirmations).

parse_trade_confirmation runs the parse synchronously inside the tool (agent
turns are long-running already); booking is HITL-gated and rides the same
service gate as the REST surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel

from .. import database
from ..config import get_settings
from ..models import ConfirmationBatch
from ..services.confirmations import service as confirmations
from ..services.confirmations.llm import build_extractor_client
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup


def _batch_out(batch: ConfirmationBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "source": batch.source,
        "default_portfolio_id": batch.default_portfolio_id,
        "documents": [
            {
                "document_id": d.id, "filename": d.filename, "status": d.status,
                "extract_mode": d.extract_mode, "error": d.error,
                "trades": [
                    {
                        "trade_id": t.id, "seq": t.seq, "family": t.family,
                        "underlying": t.underlying, "quantity": t.quantity,
                        "entry_price": t.entry_price, "currency": t.currency,
                        "counterparty": t.counterparty,
                        "external_trade_id": t.external_trade_id,
                        "confidence": t.confidence, "terms": dict(t.terms or {}),
                        "validation_status": t.validation_status,
                        "validation_errors": list(t.validation_errors or []),
                        "status": t.status,
                        "booked_position_id": t.booked_position_id,
                    }
                    for t in d.trades
                ],
            }
            for d in batch.documents
        ],
    }


class ParseTradeConfirmationInput(BaseModel):
    paths: list[str]
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("parse_trade_confirmation", args_schema=ParseTradeConfirmationInput)
def parse_trade_confirmation(paths: list[str], portfolio_id: int | None = None) -> dict:
    """Parse uploaded trade confirmation files (PDF/DOCX, scans supported) into
    reviewable extracted trades. `paths` are the stored paths returned when the
    user attached files to this chat. Returns per-document status plus each
    extracted trade's terms, validation result, and trade_id. Booking is a
    separate confirmed step (book_extracted_trade)."""
    settings = get_settings()
    uploads_root = (settings.artifact_dir / "uploads").resolve()
    resolved: list[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.is_relative_to(uploads_root):
            return {"ok": False, "error": f"path outside uploads dir: {raw}"}
        if not p.exists():
            return {"ok": False, "error": f"file not found: {raw}"}
        resolved.append(p)
    if not resolved:
        return {"ok": False, "error": "no files given"}
    try:
        client = build_extractor_client()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    database.init_db()
    with database.SessionLocal() as session:
        stored = [
            confirmations.StoredFile(
                filename=p.name, stored_path=str(p),
                sha256=confirmations.sha256_of_file(p),
                byte_len=p.stat().st_size,
                mime="application/pdf" if p.suffix.lower() == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            for p in resolved
        ]
        batch = confirmations.create_batch(
            session, files=stored, source="agent", portfolio_id=portfolio_id)
        session.commit()
        for document in batch.documents:
            confirmations.parse_document(session, document, client=client)
            session.commit()
        session.refresh(batch)
        return {"ok": True, **_batch_out(batch)}


class GetConfirmationBatchInput(BaseModel):
    batch_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_confirmation_batch", args_schema=GetConfirmationBatchInput)
def get_confirmation_batch(batch_id: int) -> dict:
    """Read a confirmation batch: per-document parse status and every extracted
    trade with its validation result and booking status."""
    database.init_db()
    with database.SessionLocal() as session:
        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            return {"ok": False, "error": f"batch {batch_id} not found"}
        return {"ok": True, **_batch_out(batch)}


class BookExtractedTradeInput(BaseModel):
    trade_id: int
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_extracted_trade", args_schema=BookExtractedTradeInput)
def book_extracted_trade(trade_id: int, portfolio_id: int | None = None) -> dict:
    """Book one reviewed extracted trade into a portfolio through the standard
    booking gate. Fails honestly on validation errors, missing target portfolio,
    or an already-booked source trade id. HITL — requires confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        result = confirmations.book_trade(
            session, trade_id, portfolio_id=portfolio_id, actor="desk_agent")
        if result.get("ok"):
            session.commit()
        else:
            session.rollback()
        return result
```

Add to `service.py` (Task 4 file) the small helper the tool uses:

```python
def sha256_of_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 1: Write failing tests** — `tests/test_confirmations_tools.py`: (a) `parse_trade_confirmation.func(paths=["/etc/passwd"])` → path-containment error; (b) with a real stored file under `settings.artifact_dir/uploads/chat/` + monkeypatched `build_extractor_client`/`extract_document` → batch created, trades returned; (c) `book_extracted_trade.func(trade_id=...)` books (reuse Task-4 seeding); (d) registration assertions:

```python
def test_confirmation_tools_registered():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    for expected in ("parse_trade_confirmation", "get_confirmation_batch",
                     "book_extracted_trade"):
        assert expected in names
        assert expected in DEEP_AGENT_TOOL_NAMES


def test_book_tool_is_hitl_write():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES, _LABEL_BY_TOOL, _RISK_LEVEL_BY_TOOL,
    )

    assert "book_extracted_trade" in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL["book_extracted_trade"] == "write"
    assert "book_extracted_trade" in _LABEL_BY_TOOL
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES as names
    assert "parse_trade_confirmation" not in names
```

- [ ] **Step 2: Run — expect failures**, then implement + register everywhere listed in **Files**, bumping the pin `assert len(QUANT_AGENT_TOOLS) == 112` → `115` and the `test_hitl.py` exact set.
- [ ] **Step 3: Run the full registration-adjacent suite** — `.venv/bin/python -m pytest tests/test_confirmations_tools.py tests/test_capability_assignments.py tests/test_hitl.py tests/test_audit_registration.py -x -q` — PASS
- [ ] **Step 4: Commit** — `feat(confirmations): agent tools (parse/get/book) with HITL booking + registrations`

---

### Task 7: Skill + catalog test pins

**Files:**
- Create: `backend/app/skills/workflows/positions/book-trade-confirmation/SKILL.md`
- Modify: every test that pins the skill catalog — enumerate with `grep -rln "book-position" tests/` and update each exact-set/count assertion (known: `tests/test_skills_catalog_v2.py`, `tests/test_routing_table.py`; the grep will reveal the rest — memory says ~6 files)

**SKILL.md** (frontmatter mirrors `positions/book-position/SKILL.md`; routing line is mandatory — unroutable-skill lesson):

```markdown
---
name: book-trade-confirmation
description: Parse uploaded trade confirmation documents (PDF/DOCX, including scans) into structured trades and book the reviewed ones. Use when the user attaches confirmation files to the chat, mentions a trade confirmation / termsheet PDF to book, or asks to turn counterparty confirmations into positions.
domain: positions
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - attachment_paths
optional_context:
  - portfolio_id
write_actions: true
confirmation_required: true
success_criteria:
  - Every attached document is parsed or reported failed with its error
  - Each extracted trade's family, key terms, validation status, and evidence are reported before any booking
  - Booking happens only per-trade after user confirmation, and booked position ids are reported
routing:
  - request: "Book trades from an uploaded confirmation document"
    persona: trader
---

## When to use

- The user attached one or more PDF/DOCX trade confirmations and wants them booked.
- A confirmation was uploaded earlier and its batch needs review or booking.

## Procedure

1. Collect the attachment stored paths from the turn's attachment manifest.
2. Call `parse_trade_confirmation(paths, portfolio_id?)`. Report per-document
   status; for failed documents report the error and stop for those files.
3. For each extracted trade, report family, underlying, quantity, entry price,
   currency, counterparty, validation status, and the evidence quotes. Never
   silently correct a validation failure — show the errors and ask.
4. Book only trades the user confirms, one `book_extracted_trade(trade_id,
   portfolio_id?)` call each (HITL confirmation applies). Report booked
   position ids.
5. `already_booked` responses are terminal — report the existing position id,
   do not retry.

## Failure modes

- `vision-capable extractor required` / extractor unavailable: report it; do
  not attempt to transcribe scans manually.
- `no_target_portfolio`: ask which portfolio to book into.
- Validation errors: surface them for human editing on the Confirmations page
  or apply the user's corrected terms via the review flow — never book around
  the gate.
```

- [ ] **Step 1: Add the skill file, then run the catalog suites** — `.venv/bin/python -m pytest tests/test_skills_catalog_v2.py tests/test_routing_table.py tests/test_skill_lint.py tests/test_personas.py -x -q` (plus every file the grep found). Update each exact-set assertion to include `book-trade-confirmation`.
- [ ] **Step 2: Full backend suite checkpoint** — `.venv/bin/python -m pytest -x -q` — fix any remaining pinned-set fallout NOW, before frontend work.
- [ ] **Step 3: Commit** — `feat(confirmations): book-trade-confirmation skill + catalog pins`

---

### Task 8: Chat attachments — backend seam

**Files:**
- Modify: `backend/app/schemas.py` (`AgentAttachmentIn` + `AgentMessageCreate.attachments`)
- Modify: `backend/app/main.py` (`POST /api/chat/uploads` endpoint near `_store_upload` ~line 725; attachment meta + manifest in `stream_chat_message` ~line 940)
- Test: `tests/test_chat_uploads.py`

**Interfaces:**
- Produces: `POST /api/chat/uploads` (multipart, single `file`) → `{"path": str, "filename": str, "sha256": str, "byte_len": int}`; `AgentMessageCreate.attachments: list[AgentAttachmentIn] | None` where `AgentAttachmentIn = {path: str, filename: str, sha256: str | None}`; user-message `meta["attachments"]`; agent-run content suffixed with an attachment manifest.

Schema:

```python
class AgentAttachmentIn(BaseModel):
    path: str
    filename: str
    sha256: str | None = None
```

Add to `AgentMessageCreate`: `attachments: list[AgentAttachmentIn] | None = None`.

Endpoint (inside `create_app`, using the existing `_store_upload` helper):

```python
    @app.post("/api/chat/uploads")
    def upload_chat_attachment(file: UploadFile = File(...)):
        upload_path = _store_upload(file, "chat")
        data = upload_path.read_bytes()
        return {
            "path": str(upload_path),
            "filename": Path(file.filename or upload_path.name).name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_len": len(data),
        }
```

(`hashlib` may need importing in `main.py`; `_store_upload` names files itself — reuse it rather than re-implementing.)

In `stream_chat_message`: persist `"attachments": [a.model_dump() for a in payload.attachments]` into `user_msg.meta` (next to `page_context`), and build the content handed to the agent run:

```python
        agent_content = payload.content
        if payload.attachments:
            manifest = "\n".join(
                f"- {a.filename} (stored at {a.path})" for a in payload.attachments
            )
            agent_content = (
                f"{payload.content}\n\n[Attached files — pass these stored paths to "
                f"parse_trade_confirmation if the user wants them parsed/booked:]\n{manifest}"
            )
```

Then find where `payload.content` is passed into the stream/persist call below (search `payload.content` within the endpoint body) and pass `agent_content` there instead — the **persisted user message keeps the original `payload.content`**.

- [ ] **Step 1: Failing tests** — `tests/test_chat_uploads.py`: (a) POST a small file to `/api/chat/uploads` → 200, sha256 matches hashlib of the bytes, file exists at `path` under `settings.artifact_dir/uploads/chat/`; (b) `AgentMessageCreate(content="x", attachments=[{"path": "/p", "filename": "f.pdf"}])` round-trips; (c) unit-test the manifest builder if it is factored as a small helper `_attachment_manifest(content, attachments) -> str` (factor it so it is testable — put it next to the endpoint).
- [ ] **Step 2: Run — fail; implement; run — PASS** — `.venv/bin/python -m pytest tests/test_chat_uploads.py -x -q`
- [ ] **Step 3: Commit** — `feat(chat): file attachments — upload endpoint + message meta + agent manifest`

---

### Task 9: Frontend — Confirmations page

**Files:**
- Modify: `frontend/src/types.ts` (Route union + `'confirmations'`; `ConfirmationBatch`, `ConfirmationDocument`, `ExtractedTrade` types)
- Modify: `frontend/src/lib/routing.ts` (path ↔ route mapping for `confirmations`, following the `audit`/`memory` entries)
- Modify: `frontend/src/api/client.ts` (six functions)
- Create: `frontend/src/routes/Confirmations.tsx`, `Confirmations.live.tsx`, `Confirmations.css`
- Modify: `frontend/src/main.tsx` (import + navItems entry `{ route: 'confirmations' as const, label: 'Confirmations' }` + render case)
- Test: `frontend/src/routes/Confirmations.test.tsx`

**Interfaces:**
- Consumes: REST from Task 5.
- Produces (api/client.ts):

```typescript
export async function uploadConfirmations(files: File[], portfolioId: number | null): Promise<ConfirmationBatch> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  if (portfolioId != null) form.append('portfolio_id', String(portfolioId));
  const res = await fetch('/api/confirmations', { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
export const listConfirmationBatches = () => api<ConfirmationBatch[]>('/api/confirmations');
export const getConfirmationBatch = (id: number) => api<ConfirmationBatch>(`/api/confirmations/${id}`);
export const updateExtractedTrade = (id: number, body: Partial<ExtractedTrade>) =>
  api<ExtractedTrade>(`/api/confirmations/trades/${id}`, { method: 'PUT', body: JSON.stringify(body) });
export const bookExtractedTrade = (id: number, portfolioId: number | null) =>
  api<{ ok: boolean; position_id?: number; error?: string; detail?: unknown }>(
    `/api/confirmations/trades/${id}/book`,
    { method: 'POST', body: JSON.stringify({ portfolio_id: portfolioId }) });
export const rejectExtractedTrade = (id: number, reason?: string) =>
  api<ExtractedTrade>(`/api/confirmations/trades/${id}/reject`,
    { method: 'POST', body: JSON.stringify({ reason }) });
```

Types (`types.ts`):

```typescript
export type ExtractedTrade = {
  id: number; document_id: number; seq: number; family: string;
  extracted_terms: Record<string, unknown>; terms: Record<string, unknown>;
  underlying: string | null; quantity: number | null; entry_price: number | null;
  currency: string | null; counterparty: string | null; trade_date: string | null;
  external_trade_id: string | null; confidence: number | null;
  evidence: Record<string, { quote: string; page: number }>;
  validation_status: 'valid' | 'invalid' | 'unsupported';
  validation_errors: unknown[]; status: 'extracted' | 'booked' | 'rejected';
  booked_position_id: number | null; reject_reason: string | null;
};
export type ConfirmationDocument = {
  id: number; filename: string; sha256: string; byte_len: number; mime: string;
  page_count: number | null; extract_mode: 'text' | 'vision' | 'mixed' | null;
  status: 'pending' | 'parsing' | 'parsed' | 'failed'; error: string | null;
  model_provenance: Record<string, string> | null; parsed_at: string | null;
  trades: ExtractedTrade[];
};
export type ConfirmationBatch = {
  id: number; source: string; default_portfolio_id: number | null;
  task_id: number | null; created_at: string; documents: ConfirmationDocument[];
};
```

**Page structure** (presentational `Confirmations.tsx` + data `Confirmations.live.tsx`, Memory-page pattern): left = upload panel (multi-file `<input type="file" multiple accept=".pdf,.docx">` styled as a dropzone + `wl-field` portfolio `<select>` + Upload `Button`) above the batch table (id, created, source, doc count, status summary badges); right/detail = selected batch's documents, each a card: filename, status `Badge`, extract-mode `Chip`, error text if failed, and its trade cards. Trade card: family + validation `Badge`, editable inputs (underlying, quantity, entry_price, currency) + a term-JSON `<textarea>` (v1 term editor is JSON text with parse-on-save), evidence list (`field — "quote" (p.N)`), validation errors list, `Book` / `Reject` buttons (Book disabled unless `validation_status === 'valid'` and a portfolio is chosen), booked → link `#<position_id>` via `routeUrl('positions', ...)`. **Poll**: while any document is `pending`/`parsing`, refetch the batch every 2s (arena pattern: poll only non-terminal). All styling token-only; reuse `Button`, `Badge`, `Chip`, `PageHeader`, `Table`, `wl-field`/`wl-input`.

- [ ] **Step 1: Write failing vitest** — `Confirmations.test.tsx` renders the presentational component with fixture batches covering: parsing doc (spinner/badge), failed doc (error shown), valid trade (Book enabled when portfolio set), invalid trade (errors listed, Book disabled), booked trade (position link). Assert on text/roles.
- [ ] **Step 2: Run — fail** — `cd frontend && npx vitest run src/routes/Confirmations.test.tsx`
- [ ] **Step 3: Implement types, routing, client, components, nav** (follow `Memory.tsx`/`Memory.live.tsx` for state/callback structure and `Memory.css` for token usage)
- [ ] **Step 4: Run tests + typecheck** — `cd frontend && npx vitest run src/routes/Confirmations.test.tsx && npx tsc --noEmit` — PASS
- [ ] **Step 5: Commit** — `feat(frontend): Confirmations page — upload, review, book`

---

### Task 10: Frontend — chat attachment composer (Desk + Pet)

**Files:**
- Modify: `frontend/src/components/ChatComposer.tsx` (+ its css): paperclip button (lucide `Paperclip`), hidden `<input type="file" multiple accept=".pdf,.docx">`, attachment chip row with remove buttons; `onSend` signature becomes `(message: string, attachments?: File[]) => void`; clear attachments after send.
- Modify: `frontend/src/hooks/useAgentChatController.ts`: `sendMessage(message, pageContext?, accountingDate?, contextUsage?, envelope?, confirmedCostPreview?, attachments?: File[])` — before the stream fetch, upload each file:

```typescript
      let attachmentRefs: Array<{ path: string; filename: string; sha256?: string }> = [];
      if (attachments && attachments.length > 0) {
        attachmentRefs = await Promise.all(attachments.map(async (file) => {
          const form = new FormData();
          form.append('file', file);
          const res = await fetch('/api/chat/uploads', { method: 'POST', body: form });
          if (!res.ok) throw new Error(await res.text());
          return res.json();
        }));
      }
```

  and include `attachments: attachmentRefs.length ? attachmentRefs : undefined` in the stream body JSON. Also thread `attachments` through every `onSend` call site (`grep -n "onSend\|sendMessage(" frontend/src` — AgentDesk page, FloatingAgentMiniChat, and the controller's internal replay calls pass `undefined`).
- Modify: `frontend/src/components/MessageList.tsx`: if `message.meta?.attachments` present, render a chip row (`📎 filename`) under the user bubble.
- Test: `frontend/src/components/ChatComposer.test.tsx` (extend if it exists, else create)

- [ ] **Step 1: Failing vitest** — attaching two files shows two chips; removing one leaves one; send calls `onSend(text, [file])` and clears chips; `.txt` file is filtered out (accept guard in code, not only the accept attr).
- [ ] **Step 2: Run — fail; implement; run — PASS** — `cd frontend && npx vitest run src/components/ChatComposer.test.tsx && npx tsc --noEmit && npm test`
- [ ] **Step 3: Commit** — `feat(frontend): chat attachments in Desk + Pet composers`

---

### Task 11: Config, docs, full-suite gate, live smoke

**Files:**
- Modify: `config/agent_channels.example.yml` — document the `confirmation_extractor` tag on one vision-capable model entry (comment + `tags: [confirmation_extractor]` example). The gitignored live `config/agent_channels.yaml` must get the real tag in the MAIN checkout at merge time (worktrees don't carry it).
- Modify: `CHANGELOG.md` — `[Unreleased]` → Added: trade-confirmation-to-book module (backend pipeline, Confirmations page, chat attachments, 3 agent tools + skill, migration 0052).
- Modify: `README.md` — user-facing feature blurb (Confirmations page + agent upload flow).
- Modify: `CLAUDE.md` (root) — new subsystem section: package layout, the `confirmation_extractor` tag two-tier resolution, review-first gate, `source_trade_id` idempotency, the registration checklist gotcha, and that parse is write-class-but-not-HITL.

- [ ] **Step 1: Write docs/config changes**
- [ ] **Step 2: Full gates** — `.venv/bin/python -m pytest -q` (repo root) and `cd frontend && npm test && npx tsc --noEmit` — ALL PASS
- [ ] **Step 3: Live smoke (mandatory before merge — reachability, not satisfiability):** tag a vision model (e.g. a ZenMux vision-capable model or direct DeepSeek if vision-capable) as `confirmation_extractor` in the live yaml; start backend; upload one text PDF and one scanned PDF via `POST /api/confirmations`; verify extracted trades appear with evidence, edit one field, book one trade, verify the position + audit rows. Record model/channel used in the PR description.
- [ ] **Step 4: Commit** — `docs(confirmations): changelog, README, CLAUDE.md + extractor tag example`

---

## Self-review notes (kept honest)

- Spec coverage: data model (T1), extraction+vision+routing (T2/T3), validation gate + booking + idempotency (T4), REST+async+audit (T5), agent tools+HITL+registrations (T6), skill+routing line (T7), chat attachments backend (T8), Confirmations page (T9), Pet/Desk composer (T10), config/docs/live smoke (T11). Out-of-scope items from the spec appear in no task — correct.
- Known verify-at-implementation points are called out inline (import path of `get_registry`, `product_family_for_quantark_class` signature, `Portfolio` constructor, `record_audit` target model, `TaskRun.error`/`finished_at` field names — check `models.py:2113` before using).
- Type consistency: `StoredFile`, `TradeSegment`, `TradeDraft`, `DocumentContent`, service function names are used identically across tasks 2–6.
