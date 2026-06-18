# RFQ Routes Migration (Plan 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the **Client RFQ** and **RFQ Approval** routes off PlaceholderRoute and onto the Warm Ledger design system. Two routes ship in this plan, both wired to the existing FastAPI backend.

**Architecture:** Each route follows the foundation's master-detail/intake pattern with two layers: a presentational component that takes data as props (testable in isolation), and a `.live.tsx` container that fetches from the API and feeds the presentational layer. RFQ Approval is the tri-column inbox/detail/audit master-detail; Client RFQ is the single-column intake (NL + Structured tabs) + status card. Reject opens an irreversible-confirm Modal with a required free-text `comment` field. The audit pane derives history from RFQ object fields only — full audit-event listing requires a backend endpoint and is deferred to a follow-up plan.

**Tech Stack:** React 19 · Vite · vanilla CSS + custom properties · `@radix-ui/react-dialog` (Modal) and `@radix-ui/react-tabs` already installed · vitest + @testing-library/react · existing primitives from foundation plan: Button, Input, Tabs, Modal, Panel, Tile, Table, Badge, Chip, ActionProposal, AppShell, PageHeader, etc.

**Spec:** `docs/superpowers/specs/2026-05-07-ui-ux-redesign-design.md` (commit 141641a) — sections "Per-route layouts · 1. Client RFQ" and "Per-route layouts · 2. RFQ Approval".

**Foundation:** `docs/superpowers/plans/2026-05-07-warm-ledger-foundation.md` (33 tasks, commits f0ca7fc..8883c34, all on `main`).

**Branch:** This plan continues to commit on `main`. The foundation plan already received explicit user consent for `main` commits; this plan extends that consent.

**Open questions resolved (carried into the plan):**

1. **Reject modal:** required free-text reason via Input, mapped to `RFQApprovalDecision.comment`. Empty submission disabled.
2. **Structured RFQ scope:** EuropeanVanillaOption + BarrierOption only (matches existing backend `_create_rfq`). Snowball/Phoenix deferred — would need backend support.
3. **Audit log:** derived from RFQ object — `created_at` (created), `updated_at` + status (transition), `approved_response` (released). No new backend endpoint. Note as limitation in UI.
4. **Backend endpoints:** confirmed `GET /api/internal/rfqs`, `POST /api/internal/rfq/{id}/{approve|reject}` with `{approver, comment, response_override}`, `GET /api/client/rfq/{id}`, `POST /api/client/rfq/{form,chat}`.

---

## File structure

**New files (RFQ Approval):**
- `frontend/src/components/RfqInbox.tsx` (+ .css + .test.tsx) — list of pending/historical RFQs, click selects
- `frontend/src/components/RfqDetail.tsx` (+ .css + .test.tsx) — selected RFQ detail, approve/reject buttons
- `frontend/src/components/RfqAudit.tsx` (+ .css + .test.tsx) — derived audit timeline
- `frontend/src/components/RfqRejectModal.tsx` (+ .css + .test.tsx) — irreversible-confirm modal with comment field
- `frontend/src/routes/RfqApproval.tsx` (+ .css) — tri-column composition
- `frontend/src/routes/RfqApproval.live.tsx` — API container

**New files (Client RFQ):**
- `frontend/src/components/RfqIntakeCard.tsx` (+ .css + .test.tsx) — NL + Structured tabs intake
- `frontend/src/components/RfqStatusCard.tsx` (+ .css + .test.tsx) — status display
- `frontend/src/routes/ClientRfq.tsx` (+ .css) — single-column composition
- `frontend/src/routes/ClientRfq.live.tsx` — API container

**Modified files:**
- `frontend/src/main.tsx` — replace `<PlaceholderRoute title="RFQ Approval" />` with `<RfqApprovalLive />` and same for Client RFQ (one task each, separately gated)

---

# Phase A · RFQ Approval

## Task 1: Build RfqInbox

**Files:**
- Create: `frontend/src/components/RfqInbox.tsx`
- Create: `frontend/src/components/RfqInbox.css`
- Create: `frontend/src/components/RfqInbox.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqInbox.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqInbox } from './RfqInbox';
import type { RFQ } from '../types';

const rfqs: RFQ[] = [
  { id: 1042, client_name: 'Lakeshore Cap', channel: 'chat', status: 'pending_approval', request_payload: {}, quote_payload: {} },
  { id: 1041, client_name: 'Meridian',     channel: 'form', status: 'approved',          request_payload: {}, quote_payload: {} },
];

describe('RfqInbox', () => {
  it('renders one row per RFQ', () => {
    render(<RfqInbox rfqs={rfqs} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText(/#1042/)).toBeInTheDocument();
    expect(screen.getByText(/Lakeshore Cap/)).toBeInTheDocument();
    expect(screen.getByText(/#1041/)).toBeInTheDocument();
  });

  it('marks selected row', () => {
    const { container } = render(<RfqInbox rfqs={rfqs} selectedId={1042} onSelect={() => {}} />);
    const selected = container.querySelectorAll('.wl-rfq-inbox__row--selected');
    expect(selected.length).toBe(1);
  });

  it('calls onSelect when row clicked', async () => {
    const onSelect = vi.fn();
    render(<RfqInbox rfqs={rfqs} selectedId={null} onSelect={onSelect} />);
    await userEvent.click(screen.getByText(/#1041/));
    expect(onSelect).toHaveBeenCalledWith(1041);
  });

  it('shows empty state when no rfqs', () => {
    render(<RfqInbox rfqs={[]} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText(/No RFQs/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run from `frontend/`: `npm test RfqInbox`
Expected: FAIL "Cannot find module './RfqInbox'".

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqInbox.tsx`:

```tsx
import type { RFQ } from '../types';
import { Badge, type BadgeVariant } from './Badge';
import { Empty } from './Empty';
import './RfqInbox.css';

type Props = {
  rfqs: RFQ[];
  selectedId: number | null;
  onSelect: (id: number) => void;
};

const statusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  approved: 'pos',
  rejected: 'neg',
  draft: 'ink',
};

export function RfqInbox({ rfqs, selectedId, onSelect }: Props) {
  if (rfqs.length === 0) {
    return <Empty message="No RFQs in inbox" symbol="∅" />;
  }
  return (
    <div className="wl-rfq-inbox">
      {rfqs.map((rfq) => {
        const isSelected = rfq.id === selectedId;
        return (
          <button
            key={rfq.id}
            type="button"
            className={`wl-rfq-inbox__row ${isSelected ? 'wl-rfq-inbox__row--selected' : ''}`.trim()}
            onClick={() => onSelect(rfq.id)}
          >
            <div className="wl-rfq-inbox__id">#{rfq.id}</div>
            <div className="wl-rfq-inbox__client">{rfq.client_name}</div>
            <Badge variant={statusVariant[rfq.status] ?? 'ink'}>{rfq.status}</Badge>
          </button>
        );
      })}
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqInbox.css`:

```css
.wl-rfq-inbox { display: flex; flex-direction: column; gap: 0; }
.wl-rfq-inbox__row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: var(--gap-2);
  align-items: center;
  padding: var(--gap-2) var(--gap-3);
  border: 0;
  border-bottom: 1px solid var(--paper-3);
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  color: var(--ink);
}
.wl-rfq-inbox__row:hover { background: var(--paper-2); }
.wl-rfq-inbox__row--selected { background: var(--paper-3); }
.wl-rfq-inbox__id {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
.wl-rfq-inbox__client {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

- [ ] **Step 4: Run tests, expect 4 PASS**

Run: `npm test RfqInbox`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqInbox.tsx frontend/src/components/RfqInbox.css frontend/src/components/RfqInbox.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqInbox component"
```

## Task 2: Build RfqDetail

**Files:**
- Create: `frontend/src/components/RfqDetail.tsx`
- Create: `frontend/src/components/RfqDetail.css`
- Create: `frontend/src/components/RfqDetail.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqDetail.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqDetail } from './RfqDetail';
import type { RFQ } from '../types';

const baseRfq: RFQ = {
  id: 1042,
  client_name: 'Lakeshore Cap',
  channel: 'chat',
  status: 'pending_approval',
  request_payload: { product_type: 'BarrierOption', underlying: 'CSI500' },
  quote_payload: { solved_value: 0.2, achieved_price: 10.041 },
  approved_response: null,
};

describe('RfqDetail', () => {
  it('renders RFQ id, client, product, prices', () => {
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.getByText(/RFQ #1042/i)).toBeInTheDocument();
    expect(screen.getByText('Lakeshore Cap')).toBeInTheDocument();
    expect(screen.getByText('BarrierOption')).toBeInTheDocument();
    expect(screen.getByText(/10\.041/)).toBeInTheDocument();
  });

  it('shows approve/reject buttons when pending_approval', () => {
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
  });

  it('hides approve/reject when not pending_approval', () => {
    render(<RfqDetail rfq={{ ...baseRfq, status: 'approved' }} onApprove={() => {}} onRejectClick={() => {}} />);
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });

  it('calls onApprove with rfq id', async () => {
    const onApprove = vi.fn();
    render(<RfqDetail rfq={baseRfq} onApprove={onApprove} onRejectClick={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /approve/i }));
    expect(onApprove).toHaveBeenCalledWith(1042);
  });

  it('calls onRejectClick with rfq id', async () => {
    const onRejectClick = vi.fn();
    render(<RfqDetail rfq={baseRfq} onApprove={() => {}} onRejectClick={onRejectClick} />);
    await userEvent.click(screen.getByRole('button', { name: /reject/i }));
    expect(onRejectClick).toHaveBeenCalledWith(1042);
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test RfqDetail`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqDetail.tsx`:

```tsx
import type { RFQ } from '../types';
import { Button } from './Button';
import { Tile } from './Tile';
import { Badge, type BadgeVariant } from './Badge';
import './RfqDetail.css';

type Props = {
  rfq: RFQ;
  onApprove: (id: number) => void;
  onRejectClick: (id: number) => void;
};

const statusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  approved: 'pos',
  rejected: 'neg',
  draft: 'ink',
};

function readString(payload: Record<string, unknown>, key: string, fallback = '—'): string {
  const value = payload[key];
  return value == null ? fallback : String(value);
}

function readNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  return typeof value === 'number' ? value : Number(value ?? 0);
}

export function RfqDetail({ rfq, onApprove, onRejectClick }: Props) {
  const product = readString(rfq.request_payload, 'product_type');
  const underlying = readString(rfq.request_payload, 'underlying');
  const solved = readNumber(rfq.quote_payload, 'solved_value');
  const price = readNumber(rfq.quote_payload, 'achieved_price');
  const isPending = rfq.status === 'pending_approval';

  return (
    <div className="wl-rfq-detail">
      <header className="wl-rfq-detail__head">
        <div>
          <div className="wl-rfq-detail__id">RFQ #{rfq.id}</div>
          <div className="wl-rfq-detail__client">{rfq.client_name}</div>
        </div>
        <Badge variant={statusVariant[rfq.status] ?? 'ink'}>{rfq.status}</Badge>
      </header>

      <dl className="wl-rfq-detail__facts">
        <div><dt>Product</dt><dd>{product}</dd></div>
        <div><dt>Underlying</dt><dd>{underlying}</dd></div>
        <div><dt>Channel</dt><dd>{rfq.channel}</dd></div>
      </dl>

      <div className="wl-rfq-detail__tiles">
        <Tile label="Solved" value={solved.toFixed(6)} />
        <Tile label="Price" value={price.toFixed(6)} />
      </div>

      {rfq.approved_response && (
        <div className="wl-rfq-detail__response">
          <div className="wl-rfq-detail__response-label">Released response</div>
          <p>{rfq.approved_response}</p>
        </div>
      )}

      {isPending && (
        <div className="wl-rfq-detail__actions">
          <Button variant="primary" onClick={() => onApprove(rfq.id)}>Approve &amp; Send</Button>
          <Button variant="danger" onClick={() => onRejectClick(rfq.id)}>Reject…</Button>
        </div>
      )}
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqDetail.css`:

```css
.wl-rfq-detail { display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-rfq-detail__head { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--gap-3); }
.wl-rfq-detail__id {
  font-family: var(--font-numeric);
  font-size: var(--type-h3-size);
  font-weight: 700;
  color: var(--ink);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.wl-rfq-detail__client { font-size: var(--type-body-size); color: var(--ink-2); margin-top: 2px; }
.wl-rfq-detail__facts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--gap-3);
  margin: 0;
  padding: var(--gap-2) 0;
  border-top: 1px solid var(--paper-3);
  border-bottom: 1px solid var(--paper-3);
}
.wl-rfq-detail__facts > div { display: flex; flex-direction: column; gap: 2px; }
.wl-rfq-detail__facts dt {
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-weight: var(--type-caps-weight);
}
.wl-rfq-detail__facts dd { margin: 0; font-size: var(--type-body-size); color: var(--ink); }
.wl-rfq-detail__tiles { display: grid; grid-template-columns: 1fr 1fr; gap: var(--gap-2); }
.wl-rfq-detail__response {
  border-left: 3px solid var(--ink);
  padding: var(--gap-2) var(--gap-3);
  background: var(--paper-2);
}
.wl-rfq-detail__response-label {
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-weight: var(--type-caps-weight);
  margin-bottom: 4px;
}
.wl-rfq-detail__response p { margin: 0; font-size: var(--type-small-size); color: var(--ink); line-height: 1.5; }
.wl-rfq-detail__actions { display: flex; gap: var(--gap-2); margin-top: var(--gap-2); }
```

- [ ] **Step 4: Run tests, expect 5 PASS**

Run: `npm test RfqDetail`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqDetail.tsx frontend/src/components/RfqDetail.css frontend/src/components/RfqDetail.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqDetail component"
```

## Task 3: Build RfqAudit

**Files:**
- Create: `frontend/src/components/RfqAudit.tsx`
- Create: `frontend/src/components/RfqAudit.css`
- Create: `frontend/src/components/RfqAudit.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqAudit.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RfqAudit } from './RfqAudit';
import type { RFQ } from '../types';

const pending: RFQ = {
  id: 1, client_name: 'C', channel: 'form', status: 'pending_approval',
  request_payload: {}, quote_payload: {}, approved_response: null,
};

const approved: RFQ = {
  ...pending, status: 'approved', approved_response: 'sent',
};

describe('RfqAudit', () => {
  it('shows created event', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/created/i)).toBeInTheDocument();
  });

  it('shows pending status when pending_approval', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/pending desk approval/i)).toBeInTheDocument();
  });

  it('shows released event when approved', () => {
    render(<RfqAudit rfq={approved} />);
    expect(screen.getByText(/released to client/i)).toBeInTheDocument();
  });

  it('notes audit limitation', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/derived from rfq fields/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test RfqAudit`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqAudit.tsx`:

```tsx
import type { RFQ } from '../types';
import './RfqAudit.css';

type Props = {
  rfq: RFQ;
};

type Event = { label: string; detail?: string };

function deriveEvents(rfq: RFQ): Event[] {
  const events: Event[] = [{ label: 'Created' }];
  if (rfq.status === 'pending_approval') {
    events.push({ label: 'Pending desk approval' });
  }
  if (rfq.status === 'approved') {
    events.push({ label: 'Approved' });
    if (rfq.approved_response) {
      events.push({ label: 'Released to client' });
    }
  }
  if (rfq.status === 'rejected') {
    events.push({ label: 'Rejected' });
  }
  return events;
}

export function RfqAudit({ rfq }: Props) {
  const events = deriveEvents(rfq);
  return (
    <div className="wl-rfq-audit">
      <ol className="wl-rfq-audit__list">
        {events.map((event, index) => (
          <li key={index} className="wl-rfq-audit__row">
            <span className="wl-rfq-audit__bullet" aria-hidden />
            <span className="wl-rfq-audit__label">{event.label}</span>
            {event.detail && <span className="wl-rfq-audit__detail">{event.detail}</span>}
          </li>
        ))}
      </ol>
      <p className="wl-rfq-audit__note">Derived from RFQ fields. Full audit-event listing requires backend endpoint (deferred).</p>
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqAudit.css`:

```css
.wl-rfq-audit { display: flex; flex-direction: column; gap: var(--gap-3); padding: var(--gap-2); }
.wl-rfq-audit__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--gap-2); }
.wl-rfq-audit__row {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--gap-2);
  align-items: center;
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink);
}
.wl-rfq-audit__bullet {
  width: 6px;
  height: 6px;
  background: var(--ink);
  border-radius: 0;
  display: inline-block;
}
.wl-rfq-audit__label { font-weight: 600; }
.wl-rfq-audit__detail { color: var(--ink-2); }
.wl-rfq-audit__note {
  margin: 0;
  font-size: var(--type-caps-size);
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

- [ ] **Step 4: Run tests, expect 4 PASS**

Run: `npm test RfqAudit`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqAudit.tsx frontend/src/components/RfqAudit.css frontend/src/components/RfqAudit.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqAudit derived timeline"
```

## Task 4: Build RfqRejectModal

**Files:**
- Create: `frontend/src/components/RfqRejectModal.tsx`
- Create: `frontend/src/components/RfqRejectModal.css`
- Create: `frontend/src/components/RfqRejectModal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqRejectModal.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqRejectModal } from './RfqRejectModal';

describe('RfqRejectModal', () => {
  it('renders title with rfq id when open', () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    expect(screen.getByText(/Reject RFQ #1042/i)).toBeInTheDocument();
  });

  it('disables confirm when reason is empty', () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    expect(screen.getByRole('button', { name: /confirm reject/i })).toBeDisabled();
  });

  it('enables confirm when reason has text', async () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    await userEvent.type(screen.getByLabelText(/reason/i), 'price too aggressive');
    expect(screen.getByRole('button', { name: /confirm reject/i })).toBeEnabled();
  });

  it('calls onConfirm with id and reason', async () => {
    const onConfirm = vi.fn();
    render(<RfqRejectModal open rfqId={1042} onConfirm={onConfirm} onOpenChange={() => {}} />);
    await userEvent.type(screen.getByLabelText(/reason/i), 'price too aggressive');
    await userEvent.click(screen.getByRole('button', { name: /confirm reject/i }));
    expect(onConfirm).toHaveBeenCalledWith(1042, 'price too aggressive');
  });

  it('calls onOpenChange(false) on cancel', async () => {
    const onOpenChange = vi.fn();
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={onOpenChange} />);
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test RfqRejectModal`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqRejectModal.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { Input } from './Input';
import { Button } from './Button';
import './RfqRejectModal.css';

type Props = {
  open: boolean;
  rfqId: number | null;
  onConfirm: (rfqId: number, reason: string) => void;
  onOpenChange: (open: boolean) => void;
};

export function RfqRejectModal({ open, rfqId, onConfirm, onOpenChange }: Props) {
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (open) setReason('');
  }, [open, rfqId]);

  const trimmed = reason.trim();
  const canConfirm = trimmed.length > 0 && rfqId != null;

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={rfqId != null ? `Reject RFQ #${rfqId}` : 'Reject RFQ'}
      description="This action is irreversible. The reason is recorded in the approval audit log."
    >
      <div className="wl-reject-modal">
        <Input
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          autoFocus
          placeholder="Explain why this RFQ is being rejected"
        />
        <div className="wl-reject-modal__actions">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="danger"
            disabled={!canConfirm}
            onClick={() => { if (canConfirm) onConfirm(rfqId!, trimmed); }}
          >
            Confirm Reject
          </Button>
        </div>
      </div>
    </Modal>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqRejectModal.css`:

```css
.wl-reject-modal { display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-reject-modal__actions { display: flex; justify-content: flex-end; gap: var(--gap-2); }
```

- [ ] **Step 4: Run tests, expect 5 PASS**

Run: `npm test RfqRejectModal`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqRejectModal.tsx frontend/src/components/RfqRejectModal.css frontend/src/components/RfqRejectModal.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqRejectModal with required reason"
```

## Task 5: Build RfqApproval presentational route

**Files:**
- Create: `frontend/src/routes/RfqApproval.tsx`
- Create: `frontend/src/routes/RfqApproval.css`

(No test for this task — it's pure composition of already-tested components. Typecheck is the gate.)

- [ ] **Step 1: Create the route file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/RfqApproval.tsx`:

```tsx
import { useMemo, useState } from 'react';
import type { RFQ } from '../types';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { RfqInbox } from '../components/RfqInbox';
import { RfqDetail } from '../components/RfqDetail';
import { RfqAudit } from '../components/RfqAudit';
import { RfqRejectModal } from '../components/RfqRejectModal';
import { Empty } from '../components/Empty';
import './RfqApproval.css';

type Props = {
  rfqs: RFQ[];
  onApprove: (id: number) => Promise<void> | void;
  onReject: (id: number, reason: string) => Promise<void> | void;
};

export function RfqApproval({ rfqs, onApprove, onReject }: Props) {
  const pending = useMemo(() => rfqs.filter((r) => r.status === 'pending_approval').length, [rfqs]);
  const [selectedId, setSelectedId] = useState<number | null>(rfqs[0]?.id ?? null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const selectedRfq = useMemo(() => rfqs.find((r) => r.id === selectedId) ?? null, [rfqs, selectedId]);

  const handleRejectClick = (id: number) => setRejectingId(id);
  const handleRejectConfirm = async (id: number, reason: string) => {
    await onReject(id, reason);
    setRejectingId(null);
  };

  return (
    <>
      <PageHeader
        title="RFQ APPROVAL"
        chips={[`${pending} pending`, `${rfqs.length} total`]}
      />
      <div className="wl-rfq-approval__cols">
        <Panel title="Inbox" meta={`${rfqs.length}`}>
          <RfqInbox rfqs={rfqs} selectedId={selectedId} onSelect={setSelectedId} />
        </Panel>
        <Panel title={selectedRfq ? `RFQ #${selectedRfq.id}` : 'Detail'} meta={selectedRfq?.status ?? ''}>
          {selectedRfq ? (
            <RfqDetail rfq={selectedRfq} onApprove={onApprove} onRejectClick={handleRejectClick} />
          ) : (
            <Empty message="Select an RFQ from the inbox" symbol="◌" />
          )}
        </Panel>
        <Panel title="Audit" meta="">
          {selectedRfq ? <RfqAudit rfq={selectedRfq} /> : <Empty message="No selection" symbol="◌" />}
        </Panel>
      </div>
      <RfqRejectModal
        open={rejectingId != null}
        rfqId={rejectingId}
        onConfirm={handleRejectConfirm}
        onOpenChange={(open) => { if (!open) setRejectingId(null); }}
      />
    </>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/RfqApproval.css`:

```css
.wl-rfq-approval__cols {
  display: grid;
  grid-template-columns: 30% 1fr 25%;
  gap: var(--gap-3);
  align-items: flex-start;
}
@media (max-width: 1100px) {
  .wl-rfq-approval__cols { grid-template-columns: 1fr; }
}
```

- [ ] **Step 2: Typecheck**

Run from `frontend/`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/RfqApproval.tsx frontend/src/routes/RfqApproval.css
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqApproval tri-column route"
```

## Task 6: Build RfqApproval live container

**Files:**
- Create: `frontend/src/routes/RfqApproval.live.tsx`

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/RfqApproval.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { RFQ } from '../types';
import { RfqApproval } from './RfqApproval';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';

export function RfqApprovalLive() {
  const [rfqs, setRfqs] = useState<RFQ[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const list = await api<RFQ[]>('/api/internal/rfqs');
      setRfqs(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, []);

  const handleApprove = async (id: number) => {
    await api(`/api/internal/rfq/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approver: 'trader', comment: 'approved from desk UI' }),
    });
    await refresh();
  };

  const handleReject = async (id: number, reason: string) => {
    await api(`/api/internal/rfq/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ approver: 'trader', comment: reason }),
    });
    await refresh();
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={300} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load RFQs: ${error}`} />;
  }

  return <RfqApproval rfqs={rfqs} onApprove={handleApprove} onReject={handleReject} />;
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/RfqApproval.live.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): wire RfqApproval to live API"
```

## Task 7: Wire RfqApproval into main.tsx

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Read current main.tsx**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/main.tsx`. Find the import block at the top and the `route === 'rfq'` line in the JSX.

- [ ] **Step 2: Add the import**

Add this import alongside the existing route imports (after `import { PlaceholderRoute } from './routes/PlaceholderRoute';`):

```ts
import { RfqApprovalLive } from './routes/RfqApproval.live';
```

- [ ] **Step 3: Replace the placeholder render**

Find this line:

```tsx
{route === 'rfq'       && <PlaceholderRoute title="RFQ Approval" />}
```

Replace with:

```tsx
{route === 'rfq'       && <RfqApprovalLive />}
```

- [ ] **Step 4: Verify**

Run from `frontend/`: `npx tsc -b --noEmit`
Expected: 0 errors.

Run: `timeout 6 npm run dev 2>&1 | head -10`
Expected: VITE ready, no errors.

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/main.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): replace placeholder with RfqApprovalLive"
```

---

# Phase B · Client RFQ

## Task 8: Build RfqIntakeCard

**Files:**
- Create: `frontend/src/components/RfqIntakeCard.tsx`
- Create: `frontend/src/components/RfqIntakeCard.css`
- Create: `frontend/src/components/RfqIntakeCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqIntakeCard.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqIntakeCard } from './RfqIntakeCard';

describe('RfqIntakeCard', () => {
  it('renders both tabs and starts on Natural Language', () => {
    render(
      <RfqIntakeCard
        defaultMessage="Quote a CSI500 snowball"
        onSubmitNL={() => {}}
        onSubmitStructured={() => {}}
      />
    );
    expect(screen.getByRole('tab', { name: /natural language/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /structured/i })).toBeInTheDocument();
    expect(screen.getByDisplayValue('Quote a CSI500 snowball')).toBeInTheDocument();
  });

  it('calls onSubmitNL with current message', async () => {
    const onSubmitNL = vi.fn();
    render(
      <RfqIntakeCard
        defaultMessage="initial"
        onSubmitNL={onSubmitNL}
        onSubmitStructured={() => {}}
      />
    );
    const textarea = screen.getByLabelText(/message/i);
    await userEvent.clear(textarea);
    await userEvent.type(textarea, 'new question');
    await userEvent.click(screen.getByRole('button', { name: /submit natural language/i }));
    expect(onSubmitNL).toHaveBeenCalledWith('new question');
  });

  it('switches to Structured tab and shows form fields', async () => {
    render(
      <RfqIntakeCard
        defaultMessage="x"
        onSubmitNL={() => {}}
        onSubmitStructured={() => {}}
      />
    );
    await userEvent.click(screen.getByRole('tab', { name: /structured/i }));
    expect(screen.getByLabelText(/product/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/unknown field/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/target price/i)).toBeInTheDocument();
  });

  it('calls onSubmitStructured with form values', async () => {
    const onSubmitStructured = vi.fn();
    render(
      <RfqIntakeCard
        defaultMessage="x"
        onSubmitNL={() => {}}
        onSubmitStructured={onSubmitStructured}
      />
    );
    await userEvent.click(screen.getByRole('tab', { name: /structured/i }));
    await userEvent.click(screen.getByRole('button', { name: /submit structured/i }));
    expect(onSubmitStructured).toHaveBeenCalledWith({
      product: 'EuropeanVanillaOption',
      unknown: 'strike',
      target: '10',
    });
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test RfqIntakeCard`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqIntakeCard.tsx`:

```tsx
import { useId, useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs';
import { Input } from './Input';
import { Button } from './Button';
import './RfqIntakeCard.css';

export type StructuredForm = {
  product: 'EuropeanVanillaOption' | 'BarrierOption';
  unknown: string;
  target: string;
};

type Props = {
  defaultMessage: string;
  onSubmitNL: (message: string) => void;
  onSubmitStructured: (form: StructuredForm) => void;
};

export function RfqIntakeCard({ defaultMessage, onSubmitNL, onSubmitStructured }: Props) {
  const [message, setMessage] = useState(defaultMessage);
  const [form, setForm] = useState<StructuredForm>({ product: 'EuropeanVanillaOption', unknown: 'strike', target: '10' });
  const messageId = useId();
  const productId = useId();

  return (
    <div className="wl-rfq-intake">
      <h2 className="wl-rfq-intake__title">SUBMIT RFQ</h2>
      <Tabs defaultValue="nl">
        <TabsList>
          <TabsTrigger value="nl">Natural Language</TabsTrigger>
          <TabsTrigger value="structured">Structured</TabsTrigger>
        </TabsList>
        <TabsContent value="nl">
          <div className="wl-rfq-intake__nl">
            <label htmlFor={messageId} className="wl-rfq-intake__label">Message</label>
            <textarea
              id={messageId}
              className="wl-rfq-intake__textarea"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={5}
            />
            <div className="wl-rfq-intake__actions">
              <Button variant="primary" onClick={() => onSubmitNL(message)}>
                Submit Natural Language ▸
              </Button>
            </div>
          </div>
        </TabsContent>
        <TabsContent value="structured">
          <div className="wl-rfq-intake__form">
            <div className="wl-rfq-intake__field">
              <label htmlFor={productId} className="wl-rfq-intake__label">Product</label>
              <select
                id={productId}
                className="wl-rfq-intake__select"
                value={form.product}
                onChange={(e) => setForm({ ...form, product: e.target.value as StructuredForm['product'] })}
              >
                <option value="EuropeanVanillaOption">EuropeanVanillaOption</option>
                <option value="BarrierOption">BarrierOption</option>
              </select>
            </div>
            <Input
              label="Unknown field"
              value={form.unknown}
              onChange={(e) => setForm({ ...form, unknown: e.target.value })}
            />
            <Input
              label="Target price"
              value={form.target}
              onChange={(e) => setForm({ ...form, target: e.target.value })}
            />
            <div className="wl-rfq-intake__actions">
              <Button variant="primary" onClick={() => onSubmitStructured(form)}>
                Submit Structured ▸
              </Button>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqIntakeCard.css`:

```css
.wl-rfq-intake {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--gap-4);
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}
.wl-rfq-intake__title {
  margin: 0;
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink);
}
.wl-rfq-intake__nl,
.wl-rfq-intake__form { display: flex; flex-direction: column; gap: var(--gap-3); padding-top: var(--gap-2); }
.wl-rfq-intake__field { display: flex; flex-direction: column; gap: var(--gap-1); }
.wl-rfq-intake__label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-rfq-intake__textarea {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  resize: vertical;
  min-height: 96px;
  border-radius: 0;
}
.wl-rfq-intake__textarea:focus {
  outline: none;
  border: 2px solid var(--ink);
  padding: calc(var(--input-padding-y) - 1px) calc(var(--input-padding-x) - 1px);
}
.wl-rfq-intake__select {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  border-radius: 0;
}
.wl-rfq-intake__select:focus {
  outline: none;
  border: 2px solid var(--ink);
  padding: calc(var(--input-padding-y) - 1px) calc(var(--input-padding-x) - 1px);
}
.wl-rfq-intake__actions { display: flex; justify-content: flex-end; }
```

- [ ] **Step 4: Run tests, expect 4 PASS**

Run: `npm test RfqIntakeCard`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqIntakeCard.tsx frontend/src/components/RfqIntakeCard.css frontend/src/components/RfqIntakeCard.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqIntakeCard with NL+Structured tabs"
```

## Task 9: Build RfqStatusCard

**Files:**
- Create: `frontend/src/components/RfqStatusCard.tsx`
- Create: `frontend/src/components/RfqStatusCard.css`
- Create: `frontend/src/components/RfqStatusCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqStatusCard.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RfqStatusCard } from './RfqStatusCard';
import type { RFQ } from '../types';

const approved: RFQ = {
  id: 1042,
  client_name: 'Demo',
  channel: 'chat',
  status: 'approved',
  request_payload: { product_type: 'BarrierOption' },
  quote_payload: { solved_value: 0.2, achieved_price: 10.04, field_label: 'KO_rate' },
  approved_response: 'Approved executable offer for 1 × Snowball on CSI500.',
};

describe('RfqStatusCard', () => {
  it('renders status, id, and response', () => {
    render(<RfqStatusCard rfq={approved} />);
    expect(screen.getByText(/RFQ #1042/)).toBeInTheDocument();
    expect(screen.getByText(/approved/i)).toBeInTheDocument();
    expect(screen.getByText(/Approved executable offer/)).toBeInTheDocument();
  });

  it('renders key terms in monospace', () => {
    const { container } = render(<RfqStatusCard rfq={approved} />);
    const terms = container.querySelectorAll('.wl-rfq-status__term');
    expect(terms.length).toBeGreaterThan(0);
  });

  it('renders fallback when no rfq', () => {
    render(<RfqStatusCard rfq={null} />);
    expect(screen.getByText(/No submitted RFQ/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test RfqStatusCard`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqStatusCard.tsx`:

```tsx
import type { RFQ } from '../types';
import { Badge, type BadgeVariant } from './Badge';
import { Empty } from './Empty';
import './RfqStatusCard.css';

type Props = {
  rfq: RFQ | null;
};

const statusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  approved: 'pos',
  rejected: 'neg',
  draft: 'ink',
};

function readNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  if (value == null || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function readString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return value == null ? null : String(value);
}

export function RfqStatusCard({ rfq }: Props) {
  if (!rfq) {
    return <Empty message="No submitted RFQ" symbol="◌" />;
  }

  const fieldLabel = readString(rfq.quote_payload, 'field_label')
    ?? readString(rfq.quote_payload, 'field_path')
    ?? 'solved field';
  const solved = readNumber(rfq.quote_payload, 'solved_value');
  const price = readNumber(rfq.quote_payload, 'achieved_price');
  const response = rfq.approved_response ?? readString(rfq.quote_payload, 'client_response') ?? '';

  return (
    <div className="wl-rfq-status">
      <header className="wl-rfq-status__head">
        <span className="wl-rfq-status__id">RFQ #{rfq.id}</span>
        <Badge variant={statusVariant[rfq.status] ?? 'ink'}>{rfq.status}</Badge>
      </header>
      {response && <p className="wl-rfq-status__response">{response}</p>}
      <div className="wl-rfq-status__terms">
        {solved != null && (
          <div className="wl-rfq-status__term">
            <span className="wl-rfq-status__term-label">{fieldLabel}</span>
            <span className="wl-rfq-status__term-value">{solved.toFixed(6)}</span>
          </div>
        )}
        {price != null && (
          <div className="wl-rfq-status__term">
            <span className="wl-rfq-status__term-label">price</span>
            <span className="wl-rfq-status__term-value">{price.toFixed(6)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/RfqStatusCard.css`:

```css
.wl-rfq-status {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--gap-4);
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}
.wl-rfq-status__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--gap-3);
}
.wl-rfq-status__id {
  font-family: var(--font-numeric);
  font-size: var(--type-h3-size);
  font-weight: 700;
  color: var(--ink);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.wl-rfq-status__response {
  margin: 0;
  font-size: var(--type-body-size);
  color: var(--ink);
  line-height: 1.5;
}
.wl-rfq-status__terms {
  display: flex;
  flex-wrap: wrap;
  gap: var(--gap-3);
  padding-top: var(--gap-2);
  border-top: 1px solid var(--paper-3);
}
.wl-rfq-status__term {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wl-rfq-status__term-label {
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
}
.wl-rfq-status__term-value {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  font-weight: 700;
  color: var(--ink);
}
```

- [ ] **Step 4: Run tests, expect 3 PASS**

Run: `npm test RfqStatusCard`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/RfqStatusCard.tsx frontend/src/components/RfqStatusCard.css frontend/src/components/RfqStatusCard.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add RfqStatusCard with bold-monospaced terms"
```

## Task 10: Build ClientRfq presentational route

**Files:**
- Create: `frontend/src/routes/ClientRfq.tsx`
- Create: `frontend/src/routes/ClientRfq.css`

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/ClientRfq.tsx`:

```tsx
import type { RFQ } from '../types';
import { PageHeader } from '../components/PageHeader';
import { RfqIntakeCard, type StructuredForm } from '../components/RfqIntakeCard';
import { RfqStatusCard } from '../components/RfqStatusCard';
import './ClientRfq.css';

type Props = {
  latest: RFQ | null;
  defaultMessage: string;
  onSubmitNL: (message: string) => void;
  onSubmitStructured: (form: StructuredForm) => void;
};

export function ClientRfq({ latest, defaultMessage, onSubmitNL, onSubmitStructured }: Props) {
  return (
    <>
      <PageHeader
        title="CLIENT RFQ"
        chips={latest ? [`RFQ #${latest.id}`, latest.status] : ['No submission yet']}
      />
      <div className="wl-client-rfq">
        <RfqIntakeCard
          defaultMessage={defaultMessage}
          onSubmitNL={onSubmitNL}
          onSubmitStructured={onSubmitStructured}
        />
        <RfqStatusCard rfq={latest} />
      </div>
    </>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/ClientRfq.css`:

```css
.wl-client-rfq {
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
  max-width: 720px;
  margin: 0 auto;
}
```

- [ ] **Step 2: Typecheck**

Run from `frontend/`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/ClientRfq.tsx frontend/src/routes/ClientRfq.css
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ClientRfq centered intake route"
```

## Task 11: Build ClientRfq live container

**Files:**
- Create: `frontend/src/routes/ClientRfq.live.tsx`

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/ClientRfq.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { RFQ } from '../types';
import { ClientRfq } from './ClientRfq';
import type { StructuredForm } from '../components/RfqIntakeCard';

const LATEST_KEY = 'openOtc.latestClientRfqId';
const DEFAULT_MESSAGE = 'Can you quote a one year CSI500 snowball solving KO rate for target premium 10?';

export function ClientRfqLive() {
  const [latest, setLatest] = useState<RFQ | null>(null);

  const remember = (rfq: RFQ) => {
    setLatest(rfq);
    localStorage.setItem(LATEST_KEY, String(rfq.id));
  };

  const refreshLatest = async (id: number) => {
    try {
      const rfq = await api<RFQ>(`/api/client/rfq/${id}`);
      remember(rfq);
    } catch {
      // Ignore — submission will refresh next time
    }
  };

  useEffect(() => {
    const stored = localStorage.getItem(LATEST_KEY);
    if (stored) void refreshLatest(Number(stored));
  }, []);

  const handleSubmitNL = async (message: string) => {
    const rfq = await api<RFQ>('/api/client/rfq/chat', {
      method: 'POST',
      body: JSON.stringify({ client_name: 'Demo Client', message }),
    });
    remember(rfq);
  };

  const handleSubmitStructured = async (form: StructuredForm) => {
    const rfq = await api<RFQ>('/api/client/rfq/form', {
      method: 'POST',
      body: JSON.stringify({
        client_name: 'Structured Demo Client',
        underlying: 'CSI500',
        product_type: form.product,
        product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
        engine_spec: { engine_name: 'BlackScholesEngine' },
        unknown: { field_path: form.unknown, lower_bound: 50, upper_bound: 150, initial_guess: 100 },
        target: { label: 'price', value: Number(form.target) },
      }),
    });
    remember(rfq);
  };

  return (
    <ClientRfq
      latest={latest}
      defaultMessage={DEFAULT_MESSAGE}
      onSubmitNL={handleSubmitNL}
      onSubmitStructured={handleSubmitStructured}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/ClientRfq.live.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): wire ClientRfq to live API"
```

## Task 12: Wire ClientRfq into main.tsx

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Add the import**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/main.tsx`. Add this import alongside the other route imports (after `import { RfqApprovalLive } from './routes/RfqApproval.live';` from Task 7):

```ts
import { ClientRfqLive } from './routes/ClientRfq.live';
```

- [ ] **Step 2: Replace the placeholder render**

Find this line:

```tsx
{route === 'client'    && <PlaceholderRoute title="Client RFQ" />}
```

Replace with:

```tsx
{route === 'client'    && <ClientRfqLive />}
```

- [ ] **Step 3: Verify**

Run from `frontend/`: `npx tsc -b --noEmit`
Expected: 0 errors.

Run: `timeout 6 npm run dev 2>&1 | head -10`
Expected: VITE ready, no errors.

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/main.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): replace placeholder with ClientRfqLive"
```

---

# Phase C · Smoke Test & Documentation

## Task 13: Final automated smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all automated checks**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend && npm test 2>&1 | tail -10
cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc -b --noEmit; echo "exit=$?"
cd /Users/fuxinyao/open-otc-trading/frontend && timeout 6 npm run dev 2>&1 | head -15
cd /Users/fuxinyao/open-otc-trading/frontend && npm run build 2>&1 | tail -20
```

Expected: all tests pass, typecheck exit=0, VITE ready, build succeeds.

- [ ] **Step 2: Update README**

Open `/Users/fuxinyao/open-otc-trading/README.md`. Find the `## UI/UX redesign (in progress)` section added by the foundation plan. Update the bullet list under "Follow-up plans migrate the remaining routes:" to mark Plan 2 done:

Replace:

```markdown
Follow-up plans migrate the remaining routes:

- Client RFQ + RFQ Approval
- Risk
- Reports + Agent Desk
- accessibility audit + prefers-reduced-motion verification
```

With:

```markdown
Follow-up plans migrate the remaining routes:

- ✅ Plan 2 — Client RFQ + RFQ Approval (`docs/superpowers/plans/2026-05-07-rfq-routes-warm-ledger.md`)
- Plan 3 — Risk
- Plan 4 — Reports + Agent Desk
- Plan 5 — accessibility audit + prefers-reduced-motion verification
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add README.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: note Plan 2 RFQ routes migration shipped"
```

- [ ] **Step 4: Hand off browser smoke checklist**

For the user to verify in a browser at `http://localhost:5173`:

- RFQ Approval tab opens, tri-column layout (inbox / detail / audit) renders correctly
- Clicking an inbox row updates the detail panel
- Approve button on a `pending_approval` RFQ calls the backend and refreshes the list
- Reject button opens the modal; empty reason disables Confirm; non-empty enables it; submit calls backend with the comment
- Audit pane shows derived events for the selected RFQ
- Client RFQ tab opens centered (max-width 720px), no floating agent pip on this route
- Natural Language tab pre-fills the default message; submit creates a new RFQ
- Structured tab shows Product / Unknown field / Target price; submit creates a new RFQ
- Status card below shows the latest submission's id, status badge, response, and bold-monospaced terms (solved field + price)

---

# Self-Review Notes

(Performed after writing the plan; fixes applied inline.)

**Spec coverage:**
- Client RFQ centered intake card with NL+Structured tabs (Tasks 8, 10) ✓
- Status card with bold-monospaced terms (Task 9) ✓
- No floating agent pip on Client RFQ (already enforced in main.tsx by `showAgent = route !== 'client'` from foundation Task 31) ✓
- Tri-column inbox / detail / audit (Task 5) ✓
- Approve & Send + Reject… buttons (Task 2) ✓
- Reject opens irreversible-confirm modal with required reason (Task 4) ✓

**Type consistency:**
- `StructuredForm` defined in `RfqIntakeCard.tsx` (Task 8), imported by `ClientRfq.tsx` (Task 10) and `ClientRfq.live.tsx` (Task 11) ✓
- `RFQ` type from `../types` is used throughout ✓
- `BadgeVariant` imported consistently (Tasks 1, 2, 9) ✓
- `RfqApprovalLive`, `ClientRfqLive` exported names match imports in main.tsx wiring (Tasks 7, 12) ✓

**Placeholder scan:** No "TBD", "TODO", "implement later", "similar to Task N", or "appropriate error handling" placeholders. Every code-bearing step has full code.

**Backend reality check:**
- Endpoints used (`/api/internal/rfqs`, `/api/internal/rfq/{id}/approve|reject`, `/api/client/rfq/{form,chat,{id}}`) are confirmed to exist in `backend/app/main.py`.
- `RFQApprovalDecision` body shape `{approver, comment, response_override}` matches Pydantic schema.
- Audit pane derives from RFQ object only — no missing endpoint dependency.

**Out of scope (explicit, deferred):**
- Audit-event listing endpoint and UI
- Snowball / Phoenix product types in structured form
- Floating-agent integration (still placeholder text from foundation Task 31)
- Toast notifications on approve/reject success — left to a polish pass

# Follow-up plans (after this lands)

- **Plan 3:** Migrate Risk route (dashboard grid, scenario matrix)
- **Plan 4:** Migrate Reports + Agent Desk routes (timeline, chat, asset pane)
- **Plan 5:** Accessibility audit, prefers-reduced-motion, dark-mode QA, audit-event endpoint, Berkeley Mono procurement
