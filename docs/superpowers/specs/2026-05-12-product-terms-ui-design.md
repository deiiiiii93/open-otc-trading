# Product Terms UI Refinement

**Date:** 2026-05-12  
**Status:** Approved  
**Scope:** `ProductTermsForm.tsx` (editable form) and its CSS in `Positions.css`

---

## Problem

The `ProductTermsForm` component — rendered inside `PositionEditForm` on the Details tab for container-portfolio positions — has three visual weaknesses:

1. **Weak section boundary.** The "Product Terms" section uses a plain `<h4>` label with no containing border, making it visually blend into the surrounding edit form.
2. **Tall 2-column grid.** Products like Snowball and Phoenix define 10 fields each. At 2 columns this produces 5 rows of inputs, requiring significant vertical scrolling inside the modal.
3. **Raw JSON textarea for nested configs.** Nested config objects (`barrier_config`, `payoff_config`, `coupon_config`, `accrual_config`) are rendered inside `<details>` accordions with a raw `<textarea>`, requiring the user to know both the JSON syntax and the field schema. These values are system-computed and not intended to be hand-edited.

---

## Design Decisions

### 1. Fieldset + Legend Structure

Replace the `<h4>Product Terms</h4>` + plain `<div>` wrapper with a `<fieldset>` + `<legend>` pair, matching the existing `wl-positions__term-form` pattern already used for the Pricing Ticket and Market Inputs sections in `Positions.tsx`.

- CSS class: reuse `wl-positions__term-form` on a wrapping `<div>`, with the `<fieldset>` and `<legend>` inside, matching the exact markup pattern of `ReadonlyObjectForm` and `PricingTicket`.
- The legend reads: `"Product Terms"` (no product type inline — the product type is already visible in the main edit form above).

### 2. Three-Column Field Grid

Change `wl-positions__product-terms-grid` from `repeat(2, minmax(0, 1fr))` to `repeat(3, minmax(0, 1fr))`.

- Simple products (4 fields: EuropeanVanilla, Asian) render as a single row of 4 → wraps to 2 rows of 2+2, still clean.
- Dense products (10 fields: Snowball, Phoenix) go from 5 rows to ~4 rows.
- Boolean `check-field` inputs span one grid cell each — no change to their markup needed.
- Mobile breakpoint (`max-width: 640px`) collapses to 1 column as before.

### 3. Structured Key-Value Grid for Nested Configs

Replace the raw `<textarea>` inside each nested config `<details>` accordion with a structured key-value grid, read-only.

**Rendering logic** (implemented inline in `ProductTermsForm.tsx`, no imports from `Positions.tsx`):
- Nested config values are always `Record<string, unknown>` (filtered by the existing `nestedConfigs` check).
- Iterate `Object.entries(value)` and for each entry:
  - If the entry value is scalar (not array, not object): render as a labeled `<input readonly>` using `wl-positions__term-field`.
  - If the entry value is boolean: render as a `wl-positions__check-field` with `<input type="checkbox" disabled>`.
  - If the entry value is itself an object or array: fall back to a `<textarea readonly>` showing `JSON.stringify(value, null, 2)` — this is an edge case and keeps complexity bounded.
- Add a small "Read-only · system computed" note below the rendered fields using `wl-positions__term-empty` styling.

**No schema editing.** The accordion body no longer accepts user edits for nested configs. If a user needs to modify a nested config they must do so through the top-level product term fields (which control the upstream computation) or via the Engine Kwargs JSON field.

**Accordion markup:** The `<details>` / `<summary>` structure stays. Only the body content changes.

---

## Files to Change

| File | What changes |
|---|---|
| `frontend/src/components/ProductTermsForm.tsx` | Wrap content in `wl-positions__term-form` div + fieldset + legend; change grid class from `product-terms-grid` to `term-grid`; replace textarea in nested config body with structured KV grid |
| `frontend/src/routes/Positions.css` | Update `wl-positions__product-terms-grid` to 3 columns; add `wl-positions__product-terms` responsive rules |

---

## What Does NOT Change

- The top-level editable fields in `PRODUCT_TERM_FIELDS` — no new fields, no reordering.
- The "Extra Fields" accordion (unknown keys not in `PRODUCT_TERM_FIELDS`) — keeps its existing `<details>` + readonly inputs structure, no change.
- The `PositionEditForm` layout above Product Terms (Underlying, Product Type, Qty, Entry Price, Status, Trade ID, Engine).
- The Engine Kwargs JSON textarea.
- The read-only `ReadonlyObjectForm` used on the Details tab for non-container portfolios — already uses fieldset + legend, already good.
- The Pricing Ticket, Market Inputs, and all other panels.
- Tests — no behavioral change, only visual restructuring.

---

## Component Render Outline

```
ProductTermsForm
└── div.wl-positions__product-terms   (unchanged wrapper)
    └── div.wl-positions__term-form   (NEW: was absent)
        └── fieldset
            ├── legend "Product Terms"
            ├── div.wl-positions__product-terms-grid  (3-col, was 2-col)
            │   └── [renderField() for each FieldSpec]
            ├── div.wl-positions__term-groups          (nested configs)
            │   └── details.wl-positions__term-group  (per config key)
            │       ├── summary (key label + field count)
            │       └── div.wl-positions__term-group-body
            │           ├── div.wl-positions__term-grid  (NEW: scalar KV grid, inline)
            │           ├── textarea[readonly]            (fallback: nested obj/array values)
            │           └── div.wl-positions__term-empty "Read-only · system computed"
            └── details.wl-positions__term-group  (Extra Fields, unchanged)
```

---

## CSS Changes

```css
/* Change: was repeat(2, ...) */
.wl-positions__product-terms-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

/* Add responsive rule at 640px breakpoint */
@media (max-width: 640px) {
  .wl-positions__product-terms-grid { grid-template-columns: 1fr; }
}
```

The `wl-positions__product-terms` wrapper and heading styles are removed (heading moves into `<legend>`).
