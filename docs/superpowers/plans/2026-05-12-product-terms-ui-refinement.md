# Product Terms UI Refinement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine `ProductTermsForm` to use a fieldset/legend structure, a 3-column field grid, and a structured read-only key-value display for nested configs (replacing raw JSON textarea).

**Architecture:** Pure presentation changes across one component (`ProductTermsForm.tsx`) and one CSS file (`Positions.css`). No data model, API, or routing changes. The only behaviorally meaningful change — nested config rendering — is covered by new unit tests.

**Tech Stack:** React 18, TypeScript, Vitest, @testing-library/react, @testing-library/jest-dom

---

## Files

| File | Action |
|---|---|
| `frontend/src/components/ProductTermsForm.test.tsx` | Create — unit tests for nested config KV rendering |
| `frontend/src/components/ProductTermsForm.tsx` | Modify — fieldset/legend wrapper, 3-col grid, nested config KV body |
| `frontend/src/routes/Positions.css` | Modify — 3-col grid, remove unused `.wl-positions__product-terms` styles |

---

### Task 1: Write failing tests for nested config rendering

**Files:**
- Create: `frontend/src/components/ProductTermsForm.test.tsx`

- [ ] **Step 1: Create the test file**

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProductTermsForm } from './ProductTermsForm';

describe('ProductTermsForm nested config rendering', () => {
  it('renders scalar nested config values as labeled readonly inputs, not raw JSON', () => {
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { barrier_level: 999, observation: 'continuous' },
        }}
        onChange={vi.fn()}
      />,
    );
    // 999 is unique — not present in any main grid field (all empty here)
    const input = screen.getByDisplayValue('999');
    expect(input.readOnly).toBe(true);
    expect(input.tagName).toBe('INPUT');
    // raw JSON key names must not appear as displayed values
    expect(screen.queryByDisplayValue(/barrier_level/)).not.toBeInTheDocument();
  });

  it('renders boolean nested config values as disabled checkboxes', () => {
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { is_monitored: true, barrier_level: 999 },
        }}
        onChange={vi.fn()}
      />,
    );
    const checkboxes = screen.getAllByRole('checkbox');
    const disabled = checkboxes.filter((cb) => (cb as HTMLInputElement).disabled);
    expect(disabled).toHaveLength(1);
    expect((disabled[0] as HTMLInputElement).checked).toBe(true);
  });

  it('falls back to a readonly textarea for array/object values within a nested config', () => {
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: {
            barrier_level: 999,
            schedule: [{ date: '2025-06-01', level: 108 }],
          },
        }}
        onChange={vi.fn()}
      />,
    );
    // scalar still renders as input
    expect(screen.getByDisplayValue('999').tagName).toBe('INPUT');
    // array value falls back to textarea
    const textareas = screen.getAllByRole('textbox');
    const textarea = textareas.find((el) => el.tagName === 'TEXTAREA') as HTMLTextAreaElement | undefined;
    expect(textarea).toBeDefined();
    expect(textarea!.readOnly).toBe(true);
  });

  it('shows a "Read-only · system computed" note for nested config sections', () => {
    render(
      <ProductTermsForm
        productType="BarrierOption"
        productKwargs={{
          barrier_config: { barrier_level: 999 },
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/read-only.*system computed/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd frontend && npx vitest run src/components/ProductTermsForm.test.tsx
```

Expected: 4 failures — `getByDisplayValue` finds no `INPUT` with value `999` because the current implementation renders a raw JSON `<textarea>`.

---

### Task 2: Update CSS

**Files:**
- Modify: `frontend/src/routes/Positions.css`

- [ ] **Step 1: Change product-terms-grid to 3 columns**

Find `.wl-positions__product-terms-grid` and change the grid-template-columns:

```css
/* BEFORE */
.wl-positions__product-terms-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--gap-3);
}

/* AFTER */
.wl-positions__product-terms-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--gap-3);
}
```

The existing `@media (max-width: 640px)` block already has `.wl-positions__product-terms-grid { grid-template-columns: 1fr; }` — leave that unchanged.

- [ ] **Step 2: Remove the unused `.wl-positions__product-terms` styles**

Delete both of these rules:

```css
/* DELETE */
.wl-positions__product-terms {
  margin-top: var(--gap-3);
}
/* DELETE */
.wl-positions__product-terms h4 {
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 var(--gap-2) 0;
}
```

The `wl-positions__term-form` class (already in the same CSS file at the `.wl-positions__term-form` and `.wl-positions__term-form fieldset` rules) provides the margin-top and fieldset/legend styles.

- [ ] **Step 3: Verify no other references to the removed classes**

```bash
grep -rn "wl-positions__product-terms" frontend/src/
```

Expected output: only two matches — `.wl-positions__product-terms-grid` in `Positions.css` and in `ProductTermsForm.tsx`. The bare `.wl-positions__product-terms` class should no longer appear anywhere.

---

### Task 3: Restructure ProductTermsForm — fieldset + legend wrapper

**Files:**
- Modify: `frontend/src/components/ProductTermsForm.tsx`

- [ ] **Step 1: Replace the outer wrapper and heading with fieldset + legend**

Change the `return` statement in `ProductTermsForm`. Replace the opening and closing div/h4:

```tsx
/* BEFORE — outer wrapper */
return (
  <div className="wl-positions__product-terms">
    <h4>Product Terms</h4>
    <div className="wl-positions__product-terms-grid">
      {fields.map(renderField)}
    </div>

    {nestedConfigs.length > 0 && (
      <div className="wl-positions__term-groups">
        {nestedConfigs.map(([key, value]) => (
          <details ...> ... </details>
        ))}
      </div>
    )}

    {extraFields.length > 0 && (
      <details className="wl-positions__term-group">
        ...
      </details>
    )}
  </div>
);

/* AFTER — outer wrapper */
return (
  <div className="wl-positions__term-form">
    <fieldset>
      <legend>Product Terms</legend>
      <div className="wl-positions__product-terms-grid">
        {fields.map(renderField)}
      </div>

      {nestedConfigs.length > 0 && (
        <div className="wl-positions__term-groups">
          {nestedConfigs.map(([key, value]) => (
            <details
              key={key}
              className="wl-positions__term-group"
              open={expandedConfigs[key]}
              onToggle={() => toggleConfig(key)}
            >
              <summary>
                <span>{key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</span>
                <small>{value && typeof value === 'object' ? `${Object.keys(value).length} fields` : ''}</small>
              </summary>
              <div className="wl-positions__term-group-body">
                <textarea
                  value={JSON.stringify(value, null, 2)}
                  rows={6}
                  onChange={(e) => {
                    try {
                      const parsed = JSON.parse(e.target.value);
                      onChange({ ...productKwargs, [key]: parsed });
                    } catch {
                      // ignore invalid JSON while typing
                    }
                  }}
                />
              </div>
            </details>
          ))}
        </div>
      )}

      {extraFields.length > 0 && (
        <details className="wl-positions__term-group">
          <summary>
            <span>Extra Fields</span>
            <small>{extraFields.length} fields</small>
          </summary>
          <div className="wl-positions__term-group-body">
            <div className="wl-positions__term-grid">
              {extraFields.map(([key, value]) => (
                <label key={key} className="wl-positions__term-field">
                  <span>{key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</span>
                  <input value={value == null ? '' : String(value)} readOnly onChange={() => {}} />
                </label>
              ))}
            </div>
          </div>
        </details>
      )}
    </fieldset>
  </div>
);
```

The nested config accordion body still uses the textarea in this step — that changes in Task 4.

- [ ] **Step 2: Run existing Positions tests to confirm no regressions**

```bash
cd frontend && npx vitest run src/routes/Positions.test.tsx
```

Expected: all existing tests pass.

---

### Task 4: Implement structured KV grid for nested config bodies

**Files:**
- Modify: `frontend/src/components/ProductTermsForm.tsx`

- [ ] **Step 1: Add `renderNestedConfigBody` helper inside the `ProductTermsForm` component**

Add this function directly inside the component body, after the existing `renderField` function:

```tsx
const renderNestedConfigBody = (value: Record<string, unknown>) => {
  const entries = Object.entries(value);
  return (
    <>
      <div className="wl-positions__term-grid">
        {entries.map(([k, v]) => {
          const label = k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
          if (typeof v === 'boolean') {
            return (
              <label key={k} className="wl-positions__check-field">
                <input type="checkbox" checked={v} disabled onChange={() => {}} />
                <span>{label}</span>
              </label>
            );
          }
          if (v !== null && typeof v === 'object') {
            return (
              <label key={k} className="wl-positions__term-field wl-positions__term-field--wide">
                <span>{label}</span>
                <textarea value={JSON.stringify(v, null, 2)} readOnly rows={4} onChange={() => {}} />
              </label>
            );
          }
          return (
            <label key={k} className="wl-positions__term-field">
              <span>{label}</span>
              <input value={v == null ? '' : String(v)} readOnly onChange={() => {}} />
            </label>
          );
        })}
      </div>
      <div className="wl-positions__term-empty">Read-only · system computed</div>
    </>
  );
};
```

`onChange={() => {}}` is required on all controlled readonly inputs/textareas to silence React's uncontrolled-to-controlled warning.

- [ ] **Step 2: Replace the textarea accordion body with `renderNestedConfigBody`**

In the `nestedConfigs.map(...)` section inside the return statement, change the `<div className="wl-positions__term-group-body">` contents:

```tsx
/* BEFORE */
<div className="wl-positions__term-group-body">
  <textarea
    value={JSON.stringify(value, null, 2)}
    rows={6}
    onChange={(e) => {
      try {
        const parsed = JSON.parse(e.target.value);
        onChange({ ...productKwargs, [key]: parsed });
      } catch {
        // ignore invalid JSON while typing
      }
    }}
  />
</div>

/* AFTER */
<div className="wl-positions__term-group-body">
  {renderNestedConfigBody(value as Record<string, unknown>)}
</div>
```

- [ ] **Step 3: Run the new tests — they should now pass**

```bash
cd frontend && npx vitest run src/components/ProductTermsForm.test.tsx
```

Expected: all 4 tests pass.

- [ ] **Step 4: Run the full test suite to confirm no regressions**

```bash
cd frontend && npx vitest run
```

Expected: all tests pass.

---

### Task 5: Commit

- [ ] **Step 1: Stage all changed files and commit**

```bash
git add \
  frontend/src/components/ProductTermsForm.test.tsx \
  frontend/src/components/ProductTermsForm.tsx \
  frontend/src/routes/Positions.css
git commit -m "feat(ui): refine product terms form — fieldset, 3-col grid, structured nested configs"
```

---

## Self-Review

**Spec coverage:**
- ✅ Fieldset + legend wrapper — Task 3
- ✅ 3-column grid — Task 2 (CSS) + Task 3 (markup uses updated class)
- ✅ Structured KV grid for nested configs, read-only — Task 4
- ✅ Fallback textarea for array/object values within nested config — Task 4 `renderNestedConfigBody`
- ✅ "Read-only · system computed" note — Task 4 `renderNestedConfigBody`
- ✅ No changes to Extra Fields section — preserved verbatim in Task 3
- ✅ No changes to main edit form, engine kwargs textarea, or non-container readonly view

**Placeholder scan:** No TBDs, no "implement later". All code steps show complete code.

**Type consistency:**
- `renderNestedConfigBody` is defined in Task 4 Step 1 and called in Task 4 Step 2 — names match exactly.
- `value as Record<string, unknown>` cast is safe: `nestedConfigs` is already filtered to `typeof value === 'object'` in the existing component logic.
- `onChange={() => {}}` pattern used consistently on every readonly controlled input/textarea.
