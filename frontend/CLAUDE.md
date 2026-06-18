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
