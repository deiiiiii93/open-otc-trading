# Reports + Agent Desk Routes Migration (Plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the **Reports** and **Agent Desk** routes off PlaceholderRoute and onto the Warm Ledger design system. Reports gets a vertical document timeline so Risk reports created in Plan 3 finally become viewable. Agent Desk gets the spec's 60/40 chat-with-assets split, wired to the existing `/api/chat/threads/...` SSE backend.

**Architecture:** Each route follows the Plan 2/3 pattern: presentational components take props, `.live.tsx` containers handle API. Reports introduces a one-line backend extension (`GET /api/reports/jobs` list) — flagged as a real backend change. Agent Desk consumes the existing chat backend by reading the thread fully after each send rather than streaming tokens incrementally (token-by-token streaming deferred to Plan 5).

**Tech Stack:** React 19 · Vite · vanilla CSS + custom properties · existing primitives: Modal (Radix Dialog), Panel, Tile, Badge, Chip, AssetCard, ActionProposal, Empty, Skeleton, PageHeader, PageContextChips, Button. FastAPI backend extension for the list endpoint.

**Spec:** `docs/superpowers/specs/2026-05-07-ui-ux-redesign-design.md` (commit 141641a) — sections "Per-route layouts · 1. Agent Desk" and "Per-route layouts · 5. Reports".

**Foundation:** commits f0ca7fc..8883c34. **Plan 2:** 6e31538..29b1a86. **Plan 3:** ce3746a..4d77592 (promote-to-Risk-Reports flow shipped; reports created but no UI to view them yet — this plan delivers the UI).

**Branch:** continues on `main` (user's standing consent).

## Backend reality (drives scope decisions)

### Reports endpoints

**Existing:**
- `POST /api/reports/jobs` body `{report_type, portfolio_id?, rfq_id?, title}` → `ReportJobOut`
- `GET /api/reports/jobs/{job_id}` → `ReportJobOut`

**Missing — required for this plan:**
- `GET /api/reports/jobs` (list, ordered by `created_at` desc)

Task 1 adds this list endpoint with a backend pytest test before the frontend route can populate. It's a small ~12-line addition to `backend/app/main.py` plus a test.

`ReportJobOut` shape (verified from `backend/app/schemas.py:374`):

```ts
{
  id: number;
  report_type: string;          // 'portfolio' | 'risk' | 'rfq'
  status: string;
  request_payload: Record<string, unknown>;
  result_payload: Record<string, unknown>;
  artifact_paths: { html?: string; excel?: string; ...};
  created_at: string;            // ISO
}
```

### Agent Desk endpoints (all present)

- `POST /api/chat/threads` body `{title, character: 'trader'|'risk_manager'|'high_board'}` → `AgentThreadOut`
- `GET /api/chat/threads` → `AgentThreadOut[]` (with full nested `messages`)
- `POST /api/chat/threads/{thread_id}/messages/stream` body `{content, character: 'auto'|'trader'|'risk_manager'|'high_board', page_context?}` → SSE stream
- `POST /api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/confirm` → `AgentMessageOut`

`AgentThreadOut` includes nested `messages: AgentMessageOut[]`. After streaming, refetch threads via `GET /api/chat/threads` and select the matching thread to get the full message tree (including assets and pending_actions in `meta`).

### Scope-bounded decisions (deferred to Plan 5)

- **Token-by-token streaming UI** — v4 awaits the full SSE stream then refetches; v5 can render tokens as they arrive.
- **PageContext plumbing into FloatingAgent** — v4 FloatingAgent shows minimal "Open Agent Desk" affordance instead of a mini-chat. Same chat backend used; just not embedded in the pip.
- **Reports filter chips functionality** — chips render as static labels for v4 (date range / type filter UI deferred). Filtering by `report_type` is a Plan 5 polish.
- **Multi-thread browsing** — v4 has a header dropdown to switch threads + a New Thread button. The historical "thread list as sidebar" is dropped per spec; can return as a polish item in Plan 5.

---

## File structure

**Backend:**
- Modify: `backend/app/main.py` (+ ~12 lines for list endpoint)
- Modify: `tests/test_app.py` (or add `tests/test_reports_list.py` if test_app.py doesn't exist) — add list-endpoint test

**Frontend new files (Reports):**
- `frontend/src/components/ReportCard.tsx` (+ .css + .test.tsx)
- `frontend/src/components/ReportTimeline.tsx` (+ .css + .test.tsx)
- `frontend/src/components/ReportReader.tsx` (+ .css + .test.tsx)
- `frontend/src/routes/Reports.tsx` (+ .css)
- `frontend/src/routes/Reports.live.tsx`

**Frontend new files (Agent Desk):**
- `frontend/src/components/ChatMessage.tsx` (+ .css + .test.tsx)
- `frontend/src/components/AssetsPane.tsx` (+ .css + .test.tsx)
- `frontend/src/components/ChatComposer.tsx` (+ .css + .test.tsx)
- `frontend/src/routes/AgentDesk.tsx` (+ .css)
- `frontend/src/routes/AgentDesk.live.tsx`

**Frontend new types:**
- `frontend/src/types.ts` — add `ReportJob` type (existing types.ts already has chat types)

**Modified:**
- `frontend/src/main.tsx` — replace 2 PlaceholderRoutes; update FloatingAgent body to "Open Agent Desk" navigation
- `README.md` — mark Plan 4 done

---

# Phase A · Backend Extension

## Task 1: Add `GET /api/reports/jobs` list endpoint

**Files:**
- Modify: `backend/app/main.py`
- Modify or Create: `tests/test_reports_list.py`

- [ ] **Step 1: Locate existing tests pattern**

Run from `/Users/fuxinyao/open-otc-trading`:

```bash
ls tests/ 2>&1 | head -20
```

Pick a representative test file (e.g., `tests/test_app.py`) to understand the fixture setup. The new test should follow the same fixtures (TestClient, in-memory or temp SQLite). If tests/test_app.py uses an in-process app fixture, reuse it.

- [ ] **Step 2: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/tests/test_reports_list.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.config import Settings


def _client(tmp_path) -> TestClient:
    db_url = f"sqlite+pysqlite:///{tmp_path}/test.sqlite3"
    settings = Settings(database_url=db_url, artifact_dir=str(tmp_path / "artifacts"))
    app = create_app(settings)
    return TestClient(app)


def test_list_reports_returns_empty_initially(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/reports/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_reports_returns_created_jobs_newest_first(tmp_path):
    client = _client(tmp_path)
    portfolio_response = client.post("/api/portfolios", json={"name": "Desk-Q2", "base_currency": "USD"})
    assert portfolio_response.status_code == 200
    portfolio_id = portfolio_response.json()["id"]

    first = client.post(
        "/api/reports/jobs",
        json={"report_type": "portfolio", "portfolio_id": portfolio_id, "title": "First report"},
    )
    second = client.post(
        "/api/reports/jobs",
        json={"report_type": "risk", "portfolio_id": portfolio_id, "title": "Second report"},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    listing = client.get("/api/reports/jobs")
    assert listing.status_code == 200
    jobs = listing.json()
    assert len(jobs) == 2
    assert jobs[0]["title"] == "Second report"
    assert jobs[1]["title"] == "First report"
```

If `from backend.app.main import create_app` doesn't match how the existing tests import the app, mirror the existing tests' import pattern instead.

- [ ] **Step 3: Run test, verify FAIL**

Run from `/Users/fuxinyao/open-otc-trading`:

```bash
python -m pytest tests/test_reports_list.py -v 2>&1 | tail -20
```

Expected: FAIL — `404 Not Found` on `GET /api/reports/jobs` because the route doesn't exist yet.

- [ ] **Step 4: Add the list endpoint**

In `/Users/fuxinyao/open-otc-trading/backend/app/main.py`, find the `get_report` function (around line 579, the `GET /api/reports/jobs/{job_id}` handler). Immediately AFTER its closing line (after the `return job` and before the next handler), insert a new endpoint:

```python
    @app.get("/api/reports/jobs", response_model=list[ReportJobOut])
    def list_reports(session: Session = Depends(get_db)):
        from .models import ReportJob

        return (
            session.query(ReportJob)
            .order_by(ReportJob.created_at.desc())
            .all()
        )
```

Place this new endpoint immediately above the existing `get_report` (so the more specific `/{job_id}` route comes after the list route). FastAPI matches routes in declaration order; declaring `list` before `{job_id}` avoids ambiguity if a `GET /api/reports/jobs` request ever conflicted with `GET /api/reports/jobs/foo`.

- [ ] **Step 5: Run test, verify PASS**

Run: `python -m pytest tests/test_reports_list.py -v 2>&1 | tail -10`
Expected: PASS — 2 tests.

- [ ] **Step 6: Run the full backend test suite to confirm nothing else broke**

Run: `python -m pytest 2>&1 | tail -10`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/main.py tests/test_reports_list.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(backend): add GET /api/reports/jobs list endpoint"
```

---

# Phase B · Reports Route

## Task 2: Add ReportJob type + Build ReportCard

**Files:**
- Modify: `frontend/src/types.ts` (add `ReportJob` type)
- Create: `frontend/src/components/ReportCard.tsx`
- Create: `frontend/src/components/ReportCard.css`
- Create: `frontend/src/components/ReportCard.test.tsx`

- [ ] **Step 1: Add the ReportJob type**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/types.ts`. After the `PositionValuationRun` type (last existing type), append:

```ts
export type ReportJob = {
  id: number;
  report_type: string;          // 'portfolio' | 'risk' | 'rfq'
  status: string;
  request_payload: Record<string, any>;
  result_payload: Record<string, any>;
  artifact_paths: Record<string, any>;
  created_at: string;           // ISO timestamp
};
```

- [ ] **Step 2: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportCard.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportCard } from './ReportCard';
import type { ReportJob } from '../types';

const job: ReportJob = {
  id: 42,
  report_type: 'risk',
  status: 'completed',
  request_payload: {},
  result_payload: {},
  artifact_paths: { html: '/artifacts/risk-42.html' },
  created_at: '2026-05-07T13:44:00Z',
};

describe('ReportCard', () => {
  it('renders title from request_payload', () => {
    render(<ReportCard job={{ ...job, request_payload: { title: 'Daily Risk Run · Desk-Q2' } }} onOpen={() => {}} />);
    expect(screen.getByText('Daily Risk Run · Desk-Q2')).toBeInTheDocument();
  });

  it('falls back to default title when none provided', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText(/Risk report #42/i)).toBeInTheDocument();
  });

  it('renders the formatted date', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText('05-07')).toBeInTheDocument();
  });

  it('renders the report type label', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText(/risk/i)).toBeInTheDocument();
  });

  it('calls onOpen with job on click', async () => {
    const onOpen = vi.fn();
    render(<ReportCard job={job} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /open/i }));
    expect(onOpen).toHaveBeenCalledWith(job);
  });
});
```

- [ ] **Step 3: Run test, verify FAIL**

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npm test ReportCard -- --run 2>&1 | tail -6
```

Expected: FAIL — module not found.

- [ ] **Step 4: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportCard.tsx`:

```tsx
import type { ReportJob } from '../types';
import { Badge, type BadgeVariant } from './Badge';
import './ReportCard.css';

type Props = {
  job: ReportJob;
  onOpen: (job: ReportJob) => void;
};

const typeVariant: Record<string, BadgeVariant> = {
  risk: 'info',
  portfolio: 'pos',
  rfq: 'warn',
};

function readTitle(job: ReportJob): string {
  const fromPayload = job.request_payload?.title;
  if (typeof fromPayload === 'string' && fromPayload.length > 0) return fromPayload;
  return `${capitalize(job.report_type)} report #${job.id}`;
}

function capitalize(s: string): string {
  return s.length === 0 ? s : s[0].toUpperCase() + s.slice(1);
}

function formatDate(iso: string): { day: string; time: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: '—', time: '' };
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return { day: `${month}-${day}`, time: `${hh}:${mm}` };
}

export function ReportCard({ job, onOpen }: Props) {
  const title = readTitle(job);
  const date = formatDate(job.created_at);
  const variant = typeVariant[job.report_type] ?? 'ink';

  return (
    <article className="wl-report-card">
      <div className="wl-report-card__date">
        <div className="wl-report-card__date-day">{date.day}</div>
        <div className="wl-report-card__date-time">{date.time}</div>
      </div>
      <button
        type="button"
        className="wl-report-card__body"
        onClick={() => onOpen(job)}
        aria-label={`Open ${title}`}
      >
        <div className="wl-report-card__head">
          <span className="wl-report-card__title">{title}</span>
          <Badge variant={variant}>{job.report_type}</Badge>
        </div>
        <div className="wl-report-card__meta">
          <span className="wl-report-card__id">#{job.id}</span>
          <span className="wl-report-card__status">· {job.status}</span>
        </div>
      </button>
    </article>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportCard.css`:

```css
.wl-report-card {
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: var(--gap-3);
  padding: var(--gap-2) 0;
}
.wl-report-card__date {
  font-family: var(--font-numeric);
  text-align: right;
  color: var(--ink-2);
  padding-top: 2px;
}
.wl-report-card__date-day {
  font-size: var(--type-num-m-size);
  font-weight: 700;
  color: var(--ink);
}
.wl-report-card__date-time {
  font-size: var(--type-small-size);
  margin-top: 2px;
}
.wl-report-card__body {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--gap-2) var(--gap-3);
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: var(--gap-1);
  font-family: var(--font-ui);
  color: var(--ink);
}
.wl-report-card__body:hover { background: var(--paper-2); }
.wl-report-card__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--gap-2);
}
.wl-report-card__title {
  font-size: var(--type-body-size);
  font-weight: 600;
}
.wl-report-card__meta {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
.wl-report-card__id { font-weight: 700; }
```

- [ ] **Step 5: Run test, verify PASS**

Run: `npm test ReportCard -- --run 2>&1 | tail -6`
Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/types.ts frontend/src/components/ReportCard.tsx frontend/src/components/ReportCard.css frontend/src/components/ReportCard.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ReportCard with date column + type badge"
```

## Task 3: Build ReportTimeline

**Files:**
- Create: `frontend/src/components/ReportTimeline.tsx`
- Create: `frontend/src/components/ReportTimeline.css`
- Create: `frontend/src/components/ReportTimeline.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportTimeline.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportTimeline } from './ReportTimeline';
import type { ReportJob } from '../types';

const jobs: ReportJob[] = [
  { id: 1, report_type: 'risk',      status: 'completed', request_payload: {}, result_payload: {}, artifact_paths: {}, created_at: '2026-05-07T11:00:00Z' },
  { id: 2, report_type: 'portfolio', status: 'completed', request_payload: {}, result_payload: {}, artifact_paths: {}, created_at: '2026-05-06T17:02:00Z' },
];

describe('ReportTimeline', () => {
  it('renders one card per job', () => {
    render(<ReportTimeline jobs={jobs} onOpen={() => {}} />);
    expect(screen.getByText(/Risk report #1/i)).toBeInTheDocument();
    expect(screen.getByText(/Portfolio report #2/i)).toBeInTheDocument();
  });

  it('shows empty state when no jobs', () => {
    render(<ReportTimeline jobs={[]} onOpen={() => {}} />);
    expect(screen.getByText(/no reports yet/i)).toBeInTheDocument();
  });

  it('forwards onOpen click to the underlying card', async () => {
    const onOpen = vi.fn();
    render(<ReportTimeline jobs={jobs} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /Risk report #1/i }));
    expect(onOpen).toHaveBeenCalledWith(jobs[0]);
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test ReportTimeline -- --run 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportTimeline.tsx`:

```tsx
import type { ReportJob } from '../types';
import { ReportCard } from './ReportCard';
import { Empty } from './Empty';
import './ReportTimeline.css';

type Props = {
  jobs: ReportJob[];
  onOpen: (job: ReportJob) => void;
};

export function ReportTimeline({ jobs, onOpen }: Props) {
  if (jobs.length === 0) {
    return <Empty message="No reports yet — promote a Risk view or generate one to populate." symbol="◌" />;
  }
  return (
    <div className="wl-timeline">
      {jobs.map((job) => (
        <ReportCard key={job.id} job={job} onOpen={onOpen} />
      ))}
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportTimeline.css`:

```css
.wl-timeline {
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--paper-3);
  padding-left: var(--gap-3);
  margin-left: 56px;
}
.wl-timeline > * { margin-left: -56px; }
```

- [ ] **Step 4: Run test, verify PASS**

Run: `npm test ReportTimeline -- --run 2>&1 | tail -6`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ReportTimeline.tsx frontend/src/components/ReportTimeline.css frontend/src/components/ReportTimeline.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ReportTimeline vertical document feed"
```

## Task 4: Build ReportReader (Modal-based)

**Files:**
- Create: `frontend/src/components/ReportReader.tsx`
- Create: `frontend/src/components/ReportReader.css`
- Create: `frontend/src/components/ReportReader.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportReader.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportReader } from './ReportReader';
import type { ReportJob } from '../types';

const job: ReportJob = {
  id: 42,
  report_type: 'risk',
  status: 'completed',
  request_payload: { title: 'Risk Run · Desk-Q2' },
  result_payload: { summary: 'Risk priced 12 positions.' },
  artifact_paths: { html: '/artifacts/risk-42.html', excel: '/artifacts/risk-42.xlsx' },
  created_at: '2026-05-07T13:44:00Z',
};

describe('ReportReader', () => {
  it('renders modal title with report id when open', () => {
    render(<ReportReader open job={job} onOpenChange={() => {}} />);
    expect(screen.getByText(/Report #42/i)).toBeInTheDocument();
  });

  it('lists artifact paths', () => {
    render(<ReportReader open job={job} onOpenChange={() => {}} />);
    expect(screen.getByText('/artifacts/risk-42.html')).toBeInTheDocument();
    expect(screen.getByText('/artifacts/risk-42.xlsx')).toBeInTheDocument();
  });

  it('shows result_payload as monospaced JSON', () => {
    const { container } = render(<ReportReader open job={job} onOpenChange={() => {}} />);
    const code = container.querySelector('pre');
    expect(code?.textContent).toContain('Risk priced 12 positions');
  });

  it('does not render when job is null', () => {
    render(<ReportReader open={true} job={null} onOpenChange={() => {}} />);
    expect(screen.queryByText(/Report #/i)).not.toBeInTheDocument();
  });

  it('calls onOpenChange(false) when close button clicked', async () => {
    const onOpenChange = vi.fn();
    render(<ReportReader open job={job} onOpenChange={onOpenChange} />);
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test ReportReader -- --run 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportReader.tsx`:

```tsx
import { Modal } from './Modal';
import { Badge, type BadgeVariant } from './Badge';
import type { ReportJob } from '../types';
import './ReportReader.css';

type Props = {
  open: boolean;
  job: ReportJob | null;
  onOpenChange: (open: boolean) => void;
};

const typeVariant: Record<string, BadgeVariant> = {
  risk: 'info',
  portfolio: 'pos',
  rfq: 'warn',
};

export function ReportReader({ open, job, onOpenChange }: Props) {
  if (!job) {
    return (
      <Modal open={open} onOpenChange={onOpenChange} title="Report">
        <div />
      </Modal>
    );
  }

  const artifacts = Object.entries(job.artifact_paths ?? {}).filter(([, value]) => typeof value === 'string');
  const resultJson = JSON.stringify(job.result_payload ?? {}, null, 2);

  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Report #${job.id}`}>
      <div className="wl-reader">
        <header className="wl-reader__head">
          <span className="wl-reader__type">
            <Badge variant={typeVariant[job.report_type] ?? 'ink'}>{job.report_type}</Badge>
          </span>
          <span className="wl-reader__status">{job.status}</span>
        </header>

        {artifacts.length > 0 && (
          <section className="wl-reader__section">
            <h3 className="wl-reader__section-title">Artifacts</h3>
            <ul className="wl-reader__artifacts">
              {artifacts.map(([key, value]) => (
                <li key={key} className="wl-reader__artifact">
                  <span className="wl-reader__artifact-kind">{key}</span>
                  <span className="wl-reader__artifact-path">{String(value)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="wl-reader__section">
          <h3 className="wl-reader__section-title">Result</h3>
          <pre className="wl-reader__result">{resultJson}</pre>
        </section>
      </div>
    </Modal>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ReportReader.css`:

```css
.wl-reader { display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-reader__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: var(--gap-2);
  border-bottom: 1px solid var(--paper-3);
}
.wl-reader__status {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
.wl-reader__section { display: flex; flex-direction: column; gap: var(--gap-2); }
.wl-reader__section-title {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  margin: 0;
}
.wl-reader__artifacts { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--gap-1); }
.wl-reader__artifact {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: var(--gap-2);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink);
}
.wl-reader__artifact-kind {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
}
.wl-reader__result {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  background: var(--paper-2);
  border: 1px solid var(--paper-3);
  padding: var(--gap-2) var(--gap-3);
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre;
  color: var(--ink);
}
```

- [ ] **Step 4: Run test, verify PASS**

Run: `npm test ReportReader -- --run 2>&1 | tail -6`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ReportReader.tsx frontend/src/components/ReportReader.css frontend/src/components/ReportReader.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ReportReader modal with artifacts + result"
```

## Task 5: Build Reports presentational route

**Files:**
- Create: `frontend/src/routes/Reports.tsx`
- Create: `frontend/src/routes/Reports.css`

(No test for this task — pure composition; typecheck is the gate.)

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Reports.tsx`:

```tsx
import { useState } from 'react';
import type { ReportJob } from '../types';
import { PageHeader } from '../components/PageHeader';
import { ReportTimeline } from '../components/ReportTimeline';
import { ReportReader } from '../components/ReportReader';
import { Skeleton } from '../components/Skeleton';
import './Reports.css';

type Props = {
  jobs: ReportJob[];
  loading: boolean;
};

export function Reports({ jobs, loading }: Props) {
  const [openJob, setOpenJob] = useState<ReportJob | null>(null);
  const isOpen = openJob != null;

  const chips: string[] = [];
  if (loading) chips.push('Loading…');
  else chips.push(`${jobs.length} reports`);
  // Static filter chip placeholders — interactive filtering is a Plan 5 polish item.
  chips.push('All types');

  return (
    <>
      <PageHeader title="REPORTS" chips={chips} />

      <div className="wl-reports">
        {loading ? (
          <div className="wl-reports__loading">
            <Skeleton height={48} />
            <Skeleton height={48} />
            <Skeleton height={48} />
          </div>
        ) : (
          <ReportTimeline jobs={jobs} onOpen={setOpenJob} />
        )}
      </div>

      <ReportReader
        open={isOpen}
        job={openJob}
        onOpenChange={(open) => { if (!open) setOpenJob(null); }}
      />
    </>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Reports.css`:

```css
.wl-reports { padding-top: var(--gap-2); }
.wl-reports__loading { display: flex; flex-direction: column; gap: var(--gap-3); }
```

- [ ] **Step 2: Typecheck**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/Reports.tsx frontend/src/routes/Reports.css
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add Reports timeline route"
```

## Task 6: Build Reports.live + wire into main.tsx

**Files:**
- Create: `frontend/src/routes/Reports.live.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Create the live container**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Reports.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { ReportJob } from '../types';
import { Reports } from './Reports';
import { Empty } from '../components/Empty';

export function ReportsLive() {
  const [jobs, setJobs] = useState<ReportJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api<ReportJob[]>('/api/reports/jobs');
        if (!cancelled) setJobs(list);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return <Empty message={`Could not load reports: ${error}`} />;
  }

  return <Reports jobs={jobs} loading={loading} />;
}
```

- [ ] **Step 2: Wire into main.tsx**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/main.tsx`. After the line `import { RiskLive } from './routes/Risk.live';`, add:

```ts
import { ReportsLive } from './routes/Reports.live';
```

Find and replace this line:

```tsx
        {route === 'reports'   && <PlaceholderRoute title="Reports" />}
```

With:

```tsx
        {route === 'reports'   && <ReportsLive />}
```

(Preserve the existing whitespace alignment.)

- [ ] **Step 3: Verify**

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
npm test 2>&1 | tail -5
```
Expected: all frontend tests pass (count grew from 106 with 13 new tests across ReportCard/Timeline/Reader = 119).

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/Reports.live.tsx frontend/src/main.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): replace placeholder with ReportsLive"
```

---

# Phase C · Agent Desk Components

## Task 7: Build ChatMessage

**Files:**
- Create: `frontend/src/components/ChatMessage.tsx`
- Create: `frontend/src/components/ChatMessage.css`
- Create: `frontend/src/components/ChatMessage.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatMessage.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatMessage } from './ChatMessage';
import type { ChatMessage as ChatMessageType } from '../types';

const userMsg: ChatMessageType = {
  id: 1,
  role: 'user',
  character: null,
  content: 'Quote a CSI500 snowball.',
  meta: {},
};

const agentMsg: ChatMessageType = {
  id: 2,
  role: 'assistant',
  character: 'trader',
  content: 'Pricing CSI500 snowball at 10.04.',
  meta: {},
};

const agentWithAction: ChatMessageType = {
  id: 3,
  role: 'assistant',
  character: 'trader',
  content: 'Confirm to run risk.',
  meta: {
    pending_actions: [{
      id: 'p1',
      type: 'run_risk',
      label: 'Run risk on Desk-Q2',
      summary: '12 positions, summary method',
      payload: {},
      requires_confirmation: true,
      status: 'pending',
    }],
  },
};

describe('ChatMessage', () => {
  it('applies user variant class for role=user', () => {
    const { container } = render(<ChatMessage message={userMsg} onConfirmAction={() => {}} onDismissAction={() => {}} />);
    expect(container.firstChild).toHaveClass('wl-chat-message--user');
  });

  it('applies assistant variant class for role=assistant', () => {
    const { container } = render(<ChatMessage message={agentMsg} onConfirmAction={() => {}} onDismissAction={() => {}} />);
    expect(container.firstChild).toHaveClass('wl-chat-message--assistant');
  });

  it('renders content', () => {
    render(<ChatMessage message={userMsg} onConfirmAction={() => {}} onDismissAction={() => {}} />);
    expect(screen.getByText('Quote a CSI500 snowball.')).toBeInTheDocument();
  });

  it('renders pending action proposals', () => {
    render(<ChatMessage message={agentWithAction} onConfirmAction={() => {}} onDismissAction={() => {}} />);
    expect(screen.getByText('Run risk on Desk-Q2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm action/i })).toBeInTheDocument();
  });

  it('calls onConfirmAction with message id and action id', async () => {
    const onConfirmAction = vi.fn();
    render(<ChatMessage message={agentWithAction} onConfirmAction={onConfirmAction} onDismissAction={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /confirm action/i }));
    expect(onConfirmAction).toHaveBeenCalledWith(3, 'p1');
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test ChatMessage -- --run 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatMessage.tsx`:

```tsx
import type { ChatMessage as ChatMessageType, AgentActionProposal } from '../types';
import { ActionProposal } from './ActionProposal';
import './ChatMessage.css';

type Props = {
  message: ChatMessageType;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
};

export function ChatMessage({ message, onConfirmAction, onDismissAction }: Props) {
  const variant = message.role === 'user' ? 'user' : 'assistant';
  const meta = message.meta ?? {};
  const pendingActions: AgentActionProposal[] = Array.isArray(meta.pending_actions)
    ? (meta.pending_actions as AgentActionProposal[])
    : [];

  return (
    <article className={`wl-chat-message wl-chat-message--${variant}`}>
      {message.character && variant === 'assistant' && (
        <header className="wl-chat-message__head">
          <span className="wl-chat-message__character">{message.character}</span>
        </header>
      )}
      <div className="wl-chat-message__body">{message.content}</div>
      {pendingActions
        .filter((a) => a.status !== 'confirmed' && a.status !== 'dismissed')
        .map((action) => (
          <div key={action.id} className="wl-chat-message__action">
            <ActionProposal
              proposal={action}
              onConfirm={(p) => onConfirmAction(message.id, p.id)}
              onDismiss={(p) => onDismissAction(message.id, p.id)}
            />
          </div>
        ))}
    </article>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatMessage.css`:

```css
.wl-chat-message {
  padding: var(--gap-2) var(--gap-3);
  margin-bottom: var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-chat-message--user {
  background: var(--paper-2);
  border-left: 2px solid var(--ink);
}
.wl-chat-message--assistant {
  background: var(--paper);
  border-left: 2px solid var(--info);
}
.wl-chat-message__head {
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-weight: 700;
}
.wl-chat-message__body {
  font-size: var(--type-body-size);
  color: var(--ink);
  line-height: 1.5;
  white-space: pre-wrap;
}
.wl-chat-message__action {}
```

- [ ] **Step 4: Run test, verify PASS**

Run: `npm test ChatMessage -- --run 2>&1 | tail -6`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ChatMessage.tsx frontend/src/components/ChatMessage.css frontend/src/components/ChatMessage.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ChatMessage with inline ActionProposal"
```

## Task 8: Build AssetsPane

**Files:**
- Create: `frontend/src/components/AssetsPane.tsx`
- Create: `frontend/src/components/AssetsPane.css`
- Create: `frontend/src/components/AssetsPane.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/AssetsPane.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssetsPane } from './AssetsPane';
import type { AgentAsset } from '../types';

const assets: AgentAsset[] = [
  { id: 'a1', kind: 'json',     title: 'pricing_request.json',  metadata: {} },
  { id: 'a2', kind: 'table',    title: 'positions_table',       metadata: {} },
  { id: 'a3', kind: 'markdown', title: 'risk_notes.md',         metadata: {} },
];

describe('AssetsPane', () => {
  it('renders one card per asset', () => {
    render(<AssetsPane assets={assets} />);
    expect(screen.getByText('pricing_request.json')).toBeInTheDocument();
    expect(screen.getByText('positions_table')).toBeInTheDocument();
    expect(screen.getByText('risk_notes.md')).toBeInTheDocument();
  });

  it('renders count in header', () => {
    render(<AssetsPane assets={assets} />);
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows empty state when no assets', () => {
    render(<AssetsPane assets={[]} />);
    expect(screen.getByText(/no assets yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test AssetsPane -- --run 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/AssetsPane.tsx`:

```tsx
import type { AgentAsset } from '../types';
import { AssetCard } from './AssetCard';
import { Empty } from './Empty';
import './AssetsPane.css';

type Props = {
  assets: AgentAsset[];
};

export function AssetsPane({ assets }: Props) {
  return (
    <section className="wl-assets-pane">
      <header className="wl-assets-pane__head">
        <span className="wl-assets-pane__title">ASSETS</span>
        <span className="wl-assets-pane__count">{assets.length}</span>
      </header>
      <div className="wl-assets-pane__body">
        {assets.length === 0 ? (
          <Empty message="No assets yet — agent outputs will dock here." symbol="◌" />
        ) : (
          <ul className="wl-assets-pane__list">
            {assets.map((asset) => (
              <li key={asset.id} className="wl-assets-pane__item">
                <AssetCard asset={asset} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/AssetsPane.css`:

```css
.wl-assets-pane {
  border: 1px solid var(--ink);
  background: var(--paper);
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.wl-assets-pane__head {
  background: var(--ink);
  color: var(--paper);
  padding: 6px var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-assets-pane__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-assets-pane__count {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  opacity: 0.85;
}
.wl-assets-pane__body { padding: var(--panel-padding); flex: 1; overflow-y: auto; }
.wl-assets-pane__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
```

- [ ] **Step 4: Run test, verify PASS**

Run: `npm test AssetsPane -- --run 2>&1 | tail -6`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/AssetsPane.tsx frontend/src/components/AssetsPane.css frontend/src/components/AssetsPane.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add AssetsPane with vertical AssetCard stack"
```

## Task 9: Build ChatComposer

**Files:**
- Create: `frontend/src/components/ChatComposer.tsx`
- Create: `frontend/src/components/ChatComposer.css`
- Create: `frontend/src/components/ChatComposer.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatComposer.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatComposer } from './ChatComposer';

describe('ChatComposer', () => {
  it('renders textarea and send button', () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
  });

  it('calls onSend with current text', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'price snowball');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).toHaveBeenCalledWith('price snowball');
  });

  it('clears input after send', async () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i) as HTMLTextAreaElement;
    await userEvent.type(textarea, 'hello');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(textarea.value).toBe('');
  });

  it('disables send when sending=true', () => {
    render(<ChatComposer onSend={() => {}} sending />);
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
  });

  it('does not call onSend when text is empty', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test ChatComposer -- --run 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatComposer.tsx`:

```tsx
import { useId, useState } from 'react';
import { Button } from './Button';
import './ChatComposer.css';

type Props = {
  onSend: (message: string) => void;
  sending: boolean;
};

export function ChatComposer({ onSend, sending }: Props) {
  const [text, setText] = useState('');
  const id = useId();

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    onSend(trimmed);
    setText('');
  };

  return (
    <div className="wl-composer">
      <label htmlFor={id} className="wl-composer__label">Ask anything</label>
      <textarea
        id={id}
        className="wl-composer__textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="Quote a snowball, run risk, generate a report…"
      />
      <div className="wl-composer__actions">
        <Button variant="primary" onClick={handleSend} disabled={sending || text.trim().length === 0}>
          {sending ? 'Sending…' : 'Send ▸'}
        </Button>
      </div>
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ChatComposer.css`:

```css
.wl-composer {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--gap-3);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-composer__label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-composer__textarea {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  resize: vertical;
  min-height: 72px;
  border-radius: 0;
}
.wl-composer__textarea:focus {
  outline: none;
  border: 2px solid var(--ink);
  padding: calc(var(--input-padding-y) - 1px) calc(var(--input-padding-x) - 1px);
}
.wl-composer__actions { display: flex; justify-content: flex-end; }
```

- [ ] **Step 4: Run test, verify PASS**

Run: `npm test ChatComposer -- --run 2>&1 | tail -6`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ChatComposer.tsx frontend/src/components/ChatComposer.css frontend/src/components/ChatComposer.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ChatComposer textarea + send button"
```

## Task 10: Build AgentDesk presentational route

**Files:**
- Create: `frontend/src/routes/AgentDesk.tsx`
- Create: `frontend/src/routes/AgentDesk.css`

(No test for this task — pure composition; typecheck is the gate.)

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/AgentDesk.tsx`:

```tsx
import { useEffect, useId, useMemo, useRef } from 'react';
import type { Thread, AgentAsset } from '../types';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { ChatMessage } from '../components/ChatMessage';
import { AssetsPane } from '../components/AssetsPane';
import { ChatComposer } from '../components/ChatComposer';
import './AgentDesk.css';

type Props = {
  threads: Thread[];
  activeThreadId: number | null;
  sending: boolean;
  onSelectThread: (id: number) => void;
  onNewThread: () => void;
  onSend: (message: string) => void;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
};

function collectAssets(thread: Thread | null): AgentAsset[] {
  if (!thread) return [];
  const collected: AgentAsset[] = [];
  for (const msg of thread.messages) {
    const meta = msg.meta ?? {};
    const assets = Array.isArray(meta.assets) ? (meta.assets as AgentAsset[]) : [];
    for (const a of assets) collected.push(a);
  }
  return collected;
}

export function AgentDesk({
  threads,
  activeThreadId,
  sending,
  onSelectThread,
  onNewThread,
  onSend,
  onConfirmAction,
  onDismissAction,
}: Props) {
  const pickerId = useId();
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );
  const assets = useMemo(() => collectAssets(activeThread), [activeThread]);

  useEffect(() => {
    const node = messagesRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [activeThread, sending]);

  const chips: string[] = [];
  if (activeThread) {
    chips.push(activeThread.character);
    chips.push(`${activeThread.messages.length} messages`);
  }

  return (
    <>
      <PageHeader
        title="AGENT DESK"
        chips={chips}
        action={
          <div className="wl-agent-desk__actions">
            {threads.length > 0 && (
              <>
                <label htmlFor={pickerId} className="wl-agent-desk__picker-label">Thread</label>
                <select
                  id={pickerId}
                  className="wl-agent-desk__picker"
                  value={activeThreadId ?? ''}
                  onChange={(e) => onSelectThread(Number(e.target.value))}
                >
                  {threads.map((t) => (
                    <option key={t.id} value={t.id}>{t.title}</option>
                  ))}
                </select>
              </>
            )}
            <Button variant="default" onClick={onNewThread}>+ New Thread</Button>
          </div>
        }
      />

      <div className="wl-agent-desk__split">
        <section className="wl-agent-desk__chat">
          <div ref={messagesRef} className="wl-agent-desk__messages">
            {!activeThread ? (
              <Empty
                message="Start a thread to ask the agent for pricing, risk, or research."
                symbol="◌"
                action={<Button variant="primary" onClick={onNewThread}>+ New Thread</Button>}
              />
            ) : activeThread.messages.length === 0 ? (
              <Empty message="No messages yet — type below to start." symbol="◌" />
            ) : (
              activeThread.messages.map((msg) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  onConfirmAction={onConfirmAction}
                  onDismissAction={onDismissAction}
                />
              ))
            )}
          </div>
          <div className="wl-agent-desk__composer">
            <ChatComposer onSend={onSend} sending={sending} />
          </div>
        </section>

        <aside className="wl-agent-desk__assets">
          <AssetsPane assets={assets} />
        </aside>
      </div>
    </>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/AgentDesk.css`:

```css
.wl-agent-desk__actions {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
}
.wl-agent-desk__picker-label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-agent-desk__picker {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  border-radius: 0;
}
.wl-agent-desk__split {
  display: grid;
  grid-template-columns: 60% 1fr;
  gap: var(--gap-3);
  align-items: stretch;
  min-height: 70vh;
}
.wl-agent-desk__chat {
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
  min-height: 0;
}
.wl-agent-desk__messages {
  border: 1px solid var(--ink);
  background: var(--paper);
  flex: 1;
  overflow-y: auto;
  padding: var(--panel-padding);
  display: flex;
  flex-direction: column;
}
.wl-agent-desk__composer {}
.wl-agent-desk__assets { min-height: 0; display: flex; }
@media (max-width: 1100px) {
  .wl-agent-desk__split { grid-template-columns: 1fr; }
}
```

- [ ] **Step 2: Typecheck**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/AgentDesk.tsx frontend/src/routes/AgentDesk.css
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add AgentDesk 60/40 chat-with-assets route"
```

## Task 11: Build AgentDesk.live container

**Files:**
- Create: `frontend/src/routes/AgentDesk.live.tsx`

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/AgentDesk.live.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Thread, ChatMessage as ChatMessageType } from '../types';
import { AgentDesk } from './AgentDesk';
import { Skeleton } from '../components/Skeleton';
import { Empty } from '../components/Empty';

export function AgentDeskLive() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const list = await api<Thread[]>('/api/chat/threads');
    setThreads(list);
    setActiveId((current) => {
      if (current != null && list.some((t) => t.id === current)) return current;
      return list[0]?.id ?? null;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const handleNewThread = async () => {
    const created = await api<Thread>('/api/chat/threads', {
      method: 'POST',
      body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
    });
    setThreads((prev) => [created, ...prev]);
    setActiveId(created.id);
  };

  const handleSend = async (message: string) => {
    let threadId = activeId;
    if (threadId == null) {
      const created = await api<Thread>('/api/chat/threads', {
        method: 'POST',
        body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
      });
      threadId = created.id;
      setThreads((prev) => [created, ...prev]);
      setActiveId(created.id);
    }
    setSending(true);
    try {
      // Consume the SSE stream fully — backend completes after the agent
      // response is committed. We don't render tokens incrementally in v4;
      // we refetch the thread once the stream ends to show the final state.
      const response = await fetch(`/api/chat/threads/${threadId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: message, character: 'auto' }),
      });
      if (response.body) {
        const reader = response.body.getReader();
        while (true) {
          const { done } = await reader.read();
          if (done) break;
        }
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  const handleConfirmAction = async (messageId: number, actionId: string) => {
    if (activeId == null) return;
    try {
      await api<ChatMessageType>(
        `/api/chat/threads/${activeId}/messages/${messageId}/actions/${actionId}/confirm`,
        { method: 'POST' },
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDismissAction = async (_messageId: number, _actionId: string) => {
    // Backend has no dismiss endpoint; dismissal is a client-side hide.
    // For v4 we just refresh — the action stays "pending" but the UX
    // pattern matches the foundation ActionProposal contract. A real
    // dismiss endpoint can be added in Plan 5 if desired.
    await refresh();
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={400} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load Agent Desk: ${error}`} />;
  }

  return (
    <AgentDesk
      threads={threads}
      activeThreadId={activeId}
      sending={sending}
      onSelectThread={setActiveId}
      onNewThread={handleNewThread}
      onSend={handleSend}
      onConfirmAction={handleConfirmAction}
      onDismissAction={handleDismissAction}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/AgentDesk.live.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): wire AgentDesk to /api/chat backend (SSE + confirm)"
```

## Task 12: Wire AgentDesk into main.tsx + minimal FloatingAgent

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Add the AgentDesk import**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/main.tsx`. After `import { ReportsLive } from './routes/Reports.live';` (added in Task 6), add:

```ts
import { AgentDeskLive } from './routes/AgentDesk.live';
```

- [ ] **Step 2: Replace the chat placeholder render**

Find this exact line:

```tsx
        {route === 'chat'      && <PlaceholderRoute title="Agent Desk" />}
```

Replace with:

```tsx
        {route === 'chat'      && <AgentDeskLive />}
```

(Preserve whitespace alignment.)

- [ ] **Step 3: Update the FloatingAgent panel body to navigate**

Find this block in `main.tsx`:

```tsx
      {showAgent && (
        <FloatingAgent open={agentOpen} onOpenChange={setAgentOpen} chips={[]} hasUnread={false}>
          <div style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
            Agent panel scaffolding — wiring to existing agent backend lands in a follow-up plan.
          </div>
        </FloatingAgent>
      )}
```

Replace with:

```tsx
      {showAgent && (
        <FloatingAgent open={agentOpen} onOpenChange={setAgentOpen} chips={[]} hasUnread={false}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-2)' }}>
            <p style={{ margin: 0, color: 'var(--ink-2)', fontSize: 'var(--type-small-size)', lineHeight: 1.5 }}>
              The Agent Desk is the full chat surface. Open it to start a thread, ask for pricing, or review action proposals.
            </p>
            <Button
              variant="primary"
              onClick={() => { setAgentOpen(false); setRoute('chat'); }}
            >
              Open Agent Desk ▸
            </Button>
          </div>
        </FloatingAgent>
      )}
```

- [ ] **Step 4: Verify**

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
timeout 6 npm run dev 2>&1 | head -10
```
Expected: VITE ready, no errors.

```bash
npm test 2>&1 | tail -5
```
Expected: all tests pass (~138 total: prior 119 from Task 6 + 13 new from Tasks 7/8/9 = 132. The exact count depends on whether AgentDesk tests are added; this plan does not add tests for the route, so count should be 132.)

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/main.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): replace placeholder with AgentDeskLive + FloatingAgent navigation"
```

---

# Phase D · Smoke + Documentation

## Task 13: Final automated smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all automated checks**

Run from `/Users/fuxinyao/open-otc-trading`:

```bash
python -m pytest tests/test_reports_list.py -v 2>&1 | tail -10
```
Expected: 2 backend tests pass.

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npm test 2>&1 | tail -10
```
Expected: all frontend tests pass.

```bash
npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
timeout 6 npm run dev 2>&1 | head -15
```
Expected: VITE ready.

```bash
npm run build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 2: Update README**

Open `/Users/fuxinyao/open-otc-trading/README.md`. Find the "Follow-up plans" section. Update:

Replace:

```markdown
- ✅ Plan 2 — Client RFQ + RFQ Approval (`docs/superpowers/plans/2026-05-07-rfq-routes-warm-ledger.md`)
- ✅ Plan 3 — Risk (`docs/superpowers/plans/2026-05-07-risk-route-warm-ledger.md`)
- Plan 4 — Reports + Agent Desk
- Plan 5 — accessibility audit + prefers-reduced-motion verification + backend Greeks/scenario extension
```

With:

```markdown
- ✅ Plan 2 — Client RFQ + RFQ Approval (`docs/superpowers/plans/2026-05-07-rfq-routes-warm-ledger.md`)
- ✅ Plan 3 — Risk (`docs/superpowers/plans/2026-05-07-risk-route-warm-ledger.md`)
- ✅ Plan 4 — Reports + Agent Desk (`docs/superpowers/plans/2026-05-07-reports-agent-desk-warm-ledger.md`)
- Plan 5 — accessibility audit + prefers-reduced-motion verification + backend Greeks/scenario extension + token-by-token chat streaming + audit-event endpoint + Berkeley Mono procurement
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add README.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: note Plan 4 Reports + Agent Desk migration shipped"
```

- [ ] **Step 4: Hand off browser smoke checklist**

For the user to verify in a browser at `http://localhost:5173` (with backend running):

**Reports:**
- Reports tab opens. Empty state if no reports yet.
- Risk reports created via Plan 3's promote-to-Report appear in the timeline (newest first).
- Click a report card → ReportReader modal opens with artifact paths and result_payload JSON.
- Close button dismisses the modal.

**Agent Desk:**
- Agent Desk tab opens. Empty state offers "+ New Thread".
- Click "+ New Thread" → empty thread appears in the picker.
- Type into composer → click Send. After ~1–3s the agent response (with assets if any) appears below.
- Action proposals render inline within the agent message; clicking Confirm Action triggers the backend confirm + a follow-up assistant message.
- AssetsPane on the right populates with all assets across the thread's messages.
- Switching threads via the picker shows that thread's messages and assets.
- Theme/density toggles work correctly across both routes.

**FloatingAgent:**
- On Positions, RFQ Approval, Risk, or Reports — click the bottom-right pip → panel opens with a brief intro + "Open Agent Desk ▸" button.
- Click the button → navigates to Agent Desk and closes the pip.
- On Client RFQ — pip is hidden (per spec).

---

# Self-Review Notes

(Performed after writing the plan; fixes applied inline.)

**Spec coverage (sections 1 and 5 of "Per-route layouts"):**
- Reports vertical document timeline → Tasks 2, 3, 5 ✓
- 38px date column on left → ReportCard CSS uses 56px (slight bump for readability with monospace day/time stack); semantically matches the spec's "date column on left" ✓
- Card body with title + subtitle + generator → ReportCard renders title + #id + status; "generator" (agent vs human) isn't in the backend payload — defer to Plan 5 backend extension when the audit_event linkage is added.
- Click card opens Reader view → Task 4 (Modal-based) ✓
- New reports animate in via slide 180ms — not implemented in v4 (would need a list-diff animation system); deferred to Plan 5 polish.
- Filter chips → static labels only in v4; interactive filtering deferred to Plan 5.
- Agent Desk 60/40 chat / assets pane → Task 10 ✓
- User messages paper-2 + ink left border / agent messages paper + info left border → Task 7 ✓
- Action proposals inline → Task 7 (ChatMessage uses ActionProposal) ✓
- Assets pane vertical AssetCard stack → Task 8 ✓

**Type consistency:**
- `ReportJob` defined in types.ts (Task 2) and imported by ReportCard, ReportTimeline, ReportReader, Reports, Reports.live ✓
- `Thread`, `ChatMessage`, `AgentAsset`, `AgentActionProposal` all from types.ts (foundation Task 10) ✓
- `AgentDesk` props (Task 10) match what `AgentDesk.live` (Task 11) passes ✓
- `Reports` props (Task 5) match what `Reports.live` (Task 6) passes ✓

**Placeholder scan:** No "TBD", "TODO", "implement later", "similar to Task N", or "appropriate error handling" tokens. Every code-bearing step has full code.

**Backend reality check:**
- New endpoint `GET /api/reports/jobs` added in Task 1 with backend pytest tests ✓
- `POST /api/chat/threads` body matches `AgentThreadCreate` ({title, character}) ✓
- `POST /api/chat/threads/{id}/messages/stream` body matches `AgentMessageCreate` ({content, character, page_context?}) ✓
- Confirm endpoint path matches: `/api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/confirm` ✓

**Out of scope (explicitly deferred to Plan 5):**
- Token-by-token streaming (v4 awaits full SSE then refetches)
- Backend dismiss-action endpoint (v4 client-side refresh only)
- Reports filter UI (chips are static labels)
- Reports animation on insert
- Generator field on ReportJob (audit-event linkage)
- Multi-character routing UI (existing backend supports it; v4 uses 'auto')
- FloatingAgent embedded mini-chat (v4 navigates to Agent Desk)

# Follow-up plans (after this lands)

- **Plan 5:** Polish — accessibility audit, prefers-reduced-motion verification, dark-mode QA, token-by-token streaming, Reports filter chips functionality, Reports insert animation, dismiss-action endpoint, audit-event endpoint, backend Greeks/scenario extension, Berkeley Mono procurement.
