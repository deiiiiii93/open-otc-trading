# Client RFQ Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Client RFQ page as a three-column workbench (My RFQs | Product Terms | Quote & Submit) with catalog-driven solve fields, an RFQ history list, and NL/Structured mode tabs — per `docs/superpowers/specs/2026-06-06-client-rfq-workbench-design.md`.

**Architecture:** Two small backend additions (catalog `unknown_field_specs`, `GET /api/client/rfqs`), then a frontend rebuild: `ClientRfq.tsx` becomes a props-driven presentational workbench (mirroring `TrySolve.tsx`), `ClientRfq.live.tsx` owns fetching/polling/submission. `RfqIntakeCard` and `RfqStatusCard` are retired. Submission payloads are unchanged.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + Radix Tabs + vitest/RTL (frontend).

**Working directory:** `/Users/fuxinyao/open-otc-trading/.claude/worktrees/client-rfq-workbench` (all commands run from here unless noted). Frontend commands run from `frontend/`.

**Test commands:**
- Backend: `PYTHONPATH=backend python -m pytest tests/test_api.py -k <expr> -q` (PYTHONPATH guard: the venv `.pth` otherwise resolves `app` from the MAIN checkout, not this worktree)
- Frontend: `cd frontend && npx vitest run <path>`
- Typecheck: `cd frontend && npx tsc -b` (vitest does NOT typecheck)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/rfq.py` | Modify | `_UNKNOWN_FIELD_SPEC_DEFAULTS` + `unknown_field_specs` on catalog templates |
| `backend/app/main.py` | Modify | `GET /api/client/rfqs` list endpoint |
| `tests/test_api.py` | Modify | Tests for both backend changes |
| `frontend/src/types.ts` | Modify | `RfqUnknownFieldSpec`; extend `RfqTemplate` |
| `frontend/src/lib/rfqStatus.ts` | Create | Shared status→BadgeVariant mapping (was private in RfqStatusCard) |
| `frontend/src/components/ProductTermsForm.tsx` | Modify | Field specs for OneTouch/RangeAccrual/Futures/Spot/KO-Reset-nested |
| `frontend/src/components/ProductTermsForm.test.tsx` | Modify | Coverage for new product entries |
| `frontend/src/components/RfqHistoryPanel.tsx` + `.css` | Create | History list rows + Clone action |
| `frontend/src/components/RfqHistoryPanel.test.tsx` | Create | History panel tests |
| `frontend/src/routes/ClientRfq.tsx` | Rewrite | Presentational workbench (tabs, 3 panels, form state, page context) |
| `frontend/src/routes/ClientRfq.css` | Rewrite | Workbench grid + field styles |
| `frontend/src/routes/ClientRfq.test.tsx` | Create | Workbench behavior tests |
| `frontend/src/routes/ClientRfq.live.tsx` | Rewrite | Fetch catalog/instruments/rfqs, poll, submit, client-name persistence |
| `frontend/src/routes/ClientRfq.live.test.tsx` | Create | Live wrapper tests (mocked fetch) |
| `frontend/src/main.tsx` | Modify | Pass `onPageContextChange` to `ClientRfqLive` |
| `frontend/src/components/RfqIntakeCard.{tsx,css,test.tsx}` | Delete | Replaced by workbench |
| `frontend/src/components/RfqStatusCard.{tsx,css,test.tsx}` | Delete | Absorbed into right-panel status detail |

One deliberate deviation from the spec sketch: the **Client Name** input lives at the top of the **My RFQs panel** (not the Terms panel) because it must be reachable in NL mode too (the Terms panel is hidden there), and it is the identity that filters the history list.

---

### Task 1: Backend — `unknown_field_specs` on catalog templates

**Files:**
- Modify: `backend/app/services/rfq.py` (after `COMMON_TEMPLATES`, ~line 267; and inside `get_rfq_catalog`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_api.py`:

```python
def test_rfq_catalog_templates_carry_unknown_field_specs(tmp_path: Path):
    client = make_client(tmp_path)

    catalog = client.get("/api/rfq/catalog").json()
    assert catalog["templates"], "catalog should have templates"
    for template in catalog["templates"]:
        specs = template["unknown_field_specs"]
        assert [spec["field_path"] for spec in specs] == template["unknown_fields"]
        for spec in specs:
            assert spec["label"]
            assert spec["lower_bound"] < spec["upper_bound"]
            assert spec["lower_bound"] <= spec["initial_guess"] <= spec["upper_bound"]

    snowball = next(t for t in catalog["templates"] if t["key"] == "snowball")
    assert snowball["unknown_field_specs"] == [
        {
            "field_path": "barrier_config.ko_rate",
            "label": "KO Rate",
            "lower_bound": -1.0,
            "upper_bound": 2.0,
            "initial_guess": 0.15,
        }
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend python -m pytest tests/test_api.py::test_rfq_catalog_templates_carry_unknown_field_specs -q`
Expected: FAIL with `KeyError: 'unknown_field_specs'`

- [ ] **Step 3: Implement** — in `backend/app/services/rfq.py`, add after the `COMMON_TEMPLATES` list:

```python
# Seed solve-field metadata for the client intake UI: per field-path label and
# default solver bounds in the same value convention as the template kwargs
# (absolute 100-scale levels for strike/barrier-like fields, decimals for rates).
_UNKNOWN_FIELD_SPEC_DEFAULTS: dict[str, dict[str, Any]] = {
    "strike": {"label": "Strike", "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 100.0},
    "volatility": {"label": "Volatility", "lower_bound": 0.01, "upper_bound": 2.0, "initial_guess": 0.2},
    "cash_payoff": {"label": "Cash Payoff", "lower_bound": 0.0, "upper_bound": 100.0, "initial_guess": 10.0},
    "barrier": {"label": "Barrier", "lower_bound": 50.0, "upper_bound": 200.0, "initial_guess": 120.0},
    "rebate": {"label": "Rebate", "lower_bound": 0.0, "upper_bound": 50.0, "initial_guess": 0.0},
    "coupon_rate": {"label": "Coupon Rate", "lower_bound": -1.0, "upper_bound": 2.0, "initial_guess": 0.15},
    "lower_barrier": {"label": "Lower Barrier", "lower_bound": 50.0, "upper_bound": 150.0, "initial_guess": 80.0},
    "upper_barrier": {"label": "Upper Barrier", "lower_bound": 100.0, "upper_bound": 200.0, "initial_guess": 120.0},
    "participation_rate": {"label": "Participation Rate", "lower_bound": 0.0, "upper_bound": 5.0, "initial_guess": 1.0},
    "forward_price": {"label": "Forward Price", "lower_bound": 1.0, "upper_bound": 10000.0, "initial_guess": 100.0},
    "barrier_config.ko_rate": {"label": "KO Rate", "lower_bound": -1.0, "upper_bound": 2.0, "initial_guess": 0.15},
    "coupon_config.coupon_rate": {"label": "Coupon Rate", "lower_bound": -1.0, "upper_bound": 2.0, "initial_guess": 0.15},
}


def _unknown_field_spec(field_path: str) -> dict[str, Any]:
    defaults = _UNKNOWN_FIELD_SPEC_DEFAULTS.get(field_path)
    if defaults is None:
        tail = field_path.rsplit(".", 1)[-1]
        defaults = {
            "label": tail.replace("_", " ").title(),
            "lower_bound": 0.0,
            "upper_bound": 200.0,
            "initial_guess": 100.0,
        }
    return {"field_path": field_path, **defaults}
```

Then in `get_rfq_catalog()`, replace the line `"templates": COMMON_TEMPLATES,` with:

```python
        "templates": [
            {
                **template,
                "unknown_field_specs": [
                    _unknown_field_spec(path) for path in template["unknown_fields"]
                ],
            }
            for template in COMMON_TEMPLATES
        ],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=backend python -m pytest tests/test_api.py::test_rfq_catalog_templates_carry_unknown_field_specs tests/test_api.py::test_rfq_catalog_and_nl_draft_missing_fields -q`
Expected: 2 passed (the existing catalog test must keep passing)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rfq.py tests/test_api.py
git commit -m "feat(rfq): catalog templates carry unknown_field_specs with solver bounds"
```

---

### Task 2: Backend — `GET /api/client/rfqs`

**Files:**
- Modify: `backend/app/main.py` (immediately after `get_client_rfq`, ~line 1435)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_api.py`:

```python
def _post_vanilla_client_rfq(client, client_name: str) -> dict:
    response = client.post(
        "/api/client/rfq/form",
        json={
            "client_name": client_name,
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.05,
                "dividend_yield": 0.02,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": "strike",
                "lower_bound": 50,
                "upper_bound": 150,
                "initial_guess": 100,
            },
            "target": {"label": "price", "value": 10},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_client_rfqs_list_orders_filters_and_limits(tmp_path: Path):
    client = make_client(tmp_path)

    first = _post_vanilla_client_rfq(client, "Client A")
    second = _post_vanilla_client_rfq(client, "Client B")
    third = _post_vanilla_client_rfq(client, "Client A")

    listed = client.get("/api/client/rfqs")
    assert listed.status_code == 200
    assert [rfq["id"] for rfq in listed.json()] == [third["id"], second["id"], first["id"]]
    assert "quote_versions" in listed.json()[0]

    filtered = client.get("/api/client/rfqs", params={"client_name": "Client A"})
    assert [rfq["id"] for rfq in filtered.json()] == [third["id"], first["id"]]

    limited = client.get("/api/client/rfqs", params={"limit": 1})
    assert [rfq["id"] for rfq in limited.json()] == [third["id"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend python -m pytest tests/test_api.py::test_client_rfqs_list_orders_filters_and_limits -q`
Expected: FAIL — `GET /api/client/rfqs` returns 404 (route resolves to `get_client_rfq` with `rfq_id="rfqs"` → 422, or 404; either way not 200)

- [ ] **Step 3: Implement** — in `backend/app/main.py`, insert right after the `get_client_rfq` endpoint (`Query`, `RFQ`, `RFQOut`, `selectinload` are already imported):

```python
    @app.get("/api/client/rfqs", response_model=list[RFQOut])
    def list_client_rfqs(
        client_name: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_db),
    ):
        query = session.query(RFQ).options(selectinload(RFQ.quote_versions))
        if client_name:
            query = query.filter(RFQ.client_name == client_name)
        return (
            query.order_by(RFQ.created_at.desc(), RFQ.id.desc())
            .limit(limit)
            .all()
        )
```

Note the `RFQ.id.desc()` tie-breaker: rows created within the same second would otherwise have unstable order.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=backend python -m pytest tests/test_api.py::test_client_rfqs_list_orders_filters_and_limits tests/test_api.py::test_client_rfq_form_and_approval -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(rfq): GET /api/client/rfqs lists recent client RFQs (filter + limit)"
```

---

### Task 3: Frontend foundations — types, status lib, ProductTermsForm coverage

**Files:**
- Modify: `frontend/src/types.ts` (RfqTemplate, ~line 299)
- Create: `frontend/src/lib/rfqStatus.ts`
- Modify: `frontend/src/components/ProductTermsForm.tsx` (`PRODUCT_TERM_FIELDS`, `NUMERIC_KEYS`)
- Test: `frontend/src/components/ProductTermsForm.test.tsx`

- [ ] **Step 1: Write the failing test** — append to `frontend/src/components/ProductTermsForm.test.tsx` (match the existing test style in that file — it renders `<ProductTermsForm productType=... productKwargs=... onChange=... />`):

```tsx
describe('intake product coverage', () => {
  it('renders OneTouchOption with a touch type select', () => {
    render(
      <ProductTermsForm
        productType="OneTouchOption"
        productKwargs={{ barrier: 120, cash_payoff: 10, touch_type: 'UP_TOUCH', maturity: 1 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Barrier')).toHaveValue(120);
    expect(screen.getByLabelText('Cash Payoff')).toHaveValue(10);
    expect(screen.getByLabelText('Touch Type')).toHaveValue('UP_TOUCH');
  });

  it('renders RangeAccrualOption with an option type select', () => {
    render(
      <ProductTermsForm
        productType="RangeAccrualOption"
        productKwargs={{ strike: 100, option_type: 'CALL', maturity: 1 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Strike')).toHaveValue(100);
    expect(screen.getByLabelText('Option Type')).toHaveValue('CALL');
  });

  it('renders Futures maturity as a primary field, not an extra', () => {
    render(
      <ProductTermsForm
        productType="Futures"
        productKwargs={{ maturity: 1, contract_multiplier: 1 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Maturity (years)')).toHaveValue(1);
  });

  it('renders nested KnockOutResetSnowballOption like SnowballOption', () => {
    render(
      <ProductTermsForm
        productType="KnockOutResetSnowballOption"
        productKwargs={{ initial_price: 100, strike: 100, barrier_config: {} }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText('Initial Price')).toHaveValue(100);
  });
});
```

Note on `getByLabelText`: `renderField` wraps `<span>{label}</span>` + input in a `<label>`, so label-text queries resolve. If the existing test file queries differently (e.g. `getByRole('spinbutton', { name })`), follow its convention instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ProductTermsForm.test.tsx`
Expected: new tests FAIL — labels like `Touch Type` not found (fields render in "Extra Fields" with title-cased keys instead)

- [ ] **Step 3: Implement.**

In `frontend/src/types.ts`, add above `RfqTemplate` and extend it:

```ts
export type RfqUnknownFieldSpec = {
  field_path: string;
  label: string;
  lower_bound: number;
  upper_bound: number;
  initial_guess: number;
};
```

```ts
export type RfqTemplate = {
  key: string;
  label: string;
  product_type: string;
  engine_spec: Record<string, any>;
  unknown_fields: string[];
  unknown_field_specs?: RfqUnknownFieldSpec[];
  product_kwargs: Record<string, any>;
};
```

Create `frontend/src/lib/rfqStatus.ts` (moved verbatim from `RfqStatusCard.tsx`, which Task 7 deletes):

```ts
import type { BadgeVariant } from '../components/Badge';

export const rfqStatusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  submitted: 'warn',
  pricing_failed: 'neg',
  approved: 'pos',
  released: 'pos',
  client_accepted: 'pos',
  booked: 'pos',
  rejected: 'neg',
  expired: 'neg',
  cancelled: 'neg',
  draft: 'ink',
};

export function rfqStatusBadge(status: string): BadgeVariant {
  return rfqStatusVariant[status] ?? 'ink';
}
```

In `frontend/src/components/ProductTermsForm.tsx`, add to `PRODUCT_TERM_FIELDS` (after the `AsianOption` entry):

```ts
  OneTouchOption: [
    { key: 'barrier', label: 'Barrier', type: 'number' },
    { key: 'cash_payoff', label: 'Cash Payoff', type: 'number' },
    { key: 'touch_type', label: 'Touch Type', type: 'select', options: ['UP_TOUCH', 'DOWN_TOUCH'] },
  ],
  RangeAccrualOption: [
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
  ],
  Futures: [
    { key: 'maturity', label: 'Maturity (years)', type: 'number' },
  ],
  SpotInstrument: [
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
  ],
  KnockOutResetSnowballOption: [
    { key: 'initial_price', label: 'Initial Price', type: 'number' },
    { key: 'strike', label: 'Strike', type: 'number' },
    { key: 'initial_date', label: 'Initial Date', type: 'date' },
    { key: 'exercise_date', label: 'Exercise Date', type: 'date' },
    { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    { key: 'contract_multiplier', label: 'Contract Multiplier', type: 'number' },
    { key: 'is_reverse', label: 'Is Reverse', type: 'boolean' },
    { key: '_otc_ki_observation_convention', label: 'KI Observation Convention', type: 'select', options: ['DAILY', 'EUROPEAN', 'NONE'] },
    { key: '_otc_lifecycle_knocked_in', label: 'Lifecycle Knocked In', type: 'boolean' },
    { key: '_otc_lifecycle_state', label: 'Lifecycle State', type: 'text' },
  ],
```

Also add `'cash_payoff',` to the `NUMERIC_KEYS` set (alphabetical position, after `'barrier_level'`).

(The flat KO-Reset contract is already handled by `FLAT_CONTRACT_FIELDS`; this nested entry only applies when kwargs are a built termsheet.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ProductTermsForm.test.tsx`
Expected: all PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/lib/rfqStatus.ts frontend/src/components/ProductTermsForm.tsx frontend/src/components/ProductTermsForm.test.tsx
git commit -m "feat(client-rfq): unknown-field-spec types, shared rfq status badges, term-form coverage for remaining catalog products"
```

---

### Task 4: `RfqHistoryPanel` component

**Files:**
- Create: `frontend/src/components/RfqHistoryPanel.tsx`
- Create: `frontend/src/components/RfqHistoryPanel.css`
- Test: `frontend/src/components/RfqHistoryPanel.test.tsx`

- [ ] **Step 1: Write the failing test** — create `frontend/src/components/RfqHistoryPanel.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqHistoryPanel } from './RfqHistoryPanel';
import type { RFQ } from '../types';

const rfqs: RFQ[] = [
  {
    id: 42,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'pending_approval',
    request_payload: { product_type: 'SnowballOption' },
    quote_payload: {},
    created_at: '2026-06-06T08:00:00Z',
  },
  {
    id: 41,
    client_name: 'Demo Client',
    channel: 'chat',
    status: 'approved',
    request_payload: { product_type: 'EuropeanVanillaOption' },
    quote_payload: {},
    created_at: '2026-06-05T08:00:00Z',
  },
];

const labelFor = (productType: string) =>
  productType === 'SnowballOption' ? 'Snowball' : productType === 'EuropeanVanillaOption' ? 'Vanilla' : productType;

describe('RfqHistoryPanel', () => {
  it('renders rows with product label and status, and selects on click', async () => {
    const onSelect = vi.fn();
    render(
      <RfqHistoryPanel rfqs={rfqs} selectedRfqId={42} productLabelFor={labelFor} onSelect={onSelect} />,
    );

    const list = screen.getByRole('list', { name: /my rfqs/i });
    const row42 = within(list).getByRole('button', { name: /#42 snowball/i });
    expect(row42).toHaveAttribute('aria-pressed', 'true');
    expect(within(list).getByText('pending_approval')).toBeInTheDocument();

    await userEvent.click(within(list).getByRole('button', { name: /#41 vanilla/i }));
    expect(onSelect).toHaveBeenCalledWith(41);
  });

  it('offers Clone only on the selected row', async () => {
    const onClone = vi.fn();
    render(
      <RfqHistoryPanel rfqs={rfqs} selectedRfqId={41} productLabelFor={labelFor} onClone={onClone} />,
    );

    expect(screen.queryByRole('button', { name: /clone rfq 42/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /clone rfq 41/i }));
    expect(onClone).toHaveBeenCalledWith(rfqs[1]);
  });

  it('shows an empty state without rfqs', () => {
    render(<RfqHistoryPanel rfqs={[]} selectedRfqId={null} productLabelFor={labelFor} />);
    expect(screen.getByText(/no rfqs submitted yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/RfqHistoryPanel.test.tsx`
Expected: FAIL — module `./RfqHistoryPanel` not found

- [ ] **Step 3: Implement** — create `frontend/src/components/RfqHistoryPanel.tsx`:

```tsx
import { CopyPlus } from 'lucide-react';
import { Badge } from './Badge';
import { Button } from './Button';
import { rfqStatusBadge } from '../lib/rfqStatus';
import type { RFQ } from '../types';
import './RfqHistoryPanel.css';

type Props = {
  rfqs: RFQ[];
  selectedRfqId: number | null;
  productLabelFor: (productType: string) => string;
  onSelect?: (rfqId: number) => void;
  onClone?: (rfq: RFQ) => void;
};

export function RfqHistoryPanel({ rfqs, selectedRfqId, productLabelFor, onSelect, onClone }: Props) {
  if (!rfqs.length) {
    return <div className="wl-rfq-history__empty">No RFQs submitted yet.</div>;
  }
  return (
    <div className="wl-rfq-history" role="list" aria-label="My RFQs">
      {rfqs.map((rfq) => {
        const selected = rfq.id === selectedRfqId;
        const productType = String(rfq.request_payload?.product_type ?? '');
        return (
          <div
            key={rfq.id}
            className={selected ? 'wl-rfq-history__row wl-rfq-history__row--active' : 'wl-rfq-history__row'}
          >
            <button
              type="button"
              className="wl-rfq-history__select"
              aria-pressed={selected}
              onClick={() => onSelect?.(rfq.id)}
            >
              <span className="wl-rfq-history__main">
                <span className="wl-rfq-history__id">#{rfq.id}</span>
                <span className="wl-rfq-history__product">{productLabelFor(productType)}</span>
              </span>
              <Badge variant={rfqStatusBadge(rfq.status)}>{rfq.status}</Badge>
              <span className="wl-rfq-history__meta">
                {formatCreatedAt(rfq.created_at)} · {rfq.channel}
              </span>
            </button>
            {selected && onClone ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => onClone(rfq)}
                aria-label={`Clone RFQ ${rfq.id} into editor`}
              >
                <CopyPlus size={14} aria-hidden="true" />
                Clone
              </Button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function formatCreatedAt(value: string | undefined): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit' })
    + ' '
    + parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
}
```

Create `frontend/src/components/RfqHistoryPanel.css`:

```css
.wl-rfq-history {
  display: flex;
  flex-direction: column;
  gap: var(--gap-1);
  max-height: 420px;
  overflow-y: auto;
}

.wl-rfq-history__row {
  display: flex;
  align-items: center;
  gap: var(--gap-1);
  border: 1px solid var(--line, #2a2f3a);
  border-radius: 8px;
}

.wl-rfq-history__row--active {
  border-color: var(--accent, #6ea8fe);
  background: color-mix(in srgb, var(--accent, #6ea8fe) 8%, transparent);
}

.wl-rfq-history__select {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px var(--gap-1);
  align-items: center;
  padding: 8px 10px;
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.wl-rfq-history__main {
  display: flex;
  gap: var(--gap-1);
  align-items: baseline;
  min-width: 0;
}

.wl-rfq-history__id {
  font-weight: 600;
}

.wl-rfq-history__product {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wl-rfq-history__meta {
  grid-column: 1 / -1;
  font-size: 11px;
  opacity: 0.65;
}

.wl-rfq-history__empty {
  padding: 12px 4px;
  opacity: 0.7;
}
```

(If `lucide-react` has no `CopyPlus` export in the installed version, use `Copy` instead — verify with the test run.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/RfqHistoryPanel.test.tsx`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RfqHistoryPanel.tsx frontend/src/components/RfqHistoryPanel.css frontend/src/components/RfqHistoryPanel.test.tsx
git commit -m "feat(client-rfq): RfqHistoryPanel with selection and clone action"
```

---

### Task 5: `ClientRfq.tsx` — presentational workbench rebuild

**Files:**
- Rewrite: `frontend/src/routes/ClientRfq.tsx`
- Rewrite: `frontend/src/routes/ClientRfq.css`
- Test: `frontend/src/routes/ClientRfq.test.tsx` (new)

- [ ] **Step 1: Write the failing tests** — create `frontend/src/routes/ClientRfq.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClientRfq } from './ClientRfq';
import type { RFQ, RfqCatalog, Underlying } from '../types';

const catalog: RfqCatalog = {
  product_types: [],
  engine_options: [],
  unknown_fields: {
    EuropeanVanillaOption: ['strike', 'volatility'],
    SnowballOption: ['barrier_config.ko_rate'],
  },
  templates: [
    {
      key: 'vanilla',
      label: 'Vanilla',
      product_type: 'EuropeanVanillaOption',
      engine_spec: { engine_name: 'BlackScholesEngine' },
      unknown_fields: ['strike', 'volatility'],
      unknown_field_specs: [
        { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
        { field_path: 'volatility', label: 'Volatility', lower_bound: 0.01, upper_bound: 2, initial_guess: 0.2 },
      ],
      product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
    },
    {
      key: 'snowball',
      label: 'Snowball',
      product_type: 'SnowballOption',
      engine_spec: { engine_name: 'SnowballQuadEngine' },
      unknown_fields: ['barrier_config.ko_rate'],
      unknown_field_specs: [
        { field_path: 'barrier_config.ko_rate', label: 'KO Rate', lower_bound: -1, upper_bound: 2, initial_guess: 0.15 },
      ],
      product_kwargs: {
        initial_price: 100,
        strike: 100,
        maturity_years: 1,
        ko_barrier_pct: 103,
        ki_barrier_pct: 75,
        ko_rate: 0.15,
        lockup_months: 3,
        trade_start_date: '2026-06-13',
        observation_frequency: 'MONTHLY',
        contract_multiplier: 1,
      },
    },
  ],
  advanced: {},
};

const underlyings = [
  { symbol: '000852.SH', display_name: 'CSI 1000', status: 'active' },
  { symbol: 'OLD.SH', display_name: 'Retired', status: 'inactive' },
] as unknown as Underlying[];

const rfqs: RFQ[] = [
  {
    id: 42,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'pending_approval',
    request_payload: {
      product_type: 'SnowballOption',
      underlying: '000852.SH',
      side: 'sell',
      quantity: 2,
      quote_mode: 'solve',
      product_kwargs: {
        initial_price: 100,
        strike: 100,
        maturity_years: 1,
        ko_barrier_pct: 103,
        ki_barrier_pct: 75,
        ko_rate: 0.15,
        lockup_months: 3,
        trade_start_date: '2026-06-13',
        observation_frequency: 'MONTHLY',
        contract_multiplier: 1,
      },
      engine_spec: { engine_name: 'SnowballQuadEngine' },
      unknown: { field_path: 'barrier_config.ko_rate', lower_bound: -1, upper_bound: 2, initial_guess: 0.15 },
      target: { label: 'premium', value: 10 },
    },
    quote_payload: {},
    created_at: '2026-06-06T08:00:00Z',
  },
  {
    id: 41,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'approved',
    request_payload: { product_type: 'EuropeanVanillaOption', underlying: '000852.SH', side: 'buy', quantity: 1 },
    quote_payload: {
      field_label: 'strike',
      solved_value: 104.2,
      achieved_price: 10.0001,
      client_response: 'Quote ready for review',
    },
    approved_response: 'Solved strike 104.2 at price 10.0001',
    created_at: '2026-06-05T08:00:00Z',
  },
];

function renderPage(overrides: Partial<Parameters<typeof ClientRfq>[0]> = {}) {
  return render(
    <ClientRfq
      catalog={catalog}
      underlyings={underlyings}
      rfqs={rfqs}
      clientName="Demo Client"
      defaultMessage="Quote me a snowball"
      {...overrides}
    />,
  );
}

describe('ClientRfq workbench', () => {
  it('renders three panels with the structured editor by default', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'CLIENT RFQ' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /my rfqs/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Product')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /submit rfq/i })).toBeInTheDocument();
  });

  it('swaps editor panels for the message panel in NL mode', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /natural language/i }));
    expect(screen.getByLabelText('Message')).toHaveValue('Quote me a snowball');
    expect(screen.queryByLabelText('Product')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: /my rfqs/i })).toBeInTheDocument();
  });

  it('submits the NL message', async () => {
    const onSubmitNL = vi.fn();
    renderPage({ onSubmitNL });
    await userEvent.click(screen.getByRole('tab', { name: /natural language/i }));
    await userEvent.click(screen.getByRole('button', { name: /submit natural language/i }));
    expect(onSubmitNL).toHaveBeenCalledWith('Quote me a snowball');
  });

  it('only lists active underlyings', () => {
    renderPage();
    const select = screen.getByLabelText('Underlying');
    expect(select).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /000852\.SH/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /OLD\.SH/ })).not.toBeInTheDocument();
  });

  it('switching product repopulates terms and solve-for defaults', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Product'), 'SnowballOption');
    expect(screen.getByLabelText('KO Barrier %')).toHaveValue(103);
    expect(screen.getByLabelText('Solve For')).toHaveDisplayValue('KO Rate');
    expect(screen.getByLabelText('Lower Bound')).toHaveValue(-1);
    expect(screen.getByLabelText('Upper Bound')).toHaveValue(2);
    expect(screen.getByLabelText('Initial Guess')).toHaveValue(0.15);
  });

  it('changing the solve-for field prefills its bounds', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Solve For'), 'volatility');
    expect(screen.getByLabelText('Lower Bound')).toHaveValue(0.01);
    expect(screen.getByLabelText('Upper Bound')).toHaveValue(2);
    expect(screen.getByLabelText('Initial Guess')).toHaveValue(0.2);
  });

  it('price mode hides the solve controls', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Quote Mode'), 'price');
    expect(screen.queryByLabelText('Solve For')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Target Value')).not.toBeInTheDocument();
  });

  it('gates submit until an underlying is chosen, then submits the form', async () => {
    const onSubmitStructured = vi.fn();
    renderPage({ onSubmitStructured });
    const submit = screen.getByRole('button', { name: /submit rfq/i });
    expect(submit).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    expect(submit).toBeEnabled();

    await userEvent.click(submit);
    expect(onSubmitStructured).toHaveBeenCalledTimes(1);
    const form = onSubmitStructured.mock.calls[0][0];
    expect(form.product).toBe('EuropeanVanillaOption');
    expect(form.underlying).toBe('000852.SH');
    expect(form.unknownField).toBe('strike');
    expect(form.quoteMode).toBe('solve');
  });

  it('gates submit when a required snowball contract term is blanked', async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.selectOptions(screen.getByLabelText('Product'), 'SnowballOption');
    const submit = screen.getByRole('button', { name: /submit rfq/i });
    expect(submit).toBeEnabled();

    await userEvent.clear(screen.getByLabelText('KI Barrier %'));
    expect(submit).toBeDisabled();
  });

  it('shows status detail for the selected RFQ', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /#41 vanilla/i }));
    expect(screen.getByText('Solved strike 104.2 at price 10.0001')).toBeInTheDocument();
    expect(screen.getByText('104.200000')).toBeInTheDocument();
  });

  it('clones a history RFQ into the editor', async () => {
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /#42 snowball/i }));
    await userEvent.click(screen.getByRole('button', { name: /clone rfq 42/i }));
    expect(screen.getByLabelText('Product')).toHaveValue('SnowballOption');
    expect(screen.getByLabelText('Underlying')).toHaveValue('000852.SH');
    expect(screen.getByLabelText('Side')).toHaveValue('sell');
    expect(screen.getByLabelText('Notional')).toHaveValue(2);
    expect(screen.getByLabelText('Target Label')).toHaveValue('premium');
    expect(screen.getByLabelText('Target Value')).toHaveValue(10);
  });

  it('reports page context with declared actions', () => {
    const onPageContextChange = vi.fn();
    renderPage({ onPageContextChange });
    const context = onPageContextChange.mock.calls.at(-1)?.[0];
    expect(context.route).toBe('client-rfq');
    expect(context.actions.map((a: { name: string }) => a.name)).toEqual([
      'submit_structured_rfq',
      'submit_nl_rfq',
    ]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ClientRfq.test.tsx`
Expected: FAIL — `ClientRfq` has no such props/markup yet

- [ ] **Step 3: Implement** — rewrite `frontend/src/routes/ClientRfq.tsx` entirely:

```tsx
import { useMemo, useState } from 'react';
import { Send } from 'lucide-react';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { ProductTermsForm } from '../components/ProductTermsForm';
import { RfqHistoryPanel } from '../components/RfqHistoryPanel';
import { rfqStatusBadge } from '../lib/rfqStatus';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type {
  PageContext,
  PageContextReporter,
  RFQ,
  RfqCatalog,
  RfqTemplate,
  RfqUnknownFieldSpec,
  Underlying,
} from '../types';
import './ClientRfq.css';

export type ClientRfqForm = {
  underlying: string;
  side: 'buy' | 'sell';
  notional: number | null;
  quoteMode: 'solve' | 'price';
  product: string;
  productTerms: Record<string, unknown>;
  engineSpec: Record<string, unknown>;
  unknownField: string;
  lowerBound: number | null;
  upperBound: number | null;
  initialGuess: number | null;
  targetLabel: 'price' | 'premium' | 'reoffer';
  targetValue: number | null;
};

type Props = {
  catalog?: RfqCatalog | null;
  underlyings?: Underlying[];
  rfqs?: RFQ[];
  selectedRfqId?: number | null;
  clientName?: string;
  defaultMessage?: string;
  loading?: boolean;
  submitting?: boolean;
  error?: string | null;
  feedback?: string | null;
  onClientNameChange?: (name: string) => void;
  onSelectRfq?: (rfqId: number) => void;
  onSubmitNL?: (message: string) => Promise<void> | void;
  onSubmitStructured?: (form: ClientRfqForm) => Promise<void> | void;
  onPageContextChange?: PageContextReporter;
};

const FALLBACK_TEMPLATE: RfqTemplate = {
  key: 'vanilla',
  label: 'Vanilla',
  product_type: 'EuropeanVanillaOption',
  engine_spec: { engine_name: 'BlackScholesEngine' },
  unknown_fields: ['strike', 'volatility'],
  unknown_field_specs: [
    { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
    { field_path: 'volatility', label: 'Volatility', lower_bound: 0.01, upper_bound: 2, initial_guess: 0.2 },
  ],
  product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
};

export function ClientRfq({
  catalog,
  underlyings = [],
  rfqs = [],
  selectedRfqId,
  clientName = 'Demo Client',
  defaultMessage = '',
  loading = false,
  submitting = false,
  error = null,
  feedback = null,
  onClientNameChange,
  onSelectRfq,
  onSubmitNL,
  onSubmitStructured,
  onPageContextChange,
}: Props) {
  const templates = catalog?.templates?.length ? catalog.templates : [FALLBACK_TEMPLATE];
  const [mode, setMode] = useState<'nl' | 'structured'>('structured');
  const [message, setMessage] = useState(defaultMessage);
  const [form, setForm] = useState<ClientRfqForm>(() => formFromTemplate(templates[0]));
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null);

  const effectiveSelectedId = selectedRfqId !== undefined ? selectedRfqId : internalSelectedId;
  const selectedRfq = rfqs.find((rfq) => rfq.id === effectiveSelectedId) ?? rfqs[0] ?? null;

  const template = templates.find((item) => item.product_type === form.product) ?? FALLBACK_TEMPLATE;
  const specs = templateSpecs(template);

  const productLabelFor = (productType: string): string =>
    templates.find((item) => item.product_type === productType)?.label ?? (productType || 'Unknown');

  const handleSelect = (rfqId: number) => {
    if (selectedRfqId === undefined) setInternalSelectedId(rfqId);
    onSelectRfq?.(rfqId);
  };

  const switchProduct = (productType: string) => {
    const nextTemplate = templates.find((item) => item.product_type === productType) ?? FALLBACK_TEMPLATE;
    const next = formFromTemplate(nextTemplate);
    setForm({
      ...next,
      underlying: form.underlying,
      side: form.side,
      notional: form.notional,
      quoteMode: form.quoteMode,
      targetLabel: form.targetLabel,
      targetValue: form.targetValue,
    });
  };

  const selectUnknownField = (fieldPath: string) => {
    const spec = specs.find((item) => item.field_path === fieldPath);
    setForm({
      ...form,
      unknownField: fieldPath,
      lowerBound: spec?.lower_bound ?? form.lowerBound,
      upperBound: spec?.upper_bound ?? form.upperBound,
      initialGuess: spec?.initial_guess ?? form.initialGuess,
    });
  };

  const cloneRfq = (rfq: RFQ) => {
    const payload = rfq.request_payload ?? {};
    const unknown = isRecord(payload.unknown) ? payload.unknown : {};
    const target = isRecord(payload.target) ? payload.target : {};
    const productType = String(payload.product_type ?? form.product);
    const sourceTemplate = templates.find((item) => item.product_type === productType) ?? null;
    setMode('structured');
    setForm({
      underlying: String(payload.underlying ?? ''),
      side: payload.side === 'sell' ? 'sell' : 'buy',
      notional: numberOrNull(payload.quantity) ?? 1,
      quoteMode: payload.quote_mode === 'price' ? 'price' : 'solve',
      product: productType,
      productTerms: isRecord(payload.product_kwargs)
        ? cloneRecord(payload.product_kwargs)
        : cloneRecord(sourceTemplate?.product_kwargs ?? {}),
      engineSpec: isRecord(payload.engine_spec)
        ? cloneRecord(payload.engine_spec)
        : cloneRecord(sourceTemplate?.engine_spec ?? {}),
      unknownField: String(unknown.field_path ?? ''),
      lowerBound: numberOrNull(unknown.lower_bound),
      upperBound: numberOrNull(unknown.upper_bound),
      initialGuess: numberOrNull(unknown.initial_guess),
      targetLabel: target.label === 'premium' || target.label === 'reoffer' ? target.label : 'price',
      targetValue: numberOrNull(target.value) ?? 0,
    });
  };

  const contractComplete = isContractComplete(form.product, form.productTerms, form.quoteMode, form.unknownField);
  const hasUnderlying = form.underlying.trim() !== '';
  const notionalValid = form.notional != null && form.notional > 0;
  const solveValid = form.quoteMode === 'price' || form.unknownField !== '';
  const canSubmit = !submitting && hasUnderlying && notionalValid && contractComplete && solveValid;

  const chips = useMemo(() => {
    const pending = rfqs.filter(
      (rfq) => rfq.status === 'pending_approval' || rfq.status === 'submitted',
    ).length;
    return [`${rfqs.length} RFQs`, `${pending} pending`];
  }, [rfqs]);

  const pageContext = useMemo((): PageContext => ({
    route: 'client-rfq',
    title: 'Client RFQ',
    path: '/',
    entity_ids: { rfq_id: selectedRfq?.id ?? null },
    snapshot: {
      rfq_count: rfqs.length,
      selected_rfq: selectedRfq
        ? {
            id: selectedRfq.id,
            status: selectedRfq.status,
            product_type: String(selectedRfq.request_payload?.product_type ?? ''),
            client_name: selectedRfq.client_name,
          }
        : null,
      editor: { mode, product: form.product, quote_mode: form.quoteMode },
    },
    loaded_context: { completeness: 'complete' },
    actions: declareActions([
      {
        name: 'submit_structured_rfq',
        required_ids: [],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/client/rfq/form',
      },
      {
        name: 'submit_nl_rfq',
        required_ids: [],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/client/rfq/chat',
      },
    ]),
    chips,
  }), [chips, form.product, form.quoteMode, mode, rfqs.length, selectedRfq]);
  usePageContextReporter(pageContext, onPageContextChange);

  return (
    <>
      <PageHeader title="CLIENT RFQ" chips={chips} />
      {error && (
        <div className="wl-client-rfq__message wl-client-rfq__message--error" role="alert">{error}</div>
      )}
      {feedback && <div className="wl-client-rfq__message" role="status">{feedback}</div>}

      <Tabs value={mode} onValueChange={(value) => setMode(value === 'nl' ? 'nl' : 'structured')}>
        <TabsList>
          <TabsTrigger value="nl">Natural Language</TabsTrigger>
          <TabsTrigger value="structured">Structured</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="wl-client-rfq__workbench" data-mode={mode}>
        <Panel
          title="My RFQs"
          meta={loading ? 'loading' : `${rfqs.length} rfqs`}
          className="wl-client-rfq__history-panel"
        >
          <label className="wl-client-rfq__field wl-client-rfq__client-name">
            <span>Client Name</span>
            <input
              value={clientName}
              onChange={(event) => onClientNameChange?.(event.currentTarget.value)}
              aria-label="Client Name"
            />
          </label>
          <RfqHistoryPanel
            rfqs={rfqs}
            selectedRfqId={selectedRfq?.id ?? null}
            productLabelFor={productLabelFor}
            onSelect={handleSelect}
            onClone={cloneRfq}
          />
        </Panel>

        {mode === 'nl' ? (
          <Panel title="Message" meta="chat intake" className="wl-client-rfq__nl-panel">
            <textarea
              className="wl-client-rfq__textarea"
              rows={6}
              value={message}
              onChange={(event) => setMessage(event.currentTarget.value)}
              aria-label="Message"
            />
            <div className="wl-client-rfq__actions">
              <Button
                variant="primary"
                disabled={submitting || !message.trim()}
                onClick={() => void onSubmitNL?.(message)}
              >
                <Send size={15} aria-hidden="true" />
                {submitting ? 'Submitting…' : 'Submit Natural Language'}
              </Button>
            </div>
          </Panel>
        ) : (
          <>
            <Panel
              title={`${productLabelFor(form.product)} Terms`}
              meta={form.product}
              className="wl-client-rfq__terms-panel"
            >
              <div className="wl-client-rfq__request-grid">
                <label className="wl-client-rfq__field">
                  <span>Product</span>
                  <select
                    value={form.product}
                    onChange={(event) => switchProduct(event.currentTarget.value)}
                    aria-label="Product"
                  >
                    {templates.map((item) => (
                      <option key={item.product_type} value={item.product_type}>{item.label}</option>
                    ))}
                  </select>
                </label>
                <label className="wl-client-rfq__field">
                  <span>Underlying</span>
                  <UnderlyingSelect
                    value={form.underlying}
                    underlyings={underlyings}
                    onChange={(underlying) => setForm({ ...form, underlying })}
                  />
                </label>
                <label className="wl-client-rfq__field">
                  <span>Side</span>
                  <select
                    value={form.side}
                    onChange={(event) =>
                      setForm({ ...form, side: event.currentTarget.value === 'sell' ? 'sell' : 'buy' })}
                    aria-label="Side"
                  >
                    <option value="buy">buy</option>
                    <option value="sell">sell</option>
                  </select>
                </label>
                <label className="wl-client-rfq__field">
                  <span>Notional</span>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={form.notional ?? ''}
                    onChange={(event) =>
                      setForm({ ...form, notional: parseNumberInput(event.currentTarget.value) })}
                    aria-label="Notional"
                  />
                </label>
              </div>
              <ProductTermsForm
                productType={form.product}
                productKwargs={form.productTerms}
                onChange={(productTerms) => setForm({ ...form, productTerms })}
              />
            </Panel>

            <Panel
              title="Quote & Submit"
              meta={form.quoteMode === 'solve' ? 'solve unknown' : 'price fixed terms'}
              className="wl-client-rfq__quote-panel"
            >
              <div className="wl-client-rfq__quote-grid">
                <label className="wl-client-rfq__field">
                  <span>Quote Mode</span>
                  <select
                    value={form.quoteMode}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        quoteMode: event.currentTarget.value === 'price' ? 'price' : 'solve',
                      })}
                    aria-label="Quote Mode"
                  >
                    <option value="solve">Solve Unknown</option>
                    <option value="price">Price Fixed Terms</option>
                  </select>
                </label>
                {form.quoteMode === 'solve' && (
                  <>
                    <label className="wl-client-rfq__field">
                      <span>Solve For</span>
                      <select
                        value={form.unknownField}
                        onChange={(event) => selectUnknownField(event.currentTarget.value)}
                        aria-label="Solve For"
                      >
                        {specs.map((spec) => (
                          <option key={spec.field_path} value={spec.field_path}>{spec.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Lower Bound</span>
                      <input
                        type="number"
                        step="any"
                        value={form.lowerBound ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, lowerBound: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Lower Bound"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Upper Bound</span>
                      <input
                        type="number"
                        step="any"
                        value={form.upperBound ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, upperBound: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Upper Bound"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Initial Guess</span>
                      <input
                        type="number"
                        step="any"
                        value={form.initialGuess ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, initialGuess: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Initial Guess"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Target Label</span>
                      <select
                        value={form.targetLabel}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            targetLabel:
                              event.currentTarget.value === 'premium' || event.currentTarget.value === 'reoffer'
                                ? event.currentTarget.value
                                : 'price',
                          })}
                        aria-label="Target Label"
                      >
                        <option value="price">price</option>
                        <option value="premium">premium</option>
                        <option value="reoffer">reoffer</option>
                      </select>
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Target Value</span>
                      <input
                        type="number"
                        step="any"
                        value={form.targetValue ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, targetValue: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Target Value"
                      />
                    </label>
                  </>
                )}
              </div>

              {!contractComplete && (
                <div className="wl-client-rfq__hint">
                  Fill all required contract terms to enable submission.
                </div>
              )}

              <div className="wl-client-rfq__actions">
                <Button
                  variant="primary"
                  disabled={!canSubmit}
                  onClick={() => void onSubmitStructured?.(form)}
                >
                  {submitting ? 'Submitting…' : 'Submit RFQ ▸'}
                </Button>
              </div>

              <div className="wl-client-rfq__status">
                <div className="wl-client-rfq__status-title">Status</div>
                {selectedRfq ? (
                  <StatusDetail rfq={selectedRfq} productLabelFor={productLabelFor} />
                ) : (
                  <p className="wl-client-rfq__empty">No RFQ selected.</p>
                )}
              </div>
            </Panel>
          </>
        )}
      </div>
    </>
  );
}

function UnderlyingSelect({
  value,
  underlyings,
  onChange,
}: {
  value: string;
  underlyings: Underlying[];
  onChange: (value: string) => void;
}) {
  const active = underlyings.filter((underlying) => underlying.status === 'active');
  const hasCurrentValue = active.some((underlying) => underlying.symbol === value);
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
      disabled={active.length === 0 && !value}
      aria-label="Underlying"
    >
      {!value ? (
        <option value="" disabled={active.length > 0}>
          {active.length ? 'Choose underlying' : 'No active underlyings'}
        </option>
      ) : null}
      {value && !hasCurrentValue ? (
        <option value={value} disabled>{`${value} (not active)`}</option>
      ) : null}
      {active.map((underlying) => (
        <option key={underlying.symbol} value={underlying.symbol}>
          {underlying.display_name && underlying.display_name !== underlying.symbol
            ? `${underlying.symbol} · ${underlying.display_name}`
            : underlying.symbol}
        </option>
      ))}
    </select>
  );
}

function StatusDetail({
  rfq,
  productLabelFor,
}: {
  rfq: RFQ;
  productLabelFor: (productType: string) => string;
}) {
  const fieldLabel = readString(rfq.quote_payload, 'field_label')
    ?? readString(rfq.quote_payload, 'field_path')
    ?? 'solved field';
  const solved = readNumber(rfq.quote_payload, 'solved_value');
  const price = readNumber(rfq.quote_payload, 'achieved_price');
  const response = rfq.approved_response ?? readString(rfq.quote_payload, 'client_response') ?? '';
  const quoteError = readString(rfq.quote_payload, 'quantark_error');
  const payload = rfq.request_payload ?? {};
  const summary = [
    String(payload.underlying ?? ''),
    String(payload.side ?? ''),
    payload.quantity != null ? String(payload.quantity) : '',
    productLabelFor(String(payload.product_type ?? '')),
  ].filter(Boolean).join(' · ');

  return (
    <div className="wl-client-rfq__status-detail">
      <header className="wl-client-rfq__status-head">
        <span className="wl-client-rfq__status-id">RFQ #{rfq.id}</span>
        <Badge variant={rfqStatusBadge(rfq.status)}>{rfq.status}</Badge>
      </header>
      {summary && <p className="wl-client-rfq__status-summary">{summary}</p>}
      {response && <p className="wl-client-rfq__status-response">{response}</p>}
      {quoteError && <p className="wl-client-rfq__status-response wl-client-rfq__status-response--error">{quoteError}</p>}
      <div className="wl-client-rfq__status-terms">
        {solved != null && (
          <div className="wl-client-rfq__status-term">
            <span>{fieldLabel}</span>
            <strong>{solved.toFixed(6)}</strong>
          </div>
        )}
        {price != null && (
          <div className="wl-client-rfq__status-term">
            <span>price</span>
            <strong>{price.toFixed(6)}</strong>
          </div>
        )}
      </div>
    </div>
  );
}

function formFromTemplate(template: RfqTemplate): ClientRfqForm {
  const specs = templateSpecs(template);
  const first = specs[0] ?? null;
  return {
    underlying: '',
    side: 'buy',
    notional: 1,
    quoteMode: 'solve',
    product: template.product_type,
    productTerms: cloneRecord(template.product_kwargs),
    engineSpec: cloneRecord(template.engine_spec),
    unknownField: first?.field_path ?? '',
    lowerBound: first?.lower_bound ?? null,
    upperBound: first?.upper_bound ?? null,
    initialGuess: first?.initial_guess ?? null,
    targetLabel: 'price',
    targetValue: 0,
  };
}

function templateSpecs(template: RfqTemplate): RfqUnknownFieldSpec[] {
  if (template.unknown_field_specs?.length) return template.unknown_field_specs;
  return (template.unknown_fields ?? []).map((path) => ({
    field_path: path,
    label: titleFromPath(path),
    lower_bound: 0,
    upper_bound: 200,
    initial_guess: 100,
  }));
}

function titleFromPath(path: string): string {
  const tail = path.split('.').pop() ?? path;
  return tail.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

// Mirrors the backend _SOLVE_TARGET_FLAT_KEY: a solve target (a path into the
// built termsheet) maps to the FLAT contract key the solver supplies, so the
// quote is not gated on a field that is being solved for.
const SOLVE_TARGET_FLAT_KEY: Record<string, string> = {
  'barrier_config.ko_rate': 'ko_rate',
  'coupon_config.coupon_rate': 'coupon_rate',
  'barrier_config.ki_barrier': 'ki_barrier_pct',
};

// Snowball-family (build_product) RFQs carry the FLAT term contract; the Quote is
// gated until these required inputs are filled. Keys mirror the backend per-family
// required_bound contract (flat side); non-build families are not listed and so
// are never gated here.
const SNOWBALL_REQUIRED_KEYS = [
  'initial_price',
  'maturity_years',
  'trade_start_date',
  'observation_frequency',
  'ko_barrier_pct',
  'ki_barrier_pct',
  'ko_rate',
  'lockup_months',
];
const REQUIRED_CONTRACT_KEYS: Record<string, string[]> = {
  SnowballOption: SNOWBALL_REQUIRED_KEYS,
  KnockOutResetSnowballOption: [...SNOWBALL_REQUIRED_KEYS, 'post_ko_barrier_pct', 'post_ko_rate'],
  PhoenixOption: [...SNOWBALL_REQUIRED_KEYS, 'coupon_barrier_pct', 'coupon_rate'],
};

function isFilled(value: unknown): boolean {
  return value !== undefined && value !== null && value !== '';
}

// For build_product families, the submit is enabled only when every required flat
// contract field is filled — except the field being solved for. Non-build families
// are never gated (return true).
function isContractComplete(
  product: string,
  productTerms: Record<string, unknown>,
  quoteMode: 'solve' | 'price',
  unknownField: string,
): boolean {
  const required = REQUIRED_CONTRACT_KEYS[product];
  if (!required) return true;
  const solveKey = quoteMode === 'solve' ? SOLVE_TARGET_FLAT_KEY[unknownField] : undefined;
  return required.every((key) => key === solveKey || isFilled(productTerms[key]));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

function numberOrNull(value: unknown): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  if (value == null || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return value == null ? null : String(value);
}
```

Rewrite `frontend/src/routes/ClientRfq.css`:

```css
.wl-client-rfq__workbench {
  display: grid;
  grid-template-columns: minmax(250px, 0.85fr) minmax(360px, 1.45fr) minmax(320px, 1.15fr);
  gap: var(--gap-3);
  align-items: start;
  margin-top: var(--gap-2);
}

.wl-client-rfq__workbench[data-mode='nl'] {
  grid-template-columns: minmax(250px, 0.85fr) minmax(0, 2.6fr);
}

.wl-client-rfq__message {
  margin: var(--gap-2) 0 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--pos, #2e7d32) 12%, transparent);
}

.wl-client-rfq__message--error {
  background: color-mix(in srgb, var(--neg, #c62828) 12%, transparent);
}

.wl-client-rfq__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

.wl-client-rfq__field input,
.wl-client-rfq__field select {
  padding: 6px 8px;
  border: 1px solid var(--line, #2a2f3a);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
}

.wl-client-rfq__client-name {
  margin-bottom: var(--gap-2);
}

.wl-client-rfq__request-grid,
.wl-client-rfq__quote-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--gap-2);
  margin-bottom: var(--gap-2);
}

.wl-client-rfq__textarea {
  width: 100%;
  resize: vertical;
  padding: 8px 10px;
  border: 1px solid var(--line, #2a2f3a);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
}

.wl-client-rfq__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--gap-2);
}

.wl-client-rfq__hint {
  font-size: 12px;
  opacity: 0.75;
  padding: 6px 0;
}

.wl-client-rfq__status {
  margin-top: var(--gap-3);
  border-top: 1px dashed var(--line, #2a2f3a);
  padding-top: var(--gap-2);
}

.wl-client-rfq__status-title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.65;
  margin-bottom: 6px;
}

.wl-client-rfq__status-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--gap-1);
}

.wl-client-rfq__status-id {
  font-weight: 600;
}

.wl-client-rfq__status-summary {
  font-size: 12px;
  opacity: 0.8;
  margin: 6px 0 0;
}

.wl-client-rfq__status-response {
  font-size: 12px;
  margin: 6px 0 0;
}

.wl-client-rfq__status-response--error {
  color: var(--neg, #ef5350);
}

.wl-client-rfq__status-terms {
  display: flex;
  gap: var(--gap-3);
  margin-top: var(--gap-2);
}

.wl-client-rfq__status-term {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}

.wl-client-rfq__empty {
  opacity: 0.7;
}

@media (max-width: 1200px) {
  .wl-client-rfq__workbench {
    grid-template-columns: minmax(230px, 0.8fr) minmax(0, 1.2fr);
  }
}
```

(Adjust CSS custom-property names to the app theme if `--line`/`--accent` etc. don't exist — check `frontend/src/routes/TrySolve.css` for the variables actually in use and mirror them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/ClientRfq.test.tsx`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ClientRfq.tsx frontend/src/routes/ClientRfq.css frontend/src/routes/ClientRfq.test.tsx
git commit -m "feat(client-rfq): rebuild page as three-column workbench with catalog-driven solve fields"
```

---

### Task 6: `ClientRfq.live.tsx` — data wrapper rewrite

**Files:**
- Rewrite: `frontend/src/routes/ClientRfq.live.tsx`
- Test: `frontend/src/routes/ClientRfq.live.test.tsx` (new)

- [ ] **Step 1: Write the failing tests** — create `frontend/src/routes/ClientRfq.live.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClientRfqLive } from './ClientRfq.live';
import type { RFQ } from '../types';

const catalog = {
  product_types: [],
  engine_options: [],
  unknown_fields: { EuropeanVanillaOption: ['strike'] },
  templates: [
    {
      key: 'vanilla',
      label: 'Vanilla',
      product_type: 'EuropeanVanillaOption',
      engine_spec: { engine_name: 'BlackScholesEngine' },
      unknown_fields: ['strike'],
      unknown_field_specs: [
        { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
      ],
      product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
    },
  ],
  advanced: {},
};

const instruments = [
  { id: 1, symbol: '000852.SH', display_name: 'CSI 1000', status: 'active' },
];

const listedRfq: RFQ = {
  id: 7,
  client_name: 'Demo Client',
  channel: 'form',
  status: 'pending_approval',
  request_payload: { product_type: 'EuropeanVanillaOption', underlying: '000852.SH', side: 'buy', quantity: 1 },
  quote_payload: {},
  created_at: '2026-06-06T08:00:00Z',
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch(onForm?: (init: RequestInit | undefined) => void) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith('/api/rfq/catalog')) return jsonResponse(catalog);
    if (url.startsWith('/api/instruments')) return jsonResponse(instruments);
    if (url.startsWith('/api/client/rfqs')) return jsonResponse([listedRfq]);
    if (url.startsWith('/api/client/rfq/form')) {
      onForm?.(init);
      return jsonResponse({ ...listedRfq, id: 8 });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('ClientRfqLive', () => {
  it('fetches catalog, instruments, and the client rfq list on mount', async () => {
    const fetchMock = mockFetch();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /#7 vanilla/i })).toBeInTheDocument();
    });
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain('/api/rfq/catalog');
    expect(urls).toContain('/api/instruments');
    expect(urls.some((url) => url.startsWith('/api/client/rfqs?client_name=Demo%20Client'))).toBe(true);
  });

  it('submits the structured form without a tenor overwrite and refreshes the list', async () => {
    let formBody: Record<string, unknown> | null = null;
    const fetchMock = mockFetch((init) => {
      formBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/rfq #8 submitted/i);
    });
    expect(formBody).not.toBeNull();
    const body = formBody as unknown as Record<string, any>;
    expect(body.client_name).toBe('Demo Client');
    expect(body.quote_mode).toBe('solve');
    expect(body.product.quantark_class).toBe('EuropeanVanillaOption');
    expect(body.product.underlying).toBe('000852.SH');
    expect(body.product.terms.maturity).toBe(1);
    expect(body.unknown.field_path).toBe('strike');
    expect(body.target).toEqual({ label: 'price', value: 0 });
    expect(body.tenor).toBeUndefined();
  });

  it('surfaces backend error detail and persists the client name', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/rfq/catalog')) return jsonResponse(catalog);
      if (url.startsWith('/api/instruments')) return jsonResponse(instruments);
      if (url.startsWith('/api/client/rfqs')) return jsonResponse([]);
      if (url.startsWith('/api/client/rfq/form')) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ detail: 'QuantArk build failed' }),
          text: async () => JSON.stringify({ detail: 'QuantArk build failed' }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('QuantArk build failed');
    });

    const nameInput = screen.getByLabelText('Client Name');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Acme');
    await waitFor(() => {
      expect(localStorage.getItem('openOtc.clientRfqName')).toBe('Acme');
    });
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.startsWith('/api/client/rfqs?client_name=Acme'))).toBe(true);
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ClientRfq.live.test.tsx`
Expected: FAIL — current live wrapper has none of this behavior

- [ ] **Step 3: Implement** — rewrite `frontend/src/routes/ClientRfq.live.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Instrument, PageContextReporter, RFQ, RfqCatalog, Underlying } from '../types';
import { ClientRfq, type ClientRfqForm } from './ClientRfq';

const CLIENT_NAME_KEY = 'openOtc.clientRfqName';
const DEFAULT_MESSAGE = 'Can you quote a one year CSI500 snowball solving KO rate for target premium 10?';
const POLL_MS = 10000;

type Props = {
  onPageContextChange?: PageContextReporter;
};

export function ClientRfqLive({ onPageContextChange }: Props) {
  const [catalog, setCatalog] = useState<RfqCatalog | null>(null);
  const [underlyings, setUnderlyings] = useState<Instrument[]>([]);
  const [rfqs, setRfqs] = useState<RFQ[]>([]);
  const [selectedRfqId, setSelectedRfqId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [clientName, setClientName] = useState(
    () => localStorage.getItem(CLIENT_NAME_KEY) ?? 'Demo Client',
  );
  const submittingRef = useRef(false);

  const refreshRfqs = useCallback(async (name: string) => {
    const listed = await api<RFQ[]>(
      `/api/client/rfqs?client_name=${encodeURIComponent(name)}&limit=20`,
    );
    setRfqs(listed);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [catalogData, instruments] = await Promise.all([
          api<RfqCatalog>('/api/rfq/catalog').catch(() => null),
          api<Instrument[]>('/api/instruments').catch(() => [] as Instrument[]),
        ]);
        if (cancelled) return;
        setCatalog(catalogData);
        setUnderlyings(instruments);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled || submittingRef.current) return;
      try {
        await refreshRfqs(clientName);
      } catch {
        // Polling failures are silent; the next tick retries.
      }
    };
    void tick();
    const interval = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [clientName, refreshRfqs]);

  const handleClientNameChange = (name: string) => {
    setClientName(name);
    localStorage.setItem(CLIENT_NAME_KEY, name);
  };

  const submit = async (request: () => Promise<RFQ>) => {
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setFeedback(null);
    try {
      const rfq = await request();
      setFeedback(`RFQ #${rfq.id} submitted (${rfq.status}).`);
      setSelectedRfqId(rfq.id);
      submittingRef.current = false;
      await refreshRfqs(clientName);
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleSubmitNL = (message: string) =>
    submit(() =>
      api<RFQ>('/api/client/rfq/chat', {
        method: 'POST',
        body: JSON.stringify({ client_name: clientName, message }),
      }));

  const handleSubmitStructured = (form: ClientRfqForm) =>
    submit(() =>
      api<RFQ>('/api/client/rfq/form', {
        method: 'POST',
        body: JSON.stringify(structuredPayload(form, clientName)),
      }));

  return (
    <ClientRfq
      catalog={catalog}
      underlyings={underlyings as unknown as Underlying[]}
      rfqs={rfqs}
      selectedRfqId={selectedRfqId}
      clientName={clientName}
      defaultMessage={DEFAULT_MESSAGE}
      loading={loading}
      submitting={submitting}
      error={error}
      feedback={feedback}
      onClientNameChange={handleClientNameChange}
      onSelectRfq={setSelectedRfqId}
      onSubmitNL={handleSubmitNL}
      onSubmitStructured={handleSubmitStructured}
      onPageContextChange={onPageContextChange}
    />
  );
}

function structuredPayload(form: ClientRfqForm, clientName: string): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    client_name: clientName,
    side: form.side,
    quantity: form.notional ?? 1,
    quote_mode: form.quoteMode,
    product: {
      asset_class: 'equity',
      product_family: inferProductFamily(form.product, form.productTerms),
      quantark_class: form.product,
      underlying: form.underlying.trim(),
      // currency omitted on purpose: the backend ProductSpecIn default
      // (CNY) applies; this form has no currency input.
      terms: form.productTerms,
    },
    engine_spec: form.engineSpec,
  };
  if (form.quoteMode === 'solve') {
    payload.unknown = {
      field_path: form.unknownField,
      lower_bound: form.lowerBound ?? 0,
      upper_bound: form.upperBound ?? 0,
      initial_guess: form.initialGuess ?? 0,
    };
    payload.target = { label: form.targetLabel, value: form.targetValue ?? 0 };
  }
  return payload;
}

function errorDetail(err: unknown): string {
  if (err instanceof Error) {
    try {
      const parsed = JSON.parse(err.message) as { detail?: unknown };
      if (parsed && typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      // Not JSON — fall through to the raw message.
    }
    return err.message || 'Request failed';
  }
  return 'Request failed';
}

function inferProductFamily(productType: string, terms: Record<string, unknown>): string {
  if (Array.isArray(terms.components) && terms.components.length > 0) return 'package';
  const normalized = productType.toLowerCase();
  if (normalized.includes('snowball') || normalized.includes('phoenix') || normalized.includes('autocallable')) return 'autocallable';
  if (normalized.includes('barrier')) return 'barrier';
  if (normalized.includes('touch')) return 'touch';
  if (normalized.includes('asian')) return 'asian';
  if (normalized.includes('rangeaccrual') || normalized.includes('range_accrual')) return 'range_accrual';
  if (normalized.includes('sharkfin')) return 'sharkfin';
  if (normalized.includes('future') || normalized.includes('forward')) return 'futures';
  if (['stock', 'fund', 'etf', 'spot', 'spotinstrument'].includes(normalized)) return 'spot';
  return 'option';
}
```

Note: `inferProductFamily` is copied unchanged from the old live wrapper. The old `LATEST_KEY` localStorage mechanism, the `tenor` parsing helpers (`parseTenorYears`, `parsePositiveNumber`, `parseNumber`), and the advanced-JSON handler are all deliberately gone.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/ClientRfq.live.test.tsx src/routes/ClientRfq.test.tsx`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ClientRfq.live.tsx frontend/src/routes/ClientRfq.live.test.tsx
git commit -m "feat(client-rfq): live wrapper with history polling, client-name persistence, error detail surfacing"
```

---

### Task 7: Wiring, retirement of old components, full verification

**Files:**
- Modify: `frontend/src/main.tsx` (~line 224)
- Delete: `frontend/src/components/RfqIntakeCard.tsx`, `RfqIntakeCard.css`, `RfqIntakeCard.test.tsx`
- Delete: `frontend/src/components/RfqStatusCard.tsx`, `RfqStatusCard.css`, `RfqStatusCard.test.tsx`

- [ ] **Step 1: Wire page context** — in `frontend/src/main.tsx`, change:

```tsx
        {route === 'client'    && <ClientRfqLive />}
```

to:

```tsx
        {route === 'client'    && <ClientRfqLive onPageContextChange={handlePageContextChange} />}
```

- [ ] **Step 2: Delete the retired components**

```bash
git rm frontend/src/components/RfqIntakeCard.tsx frontend/src/components/RfqIntakeCard.css frontend/src/components/RfqIntakeCard.test.tsx
git rm frontend/src/components/RfqStatusCard.tsx frontend/src/components/RfqStatusCard.css frontend/src/components/RfqStatusCard.test.tsx
```

- [ ] **Step 3: Verify nothing still imports them**

Run: `cd frontend && grep -rn "RfqIntakeCard\|RfqStatusCard" src || echo CLEAN`
Expected: `CLEAN`

- [ ] **Step 4: Typecheck** (vitest does not typecheck)

Run: `cd frontend && npx tsc -b`
Expected: exit 0, no errors

- [ ] **Step 5: Full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all PASS (no remaining references to deleted test files)

- [ ] **Step 6: Backend regression slice**

Run: `PYTHONPATH=backend python -m pytest tests/test_api.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat(client-rfq): wire page context; retire RfqIntakeCard and RfqStatusCard"
```

---

## Self-Review Checklist (run after Task 7)

- Spec §Layout: three columns + NL mode swap → Task 5. ✓
- Spec §Form upgrades: product labels, active-underlying dropdown, solve-for dropdown with prefilled bounds, price mode, gating → Task 5; term coverage → Task 3. ✓
- Spec §Backend: list endpoint → Task 2; `unknown_field_specs` → Task 1. ✓
- Spec §Identity & page context: client name field + persistence → Tasks 5/6; `usePageContextReporter` → Task 5; main.tsx plumbing → Task 7. ✓
- Spec §Components: RfqHistoryPanel → Task 4; retirements → Task 7. ✓
- Spec §Error handling: error strip + silent polling + pause-while-submitting → Tasks 5/6. ✓
- Spec §Testing: backend list/catalog tests (Tasks 1–2), workbench/live tests (Tasks 5–6). ✓
