# Frontend — agent guidance

**Before any UI work, read [`./UI_STYLE_GUIDE.md`](./UI_STYLE_GUIDE.md).** It is the source of
truth for styling conventions and the bugs they prevent.

Non-negotiables (full detail and the *why* are in the guide):

- **Token-only styling.** Never hardcode colors, spacing, fonts, or motion in `.css` — use the
  `var(--token)` values from `src/tokens/`. Never invent a token name that isn't defined there.
- **Theme & density are automatic.** Consume tokens; never branch on `data-theme` / `data-density`
  or add `[data-theme="dark"]` overrides in a component.
- **Verify in both themes + compact density** before claiming a UI change is done. Most polish
  bugs are dark-mode contrast issues from a bypassed token.
- **Theme raw `<input>`/`<select>`** (or use the `wl-field`/`wl-input` primitives) — unstyled
  controls default to a white background that breaks dark mode.
- **One co-located `.css` per component, BEM names, `wl-` prefix for primitives.** Reuse existing
  primitives (Button, Input, Modal, Tile, Badge, Chip, PageHeader, Table) before adding new styles.

---

## Frontend: `NumberInput` formats by default, including in bare test renders

`useThousandSeparator()` returns `thousandSeparator: true` when **no provider is mounted**
— deliberate, and pinned by `NumberInput.test.tsx > renders formatted by default`. So
`NumberInput` turns a value needing a separator into formatted **text** (`8359.56` →
`"8,359.56"`, `type="number"` → `type="text"`). In tests this makes `toHaveValue(8359.56)`
compare a number against a comma string, while values **under 1000 keep passing** — the
breakage looks arbitrary until you notice the threshold. For tests about prefill or data
flow rather than presentation, use `expectNumericValue(el, n)` from `src/test-setup.ts`.

Related: **the Booking page cannot originate a `package` position.** `ProductTermsForm`
renders the record-array ("Add Row") editor only for keys already in `product_kwargs`
(`extraFields`), and no `PRODUCT_TYPES` entry declares a `components` field, so there is no
way to introduce one on a fresh form. A `Booking.live` test asserted that flow and could
never pass; it was removed, since the same contract is covered via the reachable path in
`PositionEditForm.test.tsx`. Closing the gap means giving the form a real components entry
point — it is a product decision, not a test fix.
