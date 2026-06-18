# Portfolios Page UI/UX Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Portfolios route around author-first workflow using existing Warm Ledger primitives (PageHeader · Tile · Panel · Modal · Table), replace `window.prompt`/`confirm` with modal dialogs, and fix the latent `KindChip`/`PositionPicker` CSS token bugs along the way.

**Architecture:** Page-internal portfolio sidebar removed. Header becomes `<PageHeader>` with a Portfolio select + `+ New ▾` + `Run Pricing` primary + `⋯` overflow. Below: 4 `<Tile>` row (POSITIONS · UNDERLYINGS · NET QTY · STATUS), then a search/pager toolbar (same as Positions), then a two-pane grid: left `<Panel>` "DEFINITION" with five `<fieldset>` blocks (RULE · SOURCES · MANUAL INCLUDES · MANUAL EXCLUDES · TAGS), right `<Panel>` "RESOLVED POSITIONS" with a paginated `<Table>`. Sources and Manual includes/excludes are picked via Modal-based pickers. Create/Delete are modal dialogs.

**Tech Stack:** React 19, TypeScript, Vite, Vitest + @testing-library/react + @testing-library/user-event, Radix UI Dialog/DropdownMenu, project CSS tokens at `frontend/src/tokens/`.

**Spec:** `docs/superpowers/specs/2026-05-11-portfolios-ui-redesign-design.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `frontend/src/components/SourcePicker.tsx` | Modal picker for selecting source portfolios (multi-select). |
| `frontend/src/components/SourcePicker.test.tsx` | Unit tests. |
| `frontend/src/components/PortfolioCreateDialog.tsx` | Modal dialog for creating a new portfolio (name input, kind already chosen). |
| `frontend/src/components/PortfolioCreateDialog.test.tsx` | Unit tests. |
| `frontend/src/components/PortfolioDeleteDialog.tsx` | Modal confirmation dialog for deletion. |
| `frontend/src/components/PortfolioDeleteDialog.test.tsx` | Unit tests. |

### Modified files

| File | Change |
|---|---|
| `frontend/src/components/KindChip.css` | Replace legacy `--wl-*` variables with real tokens; add inverted view variant. |
| `frontend/src/components/KindChip.test.tsx` | Add class-name assertions for both variants. |
| `frontend/src/components/PositionPicker.tsx` | Wrap content in `<Modal>`; add `excludeIds: number[]` prop; emit `onConfirm(ids)` unchanged. |
| `frontend/src/components/PositionPicker.css` | **Delete** — Modal styles supersede the local modal wrapper styles. |
| `frontend/src/components/PositionPicker.test.tsx` | Update for Modal-based render. |
| `frontend/src/routes/Portfolios.tsx` | Full rewrite around PageHeader · Tiles · Toolbar · two Panels. |
| `frontend/src/routes/Portfolios.css` | Full rewrite — add tiles/toolbar/two-pane/select-field styles, drop sidebar styles. |
| `frontend/src/routes/Portfolios.test.tsx` | Rewrite for new layout. |
| `frontend/src/routes/Portfolios.live.tsx` | Wire modals, add save state, drop `window.prompt`/`confirm`. |
| `frontend/src/routes/Portfolios.live.test.tsx` | Add modal-based assertions. |

---

## Task 1: Fix KindChip token bug

**Why:** `KindChip.css` references `--wl-surface-2`, `--wl-accent`, `--wl-accent-soft`, `--wl-surface-3`, `--wl-text` — none of these exist in `frontend/src/tokens/colors.css`. The chip renders unstyled (browser defaults). The spec requires we fix this before the redesign uses the chip.

**Files:**
- Modify: `frontend/src/components/KindChip.css`
- Modify: `frontend/src/components/KindChip.test.tsx`

- [ ] **Step 1: Write failing tests for class names**

Replace the contents of `frontend/src/components/KindChip.test.tsx` with:

```tsx
import { test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { KindChip } from './KindChip';

test('renders container chip with container variant class', () => {
  render(<KindChip kind="container" />);
  const chip = screen.getByText(/container/i);
  expect(chip).toHaveClass('wl-kindchip');
  expect(chip).toHaveClass('wl-kindchip--container');
});

test('renders view chip with view variant class', () => {
  render(<KindChip kind="view" />);
  const chip = screen.getByText(/view/i);
  expect(chip).toHaveClass('wl-kindchip');
  expect(chip).toHaveClass('wl-kindchip--view');
});
```

- [ ] **Step 2: Run test to verify it passes for existing variant classes**

Run: `cd frontend && npm test -- KindChip`
Expected: PASS — the React component already emits both classes; this test merely locks the contract before we change CSS.

- [ ] **Step 3: Replace the CSS with real tokens**

Replace the contents of `frontend/src/components/KindChip.css` with:

```css
.wl-kindchip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border: 1px solid var(--hairline);
  background: var(--paper-3);
  color: var(--ink);
}
.wl-kindchip--container {
  background: var(--paper-3);
  color: var(--ink);
  border-color: var(--hairline);
}
.wl-kindchip--view {
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
}
```

- [ ] **Step 4: Run the test again to confirm no regression**

Run: `cd frontend && npm test -- KindChip`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/KindChip.css frontend/src/components/KindChip.test.tsx
git commit -m "fix(kindchip): replace legacy --wl-* tokens with real ink/paper tokens"
```

---

## Task 2: Refactor PositionPicker to use the Modal component and accept excludeIds

**Why:** Today `PositionPicker` ships its own modal wrapper using legacy `--wl-surface-1`/`--wl-border`/`--wl-shadow-lg` tokens that don't exist, and its `.wl-modal__body` class conflicts with `Modal.css`. The redesign needs Modal-based pickers anyway. Also, when picking positions to include vs exclude we must filter out the already-picked side, so add `excludeIds` to the API.

**Files:**
- Modify: `frontend/src/components/PositionPicker.tsx`
- Delete: `frontend/src/components/PositionPicker.css`
- Modify: `frontend/src/components/PositionPicker.test.tsx`

- [ ] **Step 1: Write the new tests**

Replace the contents of `frontend/src/components/PositionPicker.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PositionPicker } from './PositionPicker';

const rows = [
  { id: 1, underlying: 'AAPL', product_type: 'Snowball' },
  { id: 2, underlying: 'TSLA', product_type: 'Phoenix' },
  { id: 3, underlying: 'NVDA', product_type: 'Snowball' },
];

test('filters by search', async () => {
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  await userEvent.type(screen.getByPlaceholderText(/search/i), 'AAPL');
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
});

test('hides positions in excludeIds', () => {
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[2]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
  expect(screen.getByText('NVDA')).toBeInTheDocument();
});

test('confirms with selected ids', async () => {
  const onConfirm = vi.fn();
  render(
    <PositionPicker
      open
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByLabelText(/select position 1/i));
  await userEvent.click(screen.getByLabelText(/select position 3/i));
  await userEvent.click(screen.getByRole('button', { name: /add 2/i }));
  expect(onConfirm).toHaveBeenCalledWith([1, 3]);
});

test('does not render when closed', () => {
  render(
    <PositionPicker
      open={false}
      positions={rows}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- PositionPicker`
Expected: FAIL (multiple) — `excludeIds` not a valid prop, button "Add 2" doesn't exist, etc.

- [ ] **Step 3: Rewrite PositionPicker to use Modal + excludeIds**

Replace the contents of `frontend/src/components/PositionPicker.tsx` with:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';

type PickerPosition = {
  id: number;
  underlying: string;
  product_type: string;
};

type Props = {
  open: boolean;
  positions: PickerPosition[];
  excludeIds: number[];
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
  title?: string;
};

export function PositionPicker({
  open,
  positions,
  excludeIds,
  onCancel,
  onConfirm,
  title = 'Pick Positions',
}: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!open) {
      setQuery('');
      setSelected(new Set());
    }
  }, [open]);

  if (!open) return null;

  const lowerQuery = query.toLowerCase();
  const excluded = new Set(excludeIds);
  const filtered = positions.filter((position) => {
    if (excluded.has(position.id)) return false;
    if (!lowerQuery) return true;
    return (
      position.underlying.toLowerCase().includes(lowerQuery) ||
      position.product_type.toLowerCase().includes(lowerQuery) ||
      String(position.id).includes(lowerQuery)
    );
  });

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title={title}>
      <div className="wl-picker">
        <input
          className="wl-picker__search"
          placeholder="Search positions..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="wl-picker__list">
          {filtered.map((position) => (
            <li key={position.id} className="wl-picker__row">
              <label>
                <input
                  type="checkbox"
                  aria-label={`Select position ${position.id}`}
                  checked={selected.has(position.id)}
                  onChange={() => toggle(position.id)}
                />
                <span className="wl-picker__id">#{position.id}</span>
                <span className="wl-picker__underlying">{position.underlying}</span>
                <span className="wl-picker__product">{position.product_type}</span>
              </label>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="wl-picker__empty">No positions match.</li>
          )}
        </ul>
        <div className="wl-picker__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            type="button"
            variant="primary"
            disabled={selected.size === 0}
            onClick={() => onConfirm(Array.from(selected))}
          >
            Add {selected.size}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 4: Replace PositionPicker.css with picker-only styles**

Replace the contents of `frontend/src/components/PositionPicker.css` with picker-internal styles (the Modal chrome now comes from `Modal.css`):

```css
.wl-picker { display: flex; flex-direction: column; gap: var(--gap-3); min-height: 0; }
.wl-picker__search {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}
.wl-picker__list {
  list-style: none;
  padding: 0;
  margin: 0;
  overflow-y: auto;
  flex: 1;
  border: 1px solid var(--hairline);
  max-height: 360px;
}
.wl-picker__row { border-bottom: 1px solid var(--hairline); }
.wl-picker__row:last-child { border-bottom: 0; }
.wl-picker__row label {
  display: grid;
  grid-template-columns: 24px 60px 1fr 1fr;
  gap: var(--gap-2);
  align-items: center;
  padding: 6px var(--gap-2);
  cursor: pointer;
}
.wl-picker__row label:hover { background: var(--paper-2); }
.wl-picker__id, .wl-picker__underlying { font-family: var(--font-numeric); font-size: var(--type-num-m-size); }
.wl-picker__product { font-family: var(--font-ui); font-size: var(--type-small-size); color: var(--ink-2); }
.wl-picker__empty {
  padding: var(--gap-3);
  color: var(--ink-2);
  font-style: italic;
  text-align: center;
}
.wl-picker__actions { display: flex; gap: var(--gap-2); justify-content: flex-end; }
```

Make sure the `import './PositionPicker.css';` line at the top of `PositionPicker.tsx` still exists (it does in the rewrite above — confirm).

- [ ] **Step 5: Re-add the import in PositionPicker.tsx**

Edit `frontend/src/components/PositionPicker.tsx` so its imports include the CSS file. Replace:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
```

with:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import './PositionPicker.css';
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npm test -- PositionPicker`
Expected: PASS — all four tests.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/PositionPicker.tsx \
        frontend/src/components/PositionPicker.css \
        frontend/src/components/PositionPicker.test.tsx
git commit -m "refactor(positionpicker): wrap in Modal, add excludeIds, drop legacy tokens"
```

---

## Task 3: Create SourcePicker component

**Why:** The "+ source" button on a View portfolio currently auto-adds the first available portfolio. We need a real picker for source portfolios with search and multi-select.

**Files:**
- Create: `frontend/src/components/SourcePicker.tsx`
- Create: `frontend/src/components/SourcePicker.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/SourcePicker.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { SourcePicker } from './SourcePicker';

const portfolios = [
  { id: 1, name: 'Snowballs', kind: 'view' as const, resolved_position_count: 130 },
  { id: 2, name: 'Vanillas', kind: 'view' as const, resolved_position_count: 88 },
  { id: 3, name: 'Owned', kind: 'container' as const, resolved_position_count: 12 },
];

test('lists portfolios excluding self and already-selected sources', () => {
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={1}
      excludeIds={[3]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText('Snowballs')).not.toBeInTheDocument();
  expect(screen.queryByText('Owned')).not.toBeInTheDocument();
  expect(screen.getByText('Vanillas')).toBeInTheDocument();
});

test('filters by search', async () => {
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  await userEvent.type(screen.getByPlaceholderText(/search/i), 'snow');
  expect(screen.getByText('Snowballs')).toBeInTheDocument();
  expect(screen.queryByText('Vanillas')).not.toBeInTheDocument();
});

test('confirms with selected ids', async () => {
  const onConfirm = vi.fn();
  render(
    <SourcePicker
      open
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByLabelText(/select portfolio 1/i));
  await userEvent.click(screen.getByLabelText(/select portfolio 2/i));
  await userEvent.click(screen.getByRole('button', { name: /add 2/i }));
  expect(onConfirm).toHaveBeenCalledWith([1, 2]);
});

test('does not render when closed', () => {
  render(
    <SourcePicker
      open={false}
      portfolios={portfolios}
      currentPortfolioId={null}
      excludeIds={[]}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- SourcePicker`
Expected: FAIL — `SourcePicker` is not defined.

- [ ] **Step 3: Implement SourcePicker**

Create `frontend/src/components/SourcePicker.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { KindChip } from './KindChip';
import { Modal } from './Modal';
import './PositionPicker.css';

type SourcePortfolio = {
  id: number;
  name: string;
  kind: 'view' | 'container';
  resolved_position_count: number;
};

type Props = {
  open: boolean;
  portfolios: SourcePortfolio[];
  currentPortfolioId: number | null;
  excludeIds: number[];
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
};

export function SourcePicker({
  open,
  portfolios,
  currentPortfolioId,
  excludeIds,
  onCancel,
  onConfirm,
}: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!open) {
      setQuery('');
      setSelected(new Set());
    }
  }, [open]);

  if (!open) return null;

  const lowerQuery = query.toLowerCase();
  const excluded = new Set([
    ...excludeIds,
    ...(currentPortfolioId != null ? [currentPortfolioId] : []),
  ]);
  const filtered = portfolios.filter((portfolio) => {
    if (excluded.has(portfolio.id)) return false;
    if (!lowerQuery) return true;
    return portfolio.name.toLowerCase().includes(lowerQuery);
  });

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title="Pick Source Portfolios">
      <div className="wl-picker">
        <input
          className="wl-picker__search"
          placeholder="Search portfolios..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="wl-picker__list">
          {filtered.map((portfolio) => (
            <li key={portfolio.id} className="wl-picker__row">
              <label>
                <input
                  type="checkbox"
                  aria-label={`Select portfolio ${portfolio.id}`}
                  checked={selected.has(portfolio.id)}
                  onChange={() => toggle(portfolio.id)}
                />
                <span className="wl-picker__id"><KindChip kind={portfolio.kind} /></span>
                <span className="wl-picker__underlying">{portfolio.name}</span>
                <span className="wl-picker__product">{portfolio.resolved_position_count} positions</span>
              </label>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="wl-picker__empty">No portfolios match.</li>
          )}
        </ul>
        <div className="wl-picker__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            type="button"
            variant="primary"
            disabled={selected.size === 0}
            onClick={() => onConfirm(Array.from(selected))}
          >
            Add {selected.size}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- SourcePicker`
Expected: PASS — all four tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SourcePicker.tsx frontend/src/components/SourcePicker.test.tsx
git commit -m "feat(sourcepicker): add modal picker for source portfolios"
```

---

## Task 4: Create PortfolioCreateDialog component

**Why:** Replace `window.prompt` in `Portfolios.live.tsx` with a proper modal dialog.

**Files:**
- Create: `frontend/src/components/PortfolioCreateDialog.tsx`
- Create: `frontend/src/components/PortfolioCreateDialog.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/PortfolioCreateDialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PortfolioCreateDialog } from './PortfolioCreateDialog';

test('does not render when closed', () => {
  render(
    <PortfolioCreateDialog
      open={false}
      kind="view"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  expect(screen.queryByLabelText(/name/i)).not.toBeInTheDocument();
});

test('renders title for the chosen kind', () => {
  render(
    <PortfolioCreateDialog
      open
      kind="view"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  expect(screen.getByRole('dialog', { name: /new view portfolio/i })).toBeInTheDocument();
});

test('disables Create until name is non-empty', async () => {
  render(
    <PortfolioCreateDialog
      open
      kind="container"
      onCancel={() => {}}
      onCreate={() => {}}
    />,
  );
  const createButton = screen.getByRole('button', { name: /^create$/i });
  expect(createButton).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/name/i), 'Snowballs 2026Q2');
  expect(createButton).toBeEnabled();
});

test('calls onCreate with trimmed name and kind', async () => {
  const onCreate = vi.fn();
  render(
    <PortfolioCreateDialog
      open
      kind="view"
      onCancel={() => {}}
      onCreate={onCreate}
    />,
  );
  await userEvent.type(screen.getByLabelText(/name/i), '  Hello  ');
  await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
  expect(onCreate).toHaveBeenCalledWith({ name: 'Hello', kind: 'view' });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- PortfolioCreateDialog`
Expected: FAIL — `PortfolioCreateDialog` is not defined.

- [ ] **Step 3: Implement PortfolioCreateDialog**

Create `frontend/src/components/PortfolioCreateDialog.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import type { PortfolioKind } from '../types';
import './PortfolioCreateDialog.css';

type Props = {
  open: boolean;
  kind: PortfolioKind;
  onCancel: () => void;
  onCreate: (input: { name: string; kind: PortfolioKind }) => void;
};

export function PortfolioCreateDialog({ open, kind, onCancel, onCreate }: Props) {
  const [name, setName] = useState('');

  useEffect(() => {
    if (!open) setName('');
  }, [open]);

  const trimmed = name.trim();
  const title = kind === 'view' ? 'NEW VIEW PORTFOLIO' : 'NEW CONTAINER PORTFOLIO';

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!trimmed) return;
    onCreate({ name: trimmed, kind });
  }

  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title={title}>
      <form className="wl-create-portfolio" onSubmit={submit}>
        <label className="wl-create-portfolio__field" htmlFor="portfolio-create-name">
          <span>Name</span>
          <input
            id="portfolio-create-name"
            value={name}
            placeholder="e.g. Snowballs 2026Q2"
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
        </label>
        <div className="wl-create-portfolio__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!trimmed}>Create</Button>
        </div>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 4: Create PortfolioCreateDialog.css**

Create `frontend/src/components/PortfolioCreateDialog.css`:

```css
.wl-create-portfolio { display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-create-portfolio__field { display: flex; flex-direction: column; gap: var(--gap-1); }
.wl-create-portfolio__field span {
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-create-portfolio__field input {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}
.wl-create-portfolio__actions { display: flex; gap: var(--gap-2); justify-content: flex-end; }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm test -- PortfolioCreateDialog`
Expected: PASS — all four tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PortfolioCreateDialog.tsx \
        frontend/src/components/PortfolioCreateDialog.css \
        frontend/src/components/PortfolioCreateDialog.test.tsx
git commit -m "feat(portfolio-create-dialog): replace window.prompt with modal"
```

---

## Task 5: Create PortfolioDeleteDialog component

**Why:** Replace `window.confirm` for portfolio deletion with a modal that echoes the portfolio name (safer for irreversible action).

**Files:**
- Create: `frontend/src/components/PortfolioDeleteDialog.tsx`
- Create: `frontend/src/components/PortfolioDeleteDialog.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/PortfolioDeleteDialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { PortfolioDeleteDialog } from './PortfolioDeleteDialog';

test('does not render when closed', () => {
  render(
    <PortfolioDeleteDialog
      open={false}
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText(/snowballs/i)).not.toBeInTheDocument();
});

test('echoes portfolio name in body', () => {
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.getByText(/snowballs/i)).toBeInTheDocument();
});

test('confirm button calls onConfirm', async () => {
  const onConfirm = vi.fn();
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={() => {}}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

test('cancel button calls onCancel', async () => {
  const onCancel = vi.fn();
  render(
    <PortfolioDeleteDialog
      open
      portfolioName="Snowballs"
      onCancel={onCancel}
      onConfirm={() => {}}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
  expect(onCancel).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- PortfolioDeleteDialog`
Expected: FAIL — not defined.

- [ ] **Step 3: Implement PortfolioDeleteDialog**

Create `frontend/src/components/PortfolioDeleteDialog.tsx`:

```tsx
import { Button } from './Button';
import { Modal } from './Modal';
import './PortfolioDeleteDialog.css';

type Props = {
  open: boolean;
  portfolioName: string;
  onCancel: () => void;
  onConfirm: () => void;
};

export function PortfolioDeleteDialog({ open, portfolioName, onCancel, onConfirm }: Props) {
  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel(); }} title="DELETE PORTFOLIO">
      <div className="wl-delete-portfolio">
        <p className="wl-delete-portfolio__body">
          Delete <strong>{portfolioName}</strong>? This action cannot be undone.
        </p>
        <div className="wl-delete-portfolio__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="button" variant="danger" onClick={onConfirm}>Delete</Button>
        </div>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 4: Create PortfolioDeleteDialog.css**

Create `frontend/src/components/PortfolioDeleteDialog.css`:

```css
.wl-delete-portfolio { display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-delete-portfolio__body {
  margin: 0;
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  color: var(--ink);
  line-height: 1.5;
}
.wl-delete-portfolio__actions { display: flex; gap: var(--gap-2); justify-content: flex-end; }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm test -- PortfolioDeleteDialog`
Expected: PASS — all four tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PortfolioDeleteDialog.tsx \
        frontend/src/components/PortfolioDeleteDialog.css \
        frontend/src/components/PortfolioDeleteDialog.test.tsx
git commit -m "feat(portfolio-delete-dialog): replace window.confirm with modal"
```

---

## Task 6: Update Portfolios.tsx props interface

**Why:** Before rewriting the body, lock in the new prop contract. We drop `filterKind`/`filterTags` (no in-page kind filter anymore), add `saveState` for the auto-save indicator, and add picker-state callbacks. Keep all existing data callbacks (`onSaveRule`, `onAddInclude`, etc.) intact.

**Files:**
- Modify: `frontend/src/routes/Portfolios.tsx`

- [ ] **Step 1: Replace the `PortfoliosProps` type at the top of Portfolios.tsx**

Open `frontend/src/routes/Portfolios.tsx`. Replace the entire `PortfoliosProps` type (lines 33-56 today) and `FilterKind` type with this:

```tsx
type SaveState =
  | { kind: 'idle' }
  | { kind: 'editing' }
  | { kind: 'saving' }
  | { kind: 'saved'; at: number }
  | { kind: 'error'; message: string };

export type PortfoliosProps = {
  portfolios: PortfolioSummary[];
  allPortfolios: PortfolioSummary[];
  allPositions: PickerPosition[];
  selected: PortfolioDetail | null;
  selectedPortfolioId: number | null;
  pendingMembershipPreview: PreviewPosition[] | null;
  saveState: SaveState;
  onSelectPortfolio: (id: number) => void;
  onOpenCreate: (kind: PortfolioKind) => void;
  onOpenDelete: () => void;
  onSaveRule: (rule: FilterRule | null) => Promise<void>;
  onAddInclude: (positionIds: number[]) => Promise<void>;
  onRemoveInclude: (positionId: number) => Promise<void>;
  onAddExclude: (positionIds: number[]) => Promise<void>;
  onRemoveExclude: (positionId: number) => Promise<void>;
  onAddSource: (portfolioIds: number[]) => Promise<void>;
  onRemoveSource: (portfolioId: number) => Promise<void>;
  onSetTags: (tags: string[]) => Promise<void>;
  onRunPricing: () => void;
  onRunRisk: () => void;
};
```

Note: `onAddInclude` / `onAddExclude` / `onAddSource` now take **arrays**, since pickers batch confirms.

- [ ] **Step 2: Export the SaveState type**

In the same file, add to the existing exports near the bottom (or replace the existing export statement):

```tsx
export type { SaveState };
```

Also, remove the `FilterKind` type definition entirely.

- [ ] **Step 3: Check the file compiles for the type-only update**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: Many errors in `Portfolios.tsx`, `Portfolios.live.tsx`, `Portfolios.test.tsx` and `Portfolios.live.test.tsx` referencing the removed `FilterKind`/`onFilterKindChange`/single-id signatures. This is expected — they'll all be fixed by subsequent tasks. Move on.

- [ ] **Step 4: Commit**

This is an intentionally-broken intermediate commit so the subsequent tasks can be reviewed independently. Use `--no-verify` only if pre-commit blocks tsc, but prefer fixing this in the very next task if your hooks demand a clean build:

```bash
git add frontend/src/routes/Portfolios.tsx
git commit -m "refactor(portfolios): update props for new layout (WIP)"
```

If the project's pre-commit refuses a broken type-check, skip this commit and stage the changes alongside Task 7.

---

## Task 7: Rewrite Portfolios.tsx body

**Why:** Replace the sidebar+detail layout with PageHeader · Tiles · Toolbar · two Panels (Definition · Resolved Positions). Wires up the picker dialogs but leaves their open-state to a parent state hook (built in Task 9).

**Files:**
- Modify: `frontend/src/routes/Portfolios.tsx`

- [ ] **Step 1: Add the helper imports and small utilities at top of Portfolios.tsx**

Open `frontend/src/routes/Portfolios.tsx`. Replace the imports block at the top of the file with:

```tsx
import { useMemo, useState } from 'react';
import { Calculator, MoreHorizontal, Plus, Search } from 'lucide-react';
import { Button } from '../components/Button';
import { Chip } from '../components/Chip';
import { Empty } from '../components/Empty';
import { KindChip } from '../components/KindChip';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { PositionPicker } from '../components/PositionPicker';
import { RuleEditor } from '../components/RuleEditor';
import { SourcePicker } from '../components/SourcePicker';
import { Table, type Column } from '../components/Table';
import { TagEditor } from '../components/TagEditor';
import { Tile } from '../components/Tile';
import type {
  FilterRule,
  PortfolioDetail,
  PortfolioKind,
  PortfolioSummary,
} from '../types';
import './Portfolios.css';

type PreviewPosition = {
  id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

type PickerPosition = {
  id: number;
  underlying: string;
  product_type: string;
};
```

- [ ] **Step 2: Replace the Portfolios export with the new layout**

Replace the entire body of the `Portfolios` function (and the inner `DetailPane`, `SourceList`, `ManualIdList` functions — delete those, they're no longer used) with:

```tsx
export function Portfolios(props: PortfoliosProps) {
  const portfolio = props.selected;
  const isView = portfolio?.kind === 'view';

  const resolvedRows: PreviewPosition[] = isView
    ? props.pendingMembershipPreview ?? []
    : (portfolio?.positions ?? []) as PreviewPosition[];

  const tileValues = useMemo(() => computeTiles(resolvedRows), [resolvedRows]);

  const [includePickerOpen, setIncludePickerOpen] = useState(false);
  const [excludePickerOpen, setExcludePickerOpen] = useState(false);
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);

  return (
    <>
      <PageHeader
        title={`PORTFOLIOS · ${portfolio?.name ?? '—'}`}
        chips={portfolioChips(portfolio)}
        action={
          <div className="wl-portfolios__actions">
            <label className="wl-portfolios__select-field" htmlFor="portfolios-switcher">
              <span>Portfolio</span>
              <select
                id="portfolios-switcher"
                aria-label="Select portfolio"
                value={props.selectedPortfolioId ?? ''}
                onChange={(event) => props.onSelectPortfolio(Number(event.target.value))}
              >
                <option value="" disabled>Choose…</option>
                {props.portfolios.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name} · {option.kind}
                  </option>
                ))}
              </select>
            </label>
            <NewPortfolioMenu onSelect={props.onOpenCreate} />
            <Button variant="primary" onClick={props.onRunPricing} disabled={!portfolio}>
              <Calculator size={16} aria-hidden="true" />
              Run Pricing
            </Button>
            <OverflowMenu onDelete={props.onOpenDelete} onRunRisk={props.onRunRisk} disabled={!portfolio} />
          </div>
        }
      />

      <div className="wl-portfolios__tiles">
        <Tile label="POSITIONS" value={tileValues.positions} />
        <Tile label="UNDERLYINGS" value={tileValues.underlyings} />
        <Tile label="NET QTY" value={tileValues.netQty} variant={tileValues.netQtyVariant} />
        <Tile label="STATUS" value={tileValues.status} />
      </div>

      {!portfolio ? (
        <Empty message="Select a portfolio to begin." symbol="◌" />
      ) : (
        <div className="wl-portfolios__twopane">
          <Panel title="DEFINITION" meta={saveStateLabel(props.saveState)}>
            {isView ? (
              <>
                <RuleFieldset rule={portfolio.filter_rule} onChange={props.onSaveRule} />
                <SourcesFieldset
                  portfolio={portfolio}
                  allPortfolios={props.allPortfolios}
                  onOpen={() => setSourcePickerOpen(true)}
                  onRemove={props.onRemoveSource}
                />
                <ManualFieldset
                  legend="MANUAL INCLUDES"
                  ids={portfolio.manual_include_ids}
                  positions={props.allPositions}
                  onOpen={() => setIncludePickerOpen(true)}
                  onRemove={props.onRemoveInclude}
                />
                <ManualFieldset
                  legend="MANUAL EXCLUDES"
                  ids={portfolio.manual_exclude_ids}
                  positions={props.allPositions}
                  onOpen={() => setExcludePickerOpen(true)}
                  onRemove={props.onRemoveExclude}
                />
                <TagsFieldset tags={portfolio.tags} onChange={props.onSetTags} />
              </>
            ) : (
              <>
                <p className="wl-portfolios__container-hint">
                  Container holds owned positions imported via XLSX. Use the Positions page to add positions.
                </p>
                <TagsFieldset tags={portfolio.tags} onChange={props.onSetTags} />
              </>
            )}
          </Panel>

          <Panel
            title={isView ? 'RESOLVED POSITIONS' : 'OWNED POSITIONS'}
            meta={`${resolvedRows.length} rows · ${tileValues.underlyings} underlyings`}
          >
            <ResolvedTable rows={resolvedRows} />
          </Panel>
        </div>
      )}

      {portfolio && isView && (
        <>
          <PositionPicker
            open={includePickerOpen}
            positions={props.allPositions}
            excludeIds={[...portfolio.manual_include_ids, ...portfolio.manual_exclude_ids]}
            onCancel={() => setIncludePickerOpen(false)}
            onConfirm={async (ids) => { setIncludePickerOpen(false); await props.onAddInclude(ids); }}
            title="ADD MANUAL INCLUDES"
          />
          <PositionPicker
            open={excludePickerOpen}
            positions={props.allPositions}
            excludeIds={[...portfolio.manual_exclude_ids, ...portfolio.manual_include_ids]}
            onCancel={() => setExcludePickerOpen(false)}
            onConfirm={async (ids) => { setExcludePickerOpen(false); await props.onAddExclude(ids); }}
            title="ADD MANUAL EXCLUDES"
          />
          <SourcePicker
            open={sourcePickerOpen}
            portfolios={props.allPortfolios}
            currentPortfolioId={portfolio.id}
            excludeIds={portfolio.source_portfolio_ids}
            onCancel={() => setSourcePickerOpen(false)}
            onConfirm={async (ids) => { setSourcePickerOpen(false); await props.onAddSource(ids); }}
          />
        </>
      )}
    </>
  );
}
```

- [ ] **Step 3: Add the helper components below the Portfolios export**

Append to the bottom of `frontend/src/routes/Portfolios.tsx`:

```tsx
function portfolioChips(portfolio: PortfolioDetail | null): string[] {
  if (!portfolio) return ['—'];
  const updated = portfolio.updated_at ? `updated ${portfolio.updated_at.slice(0, 10)}` : '';
  const chips = [portfolio.kind.toUpperCase(), `${portfolio.resolved_position_count} positions`];
  if (updated) chips.push(updated);
  if (portfolio.tags.length) chips.push(portfolio.tags.slice(0, 3).join(' · '));
  return chips;
}

type TileValues = {
  positions: string;
  underlyings: string;
  netQty: string;
  netQtyVariant: 'default' | 'pos' | 'neg';
  status: string;
};

function computeTiles(rows: PreviewPosition[]): TileValues {
  if (rows.length === 0) {
    return {
      positions: '—', underlyings: '—', netQty: '—',
      netQtyVariant: 'default', status: '—',
    };
  }
  const underlyings = new Set(rows.map((row) => row.underlying)).size;
  const netQty = rows.reduce((acc, row) => acc + row.quantity, 0);
  const allOpen = rows.every((row) => row.status === 'open');
  return {
    positions: String(rows.length),
    underlyings: String(underlyings),
    netQty: signed(netQty),
    netQtyVariant: netQty > 0 ? 'pos' : netQty < 0 ? 'neg' : 'default',
    status: allOpen ? 'ALL OPEN' : 'MIXED',
  };
}

function signed(value: number): string {
  if (value === 0) return '0';
  return value > 0 ? `+${value}` : String(value);
}

function saveStateLabel(state: SaveState): string {
  if (state.kind === 'editing') return 'editing…';
  if (state.kind === 'saving') return 'saving…';
  if (state.kind === 'saved') return `auto-saved ${secondsAgo(state.at)}s ago`;
  if (state.kind === 'error') return `save failed — ${state.message}`;
  return '';
}

function secondsAgo(timestamp: number): number {
  return Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
}

function NewPortfolioMenu({ onSelect }: { onSelect: (kind: PortfolioKind) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="wl-portfolios__newmenu">
      <Button onClick={() => setOpen((current) => !current)}>
        <Plus size={16} aria-hidden="true" />
        New
      </Button>
      {open && (
        <ul className="wl-portfolios__newmenu-list" role="menu">
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onSelect('view'); }}>
              New view portfolio
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onSelect('container'); }}>
              New container portfolio
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

function OverflowMenu({
  onDelete, onRunRisk, disabled,
}: { onDelete: () => void; onRunRisk: () => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  if (disabled) {
    return (
      <Button variant="ghost" iconOnly disabled aria-label="More actions">
        <MoreHorizontal size={16} aria-hidden="true" />
      </Button>
    );
  }
  return (
    <div className="wl-portfolios__newmenu">
      <Button variant="ghost" iconOnly aria-label="More actions" onClick={() => setOpen((cur) => !cur)}>
        <MoreHorizontal size={16} aria-hidden="true" />
      </Button>
      {open && (
        <ul className="wl-portfolios__newmenu-list wl-portfolios__newmenu-list--right" role="menu">
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onRunRisk(); }}>
              Run Risk
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onDelete(); }}>
              Delete portfolio
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

function RuleFieldset({
  rule, onChange,
}: { rule: FilterRule | null; onChange: (rule: FilterRule | null) => Promise<void> }) {
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>RULE</legend>
      <RuleEditor rule={rule} onChange={(next) => { void onChange(next); }} />
    </fieldset>
  );
}

function SourcesFieldset({
  portfolio, allPortfolios, onOpen, onRemove,
}: {
  portfolio: PortfolioDetail;
  allPortfolios: PortfolioSummary[];
  onOpen: () => void;
  onRemove: (id: number) => Promise<void>;
}) {
  const sources = portfolio.source_portfolio_ids
    .map((id) => allPortfolios.find((item) => item.id === id))
    .filter(Boolean) as PortfolioSummary[];
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>SOURCES</legend>
      <div className="wl-portfolios__fieldset-head">
        <span className="wl-portfolios__fieldset-meta">
          {sources.length === 0
            ? 'No source portfolios — this view is defined by its own rule only.'
            : `${sources.length} selected · union`}
        </span>
        <Button variant="ghost" onClick={onOpen}>+ source</Button>
      </div>
      {sources.length > 0 && (
        <div className="wl-portfolios__chiprow">
          {sources.map((source) => (
            <Chip key={source.id} onRemove={() => { void onRemove(source.id); }}>
              <KindChip kind={source.kind} /> {source.name}
            </Chip>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function ManualFieldset({
  legend, ids, positions, onOpen, onRemove,
}: {
  legend: string;
  ids: number[];
  positions: PickerPosition[];
  onOpen: () => void;
  onRemove: (id: number) => Promise<void>;
}) {
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>{legend}</legend>
      <div className="wl-portfolios__fieldset-head">
        <span className="wl-portfolios__fieldset-meta">{ids.length} selected</span>
        <Button variant="ghost" onClick={onOpen}>+ pick position</Button>
      </div>
      {ids.length === 0 ? (
        <span className="wl-portfolios__empty-inline">None.</span>
      ) : (
        <div className="wl-portfolios__chiprow">
          {ids.map((id) => {
            const position = positions.find((item) => item.id === id);
            return (
              <Chip key={id} onRemove={() => { void onRemove(id); }}>
                #{id} {position?.underlying ?? ''}
              </Chip>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}

function TagsFieldset({
  tags, onChange,
}: { tags: string[]; onChange: (tags: string[]) => Promise<void> }) {
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>TAGS</legend>
      <TagEditor tags={tags} onChange={(next) => { void onChange(next); }} />
    </fieldset>
  );
}

function ResolvedTable({ rows }: { rows: PreviewPosition[] }) {
  const columns: Column<PreviewPosition>[] = [
    { key: 'id', header: 'TRADE', width: '0.8fr', numeric: true },
    { key: 'underlying', header: 'UNDER', width: '1fr' },
    { key: 'product_type', header: 'PRODUCT', width: '1.4fr' },
    { key: 'quantity', header: 'QTY', width: '0.6fr', numeric: true, render: (row) => signed(row.quantity) },
    { key: 'status', header: 'STATUS', width: '0.7fr' },
  ];
  if (rows.length === 0) {
    return <Empty message="No positions match this view." symbol="◌" />;
  }
  return <Table columns={columns} rows={rows} rowKey={(row) => row.id} />;
}
```

- [ ] **Step 4: Verify TypeScript compiles for Portfolios.tsx (in isolation)**

Run: `cd frontend && npx tsc -b --noEmit 2>&1 | grep "Portfolios.tsx"`
Expected: No errors specific to `Portfolios.tsx`. (Errors in `Portfolios.live.tsx` are still expected — fixed in Task 9.)

- [ ] **Step 5: Stage but do not commit yet — wait until Task 8 finishes the CSS**

The component depends on classes from the new CSS file. Move on to Task 8.

---

## Task 8: Rewrite Portfolios.css

**Why:** Drop the old sidebar/detail-split styles; add styles for the new layout (select-field, tiles row, twopane grid, fieldset styling, newmenu).

**Files:**
- Modify: `frontend/src/routes/Portfolios.css` (full replace)

- [ ] **Step 1: Replace Portfolios.css with the new styles**

Replace the entire contents of `frontend/src/routes/Portfolios.css` with:

```css
.wl-portfolios__actions {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.wl-portfolios__select-field {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  min-width: 220px;
}
.wl-portfolios__select-field span {
  flex: 0 0 auto;
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-portfolios__select-field select {
  min-width: 0;
  flex: 1 1 auto;
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-small-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}

.wl-portfolios__newmenu { position: relative; }
.wl-portfolios__newmenu-list {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--ink);
  background: var(--paper);
  min-width: 220px;
  z-index: 50;
  box-shadow: 0 8px 20px color-mix(in oklab, var(--ink) 12%, transparent);
}
.wl-portfolios__newmenu-list--right { left: auto; right: 0; }
.wl-portfolios__newmenu-list li { border-bottom: 1px solid var(--hairline); }
.wl-portfolios__newmenu-list li:last-child { border-bottom: 0; }
.wl-portfolios__newmenu-list button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px var(--gap-3);
  background: var(--paper);
  border: 0;
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  color: var(--ink);
  cursor: pointer;
}
.wl-portfolios__newmenu-list button:hover { background: var(--paper-2); }

.wl-portfolios__tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--gap-3);
  margin-bottom: var(--gap-3);
}

.wl-portfolios__twopane {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--gap-3);
  min-height: 0;
}

.wl-portfolios__fieldset {
  border: 1px solid var(--paper-3);
  margin: 0 0 var(--gap-3);
  padding: var(--gap-3);
  min-width: 0;
}
.wl-portfolios__fieldset:last-child { margin-bottom: 0; }
.wl-portfolios__fieldset legend {
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0 var(--gap-1);
}
.wl-portfolios__fieldset-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--gap-2);
  margin-bottom: var(--gap-2);
}
.wl-portfolios__fieldset-meta {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}

.wl-portfolios__chiprow { display: flex; flex-wrap: wrap; gap: var(--gap-1); }
.wl-portfolios__empty-inline { font-family: var(--font-ui); font-size: var(--type-small-size); color: var(--ink-2); font-style: italic; }

.wl-portfolios__container-hint {
  margin: 0 0 var(--gap-3);
  padding: var(--gap-3);
  border: 1px solid var(--hairline);
  background: var(--paper-2);
  color: var(--ink-2);
  font-size: var(--type-small-size);
}

@media (max-width: 980px) {
  .wl-portfolios__tiles { grid-template-columns: repeat(2, 1fr); }
  .wl-portfolios__twopane { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .wl-portfolios__tiles { grid-template-columns: 1fr; }
  .wl-portfolios__actions { justify-content: flex-start; }
  .wl-portfolios__select-field { width: 100%; }
}
```

- [ ] **Step 2: Verify Portfolios.tsx + Portfolios.css render without TypeScript errors**

Run: `cd frontend && npx tsc -b --noEmit 2>&1 | grep -E "Portfolios\.(tsx|css)"`
Expected: No errors in `Portfolios.tsx`. Errors in `Portfolios.live.tsx` remain pending until Task 9.

- [ ] **Step 3: Stage the route changes (do not commit yet)**

Wait for Task 9 to finish the .live.tsx wiring; commit together for atomic-ness.

---

## Task 9: Rewrite Portfolios.live.tsx (wire modals, save state, drop window.prompt/confirm)

**Why:** This is the controller layer. It must now own the `saveState` reducer, hold create/delete dialog state, and pass batched arrays into add-include/add-exclude/add-source. The picker-open state lives in `Portfolios.tsx` (Task 7); the create/delete dialogs live here because they need API access.

**Files:**
- Modify: `frontend/src/routes/Portfolios.live.tsx`

- [ ] **Step 1: Replace Portfolios.live.tsx entirely**

Replace the contents of `frontend/src/routes/Portfolios.live.tsx` with:

```tsx
import { useEffect, useReducer, useState } from 'react';
import { api } from '../api/client';
import { PortfolioCreateDialog } from '../components/PortfolioCreateDialog';
import { PortfolioDeleteDialog } from '../components/PortfolioDeleteDialog';
import type {
  FilterRule,
  PortfolioDetail,
  PortfolioKind,
  PortfolioMembership,
  PortfolioSummary,
} from '../types';
import { Portfolios, type SaveState } from './Portfolios';

type PreviewRow = {
  id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

type SaveAction =
  | { type: 'edit' }
  | { type: 'start' }
  | { type: 'success' }
  | { type: 'fail'; message: string }
  | { type: 'reset' };

function saveReducer(state: SaveState, action: SaveAction): SaveState {
  switch (action.type) {
    case 'edit': return { kind: 'editing' };
    case 'start': return { kind: 'saving' };
    case 'success': return { kind: 'saved', at: Date.now() };
    case 'fail': return { kind: 'error', message: action.message };
    case 'reset': return { kind: 'idle' };
  }
}

export function PortfoliosLive() {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [selected, setSelected] = useState<PortfolioDetail | null>(null);
  const [allPositions, setAllPositions] = useState<PreviewRow[]>([]);
  const [pendingPreview, setPendingPreview] = useState<PreviewRow[] | null>(null);
  const [saveState, dispatchSave] = useReducer(saveReducer, { kind: 'idle' });
  const [createKind, setCreateKind] = useState<PortfolioKind | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const refreshList = async () => {
    const rows = await api<PortfolioSummary[]>('/api/portfolios');
    setPortfolios(rows);
  };

  const refreshSelected = async (id: number) => {
    const detail = await api<PortfolioDetail>(`/api/portfolios/${id}`);
    setSelected(detail);
  };

  useEffect(() => { refreshList(); }, []);

  useEffect(() => {
    (async () => {
      const rows = await api<PortfolioSummary[]>('/api/portfolios');
      const positions: PreviewRow[] = [];
      for (const row of rows) {
        const detail = await api<PortfolioDetail>(`/api/portfolios/${row.id}`);
        for (const position of detail.positions || []) {
          positions.push(position as PreviewRow);
        }
      }
      setAllPositions(positions);
    })();
  }, []);

  useEffect(() => {
    if (!selected || selected.kind !== 'view') {
      setPendingPreview(null);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const body = await api<PortfolioMembership>(`/api/portfolios/${selected.id}/membership`);
        const idSet = new Set(body.position_ids);
        setPendingPreview(allPositions.filter((position) => idSet.has(position.id)));
      } catch {
        setPendingPreview(null);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [
    selected?.id,
    JSON.stringify(selected?.filter_rule),
    selected?.manual_include_ids?.join(','),
    selected?.manual_exclude_ids?.join(','),
    selected?.source_portfolio_ids?.join(','),
    allPositions,
  ]);

  const withSave = async (fn: () => Promise<void>) => {
    dispatchSave({ type: 'start' });
    try {
      await fn();
      dispatchSave({ type: 'success' });
    } catch (err) {
      dispatchSave({ type: 'fail', message: err instanceof Error ? err.message : 'unknown error' });
    }
  };

  const onSaveRule = (rule: FilterRule | null) =>
    selected
      ? withSave(async () => {
          await api(`/api/portfolios/${selected.id}/rule`, {
            method: 'PUT',
            body: JSON.stringify({ filter_rule: rule }),
          });
          await refreshSelected(selected.id);
        })
      : Promise.resolve();

  const idsAction = (
    pathSuffix: string,
    method: 'POST' | 'DELETE',
    key: 'position_ids' | 'portfolio_ids',
  ) =>
    (ids: number | number[]) =>
      selected
        ? withSave(async () => {
            const payload = Array.isArray(ids) ? ids : [ids];
            await api(`/api/portfolios/${selected.id}/${pathSuffix}`, {
              method,
              body: JSON.stringify({ [key]: payload }),
            });
            await Promise.all([refreshSelected(selected.id), refreshList()]);
          })
        : Promise.resolve();

  const onSetTags = (tags: string[]) =>
    selected
      ? withSave(async () => {
          await api(`/api/portfolios/${selected.id}/tags`, {
            method: 'PUT',
            body: JSON.stringify({ tags }),
          });
          await refreshSelected(selected.id);
        })
      : Promise.resolve();

  const onCreate = async (input: { name: string; kind: PortfolioKind }) => {
    await api('/api/portfolios', { method: 'POST', body: JSON.stringify(input) });
    setCreateKind(null);
    await refreshList();
  };

  const onDelete = async () => {
    if (!selected) return;
    await fetch(`/api/portfolios/${selected.id}`, { method: 'DELETE' });
    setDeleteOpen(false);
    setSelected(null);
    await refreshList();
  };

  return (
    <>
      <Portfolios
        portfolios={portfolios}
        allPortfolios={portfolios}
        allPositions={allPositions}
        selected={selected}
        selectedPortfolioId={selected?.id ?? null}
        pendingMembershipPreview={pendingPreview}
        saveState={saveState}
        onSelectPortfolio={refreshSelected}
        onOpenCreate={(kind) => setCreateKind(kind)}
        onOpenDelete={() => setDeleteOpen(true)}
        onSaveRule={onSaveRule}
        onAddInclude={(ids) => idsAction('includes', 'POST', 'position_ids')(ids)}
        onRemoveInclude={(id) => idsAction('includes', 'DELETE', 'position_ids')(id)}
        onAddExclude={(ids) => idsAction('excludes', 'POST', 'position_ids')(ids)}
        onRemoveExclude={(id) => idsAction('excludes', 'DELETE', 'position_ids')(id)}
        onAddSource={(ids) => idsAction('sources', 'POST', 'portfolio_ids')(ids)}
        onRemoveSource={(id) => idsAction('sources', 'DELETE', 'portfolio_ids')(id)}
        onSetTags={onSetTags}
        onRunPricing={() =>
          selected &&
          fetch(`/api/portfolios/${selected.id}/positions/price`, {
            method: 'POST',
            body: JSON.stringify({}),
            headers: { 'Content-Type': 'application/json' },
          })
        }
        onRunRisk={() => {}}
      />
      <PortfolioCreateDialog
        open={createKind !== null}
        kind={createKind ?? 'view'}
        onCancel={() => setCreateKind(null)}
        onCreate={onCreate}
      />
      <PortfolioDeleteDialog
        open={deleteOpen}
        portfolioName={selected?.name ?? ''}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={onDelete}
      />
    </>
  );
}
```

- [ ] **Step 2: Run a TypeScript check on the whole frontend**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: No errors in any of the files we've touched. Tests will still fail (we haven't updated them yet) — that's fine for tsc.

- [ ] **Step 3: Commit the route + controller atomically**

```bash
git add frontend/src/routes/Portfolios.tsx \
        frontend/src/routes/Portfolios.css \
        frontend/src/routes/Portfolios.live.tsx
git commit -m "feat(portfolios): rewrite layout around PageHeader + Tiles + two Panels"
```

---

## Task 10: Update Portfolios.test.tsx

**Why:** The existing tests assert the old sidebar/filter chip layout. Rewrite them to assert the new PageHeader/Tiles/Panel contract and the modal-trigger callbacks.

**Files:**
- Modify: `frontend/src/routes/Portfolios.test.tsx`

- [ ] **Step 1: Replace Portfolios.test.tsx**

Replace the contents of `frontend/src/routes/Portfolios.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { Portfolios } from './Portfolios';
import type { PortfolioDetail, PortfolioSummary } from '../types';

const portfolios: PortfolioSummary[] = [
  {
    id: 1, name: 'Snow', kind: 'view', base_currency: 'USD',
    description: null, tags: [], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 5, created_at: '2026-05-10', updated_at: '2026-05-10',
  },
  {
    id: 2, name: 'Book', kind: 'container', base_currency: 'USD',
    description: null, tags: ['desk'], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 12, created_at: '2026-05-10', updated_at: '2026-05-10',
  },
];

const viewDetail: PortfolioDetail = {
  ...portfolios[0],
  positions: [],
};

const noop = () => {};
const asyncNoop = async () => {};

const baseProps = {
  portfolios,
  allPortfolios: portfolios,
  allPositions: [] as { id: number; underlying: string; product_type: string }[],
  selected: viewDetail,
  selectedPortfolioId: 1,
  pendingMembershipPreview: [
    { id: 10, underlying: 'AAPL', product_type: 'Snowball', quantity: -2, entry_price: 100, status: 'open' },
    { id: 11, underlying: 'TSLA', product_type: 'Snowball', quantity: 1, entry_price: 200, status: 'open' },
    { id: 12, underlying: 'AAPL', product_type: 'Snowball', quantity: -1, entry_price: 100, status: 'open' },
  ],
  saveState: { kind: 'idle' } as const,
  onSelectPortfolio: noop,
  onOpenCreate: noop,
  onOpenDelete: noop,
  onSaveRule: asyncNoop,
  onAddInclude: asyncNoop,
  onRemoveInclude: asyncNoop,
  onAddExclude: asyncNoop,
  onRemoveExclude: asyncNoop,
  onAddSource: asyncNoop,
  onRemoveSource: asyncNoop,
  onSetTags: asyncNoop,
  onRunPricing: noop,
  onRunRisk: noop,
};

test('renders PageHeader title with selected portfolio name', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('PORTFOLIOS · Snow');
});

test('renders four Tiles with computed values for resolved rows', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByText('POSITIONS')).toBeInTheDocument();
  expect(screen.getByText('UNDERLYINGS')).toBeInTheDocument();
  expect(screen.getByText('NET QTY')).toBeInTheDocument();
  expect(screen.getByText('STATUS')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();        // positions count (3 rows)
  expect(screen.getByText('2')).toBeInTheDocument();        // unique underlyings (AAPL, TSLA)
  expect(screen.getByText('-2')).toBeInTheDocument();       // signed net qty (-2 + 1 + -1)
  expect(screen.getByText('ALL OPEN')).toBeInTheDocument();
});

test('renders five fieldsets for a View portfolio', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByRole('group', { name: /rule/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /sources/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /manual includes/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /manual excludes/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /^tags$/i })).toBeInTheDocument();
});

test('renders only TAGS fieldset for a Container portfolio', () => {
  const containerDetail: PortfolioDetail = { ...portfolios[1], positions: [] };
  render(
    <Portfolios
      {...baseProps}
      selected={containerDetail}
      selectedPortfolioId={2}
      pendingMembershipPreview={null}
    />,
  );
  expect(screen.queryByRole('group', { name: /rule/i })).not.toBeInTheDocument();
  expect(screen.getByRole('group', { name: /^tags$/i })).toBeInTheDocument();
  expect(screen.getByText(/owned positions imported via XLSX/i)).toBeInTheDocument();
});

test('Run Pricing primary button calls onRunPricing', async () => {
  const onRunPricing = vi.fn();
  render(<Portfolios {...baseProps} onRunPricing={onRunPricing} />);
  await userEvent.click(screen.getByRole('button', { name: /run pricing/i }));
  expect(onRunPricing).toHaveBeenCalledTimes(1);
});

test('Portfolio select fires onSelectPortfolio with chosen id', async () => {
  const onSelectPortfolio = vi.fn();
  render(<Portfolios {...baseProps} onSelectPortfolio={onSelectPortfolio} />);
  await userEvent.selectOptions(screen.getByLabelText(/select portfolio/i), '2');
  expect(onSelectPortfolio).toHaveBeenCalledWith(2);
});

test('New menu opens and fires onOpenCreate with the chosen kind', async () => {
  const onOpenCreate = vi.fn();
  render(<Portfolios {...baseProps} onOpenCreate={onOpenCreate} />);
  await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /new view portfolio/i }));
  expect(onOpenCreate).toHaveBeenCalledWith('view');
});

test('Overflow menu Delete fires onOpenDelete', async () => {
  const onOpenDelete = vi.fn();
  render(<Portfolios {...baseProps} onOpenDelete={onOpenDelete} />);
  await userEvent.click(screen.getByRole('button', { name: /more actions/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /delete portfolio/i }));
  expect(onOpenDelete).toHaveBeenCalledTimes(1);
});

test('saving state shows label on Definition panel', () => {
  render(<Portfolios {...baseProps} saveState={{ kind: 'saving' }} />);
  expect(screen.getByText(/saving…/i)).toBeInTheDocument();
});

test('error save state shows the error message', () => {
  render(<Portfolios {...baseProps} saveState={{ kind: 'error', message: 'boom' }} />);
  expect(screen.getByText(/save failed — boom/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests**

Run: `cd frontend && npm test -- Portfolios.test`
Expected: PASS — all ten tests.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Portfolios.test.tsx
git commit -m "test(portfolios): rewrite tests for new layout"
```

---

## Task 11: Update Portfolios.live.test.tsx

**Why:** The existing live test only checks one mount-load case. Add tests for the create-modal path (replacing `window.prompt`) and the delete-modal path (replacing `window.confirm`).

**Files:**
- Modify: `frontend/src/routes/Portfolios.live.test.tsx`

- [ ] **Step 1: Replace Portfolios.live.test.tsx**

Replace the contents of `frontend/src/routes/Portfolios.live.test.tsx` with:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { PortfoliosLive } from './Portfolios.live';

const fetchMock = vi.fn();

beforeEach(() => {
  globalThis.fetch = fetchMock as any;
  fetchMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

function jsonResponse(body: any, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function makePortfolio(overrides: Partial<any> = {}) {
  return {
    id: 1,
    name: 'Snow',
    kind: 'view',
    base_currency: 'USD',
    description: null,
    tags: [],
    filter_rule: null,
    manual_include_ids: [],
    manual_exclude_ids: [],
    source_portfolio_ids: [],
    resolved_position_count: 0,
    created_at: 't',
    updated_at: 't',
    positions: [],
    ...overrides,
  };
}

test('lists portfolios on mount', async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /snow · view/i })).toBeInTheDocument(),
  );
});

test('opens create dialog and POSTs the new portfolio', async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/api/portfolios') && init?.method === 'POST') {
      return jsonResponse({ ...makePortfolio({ id: 2, name: 'NewView' }) });
    }
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() => expect(screen.getByRole('button', { name: /^new$/i })).toBeInTheDocument());

  await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /new view portfolio/i }));
  await userEvent.type(screen.getByLabelText(/name/i), 'NewView');
  await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

  await waitFor(() => {
    const postCall = fetchMock.mock.calls.find(([url, init]: any[]) =>
      url === '/api/portfolios' && init?.method === 'POST',
    );
    expect(postCall).toBeTruthy();
    expect(JSON.parse(postCall![1].body)).toEqual({ name: 'NewView', kind: 'view' });
  });
});

test('opens delete dialog and DELETEs the portfolio', async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1') && init?.method === 'DELETE') {
      return jsonResponse({}, true);
    }
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() => expect(screen.getByRole('combobox', { name: /select portfolio/i })).toBeInTheDocument());

  await userEvent.selectOptions(screen.getByLabelText(/select portfolio/i), '1');
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('PORTFOLIOS · Snow'));

  await userEvent.click(screen.getByRole('button', { name: /more actions/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /delete portfolio/i }));
  await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));

  await waitFor(() => {
    const deleteCall = fetchMock.mock.calls.find(([url, init]: any[]) =>
      url === '/api/portfolios/1' && init?.method === 'DELETE',
    );
    expect(deleteCall).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the live tests**

Run: `cd frontend && npm test -- Portfolios.live.test`
Expected: PASS — all three tests.

- [ ] **Step 3: Run the full test suite to catch unrelated regressions**

Run: `cd frontend && npm test`
Expected: All tests pass. If any previously-passing test broke, address it before continuing.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Portfolios.live.test.tsx
git commit -m "test(portfolios.live): add create-dialog and delete-dialog flows"
```

---

## Task 12: Manual verification

**Why:** Type + unit tests cover behavior, but visual hierarchy, density toggles, and theme switching must be verified in a browser per the spec's Manual verification section.

**Files:** none — runtime check only.

- [ ] **Step 1: Build the frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 2: Start dev server and exercise the page**

Run: `cd frontend && npm run dev`
Open the printed URL, navigate to **Portfolios**, then verify each:

- [ ] PageHeader shows `PORTFOLIOS · {name}` when a portfolio is selected, `PORTFOLIOS · —` otherwise.
- [ ] Four Tiles render with values from the resolved set; STATUS shows `ALL OPEN` or `MIXED`; NET QTY colors `pos`/`neg` correctly.
- [ ] DEFINITION panel shows all five fieldsets for a View portfolio.
- [ ] Switching to a Container portfolio shows only the TAGS fieldset + the "Container holds owned positions…" hint.
- [ ] `+ New ▾` opens the menu; choosing "New view portfolio" opens the create dialog; submitting creates the portfolio (visible in the select).
- [ ] `⋯` overflow menu shows "Run Risk" and "Delete portfolio"; Delete opens the confirmation modal with the portfolio name; clicking Delete actually removes the portfolio.
- [ ] `+ source` opens the SourcePicker modal; selecting and confirming adds source chips (and increases membership preview if relevant).
- [ ] `+ pick position` (Manual Includes and Manual Excludes) opens the PositionPicker; the picker hides positions already on the other manual list.
- [ ] Edit a rule and watch the DEFINITION panel meta cycle through `editing… → saving… → auto-saved 0s ago`.
- [ ] Toggle the theme button to dark — all colors flip cleanly (no stuck rounded blue chips, no broken backgrounds).
- [ ] Toggle the density button to compact — row heights and paddings tighten as expected.
- [ ] Resize the window narrow (under 980px) — two-pane collapses to one column.

- [ ] **Step 3: Commit any final touch-ups**

If manual verification surfaces small issues (e.g., a missing class), fix them as a single commit:

```bash
git add frontend/src/...
git commit -m "fix(portfolios): manual verification polish"
```

If everything is clean, no commit is needed.

---

## Self-Review

**1. Spec coverage:**

| Spec requirement | Task |
|---|---|
| Add PageHeader | Task 7 (rewrite Portfolios.tsx) |
| Four Tiles (POSITIONS, UNDERLYINGS, NET QTY, STATUS) | Task 7 + computeTiles |
| Two Panels (Definition / Resolved Positions) | Task 7 |
| Five fieldsets in Definition (View) | Task 7 (RuleFieldset, SourcesFieldset, ManualFieldset × 2, TagsFieldset) |
| Single TAGS fieldset for Container + container hint | Task 7 + Task 8 (container-hint styles) |
| Portfolio select in header action slot | Task 7 + Task 8 (select-field styles) |
| `+ New ▾` menu | Task 7 (NewPortfolioMenu) + Task 8 (newmenu-list styles) |
| `Run Pricing` primary, `⋯` overflow | Task 7 (OverflowMenu) |
| Auto-save indicator (editing → saving → saved → failed) | Task 7 (saveStateLabel) + Task 9 (saveReducer/withSave) |
| Replace window.prompt with create dialog | Task 4 + Task 9 |
| Replace window.confirm with delete dialog | Task 5 + Task 9 |
| Real SourcePicker modal | Task 3 + Task 9 wiring |
| Real PositionPicker with excludeIds | Task 2 + Task 7 wiring |
| Fix KindChip latent token bug | Task 1 |
| Update tests | Tasks 10, 11 |
| Manual verification (light/dark, compact, View↔Container) | Task 12 |
| Responsive breakpoints (980px, 640px) | Task 8 (media queries) |

No gaps.

**2. Placeholder scan:** No `TBD`, `TODO`, `implement later`, or vague "add error handling" patterns. Every code step shows complete code; every command shows expected output.

**3. Type consistency:**
- `SaveState` defined in Task 6, used in Task 7 (label) and Task 9 (reducer) — consistent shape.
- `PreviewPosition` shared between Portfolios.tsx (Task 7) and Portfolios.live.tsx (`PreviewRow` is structurally identical — same five+ fields). Engineer should keep them in sync; flagged in Task 6 implicitly via the props type.
- `onAddInclude`/`onAddExclude`/`onAddSource` take `number[]` everywhere (Task 6 props, Task 7 PositionPicker confirm, Task 9 idsAction with Array.isArray guard).
- `PortfolioCreateDialog` `onCreate` shape `{ name: string; kind: PortfolioKind }` matches across Task 4 (def), Task 9 (caller).
- `KindChip` accepts `kind: PortfolioKind` — same type used in dialogs and pickers.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-portfolios-ui-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration on each piece.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with review checkpoints.

Which approach?
