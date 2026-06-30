# Memory Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a frontend Memory Console (full CRUD + approval + pin + provenance over `/api/memory`) plus the small backend additions it needs.

**Architecture:** Backend extends the existing `MemoryStore`/`FactOut` (provenance passthrough, `set_pinned`, server-enforced archived read-only) — all mutations stay in `MemoryStore`. Frontend adds a Skills-page-style `Memory.live.tsx` (data/mutations) + `Memory.tsx` (presentational) + token-only `Memory.css`, wired into routing.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + TypeScript + Vitest (frontend), pytest (backend tests).

## Global Constraints

- All memory mutations go through `MemoryStore` (never write `MemoryEntry` rows directly from the router). Verbatim invariant from spec.
- Frontend styling is **token-only** (`src/tokens/`): no hardcoded hex/rgb, no raw `px` for spacing/type, no `var(--token, #fallback)`. BEM `wl-memory__*`, co-located `.css`. Verify in light + dark + compact.
- Book `scope_id` is the **stringified portfolio integer id** (`String(portfolio.id)`). Display the name; send/filter by `String(id)`.
- Confidence valid range is `[confidence_floor, 1.0]`; `confidence_floor` is read from `/status.config.confidence_floor` (fallback `0.7`), never hard-coded elsewhere.
- Page limit per fetch is `100`; Load-more is offset-based; search is client-side over loaded rows.
- Action visibility is gated by `status` only (NOT `mutable`, which is `not pinned` = the pin-toggle state).
- Provenance badge rule: `extractor`→info, `api`→ink, other non-empty→ink verbatim, empty→ink "unknown".
- Single-operator app: no route guard, nav visible like other pages.
- `api<T>()` throws `new Error(response.text())`; surface errors via the shared `errorMessage(err)` helper (parses `{"detail": ...}`).

---

## Task 1: Backend — thread provenance (`created_by`, `meta`) through `Fact`

**Files:**
- Modify: `backend/app/services/deep_agent/memory/store.py` (`Fact` dataclass ~line 62; `_to_fact` ~line 85)
- Test: `tests/test_memory_store_crud.py`

**Interfaces:**
- Produces: `Fact.created_by: str`, `Fact.meta: dict` (read-only passthrough from `MemoryEntry.created_by` / `.meta`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_store_crud.py
def test_to_fact_exposes_created_by_and_meta(session):
    from app.models import MemoryEntry
    from app.services.deep_agent.memory.store import _to_fact
    row = MemoryEntry(scope_type="user", scope_id="desk", content="x",
                      normalized_content="x", confidence=0.9, status="active",
                      created_by="extractor", meta={"extractor_model": "deepseek/deepseek-v4-flash",
                                                    "session_id": 318})
    session.add(row); session.flush()
    fact = _to_fact(row)
    assert fact.created_by == "extractor"
    assert fact.meta == {"extractor_model": "deepseek/deepseek-v4-flash", "session_id": 318}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_store_crud.py::test_to_fact_exposes_created_by_and_meta -q`
Expected: FAIL (`Fact.__init__() got an unexpected keyword argument` once `_to_fact` is edited, or `AttributeError` for `fact.created_by`).

- [ ] **Step 3: Add fields to `Fact` and populate in `_to_fact`**

In the `Fact` dataclass, add after `mutable: bool`:
```python
    created_by: str
    meta: dict
```
In `_to_fact(row)`, add to the `Fact(...)` call:
```python
        created_by=row.created_by,
        meta=row.meta if isinstance(row.meta, dict) else {},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_store_crud.py::test_to_fact_exposes_created_by_and_meta -q`
Expected: PASS

- [ ] **Step 5: Run the full store/queue suite to catch `Fact(...)` constructor callers**

Run: `python -m pytest tests/test_memory_store_crud.py tests/test_memory_apply_diff.py tests/test_memory_queue_runjob.py -q`
Expected: PASS (every `_to_fact` path now supplies the new fields; if any test constructs `Fact(...)` directly it must add `created_by=..., meta=...`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/memory/store.py tests/test_memory_store_crud.py
git commit -m "feat(memory): expose created_by + meta on Fact for provenance"
```

---

## Task 2: Backend — `set_pinned` + server-enforced archived read-only

**Files:**
- Modify: `backend/app/services/deep_agent/memory/store.py` (guard in the public `update` and `set_status` methods; add `set_pinned`). Note: `apply_diff`/`_update_row` is the extractor path and only touches non-archived rows (`load_existing` excludes archived), so the guard lives in the public API methods the router uses — not in `_update_row`.
- Test: `tests/test_memory_store_crud.py`

**Interfaces:**
- Produces: `MemoryStore.set_pinned(session, fact_id, pinned: bool) -> Fact` (raises `MemoryNotFound`; raises `MemoryConflictError` if archived). `update`/`set_status` raise `MemoryConflictError("archived is read-only")` when the row is archived.
- Consumes: `Fact` (Task 1), `MemoryNotFound`, `MemoryConflictError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_store_crud.py
import pytest
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import (
    MemoryStore, MemoryConflictError, MemoryNotFound,
)

def _store():
    return MemoryStore(MemoryConfig())

def test_set_pinned_round_trip(session):
    s = _store()
    f = s.create(session, scope_type="user", scope_id="desk", content="pin me",
                 confidence=0.9, category=None, created_by="api")
    # api+user create is auto-pinned; unpin then re-pin
    unpinned = s.set_pinned(session, f.id, False)
    assert unpinned.pinned is False
    repinned = s.set_pinned(session, f.id, True)
    assert repinned.pinned is True

def test_set_pinned_missing_raises(session):
    with pytest.raises(MemoryNotFound):
        _store().set_pinned(session, 999999, True)

def test_archived_is_read_only(session):
    s = _store()
    f = s.create(session, scope_type="user", scope_id="desk", content="archive me",
                 confidence=0.9, category=None, created_by="api")
    s.archive(session, f.id)
    with pytest.raises(MemoryConflictError):
        s.set_pinned(session, f.id, True)
    with pytest.raises(MemoryConflictError):
        s.update(session, f.id, content="new content")
    # archive() on an already-archived row is an idempotent success
    assert s.archive(session, f.id) is True

def test_set_status_on_archived_raises(session):
    s = _store()
    f = s.create(session, scope_type="domain", scope_id="global", content="dom fact",
                 confidence=0.9, category=None, created_by="api")
    s.archive(session, f.id)
    with pytest.raises(MemoryConflictError):
        s.set_status(session, f.id, "approved")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_memory_store_crud.py -k "set_pinned or archived_is_read_only" -q`
Expected: FAIL (`AttributeError: 'MemoryStore' object has no attribute 'set_pinned'`, and `update` does not yet raise on archived).

- [ ] **Step 3: Implement the guards and `set_pinned`**

In `update(...)`, after `row = session.get(MemoryEntry, fact_id)` / the `MemoryNotFound` check and before the savepoint:
```python
        if row.status == "archived":
            raise MemoryConflictError("archived is read-only")
```
In `set_status(...)`, after the `MemoryNotFound` check, add the same guard at the top (before the transition checks):
```python
        if row.status == "archived":
            raise MemoryConflictError("archived is read-only")
```
Add a new method next to `set_status`:
```python
    def set_pinned(self, session, fact_id, pinned: bool) -> Fact:
        row = session.get(MemoryEntry, fact_id)
        if row is None:
            raise MemoryNotFound(str(fact_id))
        if row.status == "archived":
            raise MemoryConflictError("archived is read-only")
        row.pinned = bool(pinned)
        _normalize_source_error(row)
        session.flush()
        return _to_fact(row)
```
(`archive()` already no-ops when `status == "archived"` — no change needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_memory_store_crud.py -k "set_pinned or archived_is_read_only" -q`
Expected: PASS

- [ ] **Step 5: Regression — full memory store/api suite**

Run: `python -m pytest tests/test_memory_store_crud.py tests/test_memory_api.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/memory/store.py tests/test_memory_store_crud.py
git commit -m "feat(memory): set_pinned + server-enforced archived read-only"
```

---

## Task 3: Backend — `FactOut` provenance fields + pin endpoint

**Files:**
- Modify: `backend/app/routers/memory.py` (`FactOut`, `_out`, add `FactPin` + `pin_fact`)
- Test: `tests/test_memory_api.py`

**Interfaces:**
- Produces: `FactOut` with `pinned: bool`, `created_by: str`, `extractor_model: str | None`, `source_session_id: int | None`. New route `PATCH /api/memory/facts/{id}/pin` body `{pinned: bool}` → 200 `FactOut`, 404 not found, 409 archived.
- Consumes: `MemoryStore.set_pinned` (Task 2), `Fact.created_by`/`Fact.meta` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_api.py  (use the existing `client` fixture pattern in this file)
def test_factout_includes_provenance(client):
    r = client.post("/api/memory/facts", json={"scope_type": "user", "content": "usd books", "confidence": 0.9})
    assert r.status_code == 201
    body = r.json()
    assert body["pinned"] is True            # api+user create is auto-pinned
    assert body["created_by"] == "api"
    assert body["extractor_model"] is None   # manual create has empty meta
    assert body["source_session_id"] is None

def test_pin_endpoint_round_trip_and_404(client):
    fid = client.post("/api/memory/facts", json={"scope_type": "user", "content": "pin target", "confidence": 0.9}).json()["id"]
    assert client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": False}).json()["pinned"] is False
    assert client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": True}).json()["pinned"] is True
    assert client.patch("/api/memory/facts/999999/pin", json={"pinned": True}).status_code == 404

def test_pin_archived_returns_409(client):
    fid = client.post("/api/memory/facts", json={"scope_type": "user", "content": "to archive", "confidence": 0.9}).json()["id"]
    assert client.delete(f"/api/memory/facts/{fid}").status_code == 204
    assert client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": True}).status_code == 409

def test_provenance_coerces_malformed_meta(client, session):
    # write a row whose meta has wrong-typed values; _out must coerce to null, not 500
    from app.models import MemoryEntry
    from app import database
    with database.SessionLocal() as s:
        s.add(MemoryEntry(scope_type="user", scope_id="desk", content="legacy",
                          normalized_content="legacy", confidence=0.9, status="active",
                          created_by="extractor", meta={"extractor_model": 123, "session_id": "not-int"}))
        s.commit()
    rows = client.get("/api/memory/facts", params={"scope_type": "user"}).json()["items"]
    legacy = [x for x in rows if x["content"] == "legacy"][0]
    assert legacy["extractor_model"] is None and legacy["source_session_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_memory_api.py -k "provenance or pin_endpoint or pin_archived" -q`
Expected: FAIL (`FactOut` has no `pinned`; no pin route).

- [ ] **Step 3: Extend `FactOut`, `_out`, and add the pin route**

In `FactOut` add fields:
```python
    pinned: bool
    created_by: str
    extractor_model: str | None
    source_session_id: int | None
```
Rewrite `_out(fact)`:
```python
def _out(fact) -> dict:
    meta = fact.meta if isinstance(fact.meta, dict) else {}
    em = meta.get("extractor_model")
    sid = meta.get("session_id")
    return FactOut(
        id=fact.id, scope_type=fact.scope_type, scope_id=fact.scope_id,
        content=fact.content, confidence=fact.confidence, status=fact.status,
        category=fact.category, source_error=fact.source_error,
        pinned=fact.pinned, created_by=fact.created_by,
        extractor_model=em if isinstance(em, str) else None,
        source_session_id=sid if isinstance(sid, int) else None,
        created_at=fact.created_at, updated_at=fact.updated_at,
    ).model_dump()
```
Add inside `build_memory_router()` (after `approve_fact`):
```python
    class FactPin(BaseModel):
        pinned: bool

    @router.patch("/facts/{fact_id}/pin")
    def pin_fact(fact_id: int, body: FactPin):
        with database.SessionLocal() as session:
            try:
                fact = get_memory_store().set_pinned(session, fact_id, body.pinned)
                session.commit()
            except MemoryNotFound as exc:
                raise HTTPException(404, "not found") from exc
            except MemoryConflictError as exc:
                raise HTTPException(409, str(exc)) from exc
            return _out(fact)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_memory_api.py -k "provenance or pin_endpoint or pin_archived" -q`
Expected: PASS

- [ ] **Step 5: Add category-clear + archived-edit API tests (no backend change expected)**

```python
def test_patch_category_clear_and_unchanged(client):
    fid = client.post("/api/memory/facts", json={"scope_type": "user", "content": "cat fact", "confidence": 0.9, "category": "pref"}).json()["id"]
    assert client.patch(f"/api/memory/facts/{fid}", json={"confidence": 0.95}).json()["category"] == "pref"  # omitted -> unchanged
    assert client.patch(f"/api/memory/facts/{fid}", json={"category": ""}).json()["category"] is None         # "" -> cleared

def test_patch_archived_returns_409(client):
    fid = client.post("/api/memory/facts", json={"scope_type": "user", "content": "edit archived", "confidence": 0.9}).json()["id"]
    client.delete(f"/api/memory/facts/{fid}")
    assert client.patch(f"/api/memory/facts/{fid}", json={"content": "nope"}).status_code == 409
```

Run: `python -m pytest tests/test_memory_api.py -k "category_clear or patch_archived" -q`
Expected: PASS (`_clean_category("")` already returns `None`; `update` now raises on archived from Task 2, and `patch_fact` already maps `MemoryConflictError`→409).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/memory.py tests/test_memory_api.py
git commit -m "feat(memory): FactOut provenance fields + pin endpoint + archived 409"
```

---

## Task 4: Frontend — types, API client functions, error helper

**Files:**
- Modify: `frontend/src/types.ts` (add `MemoryFact`, `MemoryStatus`, `'memory'` in `Route`)
- Modify: `frontend/src/api/client.ts` (memory fns + `listPortfoliosWithIds` + `errorMessage`)
- Test: `frontend/src/api/client.memory.test.ts` (new — errorMessage unit)

**Interfaces:**
- Produces: `MemoryFact`, `MemoryStatus` types; `listMemoryFacts`, `createMemoryFact`, `patchMemoryFact`, `approveMemoryFact`, `setMemoryFactPinned`, `deleteMemoryFact`, `getMemoryStatus`, `listPortfoliosWithIds`, `errorMessage`.

- [ ] **Step 1: Write the failing test (error helper)**

```ts
// frontend/src/api/client.memory.test.ts
import { describe, it, expect } from 'vitest';
import { errorMessage } from './client';

describe('errorMessage', () => {
  it('extracts FastAPI detail', () => {
    expect(errorMessage(new Error('{"detail":"duplicate"}'))).toBe('duplicate');
  });
  it('falls back to raw text', () => {
    expect(errorMessage(new Error('plain failure'))).toBe('plain failure');
  });
  it('handles non-Error', () => {
    expect(errorMessage('boom')).toBe('boom');
  });
});

describe('memory client fns (mocked fetch)', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('listMemoryFacts builds the query string', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
    await listMemoryFacts({ scope_type: 'book', scope_id: '7', status: 'proposed', limit: 100, offset: 100 });
    const url = (fetchMock.mock.calls[0][0] as string);
    expect(url).toContain('scope_type=book'); expect(url).toContain('scope_id=7');
    expect(url).toContain('status=proposed'); expect(url).toContain('limit=100'); expect(url).toContain('offset=100');
  });

  it('createMemoryFact POSTs the body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 1 }), { status: 201 }));
    await createMemoryFact({ scope_type: 'book', scope_id: '7', content: 'x', confidence: 0.9 });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toMatchObject({ scope_type: 'book', scope_id: '7', content: 'x' });
  });

  it('api<T> throws Error whose message is the raw response body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"detail":"duplicate"}', { status: 409 }));
    await expect(getMemoryStatus()).rejects.toThrow('{"detail":"duplicate"}');
  });
});
```

Add the imports at the top of the test file:
```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { errorMessage, listMemoryFacts, createMemoryFact, getMemoryStatus } from './client';
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/api/client.memory.test.ts`
Expected: FAIL (`errorMessage` not exported).

- [ ] **Step 3: Add types**

In `frontend/src/types.ts`, add `'memory'` to the `Route` union, and:
```ts
export interface MemoryFact {
  id: number;
  scope_type: 'user' | 'book' | 'domain' | 'correction';
  scope_id: string;
  content: string;
  confidence: number;
  status: 'proposed' | 'approved' | 'active' | 'archived';
  category: string | null;
  source_error: boolean;
  pinned: boolean;
  created_by: string;
  extractor_model: string | null;
  source_session_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryStatus {
  enabled: boolean;
  config: {
    confidence_floor: number;
    max_facts_per_scope: number;
    max_correction_facts: number;
    injection_token_budget: number;
    correction_token_budget: number;
  };
  counts: Record<string, Record<string, number>>;
}
```

- [ ] **Step 4: Add client fns + `errorMessage`**

In `frontend/src/api/client.ts`:
```ts
import type { MemoryFact, MemoryStatus } from '../types';

export function errorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === 'string') return parsed.detail;
  } catch { /* not JSON */ }
  return raw || 'request failed';
}

export const listMemoryFacts = (params: {
  scope_type?: string; scope_id?: string; status?: string; limit?: number; offset?: number;
}) => {
  const q = new URLSearchParams();
  if (params.scope_type) q.set('scope_type', params.scope_type);
  if (params.scope_id) q.set('scope_id', params.scope_id);
  if (params.status) q.set('status', params.status);
  q.set('limit', String(params.limit ?? 100));
  q.set('offset', String(params.offset ?? 0));
  return api<{ items: MemoryFact[]; total: number }>(`/api/memory/facts?${q.toString()}`);
};

export const createMemoryFact = (body: {
  scope_type: string; scope_id?: string; content: string; confidence: number; category?: string | null;
}) => api<MemoryFact>('/api/memory/facts', { method: 'POST', body: JSON.stringify(body) });

export const patchMemoryFact = (id: number, body: {
  content?: string; confidence?: number; category?: string | null;
}) => api<MemoryFact>(`/api/memory/facts/${id}`, { method: 'PATCH', body: JSON.stringify(body) });

export const approveMemoryFact = (id: number) =>
  api<MemoryFact>(`/api/memory/facts/${id}/approve`, { method: 'POST' });

export const setMemoryFactPinned = (id: number, pinned: boolean) =>
  api<MemoryFact>(`/api/memory/facts/${id}/pin`, { method: 'PATCH', body: JSON.stringify({ pinned }) });

export const deleteMemoryFact = (id: number) =>
  api<void>(`/api/memory/facts/${id}`, { method: 'DELETE' });

export const getMemoryStatus = () => api<MemoryStatus>('/api/memory/status');

export const listPortfoliosWithIds = () =>
  api<Array<{ id: number; name: string }>>('/api/portfolios')
    .then((rows) => rows.map((r) => ({ id: r.id, name: r.name })));
```

- [ ] **Step 5: Run to verify it passes + typecheck**

Run: `cd frontend && npx vitest run src/api/client.memory.test.ts && npx tsc --noEmit`
Expected: PASS, no type errors (the new fns/types compile against `MemoryFact`/`MemoryStatus`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/api/client.memory.test.ts
git commit -m "feat(memory): frontend types, API client fns, errorMessage helper"
```

---

## Task 5: Frontend — routing wiring + container skeleton that lists facts

**Files:**
- Create: `frontend/src/routes/Memory.live.tsx` (skeleton), `frontend/src/routes/Memory.tsx` (skeleton), `frontend/src/routes/Memory.css`
- Modify: `frontend/src/lib/routing.ts` (`ROUTE_PATHS.memory = '/memory'`), `frontend/src/main.tsx` (import, nav item, command-palette entry, route renderer)
- Test: `frontend/src/routes/Memory.live.test.tsx` (new)

**Interfaces:**
- Consumes: client fns + types (Task 4).
- Produces: **named export** `export function MemoryLive(...)` (matches the test's `{ MemoryLive }` import and `main.tsx`'s named import); reachable at route `'memory'`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/Memory.live.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryLive } from './Memory.live';
import * as client from '../api/client';

const fact = (over = {}) => ({
  id: 1, scope_type: 'domain', scope_id: 'global', content: 'vol skew steepens',
  confidence: 0.88, status: 'proposed', category: null, source_error: false,
  pinned: false, created_by: 'extractor', extractor_model: 'deepseek/deepseek-v4-flash',
  source_session_id: 318, created_at: '2026-06-30T00:00:00', updated_at: '2026-06-30T00:00:00', ...over,
});
const status = { enabled: true, config: { confidence_floor: 0.7, max_facts_per_scope: 100, max_correction_facts: 20, injection_token_budget: 2000, correction_token_budget: 1000 }, counts: { domain: { proposed: 1 } } };

beforeEach(() => {
  vi.spyOn(client, 'getMemoryStatus').mockResolvedValue(status as any);
  vi.spyOn(client, 'listPortfoliosWithIds').mockResolvedValue([{ id: 7, name: 'Macro' }]);
  vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact()], total: 1 } as any);
});

describe('MemoryLive', () => {
  it('lands on All/Current and shows a proposed domain fact', async () => {
    render(<MemoryLive onPageContextChange={() => {}} />);
    await waitFor(() => expect(screen.getByText('vol skew steepens')).toBeInTheDocument());
    // first fetch uses no status param (Current) and no scope_type (All)
    expect(client.listMemoryFacts).toHaveBeenCalledWith(expect.not.objectContaining({ status: expect.anything(), scope_type: expect.anything() }));
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/routes/Memory.live.test.tsx`
Expected: FAIL (`Memory.live` does not exist).

- [ ] **Step 3: Create the skeleton container + presentational + css**

`frontend/src/routes/Memory.live.tsx`:
```tsx
import { useEffect, useRef, useState, useCallback } from 'react';
import { listMemoryFacts, getMemoryStatus, errorMessage } from '../api/client';
import type { MemoryFact, MemoryStatus } from '../types';
import { Memory } from './Memory';

export function MemoryLive({ onPageContextChange }: { onPageContextChange: (c: unknown) => void }) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqSeq = useRef(0);

  const loadView = useCallback(async () => {
    const token = ++reqSeq.current;
    setLoading(true);
    try {
      const [list, st] = await Promise.all([listMemoryFacts({ limit: 100, offset: 0 }), getMemoryStatus()]);
      if (token !== reqSeq.current) return;
      setFacts(list.items); setTotal(list.total); setStatus(st); setError(null);
    } catch (e) {
      if (token !== reqSeq.current) return;
      setError(errorMessage(e));
    } finally {
      if (token === reqSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void loadView(); }, [loadView]);
  useEffect(() => { onPageContextChange({ page: 'memory' }); }, [onPageContextChange]);

  return <Memory facts={facts} total={total} status={status} loading={loading} error={error} />;
}
```

`frontend/src/routes/Memory.tsx`:
```tsx
import type { MemoryFact, MemoryStatus } from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { Table, type Column } from '../components/Table';
import { Empty } from '../components/Empty';
import './Memory.css';

export interface MemoryProps {
  facts: MemoryFact[];
  total: number;
  status: MemoryStatus | null;
  loading: boolean;
  error: string | null;
}

export function Memory({ facts, loading, error }: MemoryProps) {
  const columns: Column<MemoryFact>[] = [
    { key: 'status', header: 'Status', render: (f) => f.status },
    { key: 'content', header: 'Content', render: (f) => f.content },
  ];
  return (
    <PageScaffold title="Memory">
      {loading ? <Empty message="Loading memory…" variant="loading" />
        : error ? <Empty message={error} variant="error" />
        : facts.length === 0 ? <Empty message="No facts" variant="empty" />
        : <Table columns={columns} rows={facts} getRowKey={(f) => f.id} />}
    </PageScaffold>
  );
}
```
(Verify the exact `PageScaffold`/`Table`/`Empty` prop names against `frontend/src/components/templates/PageScaffold.tsx`, `frontend/src/components/Table.tsx`, `frontend/src/components/Empty.tsx` and adjust; the structure is the contract, the prop spellings come from those files.)

`frontend/src/routes/Memory.css`:
```css
.wl-memory__row-actions { display: flex; gap: var(--gap-2); }
```

- [ ] **Step 4: Wire routing**

In `frontend/src/lib/routing.ts` add to `ROUTE_PATHS`: `memory: '/memory',`.
In `frontend/src/main.tsx`: add `import { MemoryLive } from './routes/Memory.live';`; add `{ route: 'memory' as const, label: 'Memory' }` to `navItems`; add a command-palette entry `{ id: 'jump-memory', group: 'Jump To', label: 'Memory', shortcut: '↵' }`; add the renderer `{route === 'memory' && <MemoryLive onPageContextChange={handlePageContextChange} />}`.

- [ ] **Step 5: Run to verify it passes + typecheck**

Run: `cd frontend && npx vitest run src/routes/Memory.live.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Memory.live.tsx frontend/src/routes/Memory.tsx frontend/src/routes/Memory.css frontend/src/routes/Memory.live.test.tsx frontend/src/lib/routing.ts frontend/src/main.tsx
git commit -m "feat(memory): route wiring + container skeleton listing facts"
```

---

## Task 6: Frontend — presentational console (tabs, toolbar, chips, table, modal, load-more)

**Files:**
- Modify: `frontend/src/routes/Memory.tsx`, `frontend/src/routes/Memory.css`
- Test: `frontend/src/routes/Memory.test.tsx` (new — presentational)

**Interfaces:**
- Consumes: the full `MemoryProps` (extended below); the container (Task 7) supplies the callbacks.
- Produces: the rendered console. `MemoryProps` extends to:
```ts
interface MemoryProps {
  facts: MemoryFact[]; total: number; status: MemoryStatus | null;
  loading: boolean; error: string | null; feedback: string | null;
  activeScope: 'all'|'user'|'book'|'domain'|'correction';
  statusFilter: 'current'|'proposed'|'approved'|'active'|'archived'|'all';
  search: string;
  portfolios: Array<{id:number;name:string}>; portfoliosError: string | null;
  selectedPortfolio: number | null;
  rowBusy: Set<number>; confidenceFloor: number;
  // modal contract (discriminated union; null = closed)
  modal:
    | { kind: 'create'; draft: MemoryDraft }
    | { kind: 'edit'; fact: MemoryFact; draft: MemoryDraft }
    | { kind: 'delete'; fact: MemoryFact }
    | null;
  modalSaving: boolean; modalError: string | null;
  onScope: (s: MemoryProps['activeScope']) => void;
  onStatusFilter: (s: MemoryProps['statusFilter']) => void;
  onSearch: (q: string) => void;
  onSelectPortfolio: (id: number | null) => void;
  onRefresh: () => void; onLoadMore: () => void;
  onApprove: (f: MemoryFact) => void; onPin: (f: MemoryFact) => void;
  onEdit: (f: MemoryFact) => void; onDelete: (f: MemoryFact) => void;
  onNew: () => void;
  onModalChange: (draft: MemoryDraft) => void;   // create/edit field edits
  onModalSave: () => void;                        // submit create or edit
  onModalCancel: () => void;                      // close create/edit/delete modal
  onConfirmDelete: () => void;                    // confirm in the delete modal
}

// Draft shape for the create/edit modal (scope_type/portfolio only used on create)
export interface MemoryDraft {
  scope_type: 'user' | 'book' | 'domain' | 'correction';
  portfolioId: number | null;
  content: string;
  confidence: number;
  category: string;   // '' means "no category" (cleared)
}
```

- [ ] **Step 1: Write the failing presentational tests**

```tsx
// frontend/src/routes/Memory.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Memory } from './Memory';

const base = {
  facts: [], total: 0, status: { enabled: true, config: { confidence_floor: 0.7, max_facts_per_scope: 100, max_correction_facts: 20, injection_token_budget: 2000, correction_token_budget: 1000 }, counts: { domain: { proposed: 2 }, user: { active: 5 } } },
  loading: false, error: null, feedback: null, activeScope: 'all', statusFilter: 'current', search: '',
  portfolios: [], portfoliosError: null, selectedPortfolio: null, rowBusy: new Set<number>(), confidenceFloor: 0.7,
  onScope: vi.fn(), onStatusFilter: vi.fn(), onSearch: vi.fn(), onSelectPortfolio: vi.fn(),
  onRefresh: vi.fn(), onLoadMore: vi.fn(), onApprove: vi.fn(), onPin: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onNew: vi.fn(),
} as any;
const f = (o = {}) => ({ id: 1, scope_type: 'domain', scope_id: 'global', content: 'c', confidence: 0.8, status: 'proposed', category: null, source_error: false, pinned: false, created_by: 'extractor', extractor_model: 'm', source_session_id: 9, created_at: '', updated_at: '', ...o });

describe('Memory presentational', () => {
  it('shows enabled + proposed attention chip from counts', () => {
    render(<Memory {...base} facts={[f()]} />);
    expect(screen.getByText(/proposed 2/i)).toBeInTheDocument();
  });
  it('disabled banner when status.enabled is false', () => {
    render(<Memory {...base} status={{ ...base.status, enabled: false }} facts={[f()]} />);
    expect(screen.getByText(/capture is off/i)).toBeInTheDocument();
  });
  it('Approve shown only for proposed; hidden actions for archived', () => {
    render(<Memory {...base} facts={[f({ id: 1, status: 'proposed' }), f({ id: 2, status: 'archived' })]} />);
    expect(screen.getAllByRole('button', { name: /approve/i }).length).toBe(1);
  });
  it('Load more visible when total > facts.length', () => {
    render(<Memory {...base} facts={[f()]} total={50} />);
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/routes/Memory.test.tsx`
Expected: FAIL (skeleton `Memory` lacks chips/actions/load-more).

- [ ] **Step 3a: Header — chips, config caption, disabled banner**

In `Memory.tsx`, compute and render the header (use the real `Badge`/`PageScaffold` prop names):
- `scopeCount(s) = (c => c ? Object.entries(c).filter(([k]) => k !== 'archived').reduce((a, [, v]) => a + v, 0) : 0)(status?.counts[s])`.
- `proposedCount = status ? Object.values(status.counts).reduce((a, c) => a + (c.proposed || 0), 0) : 0`.
- When `status` non-null: an `enabled` chip (`pos`/`ink`), four scope chips (`user`/`book`/`domain`/`correction`), and a `warn` `proposed N` chip only when `proposedCount > 0`. Config caption: `floor {config.confidence_floor} · budget {injection_token_budget}/corr {correction_token_budget} · cap {max_facts_per_scope}/corr {max_correction_facts}`. When `status` is null, hide chips+caption.
- Disabled banner when `status && !status.enabled`: "Memory capture is off (OPEN_OTC_MEMORY) — existing facts are still editable here."

- [ ] **Step 3b: Tabs + toolbar (filters, portfolio, refresh, new)**

- `Tabs` (User/Book/Domain/Correction/All) → `onScope`.
- Toolbar: status `select` (Current/Proposed/Approved/Active/Archived/All) → `onStatusFilter`; search input → `onSearch`; portfolio `select` only when `activeScope==='book'` (options from `portfolios`, value `selectedPortfolio`, disabled "No portfolios" when empty, `portfoliosError` text when set); `[Refresh]` → `onRefresh`; `[+ New]` → `onNew`.

- [ ] **Step 3c: Table — columns, provenance, action matrix**

- Client search filter: `visible = facts.filter(x => !search.trim() || x.content.toLowerCase().includes(search.trim().toLowerCase()) || (x.category ?? '').toLowerCase().includes(search.trim().toLowerCase()))`.
- `Table` columns: Scope (only when `activeScope==='all'`), Status (`Badge`: proposed=warn, active/approved=pos, archived=ink), Content, Confidence (`--font-numeric`), Category (or `—`), Source (badge per helper below + visible caption `{extractor_model}` + `session #{source_session_id}`, or `—`), Actions.
- Actions per row, gated by status: Approve iff `status==='proposed'`; Pin/Unpin (label from `pinned`), Edit, Delete iff `status!=='archived'`; all disabled when `rowBusy.has(f.id)`. Delete calls `onDelete(f)` (opens the confirm modal — it does not delete directly).
- Provenance helper:
```tsx
function sourceBadge(createdBy: string) {
  const v = (createdBy || '').trim();
  if (v === 'extractor') return { variant: 'info', label: 'extractor' };
  if (v === 'api') return { variant: 'ink', label: 'api' };
  return { variant: 'ink', label: v || 'unknown' };
}
```

- [ ] **Step 3d: Load-more note + modals (create/edit + delete confirm)**

- Below table: count note `Showing {visible.length} of {facts.length} loaded · {total} total`; `[Load more]` button when `facts.length < total` → `onLoadMore`. `feedback` in the `PageScaffold` feedback zone.
- Create/Edit `Modal` driven by `modal` (`kind==='create'|'edit'`): fields bound to `modal.draft` via `onModalChange`; on create show `scope_type` select and (when `draft.scope_type==='book'`) a portfolio select; confidence input `min={confidenceFloor} max={1} step={0.01}`; `modalError` shown inline. **Save disabled** unless `draft.content.trim()` and `confidenceFloor <= draft.confidence <= 1` and (`draft.scope_type!=='book'` or `draft.portfolioId!=null`), and not `modalSaving`. Save → `onModalSave`; Cancel → `onModalCancel`.
- Delete `Modal` (`kind==='delete'`): "Archive this fact? It will no longer be injected and cannot be restored from this page." Confirm → `onConfirmDelete`; Cancel → `onModalCancel`.

- [ ] **Step 3e: Keep `Memory.live` compiling (stub the new props)**

Update the Task-5 `Memory.live.tsx` to pass the expanded props so the page renders during Task 6 (real wiring lands in Task 7): add local `useState` for `activeScope='all'`, `statusFilter='current'`, `search=''`, `selectedPortfolio=null`, `modal=null`; pass `portfolios={[]} portfoliosError={null} rowBusy={new Set()} confidenceFloor={status?.config.confidence_floor ?? 0.7} modalSaving={false} modalError={null}`; pass **no-op** handlers (`onScope=setActiveScope`, `onStatusFilter=setStatusFilter`, `onSearch=setSearch`, `onSelectPortfolio=setSelectedPortfolio`, `onRefresh={() => void loadView()}`, and `() => {}` for `onLoadMore/onApprove/onPin/onEdit/onDelete/onNew/onModalChange/onModalSave/onModalCancel/onConfirmDelete`). This keeps `tsc` green; Task 7 replaces the stubs with real behavior.

- [ ] **Step 4: Run to verify they pass**

Run: `cd frontend && npx vitest run src/routes/Memory.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors (the stubbed `Memory.live` satisfies the expanded `MemoryProps`).

- [ ] **Step 5: Token-purity + theme check**

Run (from repo root, correct path + broader patterns):
```bash
! grep -nE "#[0-9a-fA-F]{3,6}|[0-9]+px|rgba?\(|var\(--[a-z0-9-]+, *#" frontend/src/routes/Memory.css
```
Expected: no matches (nonzero exit on any match). **Manual checklist:** load `/memory` and confirm readable rendering in **light**, **dark**, and **compact density** (toggle theme/density controls); numbers use `--font-numeric`; rows wrap with no horizontal scrollbar; action buttons show `:focus-visible`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Memory.tsx frontend/src/routes/Memory.css frontend/src/routes/Memory.test.tsx
git commit -m "feat(memory): presentational console (chips, tabs, table, actions, load-more)"
```

---

## Task 7: Frontend — container behaviors (mutations, modal, portfolios, load-more, partial-failure)

**Files:**
- Modify: `frontend/src/routes/Memory.live.tsx`
- Modify: `frontend/src/routes/Memory.tsx` (Create/Edit `Modal` + Delete-confirm `Modal`)
- Test: `frontend/src/routes/Memory.live.test.tsx` (extend)

**Interfaces:**
- Consumes: all Task-4 client fns + `errorMessage`.
- Produces: the wired console.

- [ ] **Step 1: Write the failing behavior tests**

```tsx
// extend frontend/src/routes/Memory.live.test.tsx
import { fireEvent } from '@testing-library/react';

it('switching to Domain tab refetches with scope_type=domain', async () => {
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('tab', { name: /domain/i }));
  await waitFor(() => expect(client.listMemoryFacts).toHaveBeenLastCalledWith(expect.objectContaining({ scope_type: 'domain' })));
});

it('approve calls approveMemoryFact then refetches', async () => {
  vi.spyOn(client, 'approveMemoryFact').mockResolvedValue({} as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /approve/i }));
  await waitFor(() => expect(client.approveMemoryFact).toHaveBeenCalledWith(1));
});

it('pin toggle calls setMemoryFactPinned with the negated flag', async () => {
  vi.spyOn(client, 'setMemoryFactPinned').mockResolvedValue({} as any);
  vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active', pinned: false })], total: 1 } as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /^pin$/i }));
  await waitFor(() => expect(client.setMemoryFactPinned).toHaveBeenCalledWith(1, true));
});

it('edit submits patchMemoryFact with changed fields incl. category clear', async () => {
  vi.spyOn(client, 'patchMemoryFact').mockResolvedValue({} as any);
  vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active', category: 'pref' })], total: 1 } as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /edit/i }));
  fireEvent.change(screen.getByLabelText(/content/i), { target: { value: 'updated' } });
  fireEvent.change(screen.getByLabelText(/category/i), { target: { value: '' } });   // clear
  fireEvent.click(screen.getByRole('button', { name: /save/i }));
  await waitFor(() => expect(client.patchMemoryFact).toHaveBeenCalledWith(1, expect.objectContaining({ content: 'updated', category: '' })));
});

it('create from All tab with book scope sends scope_id=String(id)', async () => {
  vi.spyOn(client, 'createMemoryFact').mockResolvedValue({} as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /new/i }));
  fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: 'book' } });
  fireEvent.change(screen.getByLabelText(/portfolio/i), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText(/content/i), { target: { value: 'book fact' } });
  fireEvent.click(screen.getByRole('button', { name: /save/i }));
  await waitFor(() => expect(client.createMemoryFact).toHaveBeenCalledWith(expect.objectContaining({ scope_type: 'book', scope_id: '7', content: 'book fact' })));
});

it('delete requires confirm', async () => {
  vi.spyOn(client, 'deleteMemoryFact').mockResolvedValue(undefined as any);
  vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active' })], total: 1 } as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /delete/i }));
  expect(client.deleteMemoryFact).not.toHaveBeenCalled();      // confirm modal first
  fireEvent.click(screen.getByRole('button', { name: /archive this fact|confirm/i }));
  await waitFor(() => expect(client.deleteMemoryFact).toHaveBeenCalledWith(1));
});

it('status fetch failure keeps the table, hides chips, falls back to floor 0.7', async () => {
  vi.spyOn(client, 'getMemoryStatus').mockRejectedValue(new Error('boom'));
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));     // table still renders
});

it('load more appends the next page', async () => {
  vi.spyOn(client, 'listMemoryFacts')
    .mockResolvedValueOnce({ items: [fact({ id: 1 })], total: 2 } as any)
    .mockResolvedValueOnce({ items: [fact({ id: 2, content: 'second' })], total: 2 } as any);
  render(<MemoryLive onPageContextChange={() => {}} />);
  await waitFor(() => screen.getByText('vol skew steepens'));
  fireEvent.click(screen.getByRole('button', { name: /load more/i }));
  await waitFor(() => expect(screen.getByText('second')).toBeInTheDocument());
  expect(client.listMemoryFacts).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 }));
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/routes/Memory.live.test.tsx`
Expected: FAIL (skeleton container lacks tabs/mutations/modal/load-more).

Container state per spec §F2: `facts, total, nextOffset, status, loading, error, feedback, activeScope='all', statusFilter='current', search, portfolios, portfoliosError, selectedPortfolio=null, modal, modalSaving, modalError, rowBusy(Set), reqSeq`.

- [ ] **Step 3a: `loadView` + stale-guard + offset + partial-failure**

- `loadView(reset)`: `token=++reqSeq`; build params: `scope_type = activeScope==='all'?undefined:activeScope`; `status = statusFilter==='current'?undefined:statusFilter`; `scope_id = activeScope==='book' && selectedPortfolio!=null ? String(selectedPortfolio) : undefined`; `offset = reset?0:nextOffset`. Run `Promise.allSettled([listMemoryFacts({...params, limit:100, offset}), reset?getMemoryStatus():Promise.resolve(null)])`. If `token!==reqSeq` bail. On facts success: `reset` → replace + `setNextOffset(items.length)`; else append + `setNextOffset(prev+items.length)`; set `total`. On facts failure: if `reset && facts.length===0` set `error=errorMessage(...)` (full empty-error) else `setFeedback(errorMessage(...))`. On status settled: success → setStatus; failure on initial → leave status null (chips hidden, floor fallback 0.7).
- Load portfolios on mount: `listPortfoliosWithIds().then(setPortfolios).catch(e=>setPortfoliosError(errorMessage(e)))`.
- Effects: `loadView(true)` on mount and whenever `activeScope/statusFilter/selectedPortfolio` change. `onRefresh=()=>loadView(true)`, `onLoadMore=()=>loadView(false)`.

- [ ] **Step 3b: Row mutations (approve/pin/delete) with `rowBusy`**

- `withRow(id, fn)`: add id to `rowBusy`, `await fn()`, then `loadView(true)`; catch → `setFeedback(errorMessage(e))`; finally remove id.
- `onApprove=f=>withRow(f.id,()=>approveMemoryFact(f.id))`; `onPin=f=>withRow(f.id,()=>setMemoryFactPinned(f.id,!f.pinned))`.
- `onDelete=f=>setModal({kind:'delete', fact:f})`; `onConfirmDelete=()=>{ const f=modal.fact; withRow(f.id,()=>deleteMemoryFact(f.id)).then(()=>setModal(null)); }`.

- [ ] **Step 3c: Create/Edit modal wiring (`modalSaving`, validation, category clear)**

- `onNew=()=>setModal({kind:'create', draft:{scope_type:'user', portfolioId:null, content:'', confidence:1, category:''}})`.
- `onEdit=f=>setModal({kind:'edit', fact:f, draft:{scope_type:f.scope_type, portfolioId:null, content:f.content, confidence:f.confidence, category:f.category ?? ''}})`.
- `onModalChange=draft=>setModal(m=>m && m.kind!=='delete' ? {...m, draft} : m)`.
- `onModalSave`: set `modalSaving=true`, `modalError=null`; for `create` call `createMemoryFact({scope_type:d.scope_type, scope_id: d.scope_type==='book'?String(d.portfolioId):undefined, content:d.content, confidence:d.confidence, category: d.category===''?undefined:d.category})`; for `edit` call `patchMemoryFact(fact.id, {content:d.content, confidence:d.confidence, category:d.category})` (sends `''` to clear). On success `setModal(null)` + `loadView(true)`; on error `setModalError(errorMessage(e))` (keep modal open); finally `modalSaving=false`.
- `onModalCancel=()=>setModal(null)`. `confidenceFloor = status?.config.confidence_floor ?? 0.7`.
- Pass all state + callbacks to `<Memory .../>` (replacing the Task-6 stubs).

- [ ] **Step 4: Run to verify they pass**

Run: `cd frontend && npx vitest run src/routes/Memory.live.test.tsx`
Expected: PASS

- [ ] **Step 5: Full typecheck + memory route tests**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/routes/Memory.live.test.tsx src/routes/Memory.test.tsx src/api/client.memory.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Memory.live.tsx frontend/src/routes/Memory.tsx frontend/src/routes/Memory.live.test.tsx
git commit -m "feat(memory): container behaviors — mutations, modal, portfolios, load-more, partial-failure"
```

---

## Task 8: Route guard + full-suite integration

**Files:**
- Modify (if needed): whichever test asserts an exact route/nav set
- Test: existing backend + frontend suites

- [ ] **Step 1: Find any route/nav snapshot test the new route trips**

Run: `cd frontend && grep -rln "ROUTE_PATHS\|navItems\|'memory'\|route ===" src/**/*.test.tsx src/lib/*.test.ts 2>/dev/null; npx vitest run 2>&1 | tail -30`
Expected: identify any failing "exact route set" assertion.

- [ ] **Step 2: Update the snapshot/assertion to include `'memory'`**

If a test enumerates routes/nav entries, add `'memory'` / the Memory nav item to its expected set. (No change if none exists.)

- [ ] **Step 3: Run the full frontend suite**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 4: Run the full backend memory + api suite**

Run: `python -m pytest tests/test_memory_api.py tests/test_memory_store_crud.py tests/test_memory_apply_diff.py tests/test_memory_queue_runjob.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test(memory): route-guard update + full-suite green"
```

---

## Notes for the implementer

- **Prop-name verification:** Tasks 5–7 reference shared components (`PageScaffold`, `Table`, `Tabs`, `PageToolbar`, `Badge`, `Button`, `Modal`, `Empty`). The component *structure* is the contract; confirm the exact prop spellings/variants against each component file before writing (e.g. `frontend/src/components/Table.tsx` `Column<T>` shape, `Badge` variant names `pos|neg|warn|info|ink`). The Skills page (`frontend/src/routes/Skills.tsx` / `Skills.live.tsx`) is the closest working example of all of them together.
- **No backend change for category-clear** (`_clean_category("")` already → `null`) or **archive idempotency** (already a no-op when archived) — Task 3 Step 5 only adds tests.
- **Confidence input:** `min={floor} max={1} step={0.01}`, no auto-clamp; out-of-range disables Save with an inline message.
