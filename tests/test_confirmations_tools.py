"""Agent tools over the trade-confirmation pipeline (Task 6).

Mirrors tests/test_limits_tools.py's own-DB-configuration idiom (this file's
tools call `database.SessionLocal()` directly, no injected `session`
fixture), plus `configure_settings` (tests/test_product_reference_tool.py's
established seam) because `parse_trade_confirmation` also reads
`get_settings().artifact_dir` for its path-containment check.
"""
from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from app import database
from app.config import Settings, configure_settings


VANILLA_TERMS = {
    "option_type": "call", "strike": 150.0, "maturity_years": 1.0,
    "initial_price": 148.0,
}


class FakeExtractorClient:
    """Pops canned stage-1/stage-2 JSON responses in call order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


def _seg_json(family: str = "EuropeanVanillaOption") -> str:
    return json.dumps({"trades": [{"family": family, "pages": [1], "anchor": "BUY 100"}]})


def _trade_json(external_trade_id: str, underlying: str = "AAPL") -> str:
    evidence = {"strike": {"quote": "strike 150", "page": 1}}
    if underlying != "AAPL":
        evidence["underlying"] = {
            "quote": f"Shares: {underlying} (Ticker: AAPL)", "page": 1}
    return json.dumps({
        "terms": VANILLA_TERMS, "underlying": underlying, "quantity": 100,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
        "trade_date": "2026-08-01", "external_trade_id": external_trade_id,
        "confidence": 0.9,
        "evidence": evidence,
    })


def _register_underlying(symbol: str = "AAPL") -> None:
    """Seed a BOOKABLE underlying: ACTIVE **and** tagged "underlying".

    Validation refuses anything else, because book_position would otherwise
    mint a junk instrument for an unrecognised string. Committed, since the
    tools under test open their own sessions.
    """
    from app.models import Instrument

    with database.SessionLocal() as session:
        session.add(Instrument(
            symbol=symbol, display_name=symbol, kind="stock",
            status="active", tags=["underlying"],
        ))
        session.commit()


def _configure_test_env(tmp_path, *, register_underlying: bool = True) -> Settings:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    configure_settings(settings)
    database.configure_database(settings)
    database.init_db()
    if register_underlying:
        _register_underlying()
    return settings


def _reset_settings():
    configure_settings(None)


# ---------------------------------------------------------------------------
# (a) path containment
# ---------------------------------------------------------------------------


def test_parse_rejects_path_outside_uploads_dir(tmp_path):
    from app.tools.confirmations import parse_trade_confirmation

    _configure_test_env(tmp_path)
    try:
        result = parse_trade_confirmation.func(paths=["/etc/passwd"])
    finally:
        _reset_settings()

    assert result["ok"] is False
    assert "no such upload" in result["error"]


def test_parse_rejects_missing_no_paths(tmp_path):
    from app.tools.confirmations import parse_trade_confirmation

    _configure_test_env(tmp_path)
    try:
        result = parse_trade_confirmation.func(paths=[])
    finally:
        _reset_settings()

    assert result == {"ok": False, "error": "no files given"}


# ---------------------------------------------------------------------------
# (b) parse over a real stored file, monkeypatched extractor
# ---------------------------------------------------------------------------


def test_parse_trade_confirmation_creates_batch_with_trades(tmp_path, monkeypatch):
    import app.tools.confirmations as tools_confirmations
    from app.services.confirmations import service as confirmations_service
    from app.services.confirmations.extract import DocumentContent, PageContent

    settings = _configure_test_env(tmp_path)
    try:
        monkeypatch.setattr(
            confirmations_service, "extract_document",
            lambda path: DocumentContent(
                pages=[PageContent(index=1, text="BUY 100 AAPL call")],
                page_count=1, extract_mode="text"),
        )
        fake_client = FakeExtractorClient(_seg_json(), _trade_json("TC-TOOL-1"))
        monkeypatch.setattr(
            tools_confirmations.confirmations_llm,
            "build_extractor_client", lambda selection=None: fake_client,
        )

        uploads_dir = settings.artifact_dir / "uploads" / "chat"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stored = uploads_dir / "c.pdf"
        stored.write_bytes(b"%PDF-1.4 fake confirmation")

        result = tools_confirmations.parse_trade_confirmation.func(paths=[str(stored)])
    finally:
        _reset_settings()

    assert result["ok"] is True
    assert result["source"] == "agent"
    assert len(result["documents"]) == 1
    doc = result["documents"][0]
    assert doc["status"] == "parsed"
    assert len(doc["trades"]) == 1
    trade = doc["trades"][0]
    assert trade["family"] == "EuropeanVanillaOption"
    assert trade["validation_status"] == "valid"
    assert trade["external_trade_id"] == "TC-TOOL-1"
    assert trade["status"] == "extracted"


def test_parse_trade_confirmation_missing_file_errors(tmp_path):
    from app.tools.confirmations import parse_trade_confirmation

    settings = _configure_test_env(tmp_path)
    try:
        missing = settings.artifact_dir / "uploads" / "chat" / "nope.pdf"
        result = parse_trade_confirmation.func(paths=[str(missing)])
    finally:
        _reset_settings()

    assert result["ok"] is False
    assert "no such upload" in result["error"]


# ---------------------------------------------------------------------------
# get_confirmation_batch
# ---------------------------------------------------------------------------


def test_get_confirmation_batch_round_trips(tmp_path, monkeypatch):
    import app.tools.confirmations as tools_confirmations
    from app.services.confirmations import service as confirmations_service
    from app.services.confirmations.extract import DocumentContent, PageContent
    from app.tools.confirmations import get_confirmation_batch

    settings = _configure_test_env(tmp_path)
    try:
        monkeypatch.setattr(
            confirmations_service, "extract_document",
            lambda path: DocumentContent(
                pages=[PageContent(index=1, text="BUY 100 AAPL call")],
                page_count=1, extract_mode="text"),
        )
        fake_client = FakeExtractorClient(_seg_json(), _trade_json("TC-TOOL-2"))
        monkeypatch.setattr(
            tools_confirmations.confirmations_llm,
            "build_extractor_client", lambda selection=None: fake_client,
        )
        uploads_dir = settings.artifact_dir / "uploads" / "chat"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stored = uploads_dir / "c2.pdf"
        stored.write_bytes(b"%PDF-1.4 fake confirmation 2")

        parsed = tools_confirmations.parse_trade_confirmation.func(paths=[str(stored)])
        batch_id = parsed["batch_id"]

        out = get_confirmation_batch.func(batch_id=batch_id)
    finally:
        _reset_settings()

    assert out["ok"] is True
    assert out["batch_id"] == batch_id
    assert out["documents"][0]["trades"][0]["external_trade_id"] == "TC-TOOL-2"


def test_get_confirmation_batch_not_found(tmp_path):
    from app.tools.confirmations import get_confirmation_batch

    _configure_test_env(tmp_path)
    try:
        out = get_confirmation_batch.func(batch_id=999999)
    finally:
        _reset_settings()

    assert out == {"ok": False, "error": "batch 999999 not found"}


# ---------------------------------------------------------------------------
# (c) book_extracted_trade
# ---------------------------------------------------------------------------


def _seed_extracted_trade(
    tmp_path, monkeypatch, *, external_trade_id: str,
    underlying: str = "AAPL", expect_status: str = "valid",
):
    """Seed one extracted trade + a container portfolio, via the same parse
    path Task 4/5 tests use, and return (settings, trade_id, portfolio_id).

    `underlying` defaults to the registered AAPL; pass a legal name to
    exercise the instrument-master gate.
    """
    from app.models import Portfolio
    from app.services.confirmations import service as confirmations_service
    from app.services.confirmations.extract import DocumentContent, PageContent

    settings = _configure_test_env(tmp_path)
    monkeypatch.setattr(
        confirmations_service, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="BUY 100 AAPL call")],
            page_count=1, extract_mode="text"),
    )
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Conf Tool Book", kind="container")
        session.add(portfolio)
        session.flush()

        uploads_dir = settings.artifact_dir / "uploads" / "chat"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stored = uploads_dir / "book.pdf"
        stored.write_bytes(b"%PDF-1.4 fake confirmation to book")

        from app.models import ConfirmationBatch, ConfirmationDocument
        batch = ConfirmationBatch(source="agent", default_portfolio_id=portfolio.id)
        session.add(batch)
        session.flush()
        document = ConfirmationDocument(
            batch_id=batch.id, filename="book.pdf", stored_path=str(stored),
            sha256="d" * 64, byte_len=32, mime="application/pdf",
        )
        session.add(document)
        session.flush()

        fake_client = FakeExtractorClient(
            _seg_json(), _trade_json(external_trade_id, underlying))
        confirmations_service.parse_document(session, document, client=fake_client)
        session.commit()
        trade_id = document.trades[0].id
        assert document.trades[0].validation_status == expect_status

    return settings, trade_id, portfolio.id


def test_book_extracted_trade_books_position(tmp_path, monkeypatch):
    from app.models import Position
    from app.tools.confirmations import book_extracted_trade

    settings, trade_id, portfolio_id = _seed_extracted_trade(
        tmp_path, monkeypatch, external_trade_id="TC-TOOL-BOOK"
    )
    try:
        result = book_extracted_trade.func(trade_id=trade_id, portfolio_id=portfolio_id)
    finally:
        _reset_settings()

    assert result["ok"] is True
    with database.SessionLocal() as session:
        position = session.get(Position, result["position_id"])
        assert position is not None
        assert position.portfolio_id == portfolio_id
        assert position.source_trade_id == "TC-TOOL-BOOK"


def test_book_extracted_trade_refuses_an_unregistered_underlying(tmp_path, monkeypatch):
    """The agent path enforces the SAME instrument-master gate as the web
    review screen — both go through confirmations.book_trade, so an agent
    cannot book a legal name the human path would have refused."""
    from app.models import Position
    from app.tools.confirmations import book_extracted_trade

    settings, trade_id, portfolio_id = _seed_extracted_trade(
        tmp_path, monkeypatch, external_trade_id="TC-TOOL-LEGAL",
        underlying="Apple Inc.", expect_status="invalid",
    )
    try:
        result = book_extracted_trade.func(trade_id=trade_id, portfolio_id=portfolio_id)
    finally:
        _reset_settings()

    assert result["ok"] is False
    with database.SessionLocal() as session:
        assert session.query(Position).count() == 0


def test_book_extracted_trade_rebook_fails_honestly(tmp_path, monkeypatch):
    from app.tools.confirmations import book_extracted_trade

    settings, trade_id, portfolio_id = _seed_extracted_trade(
        tmp_path, monkeypatch, external_trade_id="TC-TOOL-REBOOK"
    )
    try:
        first = book_extracted_trade.func(trade_id=trade_id, portfolio_id=portfolio_id)
        assert first["ok"] is True
        again = book_extracted_trade.func(trade_id=trade_id, portfolio_id=portfolio_id)
    finally:
        _reset_settings()

    assert again["ok"] is False
    assert again["error"] == "already_booked"
    assert again["position_id"] == first["position_id"]


# ---------------------------------------------------------------------------
# (d) registration + HITL assertions
# ---------------------------------------------------------------------------


def test_confirmation_tools_registered():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    for expected in ("parse_trade_confirmation", "get_confirmation_batch",
                     "book_extracted_trade"):
        assert expected in names
        assert expected in DEEP_AGENT_TOOL_NAMES


def test_book_tool_is_hitl_irreversible():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES, _LABEL_BY_TOOL, _RISK_LEVEL_BY_TOOL,
    )

    assert "book_extracted_trade" in INTERRUPT_TOOL_NAMES
    # NOT "write": a "write" level is stripped from the interrupt map under
    # auto mode, which let AUTO book a real position off a parsed PDF with no
    # human in the loop. See test_hitl.py's
    # test_book_extracted_trade_is_irreversible_risk_not_write.
    assert _RISK_LEVEL_BY_TOOL["book_extracted_trade"] == "irreversible"
    assert "book_extracted_trade" in _LABEL_BY_TOOL
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES as names
    assert "parse_trade_confirmation" not in names


def test_read_and_parse_tools_are_not_hitl():
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert "get_confirmation_batch" not in INTERRUPT_TOOL_NAMES
    assert "parse_trade_confirmation" not in INTERRUPT_TOOL_NAMES


def test_tool_capability_groups():
    from app.services.deep_agent.envelopes import ToolGroup
    from app.tools import QUANT_AGENT_TOOLS

    by_name = {t.name: t for t in QUANT_AGENT_TOOLS}
    assert by_name["parse_trade_confirmation"].__capability_group__ is ToolGroup.DOMAIN_WRITE
    assert by_name["get_confirmation_batch"].__capability_group__ is ToolGroup.DOMAIN_READ
    assert by_name["book_extracted_trade"].__capability_group__ is ToolGroup.DOMAIN_WRITE


# ---------------------------------------------------------------------------
# No-cycle regression: app.tools <-> app.services.confirmations
# ---------------------------------------------------------------------------


def test_service_import_before_app_tools_has_no_import_cycle():
    """Regression probe for the app.tools <-> services.confirmations edge.

    services/confirmations/service.py (and llm.py, fixed earlier) used to do
    a module-scope `from app.tools.product_term_schema import ...`. app.tools'
    own package __init__ imports app.tools.confirmations, which needs
    service.py/llm.py fully defined -- so importing services.confirmations
    BEFORE app.tools re-enters those still-executing modules and raises
    ImportError on a name not defined yet. That only "worked" here by the
    incidental ordering inside tools/__init__.py (product_term_schema
    imported ahead of .confirmations) plus the sys.modules submodule
    fallback for `from ..services.confirmations import llm as
    confirmations_llm` -- an import-block reorder in either module would
    silently reintroduce it. A fresh-interpreter subprocess is required (not
    an in-process import) because sys.modules from earlier test-collection
    imports would otherwise mask the ordering this probes for.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    backend_dir = repo_root / "backend"
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{backend_dir}:{existing}" if existing else str(backend_dir)

    result = subprocess.run(
        [sys.executable, "-c",
         "import app.services.confirmations.service; import app.tools"],
        cwd=str(repo_root), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# (e) the server-stamped extractor override (arena vision boards)
# ---------------------------------------------------------------------------


def test_upload_path_accepts_the_spellings_a_model_actually_produces(tmp_path):
    """Measured on arena run #1: told the files were at
    /artifacts/uploads/confirmations/, gpt-5.6-luna tried that, then
    /uploads/..., then the bare filenames -- four reasonable attempts, all
    rejected, and the whole match cascaded from it. /artifacts/ is how the deep
    agent's own filesystem backend mounts artifact_dir, so it is not even a wrong
    guess."""
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    (uploads / "confirmations").mkdir(parents=True)
    target = uploads / "confirmations" / "conf-04.pdf"
    target.write_bytes(b"%PDF-1.4")

    for spelling in (
        str(target),                                   # absolute (the chat path)
        "/artifacts/uploads/confirmations/conf-04.pdf",  # the virtual mount
        "/uploads/confirmations/conf-04.pdf",
        "uploads/confirmations/conf-04.pdf",
        "confirmations/conf-04.pdf",
        "conf-04.pdf",                                 # bare, unique
    ):
        assert _resolve_upload_path(spelling, uploads) == target, spelling


def test_upload_path_still_refuses_anything_outside_the_uploads_root(tmp_path):
    """Containment is unchanged and absolute -- the leniency is about SPELLING,
    never about scope."""
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    uploads.mkdir(parents=True)
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4")

    for escape in (
        str(outside),
        "/etc/passwd",
        "../secret.pdf",
        "confirmations/../../secret.pdf",
    ):
        assert _resolve_upload_path(escape, uploads) is None, escape


def test_upload_path_refuses_an_ambiguous_bare_filename(tmp_path):
    """Two files with one name must be REFUSED, not guessed."""
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    for sub in ("a", "b"):
        (uploads / sub).mkdir(parents=True)
        (uploads / sub / "dup.pdf").write_bytes(b"%PDF-1.4")

    assert _resolve_upload_path("dup.pdf", uploads) is None


def test_upload_path_returns_none_for_a_missing_file(tmp_path):
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    uploads.mkdir(parents=True)
    assert _resolve_upload_path("confirmations/nope.pdf", uploads) is None


def test_parse_error_names_the_accepted_form(tmp_path):
    """The old message stated only what was wrong, so a model could retry four
    times without ever learning what would work."""
    from app.tools.confirmations import parse_trade_confirmation

    _configure_test_env(tmp_path, register_underlying=False)
    try:
        result = parse_trade_confirmation.func(paths=["/etc/passwd"])
    finally:
        _reset_settings()

    assert result["ok"] is False
    assert "confirmations/" in result["error"]
    assert "/artifacts/uploads/" in result["error"]


def test_extractor_override_is_read_from_configurable():
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import _extractor_override_from_config

    override = {"channel": "zenmux", "provider": "bigmodel",
                "model": "z-ai/glm-5.3-flash"}
    assert _extractor_override_from_config(
        {"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: override}}
    ) == override


def test_extractor_override_absent_degrades_to_the_production_tag_ladder():
    """A missing or malformed stamp must fall back, never crash a desk turn."""
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import _extractor_override_from_config

    assert _extractor_override_from_config(None) is None
    assert _extractor_override_from_config({}) is None
    assert _extractor_override_from_config({"configurable": {}}) is None
    assert _extractor_override_from_config({"configurable": None}) is None
    # A non-dict value is IGNORED rather than trusted -- a malformed stamp must
    # not become a half-built selection that fails deep inside model_factory.
    for bad in ("glm", 7, [], {}):
        assert _extractor_override_from_config(
            {"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: bad}}
        ) is None


def test_parse_threads_the_override_into_the_extractor_client(tmp_path, monkeypatch):
    """End-to-end through the real parse path: the stamped selection is what
    build_extractor_client is asked for."""
    import app.tools.confirmations as tools_confirmations
    from app.services.confirmations import service as confirmations_service
    from app.services.confirmations.extract import DocumentContent, PageContent
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    settings = _configure_test_env(tmp_path)
    asked: list = []
    try:
        monkeypatch.setattr(
            confirmations_service, "extract_document",
            lambda path: DocumentContent(
                pages=[PageContent(index=1, text="BUY 100 AAPL call")],
                page_count=1, extract_mode="text"),
        )
        fake_client = FakeExtractorClient(_seg_json(), _trade_json("TC-OVERRIDE-1"))

        def _build(selection=None):
            asked.append(selection)
            return fake_client

        monkeypatch.setattr(
            tools_confirmations.confirmations_llm, "build_extractor_client", _build)

        uploads_dir = settings.artifact_dir / "uploads" / "chat"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stored = uploads_dir / "override.pdf"
        stored.write_bytes(b"%PDF-1.4 fake confirmation")

        override = {"channel": "zenmux", "provider": "bigmodel",
                    "model": "z-ai/glm-5.3-flash"}
        result = tools_confirmations.parse_trade_confirmation.func(
            paths=[str(stored)],
            config={"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: override}},
        )
    finally:
        _reset_settings()

    assert result["ok"] is True
    assert asked == [override]


def test_parse_without_a_stamp_asks_for_no_override(tmp_path, monkeypatch):
    """The production desk path is unchanged: no override, so the tag ladder runs."""
    import app.tools.confirmations as tools_confirmations
    from app.services.confirmations import service as confirmations_service
    from app.services.confirmations.extract import DocumentContent, PageContent

    settings = _configure_test_env(tmp_path)
    asked: list = []
    try:
        monkeypatch.setattr(
            confirmations_service, "extract_document",
            lambda path: DocumentContent(
                pages=[PageContent(index=1, text="BUY 100 AAPL call")],
                page_count=1, extract_mode="text"),
        )
        fake_client = FakeExtractorClient(_seg_json(), _trade_json("TC-OVERRIDE-2"))

        def _build(selection=None):
            asked.append(selection)
            return fake_client

        monkeypatch.setattr(
            tools_confirmations.confirmations_llm, "build_extractor_client", _build)

        uploads_dir = settings.artifact_dir / "uploads" / "chat"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stored = uploads_dir / "no-override.pdf"
        stored.write_bytes(b"%PDF-1.4 fake confirmation")

        result = tools_confirmations.parse_trade_confirmation.func(paths=[str(stored)])
    finally:
        _reset_settings()

    assert result["ok"] is True
    assert asked == [None]


def test_config_is_not_exposed_to_the_model():
    """The override is SERVER-STAMPED. If `config` were in args_schema, a model
    could choose which model reads its own documents -- the same reason fan-out
    attribution is never taken from tool input."""
    from app.tools.confirmations import parse_trade_confirmation

    assert sorted(parse_trade_confirmation.args_schema.model_fields) == [
        "paths", "portfolio_id"
    ]


def test_config_is_injected_through_the_real_invoke_path(tmp_path):
    """Guards a silent revert to tag routing.

    LangChain finds the config parameter by TYPE HINT IDENTITY --
    `_get_runnable_config_param` walks get_type_hints() for `type_ is
    RunnableConfig` -- not by the parameter's NAME. So dropping the annotation to
    a bare `config=None` stops injection entirely and silently.

    Every other test in this file calls `.func(...)` and passes config by hand, so
    none of them would notice: the suite would stay green while the arena reverted
    to reading every contestant's documents with the tag-resolved model, and the
    vision board would measure one model three times.
    """
    from langchain_core.tools.base import _get_runnable_config_param

    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import parse_trade_confirmation

    assert _get_runnable_config_param(parse_trade_confirmation.func) == "config"

    seen: dict = {}
    original = parse_trade_confirmation.func

    # The RunnableConfig ANNOTATION is what makes injection happen -- and because
    # this module uses `from __future__ import annotations`, it is a STRING that
    # get_type_hints resolves against THIS module's globals, which is why
    # RunnableConfig is imported at the top of the file rather than in-function.
    def _spy(paths, portfolio_id=None, config: RunnableConfig = None):  # type: ignore[assignment]
        seen["config"] = config
        return {"ok": False, "error": "spy"}

    override = {"channel": "zenmux", "provider": "bigmodel",
                "model": "z-ai/glm-5.3-flash"}
    _configure_test_env(tmp_path, register_underlying=False)
    object.__setattr__(parse_trade_confirmation, "func", _spy)
    try:
        parse_trade_confirmation.invoke(
            {"paths": []},
            config={"configurable": {
                # capability_gate reads `envelope` off configurable; without it
                # the call is denied before the body ever runs.
                "envelope": "desk_workflow",
                CONFIRMATION_EXTRACTOR_SELECTION_KEY: override,
            }},
        )
    finally:
        object.__setattr__(parse_trade_confirmation, "func", original)
        _reset_settings()

    delivered = (seen.get("config") or {}).get("configurable", {})
    assert delivered.get(CONFIRMATION_EXTRACTOR_SELECTION_KEY) == override


def test_upload_path_bare_filename_is_a_name_not_a_glob(tmp_path):
    """`rglob` takes a PATTERN, so the bare-filename fallback must escape it.

    Unescaped, `conf-0*.pdf` resolves a document the caller never named -- the
    resolver would quietly become a search tool, and on an arena board a model
    could reach a graded document without addressing it.
    """
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    (uploads / "confirmations").mkdir(parents=True)
    target = uploads / "confirmations" / "conf-08-mixed.pdf"
    target.write_bytes(b"%PDF-1.4")

    # The exact name still resolves.
    assert _resolve_upload_path("conf-08-mixed.pdf", uploads) == target

    # Patterns that WOULD have matched it must not resolve.
    for pattern in ("conf-08*.pdf", "conf-0?-mixed.pdf", "*.pdf", "conf-0[0-9]-mixed.pdf"):
        assert _resolve_upload_path(pattern, uploads) is None, pattern


def test_upload_path_handles_a_literal_bracket_in_a_filename(tmp_path):
    """A `[` in a real filename is a character, not a character class."""
    from app.tools.confirmations import _resolve_upload_path

    uploads = (tmp_path / "uploads").resolve()
    (uploads / "confirmations").mkdir(parents=True)
    target = uploads / "confirmations" / "conf[1]-amd.pdf"
    target.write_bytes(b"%PDF-1.4")

    assert _resolve_upload_path("conf[1]-amd.pdf", uploads) == target
