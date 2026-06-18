# Agent Skills Layer v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the v1 agent-skills layer with two new tiers (domain skills + routing skills), refactor procedure skills as workflow-scope orchestrators that name domain skills by reference, backfill six workflow procedures (covering the four candidate procedures from v1 §8 plus a first `high_board` procedure), and add two read-only report-query tools.

**Architecture:** Six tiers in `backend/app/services/deep_agent/skills/`: `policy/` (composable system-prompt fragments — unchanged), `domains/<domain>/<skill>/SKILL.md` (cards = reference; recipes = single safe operation — NEW), `procedures/<persona>/<workflow>/SKILL.md` (workflow-scope; v1 shape extended), `products/<product-id>/SKILL.md` (unchanged), `routing/<flow>/SKILL.md` (orchestrator-only — NEW). Workflow procedures *name* domain skills in their step sequence; routing skills compose persona work via `task(...)` delegations. Two read-only langchain tools (`list_reports`, `get_report`) unblock the first `high_board` procedure. One new filesystem mount (`/artifacts`) lets `high_board` `read_file` HTML report artifacts.

**Tech Stack:** Python 3.11+, deepagents 0.5.3, LangChain, pytest, SQLAlchemy. No frontend changes.

**Reference spec:** `docs/superpowers/specs/2026-05-15-agent-skills-layer-v2-design.md`
**Predecessor plan:** `docs/superpowers/plans/2026-05-14-agent-skills-layer.md` (v1 — this plan extends, doesn't replace)

**Target branch:** `feat/agent-skills-layer-v2`. Ships as one PR with ~30-35 internal commits. Behavior-preserving through Task 18; Task 19 activates the new orchestrator routing.

**File responsibility map:**

| File | Responsibility | Action |
|---|---|---|
| `backend/app/services/langchain_tools.py` | Add `list_reports_tool`, `get_report_tool` read-only tool functions + Pydantic schemas; add to `QUANT_AGENT_TOOLS` | Modify |
| `backend/app/services/deep_agent/orchestrator.py` | Add `/artifacts` mount to backend; add `/artifacts/**` read permission; pass `skills=["/skills/routing/"]` to orchestrator | Modify |
| `backend/app/services/deep_agent/personas.py` | Extend each persona's `skills=[...]` source list per v2 design | Modify |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | Add "Naming routing skills" subsection; extend routing matrix with v2 rows; remove v1 prompt-only snowball compound rule | Modify |
| `backend/app/services/deep_agent/skills/domains/portfolio/portfolio-model/SKILL.md` | Portfolio data model card (Container vs View, position relationship, query patterns) | Create |
| `backend/app/services/deep_agent/skills/domains/pricing/pricing-engines/SKILL.md` | QuantArk engine reference + product→engine map | Create |
| `backend/app/services/deep_agent/skills/domains/market-data/market-data-conventions/SKILL.md` | Sources, refresh cadence, symbol conventions, staleness thresholds | Create |
| `backend/app/services/deep_agent/skills/domains/rfq/rfq-lifecycle/SKILL.md` | RFQ state machine reference | Create |
| `backend/app/services/deep_agent/skills/domains/position/position-snapshot/SKILL.md` | Recipe: build a canonical position snapshot | Create |
| `backend/app/services/deep_agent/skills/domains/position/position-input-enumerate/SKILL.md` | Recipe: derive unique market-data input set | Create |
| `backend/app/services/deep_agent/skills/domains/pricing/pricing-run-propose/SKILL.md` | Recipe: cost-preview + price_positions (HITL) | Create |
| `backend/app/services/deep_agent/skills/domains/pricing/price-product-adhoc/SKILL.md` | Recipe: price_product for ad-hoc specs | Create |
| `backend/app/services/deep_agent/skills/domains/risk/risk-snapshot-read/SKILL.md` | Recipe: read latest risk run | Create |
| `backend/app/services/deep_agent/skills/domains/risk/risk-run-propose/SKILL.md` | Recipe: cost-preview + run_risk (HITL) | Create |
| `backend/app/services/deep_agent/skills/domains/market-data/market-data-fetch/SKILL.md` | Recipe: fetch_market_snapshot for a set of underlyings | Create |
| `backend/app/services/deep_agent/skills/domains/market-data/market-data-drift/SKILL.md` | Recipe: run_python drift computation | Create |
| `backend/app/services/deep_agent/skills/domains/rfq/rfq-draft/SKILL.md` | Recipe: NL → validated RFQ draft | Create |
| `backend/app/services/deep_agent/skills/domains/rfq/rfq-quote/SKILL.md` | Recipe: solve + quote | Create |
| `backend/app/services/deep_agent/skills/domains/rfq/rfq-submit-for-approval/SKILL.md` | Recipe: submit RFQ for approval (HITL) | Create |
| `backend/app/services/deep_agent/skills/domains/reporting/report-batch-run/SKILL.md` | Recipe: run_report_batch (inline) | Create |
| `backend/app/services/deep_agent/skills/domains/reporting/report-create-propose/SKILL.md` | Recipe: cost-preview + create_report (HITL) | Create |
| `backend/app/services/deep_agent/skills/procedures/trader/rfq-intake-and-quote/SKILL.md` | Workflow: end-to-end RFQ intake on the trader side | Create |
| `backend/app/services/deep_agent/skills/procedures/trader/portfolio-pricing-run/SKILL.md` | Workflow: trader-lens repricing | Create |
| `backend/app/services/deep_agent/skills/procedures/trader/market-data-profile/SKILL.md` | Workflow: read-only market-data audit | Create |
| `backend/app/services/deep_agent/skills/procedures/risk_manager/portfolio-pricing-run/SKILL.md` | Workflow: risk-lens repricing variant (same catalog name as trader's) | Create |
| `backend/app/services/deep_agent/skills/procedures/risk_manager/risk-report-workflow/SKILL.md` | Workflow: end-to-end risk reporting | Create |
| `backend/app/services/deep_agent/skills/procedures/high_board/report-query-and-display/SKILL.md` | Workflow: governance read-side report query (first high_board skill) | Create |
| `backend/app/services/deep_agent/skills/routing/pricing-and-risk-compound/SKILL.md` | Routing: trader pricing + risk_manager pricing-and-report compound flow | Create |
| `backend/app/services/deep_agent/skills/routing/snowball-book-audit/SKILL.md` | Routing: snowball compound (retrofit of v1 prompt-only handling) | Create |
| `backend/app/services/deep_agent/skills/routing/market-data-then-reprice/SKILL.md` | Routing: trader audit→reprice sequential flow | Create |
| `backend/app/services/deep_agent/skills/procedures/high_board/.gitkeep` | Remove (replaced by first high_board skill) | Delete |
| `tests/test_langchain_report_tools.py` | Unit tests for list_reports / get_report tools | Create |
| `tests/test_skills_catalog_v2.py` | Extended Tier-B catalog assertions for v2 surface | Create |
| `tests/test_skills_read_smoke_v2.py` | Extended Tier-C read_file smoke for new tiers + /artifacts | Create |

Sentinel: any path that mentions "Workflow procedures that name domain skills" follows the §1 composition pattern in the spec — the procedure body uses domain skill names as references; the persona may execute from memory if the catalog description is sufficient, or `read_file` the named domain skill for the full recipe.

---

## Task 1: Branch + Day-0 verification

**Goal:** Lock in the implementation assumptions about `ReportJob` schema, artifact storage layout, and FastAPI report endpoints. The spec's §6.1 verification items must be confirmed BEFORE any code change. If anything diverges, adjust the tool sketches in Task 2/3 accordingly.

**Files:**
- No file changes in this task — pure verification.

- [ ] **Step 1: Create and switch to the v2 feature branch**

```bash
cd /Users/fuxinyao/open-otc-trading
git checkout -b feat/agent-skills-layer-v2
```

Expected: branch created from current `main` HEAD (commit `59bc7c9` — the v2 spec commit).

- [ ] **Step 2: Verify `ReportJob` SQLAlchemy model fields**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '595,610p' backend/app/models.py
```

Expected output should match:

```python
class ReportJob(Base):
    __tablename__ = "report_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(...)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_paths: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ...
```

**Key facts to confirm:**
- `portfolio_id` is NOT a top-level column — it lives inside `request_payload` (JSON dict).
- `title` is NOT a top-level column — it also lives inside `request_payload`.
- `artifact_paths` is a JSON dict; reports.py:171 sets keys `"html"` and `"excel"`.
- `id` is the primary key (the tool will surface it as `report_id` in its return dict for clarity).

- [ ] **Step 3: Verify `ReportJobOut` Pydantic schema**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '808,820p' backend/app/schemas.py
```

Expected output should match:

```python
class ReportJobOut(BaseModel):
    id: int
    report_type: str
    status: str
    request_payload: dict[str, Any]
    result_payload: dict[str, Any]
    artifact_paths: dict[str, Any]
    task_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
```

Confirm: `task_id` is optional (set elsewhere when a task is queued).

- [ ] **Step 4: Verify existing FastAPI report endpoints (the tool implementations will mirror them)**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '2122,2141p' backend/app/main.py
```

Expected: `GET /api/reports/jobs` orders by `created_at desc, id desc` and returns all jobs (no filtering). `GET /api/reports/jobs/{job_id}` returns 404 if not found.

The tool versions will:
- Add optional filters: `portfolio_id` (filter via `request_payload`), `report_type`, `status`, `limit`.
- Translate ORM `id` → output key `report_id` for consistency with how skills refer to reports.

- [ ] **Step 5: Verify artifacts directory exists and contains report files**

```bash
cd /Users/fuxinyao/open-otc-trading
ls artifacts/*.html 2>/dev/null | head -3 && echo "---" && ls artifacts/*.xlsx 2>/dev/null | head -3
```

Expected: at least a few `report-N.html` and `report-N.xlsx` files exist (these will be useful smoke-test fixtures for Task 4 and Task 21).

If empty, the v2 layer still works — but `Task 21` smoke tests for `/artifacts` reads need a synthesized HTML fixture instead.

- [ ] **Step 6: Confirm existing v1 plan tests live at `tests/` (top-level), not `backend/tests/`**

```bash
cd /Users/fuxinyao/open-otc-trading
ls tests/test_skills_loader.py tests/test_skills_catalog.py
```

Expected: both files exist. The v2 test additions will live in the same directory.

- [ ] **Step 7: Run the existing test suite to confirm a clean starting state**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -q 2>&1 | tail -15
```

Expected: all v1 tests pass. Capture the baseline counts in a scratch note for comparison after Task 22.

- [ ] **Step 8: No commit — verification only**

This task produces no file changes. If any assumption differs from the spec sketch, return to the spec, update §6.1 verification items, then resume.

---

## Task 2: TDD `list_reports` tool

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Create: `tests/test_langchain_report_tools.py`

- [ ] **Step 1: Write the failing tests for `list_reports`**

`tests/test_langchain_report_tools.py`:

```python
"""Unit tests for list_reports and get_report langchain tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.models import ReportJob


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite DB per test; rebinds database.SessionLocal."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    monkeypatch.setattr(database, "settings", settings)
    monkeypatch.setattr(
        database,
        "engine",
        database._build_engine(settings.database_url),
    )
    monkeypatch.setattr(
        database,
        "SessionLocal",
        database._build_session_factory(database.engine),
    )
    database.init_db()
    yield settings


def _insert_report(
    *,
    report_type: str,
    status: str,
    portfolio_id: int | None,
    title: str,
    artifact_paths: dict | None = None,
) -> int:
    """Insert a ReportJob row and return its id."""
    with database.SessionLocal() as session:
        job = ReportJob(
            report_type=report_type,
            status=status,
            request_payload={"portfolio_id": portfolio_id, "title": title},
            result_payload={},
            artifact_paths=artifact_paths or {},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def test_list_reports_returns_empty_when_no_rows(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    result = list_reports_tool.invoke({})
    assert result == {"reports": [], "total": 0}


def test_list_reports_returns_newest_first(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    first_id = _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="First"
    )
    second_id = _insert_report(
        report_type="risk", status="completed", portfolio_id=1, title="Second"
    )

    result = list_reports_tool.invoke({})
    assert result["total"] == 2
    assert [r["report_id"] for r in result["reports"]] == [second_id, first_id]
    assert result["reports"][0]["title"] == "Second"
    assert result["reports"][0]["portfolio_id"] == 1


def test_list_reports_filters_by_portfolio_id(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="P1"
    )
    _insert_report(
        report_type="risk", status="completed", portfolio_id=2, title="P2"
    )

    result = list_reports_tool.invoke({"portfolio_id": 1})
    assert result["total"] == 1
    assert result["reports"][0]["portfolio_id"] == 1
    assert result["reports"][0]["title"] == "P1"


def test_list_reports_filters_by_report_type(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="P"
    )
    _insert_report(
        report_type="risk", status="completed", portfolio_id=1, title="R"
    )
    _insert_report(
        report_type="rfq", status="completed", portfolio_id=1, title="Q"
    )

    result = list_reports_tool.invoke({"report_type": "risk"})
    assert result["total"] == 1
    assert result["reports"][0]["report_type"] == "risk"


def test_list_reports_filters_by_status(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="Done"
    )
    _insert_report(
        report_type="portfolio", status="queued", portfolio_id=1, title="Queued"
    )

    result = list_reports_tool.invoke({"status": "completed"})
    assert result["total"] == 1
    assert result["reports"][0]["status"] == "completed"


def test_list_reports_respects_limit(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    for i in range(5):
        _insert_report(
            report_type="portfolio",
            status="completed",
            portfolio_id=1,
            title=f"R{i}",
        )

    result = list_reports_tool.invoke({"limit": 3})
    assert result["total"] == 3
    # `total` reflects rows returned, not the underlying DB count.


def test_list_reports_surfaces_artifact_paths(isolated_db):
    from app.services.langchain_tools import list_reports_tool

    _insert_report(
        report_type="portfolio",
        status="completed",
        portfolio_id=1,
        title="With artifacts",
        artifact_paths={"html": "/artifacts/report-1.html", "excel": "/artifacts/report-1.xlsx"},
    )

    result = list_reports_tool.invoke({})
    assert result["total"] == 1
    paths = result["reports"][0]["artifact_paths"]
    assert paths == {"html": "/artifacts/report-1.html", "excel": "/artifacts/report-1.xlsx"}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_langchain_report_tools.py -v 2>&1 | tail -30
```

Expected: ImportError on `list_reports_tool`.

- [ ] **Step 3: Implement `list_reports_tool` in `langchain_tools.py`**

Locate the existing tool block (after `get_latest_risk_run_tool` around line 720). Insert the new schema and tool function just BEFORE the `# Portfolio CRUD` section. The exact insertion location should be after `get_latest_risk_run_tool` and before `run_risk_tool`, but the precise line depends on the current file state — use grep to find the anchor.

Anchor command:

```bash
cd /Users/fuxinyao/open-otc-trading
grep -n "^@tool\|^QUANT_AGENT_TOOLS = \[" backend/app/services/langchain_tools.py | head -40
```

Add at the top of the file with other imports (if not already present):

```python
from typing import Any, Literal
```

Add the schema in the schemas block (near the other Pydantic schemas in this file):

```python
class ListReportsInput(BaseModel):
    portfolio_id: int | None = Field(
        default=None, description="Filter to one portfolio_id (from request_payload)."
    )
    report_type: Literal["portfolio", "risk", "rfq"] | None = Field(
        default=None, description="Filter by report type."
    )
    status: Literal["queued", "running", "completed", "failed"] | None = Field(
        default=None, description="Filter by job status."
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max rows to return.")
```

Add the tool function. Place it AFTER `get_latest_risk_run_tool` and BEFORE `run_risk_tool` for logical grouping (read-only report-read primitives sit alongside the other read-only tools):

```python
@tool("list_reports", args_schema=ListReportsInput)
def list_reports_tool(
    portfolio_id: int | None = None,
    report_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return recent ReportJob rows, newest-first, with optional filters.

    portfolio_id and title are stored inside ReportJob.request_payload (JSON
    dict), so portfolio_id filtering is applied in Python after the SQL
    query. report_type and status are top-level columns and filter in SQL.
    """
    from ..models import ReportJob

    database.init_db()
    with database.SessionLocal() as session:
        query = session.query(ReportJob)
        if report_type is not None:
            query = query.filter(ReportJob.report_type == report_type)
        if status is not None:
            query = query.filter(ReportJob.status == status)
        rows = (
            query.order_by(ReportJob.created_at.desc(), ReportJob.id.desc())
            .limit(min(limit, 100))
            .all()
        )

    reports: list[dict[str, Any]] = []
    for job in rows:
        payload = job.request_payload or {}
        if portfolio_id is not None and payload.get("portfolio_id") != portfolio_id:
            continue
        reports.append(
            {
                "report_id": job.id,
                "report_type": job.report_type,
                "status": job.status,
                "portfolio_id": payload.get("portfolio_id"),
                "title": payload.get("title"),
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "artifact_paths": job.artifact_paths or {},
            }
        )

    return {"reports": reports, "total": len(reports)}
```

If `database` is not already imported at the top of `langchain_tools.py`, add `from .. import database` near the other module imports.

- [ ] **Step 4: Add `list_reports_tool` to `QUANT_AGENT_TOOLS`**

Find the `QUANT_AGENT_TOOLS = [...]` list (currently ends around line 1367 in v1). Add `list_reports_tool` to the read-only section, near `get_latest_risk_run_tool`:

```python
QUANT_AGENT_TOOLS = [
    price_product_tool,
    solve_rfq_tool,
    get_rfq_catalog_tool,
    draft_rfq_from_natural_language_tool,
    validate_rfq_terms_tool,
    create_or_update_rfq_draft_tool,
    quote_rfq_tool,
    submit_rfq_for_approval_tool,
    get_positions_tool,
    calculate_risk_tool,
    recommend_hedge_tool,
    run_report_batch_tool,
    fetch_market_snapshot_tool,
    get_latest_position_valuations_tool,
    get_latest_risk_run_tool,
    list_reports_tool,  # NEW (v2) — read-only report listing
    # Persisted-action / HITL-gated:
    price_positions_tool,
    # ... rest unchanged
]
```

- [ ] **Step 5: Run the tests to verify list_reports passes**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_langchain_report_tools.py -v -k "list_reports" 2>&1 | tail -20
```

Expected: all 7 `list_reports`-related tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/langchain_tools.py tests/test_langchain_report_tools.py
git commit -m "feat(tools): add list_reports read-only tool

Adds list_reports langchain tool with optional filters (portfolio_id,
report_type, status, limit). Filters portfolio_id in Python since it
lives inside ReportJob.request_payload (JSON). Newest-first ordering
matches the existing /api/reports/jobs FastAPI endpoint.

Returns: {reports: [{report_id, report_type, status, portfolio_id,
title, created_at, artifact_paths}], total}."
```

---

## Task 3: TDD `get_report` tool

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Modify: `tests/test_langchain_report_tools.py` (add tests)

- [ ] **Step 1: Append failing tests for `get_report` to the test file**

Append to `tests/test_langchain_report_tools.py`:

```python
def test_get_report_returns_full_row(isolated_db):
    from app.services.langchain_tools import get_report_tool

    rid = _insert_report(
        report_type="risk",
        status="completed",
        portfolio_id=42,
        title="Q3 Risk Review",
        artifact_paths={"html": "/artifacts/report-7.html", "excel": "/artifacts/report-7.xlsx"},
    )

    result = get_report_tool.invoke({"report_id": rid})
    assert result["report_id"] == rid
    assert result["report_type"] == "risk"
    assert result["status"] == "completed"
    assert result["portfolio_id"] == 42
    assert result["title"] == "Q3 Risk Review"
    assert result["artifact_paths"] == {
        "html": "/artifacts/report-7.html",
        "excel": "/artifacts/report-7.xlsx",
    }
    assert "created_at" in result and result["created_at"] is not None
    # result_payload should be surfaced for summary access
    assert "summary" in result
    assert "result_payload" in result


def test_get_report_surfaces_result_payload_as_summary(isolated_db):
    """For the agent, `summary` is the most useful slice of result_payload."""
    from app.services.langchain_tools import get_report_tool

    with database.SessionLocal() as session:
        job = ReportJob(
            report_type="portfolio",
            status="completed",
            request_payload={"portfolio_id": 1, "title": "Summary test"},
            result_payload={
                "summary": {"totals": {"delta": 100.0, "gamma": 5.0}},
                "rows": [{"position_id": 1, "value": 1.0}],
            },
            artifact_paths={"html": "/artifacts/x.html"},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        rid = job.id

    result = get_report_tool.invoke({"report_id": rid})
    assert result["summary"] == {"totals": {"delta": 100.0, "gamma": 5.0}}
    # result_payload is also exposed for completeness (no summary key fallback)
    assert result["result_payload"]["rows"] == [{"position_id": 1, "value": 1.0}]


def test_get_report_missing_id_raises(isolated_db):
    from app.services.langchain_tools import get_report_tool

    with pytest.raises(ValueError) as exc:
        get_report_tool.invoke({"report_id": 99999})
    assert "99999" in str(exc.value)
    assert "not found" in str(exc.value).lower()
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_langchain_report_tools.py -v -k "get_report" 2>&1 | tail -15
```

Expected: ImportError on `get_report_tool`.

- [ ] **Step 3: Add the `GetReportInput` schema and `get_report_tool` to `langchain_tools.py`**

Place the schema near `ListReportsInput`:

```python
class GetReportInput(BaseModel):
    report_id: int = Field(description="ReportJob id from list_reports.")
```

Place the tool function just after `list_reports_tool`:

```python
@tool("get_report", args_schema=GetReportInput)
def get_report_tool(report_id: int) -> dict[str, Any]:
    """Return full ReportJob row for one id including artifact_paths and summary.

    Surfaces request_payload fields (portfolio_id, title) at the top level for
    convenience. The `summary` key extracts result_payload["summary"] if
    present; the full `result_payload` is also returned for completeness.
    Raises ValueError if the report_id is not found.
    """
    from ..models import ReportJob

    database.init_db()
    with database.SessionLocal() as session:
        job = session.get(ReportJob, report_id)
        if job is None:
            raise ValueError(f"Report job not found: report_id={report_id}")

        request_payload = job.request_payload or {}
        result_payload = job.result_payload or {}
        return {
            "report_id": job.id,
            "report_type": job.report_type,
            "status": job.status,
            "portfolio_id": request_payload.get("portfolio_id"),
            "title": request_payload.get("title"),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "artifact_paths": job.artifact_paths or {},
            "request_payload": request_payload,
            "result_payload": result_payload,
            "summary": result_payload.get("summary"),
        }
```

- [ ] **Step 4: Add `get_report_tool` to `QUANT_AGENT_TOOLS`**

Add right after `list_reports_tool`:

```python
    list_reports_tool,
    get_report_tool,                  # NEW (v2) — read-only single report fetch
```

- [ ] **Step 5: Run the tests to verify all get_report tests pass**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_langchain_report_tools.py -v 2>&1 | tail -20
```

Expected: all 10 tests PASS (7 list_reports + 3 get_report).

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all previously passing tests still pass; new tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/langchain_tools.py tests/test_langchain_report_tools.py
git commit -m "feat(tools): add get_report read-only tool

Adds get_report langchain tool returning a full ReportJob row by id,
including artifact_paths, result_payload, and a convenience 'summary'
key (extracted from result_payload.summary if present). Surfaces
request_payload fields (portfolio_id, title) at the top level.

Raises ValueError if the id is not found (clear error for the agent
to surface to the user)."
```

---

## Task 4: Add `/artifacts` filesystem mount and read permission

**Goal:** Make HTML report artifacts under `artifacts/` readable via `read_file` from inside the agent (specifically for the `high_board` `report-query-and-display` workflow). XLSX paths stay surface-only — the skill body governs use.

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py`

- [ ] **Step 1: Read the current orchestrator wiring to locate insertion points**

```bash
cd /Users/fuxinyao/open-otc-trading
sed -n '1,90p' backend/app/services/deep_agent/orchestrator.py
```

Confirm: `_SKILLS_FS_ROOT` is defined; `_build_backend()` returns a `CompositeBackend` with routes={"/skills/": skills_fs}; `_filesystem_permissions()` returns a list of `FilesystemPermission` rules ending in a deny-all `/**`.

- [ ] **Step 2: Add the `_ARTIFACTS_ROOT` constant**

In `orchestrator.py`, near the existing `_SKILLS_FS_ROOT`, add:

```python
_ARTIFACTS_ROOT = Path(__file__).parent.parent.parent.parent.parent / "artifacts"
```

(That walks up from `backend/app/services/deep_agent/orchestrator.py` to the repo root, then into `artifacts/`.)

Verify the path resolution with a quick print:

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from pathlib import Path
p = Path('backend/app/services/deep_agent/orchestrator.py').resolve()
artifacts = p.parent.parent.parent.parent.parent / 'artifacts'
print('Resolves to:', artifacts)
print('Exists:', artifacts.is_dir())
"
```

Expected: prints `/Users/fuxinyao/open-otc-trading/artifacts` and `Exists: True`.

- [ ] **Step 3: Extend `_build_backend()` to add an `/artifacts/` route**

Update the function in `orchestrator.py`:

```python
def _build_backend() -> Any:
    """Build a CompositeBackend that routes /skills/ AND /artifacts/ to
    FilesystemBackends rooted at the on-disk trees, with StateBackend as the
    default for /trading_desk/, /large_tool_results/, and everything else.
    """
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend

    skills_fs = FilesystemBackend(root_dir=str(_SKILLS_FS_ROOT), virtual_mode=True)
    artifacts_fs = FilesystemBackend(
        root_dir=str(_ARTIFACTS_ROOT), virtual_mode=True
    )
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/skills/": skills_fs,
            "/artifacts/": artifacts_fs,
        },
    )
```

- [ ] **Step 4: Extend `_filesystem_permissions()` to allow read on `/artifacts/**`**

Insert the new `FilesystemPermission` rule AFTER the existing `/skills` rule and BEFORE the trailing deny-all:

```python
def _filesystem_permissions() -> list[Any]:
    from deepagents.middleware.permissions import FilesystemPermission

    return [
        FilesystemPermission(
            operations=["read"],
            paths=["/"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/trading_desk", "/trading_desk/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/large_tool_results", "/large_tool_results/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/skills", "/skills/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/artifacts", "/artifacts/**"],  # NEW (v2)
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/", "/**"],
            mode="deny",
        ),
    ]
```

- [ ] **Step 5: Smoke-check that the orchestrator still builds**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -v -k "orchestrator or persona or skills_catalog" 2>&1 | tail -20
```

Expected: existing tests that exercise the orchestrator build still pass. No new failures.

- [ ] **Step 6: Smoke-check `read_file` works on a real artifact (manual ad-hoc)**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
from pathlib import Path
from deepagents.backends.filesystem import FilesystemBackend
artifacts = Path('artifacts').resolve()
fs = FilesystemBackend(root_dir=str(artifacts), virtual_mode=True)
# Find first HTML
candidates = sorted(artifacts.glob('*.html'))
print('Found', len(candidates), 'HTML artifacts.')
if candidates:
    name = candidates[0].name
    # FilesystemBackend exposes a read API; the exact method may be 'read_text' or 'read'.
    try:
        text = fs.read(f'/{name}')
    except AttributeError:
        text = fs.read_text(f'/{name}')
    print('Read', len(text), 'chars from', name)
    print('First 200 chars:', text[:200])
"
```

Expected: at least one HTML found and read. If `FilesystemBackend`'s read method has a different name (`read`, `read_text`, or `read_file`), use whichever works. The test in Task 21 will lock the exact method down by importing the same backend the orchestrator uses.

- [ ] **Step 7: Run the full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/orchestrator.py
git commit -m "feat(deep_agent): mount /artifacts in FilesystemBackend + read permission

Adds an artifacts FilesystemBackend route alongside /skills/ in
CompositeBackend, and a /artifacts/** read-allow permission rule
before the deny-all tail. Enables the upcoming high_board
report-query-and-display skill to read_file HTML report artifacts;
XLSX paths stay surface-only by skill-body governance."
```

---

## Task 5: Scaffold v2 skills directory tree

**Goal:** Create `domains/`, `routing/`, and `procedures/high_board/`'s first-procedure-aware directory (since the v1 .gitkeep will be removed when the first skill lands in Task 17). Add a brief addendum to `skills/README.md` documenting the new tiers.

**Files:**
- Create: 7 `domains/<domain>/` directories
- Create: 1 `routing/` directory
- Modify: `backend/app/services/deep_agent/skills/README.md` (append v2 sections)

- [ ] **Step 1: Create the new directory tree**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/position
mkdir -p backend/app/services/deep_agent/skills/domains/portfolio
mkdir -p backend/app/services/deep_agent/skills/domains/pricing
mkdir -p backend/app/services/deep_agent/skills/domains/risk
mkdir -p backend/app/services/deep_agent/skills/domains/market-data
mkdir -p backend/app/services/deep_agent/skills/domains/rfq
mkdir -p backend/app/services/deep_agent/skills/domains/reporting
mkdir -p backend/app/services/deep_agent/skills/routing
```

Subdirectories per skill will be created in Tasks 8-18 by the SKILL.md authoring steps.

- [ ] **Step 2: Read the existing skills/README.md to find the append point**

```bash
cd /Users/fuxinyao/open-otc-trading
wc -l backend/app/services/deep_agent/skills/README.md
cat backend/app/services/deep_agent/skills/README.md
```

Note the current structure. The append should add v2 sections without rewriting v1 content.

- [ ] **Step 3: Append v2 documentation to `skills/README.md`**

Open `backend/app/services/deep_agent/skills/README.md` and append at the end:

````markdown

## v2 additions (2026-05-15) — domain + routing tiers

Reference: `docs/superpowers/specs/2026-05-15-agent-skills-layer-v2-design.md`.

Two new tiers extend the v1 layout:

- **`domains/<domain>/<skill-name>/SKILL.md`** — per-domain skills. Two flavors:
  - **Cards**: reference content for one domain (`portfolio-model`,
    `pricing-engines`, `market-data-conventions`, `rfq-lifecycle`). Free-form
    body, no fixed schema. Frontmatter: `metadata.tier: domain-card`.
  - **Recipes**: single safe operation within a domain (e.g.,
    `position-snapshot`, `pricing-run-propose`). 5-section body schema
    (when applies / inputs / step sequence / what success / tool preferences).
    Frontmatter: `metadata.tier: domain-recipe`.
- **`routing/<flow>/SKILL.md`** — orchestrator-only compound-flow skills. Body
  describes a sequence of `task(...)` delegations. Frontmatter:
  `metadata.tier: routing`.

### Add a domain card

1. `mkdir skills/domains/<domain>/<card-name>/`
2. Author `SKILL.md` with frontmatter (`name`, `description`,
   `metadata.tier: domain-card`, optional `related_tools`/`related_products`)
   + free-form body sections.
3. Update the per-persona `skills=[...]` source list in `personas.py` if the
   domain wasn't already wired for that persona.

### Add a domain recipe

1. `mkdir skills/domains/<domain>/<recipe-name>/`
2. Author `SKILL.md` with frontmatter (`name`, `description`, `allowed-tools`,
   `metadata.tier: domain-recipe`, optional `related_cards`/`related_tools`)
   + the 5-section schema:
   - `## When this applies`
   - `## Inputs to inspect first`
   - `## Step sequence`
   - `## What success looks like`
   - `## Tool preferences`
3. Workflow procedures that compose this recipe should NAME it in their step
   sequence (the persona may execute from memory or `read_file` the recipe).

### Add a routing skill

1. `mkdir skills/routing/<flow-name>/`
2. Author `SKILL.md` with frontmatter (`name`, `description`,
   `metadata.tier: routing`, optional `related_personas`/`related_procedures`)
   + body sections:
   - `## When this applies`
   - `## Step sequence` — sequence of `task(...)` delegations with conditional
     branches if needed
   - `## What success looks like`
   - `## Routing notes` — when to NOT use this skill; cross-references
3. Add a row to the Routing matrix in `prompts/orchestrator.md`.
4. Add a Tier-B test assertion in `tests/test_skills_catalog_v2.py` confirming
   the orchestrator's catalog contains this skill name.

### Workflow procedures (v2 shape)

Workflow procedures (under `procedures/<persona>/<workflow>/SKILL.md`) now
*name* domain skills in their step sequence rather than inlining every action.
Example: a `step sequence` entry says "Apply the `position-snapshot` domain
recipe" instead of repeating its body. The persona may execute from memory
if the catalog description is enough or `read_file` the named skill.

The v1 anchor (`snowball-position-diagnostics` in trader/ and risk_manager/)
is grandfathered as self-contained — both shapes are valid.
````

- [ ] **Step 4: Sanity-check the README**

```bash
cd /Users/fuxinyao/open-otc-trading
head -30 backend/app/services/deep_agent/skills/README.md
echo "---"
tail -50 backend/app/services/deep_agent/skills/README.md
```

Expected: v1 content at the top is unchanged; v2 addendum is at the bottom.

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains backend/app/services/deep_agent/skills/routing backend/app/services/deep_agent/skills/README.md
git commit -m "feat(agent-skills): scaffold v2 domains/ and routing/ trees + README addendum

Adds empty domain subdirectories (position, portfolio, pricing, risk,
market-data, rfq, reporting) and routing/ at skills/ root. Documents
the v2 tiers (domain cards, domain recipes, routing skills) in the
README. SKILL.md files land in subsequent tasks."
```

---

## Task 6: Wire `personas.py` — extend per-persona `skills=[...]` source lists

**Goal:** Each persona's `SkillsMiddleware` source list expands to include the relevant `/skills/domains/<domain>/` paths per the spec §2 table. This is the catalog-isolation control: trader only sees rfq/pricing/position/market-data domains; risk_manager doesn't see rfq, etc.

**Files:**
- Modify: `backend/app/services/deep_agent/personas.py`

- [ ] **Step 1: Read the current personas.py to locate the `skills=[...]` arguments**

```bash
cd /Users/fuxinyao/open-otc-trading
grep -n "skills=\[" backend/app/services/deep_agent/personas.py
```

Expected: three matches, one for each persona's SubAgent spec.

- [ ] **Step 2: Read the full file to see how the SubAgent specs are structured**

```bash
cd /Users/fuxinyao/open-otc-trading
cat backend/app/services/deep_agent/personas.py
```

Note the existing `skills=[...]` argument shape for each persona. They currently list `["/skills/procedures/trader/", "/skills/products/"]` and similar.

- [ ] **Step 3: Update the trader SubAgent's `skills=[...]`**

In `personas.py`, replace the trader's `skills=[...]` with:

```python
        skills=[
            "/skills/procedures/trader/",
            "/skills/domains/position/",        # NEW (v2)
            "/skills/domains/pricing/",         # NEW (v2)
            "/skills/domains/market-data/",     # NEW (v2)
            "/skills/domains/rfq/",             # NEW (v2)
            "/skills/products/",
        ],
```

- [ ] **Step 4: Update the risk_manager SubAgent's `skills=[...]`**

```python
        skills=[
            "/skills/procedures/risk_manager/",
            "/skills/domains/position/",        # NEW (v2)
            "/skills/domains/risk/",            # NEW (v2)
            "/skills/domains/market-data/",     # NEW (v2)
            "/skills/domains/pricing/",         # NEW (v2) — for pricing-run-propose used in risk lens
            "/skills/domains/reporting/",       # NEW (v2)
            "/skills/products/",
        ],
```

- [ ] **Step 5: Update the high_board SubAgent's `skills=[...]`**

```python
        skills=[
            "/skills/procedures/high_board/",
            "/skills/domains/portfolio/",       # NEW (v2)
            "/skills/domains/reporting/",       # NEW (v2)
        ],
```

(high_board does NOT get the rfq, position, pricing, risk, or market-data domains — its scope is governance read-side review per §2.)

- [ ] **Step 6: Run the orchestrator/persona test suite to confirm no smoke regression**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -v -k "persona or orchestrator or skills_catalog" 2>&1 | tail -25
```

Expected: existing tests still pass. Catalog assertions in `test_skills_catalog.py` will pass because the new domain directories are empty (no SKILL.md files yet), so they contribute 0 entries. The v1 catalog assertions only check for specific skill names that haven't moved.

- [ ] **Step 7: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/personas.py
git commit -m "feat(agent-skills): extend persona skills= source lists for v2

Adds per-persona /skills/domains/<domain>/ source paths per spec §2:
- trader: position, pricing, market-data, rfq
- risk_manager: position, risk, market-data, pricing (for cross-lens
  pricing-run-propose), reporting
- high_board: portfolio, reporting (no rfq/position/risk per spec
  - high_board scope is governance read-side review)

Empty subdirectories contribute 0 catalog entries until SKILL.md
files land in subsequent tasks."
```

---

## Task 7: Wire orchestrator — `skills=["/skills/routing/"]`

**Goal:** Activate the orchestrator's own skills source so it can see routing-tier SKILL.md files in its catalog.

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py`

- [ ] **Step 1: Read the current `build_orchestrator` to see the `create_deep_agent` call**

```bash
cd /Users/fuxinyao/open-otc-trading
grep -n "create_deep_agent" backend/app/services/deep_agent/orchestrator.py
```

Expected: one match in `build_orchestrator`.

- [ ] **Step 2: Add `skills=["/skills/routing/"]` to the `create_deep_agent` call**

In `orchestrator.py`'s `build_orchestrator` function, modify the `create_deep_agent(...)` call to include the new kwarg:

```python
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=_orchestrator_prompt(),
        subagents=all_personas(model, tools),
        interrupt_on=interrupt_on if interrupt_on is not None else interrupt_on_config(),
        checkpointer=checkpointer,
        backend=_build_backend(),
        permissions=_filesystem_permissions(),
        skills=["/skills/routing/"],                              # NEW (v2)
        name="otc_desk_orchestrator",
    )
```

- [ ] **Step 3: Smoke-check that the orchestrator builds**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -v -k "orchestrator" 2>&1 | tail -15
```

Expected: existing tests still pass. With `routing/` empty, the orchestrator catalog has 0 entries; the kwarg is accepted but contributes nothing yet. SKILL.md files land in Task 18.

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/orchestrator.py
git commit -m "feat(agent-skills): wire orchestrator skills=[/skills/routing/]

Activates the orchestrator's own SkillsMiddleware source so routing-tier
SKILL.md files surface in its catalog. Source is empty until Task 18
authors the three routing skills (pricing-and-risk-compound,
snowball-book-audit, market-data-then-reprice)."
```

---

## Task 8: Author 4 domain cards

**Goal:** Land all four domain CARDS (free-form reference content) in one commit. These are the smallest unit of self-contained domain knowledge; the subsequent recipe and procedure tasks reference them by name.

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/portfolio/portfolio-model/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/pricing/pricing-engines/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/market-data/market-data-conventions/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/rfq/rfq-lifecycle/SKILL.md`

- [ ] **Step 1: Create the four card subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/portfolio/portfolio-model
mkdir -p backend/app/services/deep_agent/skills/domains/pricing/pricing-engines
mkdir -p backend/app/services/deep_agent/skills/domains/market-data/market-data-conventions
mkdir -p backend/app/services/deep_agent/skills/domains/rfq/rfq-lifecycle
```

- [ ] **Step 2: Write `portfolio-model` card**

`backend/app/services/deep_agent/skills/domains/portfolio/portfolio-model/SKILL.md`:

````markdown
---
name: portfolio-model
description: Foundational reference for the portfolio data model on this desk — distinguishes Container vs View portfolios, explains how positions resolve to portfolios via sources, and documents the query patterns (list_portfolios / get_portfolio / get_positions). Read once at the start of any portfolio-touching workflow.
metadata:
  tier: domain-card
  related_tools: list_portfolios get_portfolio get_positions set_portfolio_rule
---

# Portfolio model — domain card

## Container vs View

- **Container**: portfolio that *holds* positions explicitly; mutated via
  `add_positions_to_portfolio` / `remove_positions_from_portfolio`.
- **View**: portfolio defined by *rules* (source filters); membership is
  derived, not stored. Mutated via `set_portfolio_rule` /
  `add_portfolio_sources` / `remove_portfolio_sources`.
- Lifecycle differences: Containers persist position links; Views recompute
  on read. Implications for staleness and consistency — when a position is
  added to a backing source, a View picks it up on the next query; a
  Container needs explicit `add_positions_to_portfolio`.

## Portfolio ↔ Position relationship

- Positions are owned by a `portfolio_id` (Container) or matched via source
  rules (View).
- A position can appear in multiple Views simultaneously; in only one
  Container at a time.
- `get_positions(portfolio_id=...)` resolves either kind transparently — the
  caller does not need to know whether the target is a Container or View.

## How to query a portfolio

- **Enumerate**: `list_portfolios` (paginated; check for `total > returned`).
- **Inspect**: `get_portfolio(portfolio_id)` returns metadata + kind
  (Container/View) + sources/rules if View.
- **Positions inside**: `get_positions(portfolio_id, ...filters...)`.
- **Common gotcha**: an empty View is valid (rule matched zero positions);
  an empty Container often signals stale state. Treat differently — surface
  the distinction to the user.

## See also

- Recipe: `position-snapshot` (consumes a portfolio_id, returns aggregated view)
````

- [ ] **Step 3: Write `pricing-engines` card**

`backend/app/services/deep_agent/skills/domains/pricing/pricing-engines/SKILL.md`:

````markdown
---
name: pricing-engines
description: Reference for the QuantArk pricing engines available on this desk and how product type maps to engine choice. Read before any pricing-related decision so the engine selection and input requirements are explicit.
metadata:
  tier: domain-card
  related_tools: price_product price_positions
  related_products: snowball-cn
---

# Pricing engines — domain card

## Engines available

- **Black-Scholes** (analytic): `EuropeanVanillaOption`. Closed-form, fast.
- **Monte Carlo (daily-grid)**: `SnowballOption`. Daily KI observation +
  monthly KO grid. Path-dependent, expensive.
- **Monte Carlo (event-driven)**: `PhoenixOption`. Coupon-on-observation,
  KI/KO events. Path-dependent.

## Product type → engine map

| product_type | Engine | Cost class |
|---|---|---|
| `EuropeanVanillaOption` | Black-Scholes | cheap |
| `SnowballOption` | MC daily-grid | expensive |
| `PhoenixOption` | MC event-driven | medium |

## Required inputs per engine

- **BS**: spot, vol (flat or ATM), r, q, T.
- **MC daily-grid**: spot, vol surface or flat ATM, r, q, dividend schedule,
  KI/KO levels, observation calendar.
- **MC event-driven**: same as above + per-event coupon definition.

## When to cost-preview before running

- `price_positions` over a snowball/phoenix book: **ALWAYS** cost-preview.
- `price_positions` over a vanilla-only book: cost-preview optional.
- `price_product` for a single MC spec: cost-preview if simulation count is
  unbounded or > 10k paths.

## See also

- Recipe: `pricing-run-propose`
- Recipe: `price-product-adhoc`
- Product card: `snowball-cn`
````

- [ ] **Step 4: Write `market-data-conventions` card**

`backend/app/services/deep_agent/skills/domains/market-data/market-data-conventions/SKILL.md`:

````markdown
---
name: market-data-conventions
description: Reference for market-data sources, refresh cadence, symbol conventions, and what counts as stale or drifted on this desk. Read before any market-data fetch or drift analysis.
metadata:
  tier: domain-card
  related_tools: fetch_market_snapshot import_position_market_inputs
---

# Market data conventions — domain card

## Sources

- **A-share (CN)**: akshare for index spot, sector spot, single-name spot;
  historical vol from rolling realized.
- **HK**: akshare HK feed; less frequent refresh than A-share.
- **OTC / proprietary**: stored vol surfaces and dividend curves live in the
  desk's pricing-profile store (not market-data per se).

## Refresh cadence

- **Intraday spot**: snapshot is "as of last fetch". No streaming; explicit
  fetch required.
- **Day-end EOD**: A-share market closes 15:00 CST; data settles ~15:30 CST.
- **Vol surfaces**: refreshed weekly unless explicitly requested.

## Symbol conventions

- **Indices**: `000300.SH` (CSI 300), `000905.SH` (CSI 500), `000852.SH`
  (CSI 1000).
- **Single names (A-share)**: `<code>.SH` for Shanghai, `<code>.SZ` for
  Shenzhen.
- **HK indices**: `HSI`, `HSCEI` (no exchange suffix).

## Staleness thresholds (desk default)

- **Spot stale**: last fetch > 1 BD ago.
- **Vol stale**: last fetch > 5 BD ago.
- **Drift (spot)**: `|current − stored| / stored > 1%` (trader lens),
  `> 2%` (risk lens — gamma is more sensitive to spot dispersion than P&L).

## Day-count / settlement

- **A-share equities**: T+1 settlement, ACT/365 day-count.
- **OTC structured products**: per-contract day-count; check the product card.

## See also

- Recipe: `market-data-fetch`
- Recipe: `market-data-drift`
````

- [ ] **Step 5: Write `rfq-lifecycle` card**

`backend/app/services/deep_agent/skills/domains/rfq/rfq-lifecycle/SKILL.md`:

````markdown
---
name: rfq-lifecycle
description: RFQ state machine reference — states, transitions, HITL gates, audit events. Read once at the start of any RFQ-touching workflow so the transitions are explicit. The state machine is enforced by the tool surface, not by skill content.
metadata:
  tier: domain-card
  related_tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft solve_rfq quote_rfq submit_rfq_for_approval approve_rfq reject_rfq release_rfq mark_rfq_client_accepted book_rfq_to_position
---

# RFQ lifecycle — domain card

## States

```
draft → quoted → submitted_for_approval → (approved | rejected)
       → released → client_accepted → booked
```

## Transitions and tools

| From | To | Tool | HITL? |
|---|---|---|---|
| (none) | draft | `create_or_update_rfq_draft` | no |
| draft | draft (updated) | `create_or_update_rfq_draft` | no |
| draft | quoted | `quote_rfq` | no |
| quoted | submitted_for_approval | `submit_rfq_for_approval` | YES |
| submitted | approved | `approve_rfq` | YES (high_board) |
| submitted | rejected | `reject_rfq` | YES (high_board) |
| approved | released | `release_rfq` | YES |
| released | client_accepted | `mark_rfq_client_accepted` | YES |
| client_accepted | booked | `book_rfq_to_position` | YES |

## Persona ownership

- **trader**: draft, validate, quote. Owns up through `submit_for_approval`.
- **high_board**: approve / reject. Governance gate.
- **trader**: release, mark_client_accepted, book_to_position. Post-approval
  execution.

## Audit

Every transition emits an audit event with actor + timestamp + diff. Skills
do not need to handle auditing — the tools do it. Skills SHOULD reference
the audit event type when reporting (e.g., "approved RFQ <id>, audit event
`rfq.approved`").

## Compute-cost note

- `draft_rfq_from_natural_language`: small LLM call (server-side); cheap.
- `solve_rfq`: invokes pricing engine; cost class follows
  `pricing-engines` card.
- All transition tools: cheap (DB writes).

## See also

- Recipe: `rfq-draft`
- Recipe: `rfq-quote`
- Recipe: `rfq-submit-for-approval`
- Procedure: `rfq-intake-and-quote`
````

- [ ] **Step 6: Sanity-check the four cards parse as catalog entries**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_skills_catalog.py -v 2>&1 | tail -15
```

Expected: existing v1 catalog tests still pass. The four new cards will get coverage when Task 20 lands.

Optional sanity check that the files have valid frontmatter:

```bash
cd /Users/fuxinyao/open-otc-trading
for f in backend/app/services/deep_agent/skills/domains/{portfolio/portfolio-model,pricing/pricing-engines,market-data/market-data-conventions,rfq/rfq-lifecycle}/SKILL.md; do
  echo "=== $f ==="
  head -8 "$f"
done
```

Expected: each file shows valid frontmatter starting with `---` and a `name:` field matching the parent directory name.

- [ ] **Step 7: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains
git commit -m "feat(agent-skills): add 4 domain cards (portfolio-model, pricing-engines, market-data-conventions, rfq-lifecycle)

Reference content for each domain — free-form body sections, no fixed
schema. Read once per session per relevant workflow. Workflow procedures
and domain recipes reference these cards by name in their step sequences."
```

---

## Task 9: Author 2 position-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/position/position-snapshot/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/position/position-input-enumerate/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/position/position-snapshot
mkdir -p backend/app/services/deep_agent/skills/domains/position/position-input-enumerate
```

- [ ] **Step 2: Write `position-snapshot` recipe**

`backend/app/services/deep_agent/skills/domains/position/position-snapshot/SKILL.md`:

````markdown
---
name: position-snapshot
description: Build a canonical position snapshot for a portfolio — combines positions metadata with the latest stored valuations into a single in-context view. Pure read. Read before any pricing, risk, or diagnostics workflow.
allowed-tools: get_positions get_latest_position_valuations run_python
metadata:
  tier: domain-recipe
  related_tools: get_positions get_latest_position_valuations
---

# position-snapshot — domain recipe

## When this applies

- Pre-step for any workflow that needs a position view.

## Inputs to inspect first

- `portfolio_id` from the caller.

## Step sequence

1. `get_positions(portfolio_id)`.
2. `get_latest_position_valuations(portfolio_id)` (note: 500-row limit;
   if positions > 500, use `run_python` reduce per v1 large-portfolio
   pattern — see git commit `c0ae172`).
3. Join on `position.id` ↔ `valuation.position_id` (NOT `valuation.id` —
   v1 commit `73b0ae7` fixed this mistake).

## What success looks like

A combined view: `<N> positions, <K> with stored valuations, <M> missing
valuations`.

## Tool preferences

- READ-ONLY. No HITL.
- For portfolios > 500 positions, MUST use `run_python` reduce.
````

- [ ] **Step 3: Write `position-input-enumerate` recipe**

`backend/app/services/deep_agent/skills/domains/position/position-input-enumerate/SKILL.md`:

````markdown
---
name: position-input-enumerate
description: From a position snapshot, derive the unique set of market-data inputs the portfolio depends on (underlying × input_type pairs). Pure read + run_python. Read before market-data fetch/drift workflows.
allowed-tools: run_python
metadata:
  tier: domain-recipe
  related_tools: run_python
---

# position-input-enumerate — domain recipe

## When this applies

- Pre-step for `market-data-fetch` over a portfolio's full underlying set.
- Coverage audit ("what inputs do we need to keep fresh?").

## Inputs to inspect first

- A position snapshot built via `position-snapshot`.

## Step sequence

1. `run_python` script over the snapshot: extract `(underlying, input_type)`
   tuples from each position's spec. `input_type ∈ {spot, vol, r, q,
   dividend_schedule}`.
2. Dedupe and return the unique set with a count of positions per pair
   (gives a "blast radius if this input drifts" metric).

## What success looks like

`<N> unique (underlying, input_type) pairs across <P> positions. Top by
blast-radius: <pair>: <count>; ...`

## Tool preferences

- READ-ONLY. `run_python` only.
````

- [ ] **Step 4: Smoke-check**

```bash
cd /Users/fuxinyao/open-otc-trading
ls backend/app/services/deep_agent/skills/domains/position/*/SKILL.md
```

Expected: 2 files listed.

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/position
git commit -m "feat(agent-skills): add 2 position-domain recipes

- position-snapshot: get_positions + get_latest_position_valuations,
  joined on position.id ↔ valuation.position_id (the v1 lesson learned).
  Run-python reduce required for portfolios > 500 positions.
- position-input-enumerate: derive unique (underlying, input_type) pairs
  from a snapshot. Powers market-data-fetch / drift workflows."
```

---

## Task 10: Author 2 pricing-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/pricing/pricing-run-propose/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/pricing/price-product-adhoc/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/pricing/pricing-run-propose
mkdir -p backend/app/services/deep_agent/skills/domains/pricing/price-product-adhoc
```

- [ ] **Step 2: Write `pricing-run-propose` recipe**

`backend/app/services/deep_agent/skills/domains/pricing/pricing-run-propose/SKILL.md`:

````markdown
---
name: pricing-run-propose
description: Cost-preview then propose price_positions for a portfolio or subset. HITL-gated write. Use ONLY when staleness/drift analysis has flagged a real refresh need — never as a default first action.
allowed-tools: price_positions
metadata:
  tier: domain-recipe
  related_cards: pricing-engines
  related_tools: price_positions
---

# pricing-run-propose — domain recipe

## When this applies

- A pricing or risk workflow has identified stale/drifted positions and
  decided a fresh `price_positions` run is justified.

## Inputs to inspect first

- The flagged `position_ids` list from the calling workflow.
- The product types of the flagged positions (cost class via
  `pricing-engines` card).

## Step sequence

1. Compose cost-preview: "`<K>` positions × `<engine-cost class>`. Estimated
   runtime: `<T>`."
2. State the preview to the user. Pause on the user's confirmation (HITL).
3. Call `price_positions(portfolio_id, position_ids=[...])`.
4. Confirm by re-reading `get_latest_position_valuations` for the affected IDs.

## What success looks like

"Repriced `<K>` positions; new `valuation_date = <D>`; `max_diff_vs_prior =
<X>`."

## Tool preferences

- WRITE (HITL): `price_positions`. Cost-preview MANDATORY per policy.
- Do NOT call without a flagged position list from upstream — full-portfolio
  blanket repricing is a separate explicit user request.
````

- [ ] **Step 3: Write `price-product-adhoc` recipe**

`backend/app/services/deep_agent/skills/domains/pricing/price-product-adhoc/SKILL.md`:

````markdown
---
name: price-product-adhoc
description: Price a single product spec ad-hoc (no portfolio context, no persistence). Read pricing-engines card first to pick the right engine and cost-preview if MC. Used for "what would X cost" exploratory queries.
allowed-tools: price_product
metadata:
  tier: domain-recipe
  related_cards: pricing-engines
  related_tools: price_product
---

# price-product-adhoc — domain recipe

## When this applies

- Exploratory pricing: "what would a 24m snowball on CSI 500 with KI=80,
  KO=103 cost?"
- Pricing inside `rfq-quote` recipe (via `solve_rfq`, not this directly —
  kept as a separate path for ad-hoc queries that aren't RFQs).

## Inputs to inspect first

- The product spec (type, terms, underlying).
- `pricing-engines` card if engine choice is unclear.

## Step sequence

1. Validate the spec is well-formed enough to price (required terms present).
2. Cost-preview IF engine is MC AND simulation count is > 10k paths.
3. `price_product(product_type, terms, market_inputs)`.

## What success looks like

"Price = `<P>`, engine = `<E>`, key inputs used: `<spot/vol/r/q>`.
Sensitivity caveats: `<list>`."

## Tool preferences

- Compute. No HITL (not in HITL config). Cost-preview only if MC + high paths.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/pricing
git commit -m "feat(agent-skills): add 2 pricing-domain recipes

- pricing-run-propose: cost-preview + price_positions (HITL). Used by
  trader and risk-lens portfolio-pricing-run workflows.
- price-product-adhoc: price_product for one-off specs. Cost-preview
  if MC with > 10k paths."
```

---

## Task 11: Author 2 risk-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/risk/risk-snapshot-read/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/risk/risk-run-propose/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/risk/risk-snapshot-read
mkdir -p backend/app/services/deep_agent/skills/domains/risk/risk-run-propose
```

- [ ] **Step 2: Write `risk-snapshot-read` recipe**

`backend/app/services/deep_agent/skills/domains/risk/risk-snapshot-read/SKILL.md`:

````markdown
---
name: risk-snapshot-read
description: Read and interpret the most recent persisted risk run for a portfolio. Pure read. Use before any risk decision or as a freshness check for risk-run-propose.
allowed-tools: get_latest_risk_run
metadata:
  tier: domain-recipe
  related_tools: get_latest_risk_run
---

# risk-snapshot-read — domain recipe

## When this applies

- Pre-step for any risk-related workflow.
- Standalone: "what's our latest risk view on portfolio X?"

## Inputs to inspect first

- `portfolio_id`.

## Step sequence

1. `get_latest_risk_run(portfolio_id)`.
2. Extract: run timestamp, `valuation_date`, totals
   (delta/gamma/vega/theta), per-position contributions (if returned).
3. Compute currency: BD-since-run, BD-since-`valuation_date`.

## What success looks like

"Latest risk run: `<ts>`, `valuation_date=<D>`, totals: `delta=<>, gamma=<>,
vega=<>`. `<K>` positions contributing. Currency: `<X>` BD stale."

## Tool preferences

- READ-ONLY. No HITL.
````

- [ ] **Step 3: Write `risk-run-propose` recipe**

`backend/app/services/deep_agent/skills/domains/risk/risk-run-propose/SKILL.md`:

````markdown
---
name: risk-run-propose
description: Cost-preview then propose run_risk. HITL-gated write. Use when risk-snapshot-read shows stale data or no run exists.
allowed-tools: run_risk
metadata:
  tier: domain-recipe
  related_tools: run_risk
---

# risk-run-propose — domain recipe

## When this applies

- Latest risk run > 1 BD stale OR positions changed since last run.
- `risk-report-workflow` upstream check has decided a fresh run is needed.

## Inputs to inspect first

- The portfolio's position count and product-type mix (cost driver).

## Step sequence

1. Compose cost-preview: "`<N>` positions; `<X>` snowball/phoenix MC;
   estimated runtime `<T>`."
2. State preview to user. HITL pause.
3. `run_risk(portfolio_id, method="summary"|"detail")`. Method per caller's
   need.
4. Confirm via `get_latest_risk_run` (new run should appear).

## What success looks like

"Fresh risk run completed: `ts=<>`, `valuation_date=<>`, totals
`delta=<>, gamma=<>, vega=<>`."

## Tool preferences

- WRITE (HITL): `run_risk`. Cost-preview MANDATORY.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/risk
git commit -m "feat(agent-skills): add 2 risk-domain recipes

- risk-snapshot-read: read latest persisted risk run + interpret currency.
  Pure read.
- risk-run-propose: cost-preview + run_risk (HITL). Used by
  risk-report-workflow when staleness check fails."
```

---

## Task 12: Author 2 market-data-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/market-data/market-data-fetch/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/market-data/market-data-drift/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/market-data/market-data-fetch
mkdir -p backend/app/services/deep_agent/skills/domains/market-data/market-data-drift
```

- [ ] **Step 2: Write `market-data-fetch` recipe**

`backend/app/services/deep_agent/skills/domains/market-data/market-data-fetch/SKILL.md`:

````markdown
---
name: market-data-fetch
description: Fetch current market snapshot for a set of underlyings, respecting symbol conventions from market-data-conventions card. Pure read.
allowed-tools: fetch_market_snapshot
metadata:
  tier: domain-recipe
  related_cards: market-data-conventions
  related_tools: fetch_market_snapshot
---

# market-data-fetch — domain recipe

## When this applies

- Any workflow needing current spot / vol / r / q for one or more underlyings.

## Inputs to inspect first

- List of `(underlying, input_type)` pairs (often from
  `position-input-enumerate`).
- `market-data-conventions` card for symbol formatting.

## Step sequence

1. Normalize each underlying to the canonical symbol per card conventions
   (e.g., `CSI 300` → `000300.SH`).
2. Group by `input_type`; one `fetch_market_snapshot` call per
   `(input_type, batch_of_symbols)`.
3. Surface returns + any per-symbol fetch failures.

## What success looks like

"Fetched `<N>` underlyings across `<M>` input_types; `<F>` failed (list)."

## Tool preferences

- READ-ONLY. No HITL.
- Batch by `input_type` — do NOT call once per underlying for large sets.
````

- [ ] **Step 3: Write `market-data-drift` recipe**

`backend/app/services/deep_agent/skills/domains/market-data/market-data-drift/SKILL.md`:

````markdown
---
name: market-data-drift
description: Compute drift between freshly fetched market snapshot and the inputs stored against positions. Returns per-input drift magnitude. Read + run_python compute.
allowed-tools: run_python
metadata:
  tier: domain-recipe
  related_cards: market-data-conventions
---

# market-data-drift — domain recipe

## When this applies

- After `market-data-fetch` has produced a current snapshot; need to compare
  vs what positions were last priced with.

## Inputs to inspect first

- The fetched snapshot.
- The position-stored inputs (from `position-snapshot` valuation rows).

## Step sequence

1. `run_python` script: for each `(underlying, input_type)`, compute
   `(current - stored) / stored` and `absolute_diff`.
2. Apply thresholds from `market-data-conventions` card (1% trader / 2% risk).
3. Return a sorted drift table with classification
   (`within-threshold` / `drifted` / `missing`).

## What success looks like

"Drift across `<N>` inputs: `<D>` drifted (>threshold), `<M>` missing
snapshot, `<W>` within tolerance. Top drift: `<input>: <pct>`."

## Tool preferences

- READ-ONLY + compute. `run_python` only.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/market-data
git commit -m "feat(agent-skills): add 2 market-data-domain recipes

- market-data-fetch: batched fetch_market_snapshot respecting symbol
  conventions; one call per (input_type, batch) to avoid per-underlying
  overhead.
- market-data-drift: run_python computation of per-input drift vs stored;
  applies lens-specific thresholds (1% trader / 2% risk)."
```

---

## Task 13: Author 3 rfq-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/rfq/rfq-draft/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/rfq/rfq-quote/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/rfq/rfq-submit-for-approval/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/rfq/rfq-draft
mkdir -p backend/app/services/deep_agent/skills/domains/rfq/rfq-quote
mkdir -p backend/app/services/deep_agent/skills/domains/rfq/rfq-submit-for-approval
```

- [ ] **Step 2: Write `rfq-draft` recipe**

`backend/app/services/deep_agent/skills/domains/rfq/rfq-draft/SKILL.md`:

````markdown
---
name: rfq-draft
description: From natural-language input, produce a validated RFQ draft row ready to quote. Chains draft → validate → persist. Used by rfq-intake-and-quote.
allowed-tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle
---

# rfq-draft — domain recipe

## When this applies

- Trader receives a natural-language RFQ request needing structured persistence.

## Inputs to inspect first

- The user's natural-language description.

## Step sequence

1. `draft_rfq_from_natural_language(text)` → candidate terms.
2. `validate_rfq_terms(terms)` → report violations. If any HARD violations,
   stop and surface to user; do NOT persist a known-invalid draft.
3. `create_or_update_rfq_draft(terms)` → persisted `draft_id`.

## What success looks like

"Draft RFQ `<id>` created: `product=<>, underlying=<>, notional=<>`, key
terms = `<...>`. Validated."

## Tool preferences

- COMPUTE + WRITE. None HITL-gated (drafts are non-binding).
````

- [ ] **Step 3: Write `rfq-quote` recipe**

`backend/app/services/deep_agent/skills/domains/rfq/rfq-quote/SKILL.md`:

````markdown
---
name: rfq-quote
description: Solve and quote an existing RFQ draft. Chains solve_rfq (price the spec) + quote_rfq (persist the quote). Used by rfq-intake-and-quote.
allowed-tools: solve_rfq quote_rfq
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle pricing-engines
---

# rfq-quote — domain recipe

## When this applies

- A validated RFQ draft exists; trader needs to produce the quote.

## Inputs to inspect first

- The RFQ draft (from `rfq-draft` or user-supplied id).

## Step sequence

1. Cost-preview if the product is MC-priced (snowball/phoenix). State preview.
2. `solve_rfq(draft_id)` → computed price + engine used.
3. `quote_rfq(draft_id, price)` → persists `quoted` state.

## What success looks like

"Quoted RFQ `<id>`: `price=<>, engine=<>, valuation_date=<>`. State: quoted."

## Tool preferences

- COMPUTE. No HITL config on solve/quote tools. Cost-preview for MC per policy.
````

- [ ] **Step 4: Write `rfq-submit-for-approval` recipe**

`backend/app/services/deep_agent/skills/domains/rfq/rfq-submit-for-approval/SKILL.md`:

````markdown
---
name: rfq-submit-for-approval
description: Submit a quoted RFQ for high_board approval. HITL-gated. Single tool wrapper; kept as a recipe to encode the pre-submit sanity check.
allowed-tools: submit_rfq_for_approval
metadata:
  tier: domain-recipe
  related_cards: rfq-lifecycle
---

# rfq-submit-for-approval — domain recipe

## When this applies

- Quoted RFQ is ready for governance review.
- User explicitly asks to "submit" / "send for approval".

## Inputs to inspect first

- The RFQ row (must be in `quoted` state).

## Step sequence

1. Verify RFQ is in `quoted` state (the tool will reject otherwise; better
   to catch up front).
2. Compose HITL summary: RFQ id, terms, quoted price, requested approver.
3. `submit_rfq_for_approval(rfq_id)` — HITL pause.

## What success looks like

"RFQ `<id>` submitted for approval. Audit event: `rfq.submitted`. Approver:
high_board."

## Tool preferences

- WRITE (HITL): `submit_rfq_for_approval`.
- Do NOT submit RFQs in any state other than `quoted` — surface the state
  mismatch to the user.
````

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/rfq
git commit -m "feat(agent-skills): add 3 rfq-domain recipes

- rfq-draft: NL → validated draft. Halts on hard validation violations.
- rfq-quote: solve_rfq + quote_rfq. Cost-preview for MC products.
- rfq-submit-for-approval: HITL-gated submit; verifies quoted-state
  precondition before invoking the tool."
```

---

## Task 14: Author 2 reporting-domain recipes

**Files:**
- Create: `backend/app/services/deep_agent/skills/domains/reporting/report-batch-run/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/domains/reporting/report-create-propose/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/domains/reporting/report-batch-run
mkdir -p backend/app/services/deep_agent/skills/domains/reporting/report-create-propose
```

- [ ] **Step 2: Write `report-batch-run` recipe**

`backend/app/services/deep_agent/skills/domains/reporting/report-batch-run/SKILL.md`:

````markdown
---
name: report-batch-run
description: Run a report batch to produce inline summary content (NOT persisted). Used by risk-report-workflow as the compute step before create_report. Read-mostly compute.
allowed-tools: run_report_batch
metadata:
  tier: domain-recipe
  related_tools: run_report_batch
---

# report-batch-run — domain recipe

## When this applies

- Inside `risk-report-workflow`, after `run_risk` has produced fresh data
  and before `create_report` is proposed.
- Standalone: "give me a one-shot summary without persisting".

## Inputs to inspect first

- Portfolio snapshot + latest risk run (results of `risk-snapshot-read`).

## Step sequence

1. Compose the `PortfolioSnapshot` payload (positions + selected fields).
2. `run_report_batch(title, report_type, portfolio_payload)` — returns
   inline summary + `artifact_hint`.
3. Inspect the summary's `totals` and `breakdowns`. Surface anomalies.

## What success looks like

"Report batch summary: `<metrics>`. Artifact hint: `<path-template>`.
Status: ready (NOT persisted)."

## Tool preferences

- COMPUTE. No HITL config; cost-preview if portfolio is large.
- Result is in-context only — does NOT persist until `report-create-propose`
  runs.
````

- [ ] **Step 3: Write `report-create-propose` recipe**

`backend/app/services/deep_agent/skills/domains/reporting/report-create-propose/SKILL.md`:

````markdown
---
name: report-create-propose
description: Cost-preview then propose create_report to persist a report job. HITL-gated write. Used by risk-report-workflow as the final persistence step.
allowed-tools: create_report
metadata:
  tier: domain-recipe
  related_tools: create_report
---

# report-create-propose — domain recipe

## When this applies

- Inside `risk-report-workflow`, after `report-batch-run` has confirmed the
  payload looks correct.
- User explicitly asks to "save the report" / "persist".

## Inputs to inspect first

- The intended `portfolio_id`, `report_type`, `title`.

## Step sequence

1. Compose cost-preview: `report_type`, expected artifact set (`html`,
   `xlsx`), approximate runtime.
2. State preview. HITL pause.
3. `create_report(portfolio_id, report_type, title)` → returns
   `report_job_id, task_id, status`.

## What success looks like

"Report queued: `report_job_id=<>`, `task_id=<>`, `status=<>`. Artifacts
will land under `/artifacts/<safe_name>.{html,xlsx}`."

## Tool preferences

- WRITE (HITL): `create_report`. Cost-preview MANDATORY.
- Surface the `task_id` for monitoring; do NOT poll status from this recipe.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/domains/reporting
git commit -m "feat(agent-skills): add 2 reporting-domain recipes

- report-batch-run: inline summary compute via run_report_batch (NOT
  persisted). Used by risk-report-workflow.
- report-create-propose: cost-preview + create_report (HITL). Final
  persistence step; surfaces task_id for monitoring."
```

---

## Task 15: Author 3 trader workflow procedures

**Goal:** Workflow procedures that name domain skills in their step sequences. These are end-to-end task recipes for the trader persona.

**Files:**
- Create: `backend/app/services/deep_agent/skills/procedures/trader/rfq-intake-and-quote/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/procedures/trader/portfolio-pricing-run/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/procedures/trader/market-data-profile/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/procedures/trader/rfq-intake-and-quote
mkdir -p backend/app/services/deep_agent/skills/procedures/trader/portfolio-pricing-run
mkdir -p backend/app/services/deep_agent/skills/procedures/trader/market-data-profile
```

- [ ] **Step 2: Write `rfq-intake-and-quote` procedure**

`backend/app/services/deep_agent/skills/procedures/trader/rfq-intake-and-quote/SKILL.md`:

````markdown
---
name: rfq-intake-and-quote
description: End-to-end RFQ intake on the trader side — natural-language draft, term validation, pricing, and quote production. Read when the user asks "quote this", "draft an RFQ for X", "what would Y cost", or pastes a request to be turned into a quotable RFQ. Stops BEFORE submit-for-approval (that's governance).
allowed-tools: draft_rfq_from_natural_language validate_rfq_terms create_or_update_rfq_draft solve_rfq quote_rfq get_rfq_catalog
metadata:
  tier: procedure
  persona: trader
  related_domains: rfq pricing
  related_products: snowball-cn
---

# rfq-intake-and-quote — workflow procedure

## When this applies

- User pastes an RFQ description in natural language.
- User asks to quote a specific product spec.
- User explicitly names this skill from the orchestrator.

## Inputs to inspect first

1. Read the `rfq-lifecycle` domain card if not loaded this session.
2. If the product type is recognizable, read the matching product card.

## Step sequence

1. Apply the `rfq-draft` domain recipe
   (`draft_rfq_from_natural_language` → `validate_rfq_terms` →
   `create_or_update_rfq_draft`).
2. Apply the `rfq-quote` domain recipe (`solve_rfq` → `quote_rfq`).
3. Report the quoted price, the inputs used, and the draft ID. Do NOT submit.

## What success looks like

"Drafted RFQ `<id>` for `<product>`: `quote = <price>, model = <engine>,
inputs = <spot/vol/r>`. Ready for review; not submitted."

## Tool preferences

- READ-FIRST: `get_rfq_catalog` if product type is ambiguous.
- COMPUTE: `solve_rfq` / `quote_rfq` (no HITL — quote-time, not bookings).
- Do NOT call `submit_rfq_for_approval` from this skill. Surface the draft
  ID and let the user/orchestrator decide.
- Cost-preview discipline: `solve_rfq` is compute-heavy. Apply the
  cost-preview policy if the spec involves Monte Carlo (snowball, phoenix-MC).
````

- [ ] **Step 3: Write `portfolio-pricing-run` (trader lens) procedure**

`backend/app/services/deep_agent/skills/procedures/trader/portfolio-pricing-run/SKILL.md`:

````markdown
---
name: portfolio-pricing-run
description: Trader-lens repricing workflow — snapshot the book, identify stale or drifted positions, propose a price_positions run with cost preview, and verify the result. Read when the user asks "reprice", "refresh prices", "what's changed since last pricing", or before any pricing-impact decision.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions run_python
metadata:
  tier: procedure
  persona: trader
  related_domains: position market-data pricing
---

# portfolio-pricing-run (trader lens) — workflow procedure

## When this applies

- User requests repricing of a portfolio.
- User asks about pricing freshness or drift.
- Trader-side prerequisite before any pricing-impact decision.

## Inputs to inspect first

1. Apply the `position-snapshot` domain recipe.
2. Apply the `market-data-fetch` domain recipe for the snapshot's underlyings.

## Step sequence

1. Compute per-position staleness: days-since-last-valuation AND
   spot-drift-vs-stored. Use `run_python` for portfolios with > 20
   positions (RFSW pattern).
2. Flag positions: stale-by-time (> 1 BD) OR drifted (> 1% spot change).
3. Cost-preview the `price_positions` call: estimated `<N>` positions ×
   `<engine-cost>`.
4. Propose `price_positions` with the flagged set as `position_ids`
   (HITL pause).
5. After approval, verify result by re-reading
   `get_latest_position_valuations`.

## What success looks like

"`<N>` positions, `<K>` flagged stale, `<M>` flagged drift, cost-preview
`<X>`, repriced `<K+M>` positions. New valuations stored; max drift now
`<Y>`."

## Tool preferences

- READ-FIRST: `get_positions`, `get_latest_position_valuations`,
  `fetch_market_snapshot`. No HITL.
- WRITE (HITL): `price_positions` after cost-preview.
- `run_python` for any aggregation across > 20 positions.
- Do NOT propose `price_product` from this skill — that's `price-product-adhoc`
  for one-off specs.
````

- [ ] **Step 4: Write `market-data-profile` procedure**

`backend/app/services/deep_agent/skills/procedures/trader/market-data-profile/SKILL.md`:

````markdown
---
name: market-data-profile
description: Audit the market-data freshness and coverage backing a portfolio's pricing — enumerate unique underlyings, fetch current snapshots, run drift analysis vs stored, and flag remediation candidates. Read-only diagnostic. Read when the user asks "is our market data fresh", "any drift in inputs", "what underlyings need refresh", or before any pricing/risk run.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot run_python
metadata:
  tier: procedure
  persona: trader
  related_domains: position market-data
---

# market-data-profile — workflow procedure

## When this applies

- User asks about market-data quality, freshness, or coverage.
- Pre-step before user-initiated pricing or risk runs (offer this
  proactively if the user mentions a stale book).

## Inputs to inspect first

1. Read the `market-data-conventions` domain card if not loaded this session.
2. Apply the `position-input-enumerate` domain recipe to list unique
   `(underlying, input_type)` pairs from current positions.

## Step sequence

1. For each unique underlying, apply the `market-data-fetch` domain recipe.
2. Apply the `market-data-drift` domain recipe to compare current vs stored.
3. Build a per-underlying flag table: stale (> 1 BD), drifted (> 1% spot OR
   > 5% vol), missing (no snapshot returned).
4. Group flags by remediation type. Do NOT remediate — surface candidates only.

## What success looks like

"Profiled `<N>` underlyings: `<S>` stale, `<D>` drifted, `<M>` missing.
Remediation candidates by type: `<list>`. Recommend
`import_position_market_inputs` for `<X>` / `fetch_market_snapshot` refresh
for `<Y>`."

## Tool preferences

- READ-ONLY. No HITL.
- `run_python` for drift aggregation across > 10 underlyings.
- Do NOT propose `import_position_market_inputs` from this skill (it's
  HITL-gated governance write — surface candidates only).
````

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/procedures/trader
git commit -m "feat(agent-skills): add 3 trader workflow procedures

- rfq-intake-and-quote: end-to-end RFQ intake. Stops before submit
  (governance handoff). Composes rfq-draft + rfq-quote recipes.
- portfolio-pricing-run (trader lens): snapshot + staleness + drift +
  propose price_positions. 1% drift threshold for trader lens.
- market-data-profile: read-only audit; flags drift candidates without
  remediating (import_position_market_inputs is a governance write
  that should be its own explicit ask)."
```

---

## Task 16: Author 2 risk_manager workflow procedures

**Files:**
- Create: `backend/app/services/deep_agent/skills/procedures/risk_manager/portfolio-pricing-run/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/procedures/risk_manager/risk-report-workflow/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/procedures/risk_manager/portfolio-pricing-run
mkdir -p backend/app/services/deep_agent/skills/procedures/risk_manager/risk-report-workflow
```

- [ ] **Step 2: Write `portfolio-pricing-run` (risk lens) procedure**

`backend/app/services/deep_agent/skills/procedures/risk_manager/portfolio-pricing-run/SKILL.md`:

Cross-persona variant: same catalog name as trader's, different body. Each persona's `SkillsMiddleware` sees only its own source dir, so catalogs never collide.

````markdown
---
name: portfolio-pricing-run
description: Risk-lens repricing workflow — confirm pricing inputs are fresh enough to underpin a risk run, propose price_positions only when staleness threatens risk-input integrity, then handoff to the risk-report-workflow. Read BEFORE any run_risk if you suspect prices feeding the risk run are stale.
allowed-tools: get_positions get_latest_position_valuations fetch_market_snapshot price_positions run_python
metadata:
  tier: procedure
  persona: risk_manager
  related_domains: position market-data pricing risk
  related_skills: risk-report-workflow
---

# portfolio-pricing-run (risk lens) — workflow procedure

## When this applies

- Pre-step before `run_risk` when valuations look stale.
- Risk-side request to "make sure prices are current before the risk run."
- Compound flow handoff from trader (`portfolio-pricing-run` trader lens
  completed).

## Inputs to inspect first

1. Apply the `position-snapshot` domain recipe.
2. Read `get_latest_risk_run` — establishes WHICH valuations the most recent
   risk run consumed (the relevant staleness reference, not "today").
3. Apply the `market-data-fetch` domain recipe for any underlying whose
   stored input is older than the last risk run.

## Step sequence

1. For each position, compute: `(now - valuation_date) BD` AND
   `(now - last_risk_run.valuation_date) BD`. The relevant question for risk
   is "do these prices reflect the same regime as the risk run?"
2. Flag positions where `stored valuation_date < last_risk_run.valuation_date`
   OR where spot has drifted > 2% (tighter threshold than trader lens; risk
   is gamma-sensitive).
3. If NO positions flagged: report "pricing inputs current vs latest risk
   run; no repricing needed." Stop. Risk run can proceed.
4. If positions flagged: cost-preview `price_positions`, propose run, HITL
   pause.
5. After approval: confirm completion via `get_latest_position_valuations`,
   then explicitly handoff to `risk-report-workflow` (or signal the
   orchestrator that risk run is ready to proceed).

## What success looks like

"`<N>` positions, `<K>` needed refresh vs last risk run, repriced `<K>`;
pricing inputs now consistent with `valuation_date <D>`. Risk run can proceed."

## Tool preferences

- READ-FIRST: `get_positions`, `get_latest_position_valuations`,
  `get_latest_risk_run`, `fetch_market_snapshot`. No HITL.
- WRITE (HITL): `price_positions` after cost-preview.
- `run_python` for aggregation across > 20 positions.
- Do NOT propose `run_risk` from this skill — that's `risk-report-workflow`'s
  job.
````

- [ ] **Step 3: Write `risk-report-workflow` procedure**

`backend/app/services/deep_agent/skills/procedures/risk_manager/risk-report-workflow/SKILL.md`:

````markdown
---
name: risk-report-workflow
description: End-to-end risk reporting workflow — verify risk run currency, propose run_risk if stale, generate the report batch, and propose create_report. Read when the user asks for a risk report, a portfolio risk summary, or any governance-grade risk artifact for a portfolio.
allowed-tools: get_positions get_latest_risk_run calculate_risk run_risk run_report_batch create_report recommend_hedge run_python
metadata:
  tier: procedure
  persona: risk_manager
  related_domains: position risk reporting
  related_skills: portfolio-pricing-run
---

# risk-report-workflow — workflow procedure

## When this applies

- User requests a risk report or portfolio risk summary.
- Governance ask: "what's our exposure on portfolio X."
- Compound flow handoff after `portfolio-pricing-run` (risk lens) completes.

## Inputs to inspect first

1. Apply the `position-snapshot` domain recipe.
2. Apply the `risk-snapshot-read` domain recipe (wraps `get_latest_risk_run`)
   to see the most recent persisted risk run + its `valuation_date`.
3. If `pricing-run-propose` has not been run this session AND latest risk run
   is stale (> 1 BD), recommend the orchestrator route through
   `portfolio-pricing-run` (risk lens) first. STOP this skill; do not
   silently reprice.

## Step sequence

1. Determine risk-run currency: compare `last_risk_run.valuation_date` to
   today AND to position last-modified timestamps.
2. If stale (> 1 BD OR positions changed since last run): apply the
   `risk-run-propose` domain recipe (cost-preview + `run_risk` HITL).
3. Apply the `report-batch-run` domain recipe to produce the inline summary
   payload (read-mostly; no persistence yet).
4. Inspect the inline summary. If risk metrics breach desk limits, call
   `recommend_hedge` and include the recommendation in the report draft.
5. Apply the `report-create-propose` domain recipe (cost-preview +
   `create_report` HITL) to persist the report.
6. Report `report_job_id`, `task_id`, `status`, and a one-paragraph executive
   summary.

## What success looks like

"Risk report queued: `report_job_id=<X>, task_id=<Y>, status=<Z>`. Portfolio
totals: `delta=<D>, gamma=<G>, vega=<V>`. `<K>` positions in gamma-spike
zone. Hedge recommendation: `<H>` (if any)."

## Tool preferences

- READ-FIRST: `get_positions`, `get_latest_risk_run`, `calculate_risk` (for
  hypothetical hedge sizing). No HITL.
- WRITE (HITL): `run_risk`, `create_report`. Each preceded by its own
  cost-preview per policy.
- `run_python` for > 50-position aggregations.
- Do NOT propose `price_positions` from this skill — if pricing is stale,
  bounce back to `portfolio-pricing-run`. Separation of concerns: pricing
  workflow owns valuations; risk workflow owns risk + reports.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/procedures/risk_manager
git commit -m "feat(agent-skills): add 2 risk_manager workflow procedures

- portfolio-pricing-run (risk lens): cross-persona variant of trader's,
  with the SAME catalog name. Stricter drift threshold (2% vs 1%) for
  gamma sensitivity. Hands off to risk-report-workflow.
- risk-report-workflow: end-to-end risk reporting. Refuses to reprice
  silently — if pricing is stale, bounces back to portfolio-pricing-run.
  Preserves 'one cost-preview = one procedure decision' invariant."
```

---

## Task 17: Author 1 high_board workflow procedure

**Goal:** The first procedure for the `high_board` persona — governance read-side report query/display. Uses the two new tools (`list_reports`, `get_report`) from Tasks 2-3 and reads HTML artifacts via the `/artifacts` mount from Task 4.

**Files:**
- Delete: `backend/app/services/deep_agent/skills/procedures/high_board/.gitkeep` (replaced by first real skill)
- Create: `backend/app/services/deep_agent/skills/procedures/high_board/report-query-and-display/SKILL.md`

- [ ] **Step 1: Remove the .gitkeep and create the procedure subdirectory**

```bash
cd /Users/fuxinyao/open-otc-trading
rm backend/app/services/deep_agent/skills/procedures/high_board/.gitkeep
mkdir -p backend/app/services/deep_agent/skills/procedures/high_board/report-query-and-display
```

- [ ] **Step 2: Write `report-query-and-display` procedure**

`backend/app/services/deep_agent/skills/procedures/high_board/report-query-and-display/SKILL.md`:

````markdown
---
name: report-query-and-display
description: Governance read-side workflow — locate persisted reports for a portfolio, fetch metadata + inline summary, and present an interpretation with pointers to artifacts (HTML/XLSX). Read when the user asks "show me the latest report", "what reports do we have for X", "what does last week's risk report say", or any governance review of historical reports.
allowed-tools: list_reports get_report list_portfolios get_portfolio
metadata:
  tier: procedure
  persona: high_board
  related_domains: portfolio reporting
---

# report-query-and-display — workflow procedure

## When this applies

- User asks to review or quote from a persisted report.
- Governance check: "what does the most recent risk/portfolio/rfq report
  say about portfolio X."
- Pre-decision review before approving a quote or release.

## Inputs to inspect first

1. Read the `portfolio-model` domain card if not loaded this session —
   needed to disambiguate Container vs View when the user names a portfolio
   by label.
2. Clarify `portfolio_id` per the Clarification Protocol policy if ambiguous:
   if the user gave a name, call `list_portfolios` and confirm the resolved
   ID before proceeding. Do NOT guess.

## Step sequence

1. Call `list_reports(portfolio_id=<resolved_id>, report_type=<user-requested
   type or omitted>)`. Inspect the returned ReportJobs: surface only
   `status = "completed"` rows unless the user explicitly asked about
   pending/failed jobs.
2. Pick the target report(s):
   - If "the latest", take the highest `created_at`.
   - If a date range was named, filter in-context (no extra tool calls).
   - If multiple candidates remain ambiguous, ask the user to pick
     (do NOT auto-select).
3. For each selected report, call `get_report(report_id=<id>)`. Read its
   `summary` field, `status`, and `artifact_paths`.
4. Compose a structured display:
   - One-paragraph interpretation of the summary, plain-language, citing
     concrete numbers from the summary payload.
   - A small table: report metadata (`id`, `type`, `title`, `created_at`,
     `status`).
   - Artifact references: HTML path, XLSX path.
   - For each HTML artifact, `read_file(html_path, limit=2000)` and quote
     2-3 relevant excerpts. Cap quoted content at ~300 words; if the report
     is larger, summarize the structure ("sections: …") and let the user
     request a deep-read of one section.
   - For XLSX artifacts, surface the path only — binary file, not read.
5. If the user requested an interpretation the summary can't answer (e.g.,
   position-level detail not in the summary), say so explicitly and propose
   the next step: route to risk_manager's `risk-report-workflow` for a fresh,
   deeper-grained report. Do NOT call `create_report` from this skill.

## What success looks like

"Reviewed report `<id>` (`<type>, <title>`, created `<date>`, status
`<status>`). Summary: `<one-paragraph interpretation citing concrete
metrics>`. Artifacts: `<html_path>`, `<xlsx_path>`. Anomalies flagged:
`<list or 'none'>`. Next-step recommendations: `<list or 'no follow-up
needed'>`."

## Tool preferences

- READ-ONLY. No HITL. No cost-preview required (read-only tools).
- `list_portfolios` / `get_portfolio` ONLY for portfolio disambiguation.
- `read_file` on `/artifacts/*.html` ALLOWED — use for inline quoting.
- `read_file` on `/artifacts/*.xlsx` DISALLOWED — binary, treat as reference.
- Do NOT call `run_report_batch`, `create_report`, `run_risk`, or any write
  tool from this skill. If a fresh report is needed, bounce back: "Existing
  reports don't cover `<X>`; recommend running `risk-report-workflow` on
  `persona=risk_manager`."
````

- [ ] **Step 3: Verify the .gitkeep is gone and the new skill is in place**

```bash
cd /Users/fuxinyao/open-otc-trading
ls -la backend/app/services/deep_agent/skills/procedures/high_board/
```

Expected: directory contains only `report-query-and-display/` (no `.gitkeep`).

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add -A backend/app/services/deep_agent/skills/procedures/high_board
git commit -m "feat(agent-skills): add high_board/report-query-and-display procedure

First high_board workflow skill. Uses list_reports + get_report tools
(Tasks 2-3) to query persisted ReportJobs and read_file on HTML
artifacts via the /artifacts mount (Task 4). XLSX paths surfaced as
references only. Refuses to generate fresh reports — bounces back to
risk-report-workflow for that.

Removes the v1 .gitkeep placeholder; high_board procedures subtree
is now non-empty."
```

---

## Task 18: Author 3 routing skills

**Goal:** Activate orchestrator-side compound-flow routing. These are the first skills the orchestrator itself reads.

**Files:**
- Create: `backend/app/services/deep_agent/skills/routing/pricing-and-risk-compound/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/routing/snowball-book-audit/SKILL.md`
- Create: `backend/app/services/deep_agent/skills/routing/market-data-then-reprice/SKILL.md`

- [ ] **Step 1: Create subdirectories**

```bash
cd /Users/fuxinyao/open-otc-trading
mkdir -p backend/app/services/deep_agent/skills/routing/pricing-and-risk-compound
mkdir -p backend/app/services/deep_agent/skills/routing/snowball-book-audit
mkdir -p backend/app/services/deep_agent/skills/routing/market-data-then-reprice
```

- [ ] **Step 2: Write `pricing-and-risk-compound` routing skill**

`backend/app/services/deep_agent/skills/routing/pricing-and-risk-compound/SKILL.md`:

````markdown
---
name: pricing-and-risk-compound
description: Compound flow when the user wants BOTH pricing health AND risk health on the same portfolio. Chains trader's portfolio-pricing-run, then risk_manager's portfolio-pricing-run (risk lens), then risk_manager's risk-report-workflow. Read when the user asks "give me the full picture", "pricing AND risk", "is portfolio X OK across the board", or any compound pricing+risk query.
metadata:
  tier: routing
  related_personas: trader risk_manager
  related_procedures: portfolio-pricing-run risk-report-workflow
---

# pricing-and-risk-compound — routing skill

## When this applies

- Compound pricing + risk queries on a single portfolio.
- "Full check" / "comprehensive audit" requests scoped to one portfolio.
- Pre-decision review before a governance ask.

## Step sequence

1. Apply Clarification Protocol: confirm `portfolio_id` if not explicit. Do
   NOT proceed on ambiguous portfolio reference.
2. Delegate to trader with `portfolio-pricing-run` (trader lens):
   - description: "Use `portfolio-pricing-run`. Walk through
     `portfolio_id=<id>` for pricing health. Flag stale/drifted positions;
     surface cost-preview if you propose repricing."
   - Wait for trader to return findings. If trader proposed repricing AND
     user confirmed via HITL, repricing has already executed before reply.
3. Synthesize trader findings. Extract: position count, flagged set,
   repricing outcome (if any), latest `valuation_date`.
4. Delegate to risk_manager with `portfolio-pricing-run` (risk lens):
   - description includes trader's flagged set and `valuation_date` for
     handoff.
   - description: "Use `portfolio-pricing-run` (risk lens). Trader's pricing
     pass: `<summary>`. Verify risk inputs are current vs latest risk run;
     propose repricing only if it would change the risk view."
   - Wait for risk_manager reply.
5. Delegate to risk_manager with `risk-report-workflow`:
   - description: "Use `risk-report-workflow`. Pricing inputs verified
     current by prior step (`valuation_date=<D>`). Produce the risk run if
     stale, then the report."
6. Synthesize combined report. Cite each persona's findings explicitly
   (existing Compound queries policy).

## What success looks like

A combined report:
- Trader findings: pricing freshness, flagged positions, repricing outcome.
- Risk_manager findings: risk-input currency, risk run details, report id.
- Joint observations: positions flagged by both lenses.

## Routing notes

- If the user only wants pricing OR only wants risk, do NOT route this flow —
  delegate directly to the single relevant persona+procedure.
- If trader's pricing pass surfaces no flagged positions, you MAY skip step 4
  (risk-lens repricing) and go straight to step 5 — but state the skip
  explicitly so the user knows.
- HITL pauses inside delegations are normal. If the user rejects a
  cost-preview at any step, surface the rejection and stop the routing flow.
````

- [ ] **Step 3: Write `snowball-book-audit` routing skill**

`backend/app/services/deep_agent/skills/routing/snowball-book-audit/SKILL.md`:

````markdown
---
name: snowball-book-audit
description: Compound flow for Snowball portfolios — chains trader's snowball-position-diagnostics (pricing health) with risk_manager's snowball-position-diagnostics (risk health). Read when the user asks "is the snowball book OK", "full snowball check", or any compound snowball query. Retrofits the v1 manual orchestration into a named flow.
metadata:
  tier: routing
  related_personas: trader risk_manager
  related_procedures: snowball-position-diagnostics
  related_products: snowball-cn
---

# snowball-book-audit — routing skill

## When this applies

- Compound Snowball queries spanning pricing + risk lenses on the same
  portfolio.
- "Audit the snowball book" / "snowball health check" requests.

## Step sequence

1. Apply Clarification Protocol on `portfolio_id`.
2. Delegate to trader with `snowball-position-diagnostics`:
   - description: "Use `snowball-position-diagnostics`. Walk through
     `portfolio_id=<id>` for pricing health: KO/KI distance, autocall
     proximity, stale-input check. Read only — do NOT propose
     price_positions yet."
3. Synthesize trader response. Extract: positions near KI, positions near
   KO, pricing-run age.
4. Delegate to risk_manager with `snowball-position-diagnostics`:
   - description: "Use `snowball-position-diagnostics`. Walk through
     `portfolio_id=<id>` for risk health. Trader flagged `<K>` positions
     within 5% of KI: `<list>`. Read latest risk run; propose run_risk
     only if stale."
5. Synthesize combined report.

## What success looks like

"Snowball book audit for portfolio `<id>`:
- Pricing (trader): `<N>` positions, `<K>` near KI, `<M>` near KO, accrual
  `<ok|drift>`, pricing age `<X>` BD.
- Risk (risk_manager): `vega=<>, delta=<>, gamma=<>`; `<K>` in gamma-spike
  zone; hedge recommendation: `<>`.
- Joint: positions flagged by both lenses: `<list>`."

## Routing notes

- Snowball-specific. Do NOT use for non-Snowball portfolios; route through
  `pricing-and-risk-compound` instead.
- Canonical example of "one concept, two persona lenses" routing — the SAME
  skill name in two persona catalogs, composed by this routing skill.
````

- [ ] **Step 4: Write `market-data-then-reprice` routing skill**

`backend/app/services/deep_agent/skills/routing/market-data-then-reprice/SKILL.md`:

````markdown
---
name: market-data-then-reprice
description: Sequential trader flow — audit market data freshness across a portfolio's underlyings, then (only if drift found) propose a repricing run. Single persona; routing skill exists to encode the audit→reprice ordering and the "skip reprice if no drift" decision.
metadata:
  tier: routing
  related_personas: trader
  related_procedures: market-data-profile portfolio-pricing-run
---

# market-data-then-reprice — routing skill

## When this applies

- User asks "refresh inputs and reprice" / "make sure data is current then
  reprice".
- Trader-initiated weekly hygiene scan.

## Step sequence

1. Apply Clarification Protocol on `portfolio_id`.
2. Delegate to trader with `market-data-profile`:
   - description: "Use `market-data-profile`. Audit market-data freshness/
     coverage on `portfolio_id=<id>`. Surface drift candidates; do NOT
     remediate."
3. Inspect trader findings. Branch:
   a. **No drift found**: report "Market data current for portfolio `<id>`;
      no repricing needed." STOP.
   b. **Drift candidates surfaced for inputs requiring import**: surface the
      candidates to the user and pause — `import_position_market_inputs` is
      HITL-gated governance write, not in scope for this routing flow.
   c. **Drift handled by snapshot refresh only**: proceed to step 4.
4. Delegate to trader with `portfolio-pricing-run`:
   - description: "Use `portfolio-pricing-run`. Market-data audit completed;
     drift detected on `<list of underlyings>`. Reprice positions affected
     by drifted inputs."
5. Synthesize: data audit outcome + repricing outcome.

## What success looks like

- No-drift path: "Audit clean; no repricing needed."
- Drift-then-reprice path: "Audit: `<D>` drifted inputs. Repriced `<K>`
  positions. Max drift now `<X>`."
- Drift-needs-import path: "Audit: `<D>` drifted inputs requiring re-import.
  Pause for user decision on `import_position_market_inputs`."

## Routing notes

- Single-persona compound flow. Lives here (not in trader's procedures)
  because the audit→reprice ordering is a routing concern: the orchestrator
  decides whether to invoke repricing based on audit output, not the trader.
- If the user explicitly wants ONLY the audit (not reprice), route directly
  to `market-data-profile` and skip this skill.
````

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/skills/routing
git commit -m "feat(agent-skills): add 3 routing skills (orchestrator-only)

- pricing-and-risk-compound: trader pricing + risk_manager pricing +
  risk-report. The general compound flow for any portfolio.
- snowball-book-audit: retrofits v1's prompt-only snowball compound
  routing into a named SKILL.md. Single source of truth.
- market-data-then-reprice: single-persona routing flow (trader) that
  encodes audit→reprice ordering and skip-if-clean decision."
```

---

## Task 19: Update `prompts/orchestrator.md` — naming routing skills + matrix delta

**Goal:** Activate the new orchestrator behavior. The prompt gains a "Naming routing skills" subsection; the routing matrix gets new rows for the three routing skills; and the v1 prompt-only snowball compound routing rule is REMOVED (the `snowball-book-audit` routing skill is now the single source of truth).

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`

- [ ] **Step 1: Read the current orchestrator prompt to locate the sections to modify**

```bash
cd /Users/fuxinyao/open-otc-trading
cat backend/app/services/deep_agent/prompts/orchestrator.md
```

Identify three landmarks (their exact line numbers will vary):
1. The existing "Naming skills in delegations" section (added in v1 Task 10).
2. The existing Routing matrix table.
3. Any v1 prompt-only snowball compound-routing rule (look for "snowball" in the routing section).

- [ ] **Step 2: Insert "Naming routing skills" subsection**

After the existing "## Naming skills in delegations" section (and BEFORE the next major section), append a new subsection:

````markdown

### Naming routing skills

You have your OWN skills catalog now: `/skills/routing/`. When a user request
matches a compound flow you see in your catalog, `read_file` the matching
routing skill BEFORE issuing any `task(...)` calls.

A routing skill tells you:
- What persona(s) to delegate to and in what order.
- What `description` content to put in each `task(...)` argument (it names
  the procedure skill the persona should use).
- How to synthesize the persona replies.

You still author each `task(...)` call yourself — routing skills are recipes,
not auto-execution. The routing skill body uses the orchestrator's existing
clarification, cost-preview, and HITL rules; it does not bypass them.

If no routing skill matches the user's request, fall back to the Routing
matrix below for single-persona delegations.
````

- [ ] **Step 3: Update the Routing matrix to include the three new routing skills**

Find the existing Routing matrix table. Append (or insert before any
"single-persona only" notes) these rows:

```markdown
| Compound pricing + risk health on one portfolio        | (routing skill)        | pricing-and-risk-compound        |
| Snowball book audit (pricing + risk on same portfolio) | (routing skill)        | snowball-book-audit              |
| Market-data audit followed by repricing (trader only)  | (routing skill)        | market-data-then-reprice         |
```

Format these rows as a "Compound flows (handled by routing skills)" subsection at the bottom of the matrix:

```markdown
**Compound flows (handled by routing skills):**

| Request shape                                          | Persona                | Routing skill                    |
|--------------------------------------------------------|------------------------|----------------------------------|
| Compound pricing + risk health on one portfolio        | trader + risk_manager  | pricing-and-risk-compound        |
| Snowball book audit (pricing + risk on same portfolio) | trader + risk_manager  | snowball-book-audit              |
| Market-data audit followed by repricing (trader only)  | trader                 | market-data-then-reprice         |
```

- [ ] **Step 4: Remove v1 prompt-only snowball compound routing rule**

Locate any text in the orchestrator prompt that explicitly handles "snowball compound" or "trader + risk_manager for snowball" outside the new routing skill. Examples to look for:
- A row in the v1 Routing matrix mapping snowball compound queries directly to a manual delegation sequence.
- A separate paragraph describing the snowball book audit handoff.

Replace those with a single reference to `snowball-book-audit`. If the v1 had a row like:

```markdown
| Snowball book audit | trader + risk_manager | snowball-position-diagnostics (both personas) |
```

Replace it (or remove the row and ensure the new "Compound flows" subsection covers it):

```markdown
| Snowball book audit | (see Compound flows below) | snowball-book-audit |
```

The single-persona rows for `snowball-position-diagnostics` (one for trader, one for risk_manager when the request is single-lens) should REMAIN — the routing skill is for compound queries, not single-lens.

- [ ] **Step 5: Add an in-context audit signal at the bottom of the file**

At the end of `orchestrator.md`, append a short note for future maintainers:

```markdown

---

*v2 routing additions (2026-05-15): `/skills/routing/` catalog contains
`pricing-and-risk-compound`, `snowball-book-audit`,
`market-data-then-reprice`. See
`docs/superpowers/specs/2026-05-15-agent-skills-layer-v2-design.md` §5 for
the design rationale.*
```

- [ ] **Step 6: Run the orchestrator/persona test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -v -k "orchestrator or persona or skills_catalog" 2>&1 | tail -20
```

Expected: existing tests still pass. The v1 catalog assertions check specific skill names that still exist.

- [ ] **Step 7: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/deep_agent/prompts/orchestrator.md
git commit -m "docs(agent-skills): orchestrator prompt — naming routing skills + matrix delta

- Adds 'Naming routing skills' subsection: orchestrator reads its own
  routing catalog before issuing task(...) calls; routing skill names
  the procedure each persona should use.
- Extends Routing matrix with a 'Compound flows' subsection covering
  the three new routing skills.
- Removes v1 prompt-only snowball compound routing rule. The
  snowball-book-audit routing skill (Task 18) is now the single source
  of truth for the snowball compound flow.

Single-lens snowball-position-diagnostics rows remain unchanged."
```

---

## Task 20: Extended Tier-B catalog tests for v2 surface

**Goal:** Assert that each persona's catalog (trader, risk_manager, high_board) and the orchestrator's catalog contain exactly the expected skill names after v2. This is the primary regression guard against per-source-list mistakes and skill-name typos.

**Files:**
- Create: `tests/test_skills_catalog_v2.py`

- [ ] **Step 1: Write the catalog assertion tests**

`tests/test_skills_catalog_v2.py`:

```python
"""Extended Tier-B catalog assertions for the v2 skills layer.

These tests verify that each persona's `skills=[...]` source list and the
orchestrator's `skills=["/skills/routing/"]` produce the expected catalog
entries after v2. They exercise SkillsMiddleware's source-loading machinery
directly via the `_list_skills` helper backed by a FilesystemBackend
pointing at the real on-disk skills tree.

Sibling to `test_skills_catalog.py` (v1 — kept for the v1 anchor skills).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills


_SKILLS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "app"
    / "services"
    / "deep_agent"
    / "skills"
)


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(_SKILLS_ROOT), virtual_mode=True)


def _names(skills) -> set[str]:
    return {s["name"] for s in skills}


# -----------------------------------------------------------------------------
# Per-persona procedure source assertions
# -----------------------------------------------------------------------------


def test_trader_procedures_source_v2(skills_backend: FilesystemBackend):
    """Trader procedures: v1 snowball anchor + 3 new workflow procedures."""
    skills = _list_skills(skills_backend, "/procedures/trader/")
    assert _names(skills) == {
        "snowball-position-diagnostics",        # v1 anchor
        "rfq-intake-and-quote",                 # v2
        "portfolio-pricing-run",                # v2 (trader lens)
        "market-data-profile",                  # v2
    }


def test_risk_manager_procedures_source_v2(skills_backend: FilesystemBackend):
    """Risk_manager procedures: v1 snowball + 2 new workflow procedures."""
    skills = _list_skills(skills_backend, "/procedures/risk_manager/")
    assert _names(skills) == {
        "snowball-position-diagnostics",        # v1 anchor
        "portfolio-pricing-run",                # v2 (risk lens, same name as trader's)
        "risk-report-workflow",                 # v2
    }


def test_high_board_procedures_source_v2(skills_backend: FilesystemBackend):
    """High_board procedures: 1 new workflow procedure (first ever)."""
    skills = _list_skills(skills_backend, "/procedures/high_board/")
    assert _names(skills) == {"report-query-and-display"}


# -----------------------------------------------------------------------------
# Per-domain source assertions (cards + recipes)
# -----------------------------------------------------------------------------


def test_position_domain_has_2_recipes(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/position/")
    assert _names(skills) == {"position-snapshot", "position-input-enumerate"}
    for s in skills:
        assert s["metadata"]["tier"] == "domain-recipe"


def test_portfolio_domain_has_1_card(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/portfolio/")
    assert _names(skills) == {"portfolio-model"}
    assert skills[0]["metadata"]["tier"] == "domain-card"


def test_pricing_domain_has_1_card_and_2_recipes(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/pricing/")
    assert _names(skills) == {
        "pricing-engines",          # card
        "pricing-run-propose",      # recipe
        "price-product-adhoc",      # recipe
    }
    by_name = {s["name"]: s for s in skills}
    assert by_name["pricing-engines"]["metadata"]["tier"] == "domain-card"
    assert by_name["pricing-run-propose"]["metadata"]["tier"] == "domain-recipe"
    assert by_name["price-product-adhoc"]["metadata"]["tier"] == "domain-recipe"


def test_risk_domain_has_2_recipes(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/risk/")
    assert _names(skills) == {"risk-snapshot-read", "risk-run-propose"}


def test_market_data_domain_has_1_card_and_2_recipes(
    skills_backend: FilesystemBackend,
):
    skills = _list_skills(skills_backend, "/domains/market-data/")
    assert _names(skills) == {
        "market-data-conventions",  # card
        "market-data-fetch",        # recipe
        "market-data-drift",        # recipe
    }


def test_rfq_domain_has_1_card_and_3_recipes(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/rfq/")
    assert _names(skills) == {
        "rfq-lifecycle",            # card
        "rfq-draft",                # recipe
        "rfq-quote",                # recipe
        "rfq-submit-for-approval",  # recipe
    }


def test_reporting_domain_has_2_recipes(skills_backend: FilesystemBackend):
    skills = _list_skills(skills_backend, "/domains/reporting/")
    assert _names(skills) == {"report-batch-run", "report-create-propose"}


# -----------------------------------------------------------------------------
# Routing source (orchestrator-only)
# -----------------------------------------------------------------------------


def test_routing_source_has_3_skills(skills_backend: FilesystemBackend):
    """Orchestrator's catalog: 3 routing skills."""
    skills = _list_skills(skills_backend, "/routing/")
    assert _names(skills) == {
        "pricing-and-risk-compound",
        "snowball-book-audit",
        "market-data-then-reprice",
    }
    for s in skills:
        assert s["metadata"]["tier"] == "routing"


# -----------------------------------------------------------------------------
# Cross-persona name reuse: portfolio-pricing-run appears under BOTH personas
# with DIFFERENT bodies. Mirrors the v1 snowball-position-diagnostics pattern.
# -----------------------------------------------------------------------------


def test_portfolio_pricing_run_exists_under_both_personas_with_different_bodies(
    skills_backend: FilesystemBackend,
):
    trader = _list_skills(skills_backend, "/procedures/trader/")
    risk = _list_skills(skills_backend, "/procedures/risk_manager/")
    trader_ppr = next(s for s in trader if s["name"] == "portfolio-pricing-run")
    risk_ppr = next(s for s in risk if s["name"] == "portfolio-pricing-run")
    # Same name, different paths (different SKILL.md files).
    assert trader_ppr["path"] != risk_ppr["path"]
    # Different persona metadata.
    assert trader_ppr["metadata"]["persona"] == "trader"
    assert risk_ppr["metadata"]["persona"] == "risk_manager"


# -----------------------------------------------------------------------------
# Composite per-persona catalog assertions (mirroring run-time behavior:
# each persona sees its procedure source + each domain source + products).
# -----------------------------------------------------------------------------


def _persona_catalog(
    skills_backend: FilesystemBackend, sources: list[str]
) -> set[str]:
    seen: set[str] = set()
    for src in sources:
        for s in _list_skills(skills_backend, src):
            seen.add(s["name"])
    return seen


def test_trader_total_catalog_size(skills_backend: FilesystemBackend):
    """Trader sources: procedures/trader/, 4 domains, products/. Expect ~17."""
    sources = [
        "/procedures/trader/",
        "/domains/position/",
        "/domains/pricing/",
        "/domains/market-data/",
        "/domains/rfq/",
        "/products/",
    ]
    catalog = _persona_catalog(skills_backend, sources)
    # 4 procedures + 2 + 3 + 3 + 4 + 1 = 17
    assert len(catalog) == 17, f"Expected 17 entries, got {len(catalog)}: {catalog}"
    # Sanity: a few must-have anchors
    assert "snowball-position-diagnostics" in catalog
    assert "rfq-intake-and-quote" in catalog
    assert "snowball-cn" in catalog


def test_risk_manager_total_catalog_size(skills_backend: FilesystemBackend):
    """Risk_manager sources: procedures/risk_manager/, 5 domains, products/. Expect ~16."""
    sources = [
        "/procedures/risk_manager/",
        "/domains/position/",
        "/domains/risk/",
        "/domains/market-data/",
        "/domains/pricing/",
        "/domains/reporting/",
        "/products/",
    ]
    catalog = _persona_catalog(skills_backend, sources)
    # 3 + 2 + 2 + 3 + 3 + 2 + 1 = 16
    assert len(catalog) == 16, f"Expected 16 entries, got {len(catalog)}: {catalog}"
    assert "risk-report-workflow" in catalog
    assert "snowball-position-diagnostics" in catalog


def test_high_board_total_catalog_size(skills_backend: FilesystemBackend):
    """High_board sources: procedures/high_board/, portfolio + reporting domains. Expect 4."""
    sources = [
        "/procedures/high_board/",
        "/domains/portfolio/",
        "/domains/reporting/",
    ]
    catalog = _persona_catalog(skills_backend, sources)
    # 1 procedure + 1 card + 2 recipes = 4
    assert catalog == {
        "report-query-and-display",
        "portfolio-model",
        "report-batch-run",
        "report-create-propose",
    }


def test_orchestrator_total_catalog_size(skills_backend: FilesystemBackend):
    """Orchestrator source: routing/. Expect exactly 3."""
    catalog = _persona_catalog(skills_backend, ["/routing/"])
    assert catalog == {
        "pricing-and-risk-compound",
        "snowball-book-audit",
        "market-data-then-reprice",
    }
```

- [ ] **Step 2: Run the new catalog tests**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_skills_catalog_v2.py -v 2>&1 | tail -30
```

Expected: all assertions PASS.

If `_list_skills` import path differs from the v1 plan's assumption (e.g., the helper is private or renamed), adapt the test to use the public `SkillsMiddleware` API as v1 fallback. Look at how `tests/test_skills_catalog.py` imports it (the v1 tests already work today, so the same import path holds for v2).

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all v1 + v2 tests pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add tests/test_skills_catalog_v2.py
git commit -m "test(agent-skills): tier-B catalog assertions for v2 surface

Asserts:
- Per-persona procedure source contents (trader 4, risk_manager 3, high_board 1).
- Per-domain source contents (cards + recipes per spec §4).
- Routing source contains exactly 3 skills.
- Cross-persona portfolio-pricing-run is two SKILL.md with the same name
  and different bodies (mirrors v1 snowball pattern).
- Total per-persona catalog sizes match spec §2 (trader 17, risk_manager
  16, high_board 4, orchestrator 3)."
```

---

## Task 21: Extended Tier-C read_file smoke tests for new tiers + `/artifacts`

**Goal:** Verify that `read_file` works (without HITL) on (a) a domain card, (b) a domain recipe, (c) a routing skill, and (d) an HTML artifact under `/artifacts/`. These cover the new filesystem mount and the new skill tiers.

**Files:**
- Create: `tests/test_skills_read_smoke_v2.py`

- [ ] **Step 1: Locate a sample HTML artifact to read in the smoke test**

```bash
cd /Users/fuxinyao/open-otc-trading
ls artifacts/*.html | head -1
```

Pick the first available HTML (e.g., `artifacts/report-1.html`). If none exist, synthesize a fixture before running the test (the test does this lazily — see Step 2).

- [ ] **Step 2: Write the smoke tests**

`tests/test_skills_read_smoke_v2.py`:

```python
"""Tier-C read_file smoke tests for the v2 surface.

Verifies that read_file:
1. Works on domain cards (e.g., portfolio-model SKILL.md).
2. Works on domain recipes (e.g., position-snapshot SKILL.md).
3. Works on routing skills (e.g., pricing-and-risk-compound SKILL.md).
4. Works on HTML artifacts under /artifacts/ via the new mount.
5. Does not appear in the HITL interrupt config (no HITL pause on reads).

Mirrors the v1 read-smoke test for the snowball-cn product card but extends
to new tiers and the artifacts mount added in v2.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.backends.filesystem import FilesystemBackend

from app.services.deep_agent.hitl import interrupt_on_config


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = (
    _REPO_ROOT / "backend" / "app" / "services" / "deep_agent" / "skills"
)
_ARTIFACTS_ROOT = _REPO_ROOT / "artifacts"


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(_SKILLS_ROOT), virtual_mode=True)


@pytest.fixture
def artifacts_backend(tmp_path: Path) -> FilesystemBackend:
    """Artifacts backend. If /artifacts/ has no HTML, synthesize one so the
    smoke test doesn't depend on environment state."""
    if not list(_ARTIFACTS_ROOT.glob("*.html")):
        # Use a tmp_path-rooted backend with a synthesized HTML.
        (tmp_path / "report-test.html").write_text(
            "<html><body><h1>Test report</h1><p>Body.</p></body></html>",
            encoding="utf-8",
        )
        return FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    return FilesystemBackend(root_dir=str(_ARTIFACTS_ROOT), virtual_mode=True)


def _read(backend: FilesystemBackend, path: str, limit: int | None = None) -> str:
    """Adapter for FilesystemBackend's read method.

    The exact method name varies across deepagents versions. Try the common
    candidates in order.
    """
    for attr in ("read_file", "read_text", "read"):
        if hasattr(backend, attr):
            method = getattr(backend, attr)
            try:
                # Most read methods accept limit as a kwarg; tolerate either.
                if limit is not None:
                    try:
                        return method(path, limit=limit)
                    except TypeError:
                        text = method(path)
                        # Bound output manually to mirror limit semantics.
                        return text[: max(0, limit) * 80]  # ~80 chars/line
                return method(path)
            except Exception:  # noqa: BLE001
                continue
    raise RuntimeError("No usable read method on FilesystemBackend")


def test_read_domain_card(skills_backend: FilesystemBackend):
    """Read a domain card (portfolio-model) and assert content."""
    text = _read(skills_backend, "/domains/portfolio/portfolio-model/SKILL.md")
    assert "name: portfolio-model" in text
    assert "Container vs View" in text


def test_read_domain_recipe(skills_backend: FilesystemBackend):
    """Read a domain recipe (position-snapshot) and assert structure."""
    text = _read(skills_backend, "/domains/position/position-snapshot/SKILL.md")
    assert "name: position-snapshot" in text
    assert "## When this applies" in text
    assert "## Step sequence" in text


def test_read_routing_skill(skills_backend: FilesystemBackend):
    """Read a routing skill (pricing-and-risk-compound)."""
    text = _read(skills_backend, "/routing/pricing-and-risk-compound/SKILL.md")
    assert "name: pricing-and-risk-compound" in text
    assert "Delegate to trader" in text or "delegate to trader" in text.lower()


def test_read_html_artifact(artifacts_backend: FilesystemBackend):
    """Read an HTML artifact (real or synthesized) via the artifacts mount."""
    # Discover one HTML at root of the backend's virtual filesystem.
    candidates = list(_ARTIFACTS_ROOT.glob("*.html"))
    if candidates:
        path = f"/{candidates[0].name}"
    else:
        path = "/report-test.html"  # Synthesized in fixture
    text = _read(artifacts_backend, path, limit=2000)
    assert "<html" in text.lower()
    assert "</html>" in text.lower() or len(text) >= 200  # truncation tolerant


def test_read_file_is_not_hitl_gated():
    """read_file must never appear in interrupt-on config — skills depend on it."""
    config = interrupt_on_config()
    assert "read_file" not in config, (
        f"read_file must not be HITL-gated, got config: {list(config)}"
    )
```

- [ ] **Step 3: Run the smoke tests**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/test_skills_read_smoke_v2.py -v 2>&1 | tail -20
```

Expected: all 5 tests PASS. If a particular `_read` adapter line fails for the deepagents version installed, the helper falls back to other method names.

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add tests/test_skills_read_smoke_v2.py
git commit -m "test(agent-skills): tier-C read_file smoke for new tiers + /artifacts

Asserts read_file works (no HITL pause) on:
- A domain card (portfolio-model)
- A domain recipe (position-snapshot)
- A routing skill (pricing-and-risk-compound)
- An HTML artifact under /artifacts/ (real or synthesized fixture)

Also re-asserts read_file is not in the HITL interrupt config —
preserves the v1 progressive-disclosure guarantee."
```

---

## Task 22: Final verification — full test suite + commit-history check

**Goal:** Run the entire test suite, confirm a clean pass, and sanity-check the commit log against the spec's §6.4 phasing.

**Files:**
- No file changes.

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/fuxinyao/open-otc-trading
python -m pytest tests/ -v 2>&1 | tail -40
```

Expected: ALL tests pass. Compare the total count to the baseline captured in Task 1 Step 7:
- Baseline (before v2): N tests.
- After v2: N + (~10 new tool tests) + (~16 new catalog tests) + (~5 new read-smoke tests) ≈ N + 31 tests.

If any test fails, do NOT proceed. Fix the failure, then commit the fix.

- [ ] **Step 2: Sanity-check the commit log for v2 phasing**

```bash
cd /Users/fuxinyao/open-otc-trading
git log --oneline main..feat/agent-skills-layer-v2 | tee /tmp/v2-commit-log.txt
wc -l /tmp/v2-commit-log.txt
```

Expected: ~25-35 commits on `feat/agent-skills-layer-v2`. Each commit corresponds to one phase step from spec §6.4.

Verify the phasing order broadly:
1. Tool foundation commits (list_reports, get_report).
2. Filesystem extension commit (`/artifacts` mount).
3. Skills-layer scaffold + persona/orchestrator wiring.
4. Domain cards commit.
5. Domain recipes (6 commits).
6. Workflow procedures (3 commits — one per persona).
7. Routing skills (1 commit).
8. Orchestrator prompt update.
9. Test commits (catalog v2, read smoke).

- [ ] **Step 3: Sanity-check skill counts on disk match the spec**

```bash
cd /Users/fuxinyao/open-otc-trading
find backend/app/services/deep_agent/skills -name "SKILL.md" | wc -l
echo "---"
find backend/app/services/deep_agent/skills -name "SKILL.md" | sort
```

Expected: 29 SKILL.md files (26 new from v2 + 3 retained from v1: snowball-cn, snowball-position-diagnostics × 2).

- [ ] **Step 4: Sanity-check `QUANT_AGENT_TOOLS` has the two new tools**

```bash
cd /Users/fuxinyao/open-otc-trading
grep -E "list_reports_tool|get_report_tool" backend/app/services/langchain_tools.py | head -10
```

Expected: both tools appear in the file body (definitions) and in the `QUANT_AGENT_TOOLS` list.

- [ ] **Step 5: Manual smoke — build an orchestrator**

```bash
cd /Users/fuxinyao/open-otc-trading
python -c "
import os
os.environ.setdefault('OPENAI_API_KEY', 'x')
from langchain_openai import ChatOpenAI
from app.services.deep_agent.orchestrator import build_orchestrator
from app.services.deep_agent.checkpointer import build_checkpointer
from app.services.langchain_tools import QUANT_AGENT_TOOLS

model = ChatOpenAI(model='gpt-4o-mini')
agent = build_orchestrator(model=model, tools=QUANT_AGENT_TOOLS, checkpointer=build_checkpointer())
print('OK, agent built:', type(agent).__name__)
"
```

Expected: prints `OK, agent built: <class>`. If `build_checkpointer()` or `ChatOpenAI` is unavailable, substitute with whatever existing test-fixture stub the v1 plan used.

- [ ] **Step 6: No commit unless a fix was needed**

If any step in Tasks 1-22 surfaced an issue that required a fix, ensure the fix is its own commit. The branch should now be ready for review/merge.

- [ ] **Step 7: Summary report (no commit; output for the human reviewer)**

Output a one-paragraph summary:

```
v2 Agent Skills Layer implementation complete on feat/agent-skills-layer-v2.
- Total SKILL.md files: 29 (26 new + 3 retained).
- New langchain tools: 2 (list_reports, get_report) — read-only, no HITL.
- New filesystem mount: /artifacts (read-only for all personas; HTML
  reading governed by skill body).
- Orchestrator now has its own routing-skill catalog with 3 entries.
- Tier-B catalog tests assert per-persona catalog contents match spec §2.
- Tier-C read-smoke tests confirm read_file works on new tiers and
  /artifacts.
- All v1 tests continue to pass; ~31 new tests added.
- v1 prompt-only snowball compound routing removed; replaced by the
  snowball-book-audit routing skill.

Ready for PR review. Spec: docs/superpowers/specs/2026-05-15-agent-skills-layer-v2-design.md.
```

---

## Self-review checklist (for the engineer executing this plan)

Before opening the PR, run these final checks:

1. **All tasks completed?** Each `- [ ]` in this plan should be `- [x]`.
2. **No surprise commits?** `git log` on the branch should match §6.4 phasing.
3. **Tests green?** `python -m pytest tests/ -v` exits 0.
4. **No accidental file deletions?** `git diff --stat main..HEAD` shows ONLY adds + modifications to expected paths.
5. **Spec is the source of truth.** Anywhere the implementation diverged from the spec (e.g., a `ReportJob` field name turned out different), update the spec or add a note in the PR description.

End of plan.
