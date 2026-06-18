# Dark-mode coverage (Plan 5a Task 7)

Audited: 2026-05-08

## Method

Visual QA against a live browser was not feasible from the agent session
(headless). The audit fell back to a static, grep-driven approach to find
hardcoded colors that bypass tokens — paired with a production build smoke
to confirm Vite still emits a clean bundle.

## Audit grep

```
grep -rnE "#[0-9A-Fa-f]{3,6}|rgba?\(" frontend/src --include="*.css"
```

All hits are inside `frontend/src/tokens/colors.css` — the single source of
truth for color tokens, with explicit `[data-theme="dark"]` and
`@media (prefers-color-scheme: dark)` overrides. No component CSS file holds
a hardcoded color.

```
grep -rnE "#[0-9A-Fa-f]{6}|rgba?\(" frontend/src/routes frontend/src/components \
  --include="*.tsx" --include="*.ts"
```

No matches — no hardcoded colors in TSX inline styles either.

## color-mix usages

```
ActionProposal.css   color-mix(in oklab, var(--warn) 10%, var(--paper))
CommandPalette.css   color-mix(in oklab, var(--ink) 42%, transparent)   ← Plan 5a Task 5
CommandPalette.css   color-mix(in oklab, var(--ink) 28%, transparent)   ← Plan 5a Task 5
FloatingAgent.css    color-mix(in oklab, var(--ink) 18%, transparent)   ← Plan 5a Task 5
FloatingAgent.css    color-mix(in oklab, var(--ink) 18%, transparent)   ← Plan 5a Task 5
Modal.css            color-mix(in oklab, var(--ink) 42%, transparent)   ← Plan 5a Task 5
Modal.css            color-mix(in oklab, var(--ink) 18%, transparent)   ← Plan 5a Task 5
ScenarioGrid.css     color-mix(in oklab, var(--paper) 80%, transparent)
```

Every call mixes named tokens (`var(--ink)`, `var(--warn)`, `var(--paper)`).
All these tokens have `[data-theme="dark"]` overrides, so the mix tracks
the active theme automatically.

## Build smoke

`npm run build` succeeds, emits 76.64 kB CSS (gzip 27.06 kB) with no
warnings about unrecognised color values. CSS bundle is identical structure
to pre-Plan-5a; only the rgba→color-mix substitutions changed.

## Caveat (browser-eyeballing not done)

Subtle visual issues that don't show up in static analysis (e.g. a text
color that's tokenised but doesn't have enough contrast against a tokenised
background in dark mode) cannot be ruled out from this audit. The user
should do a final eyeball pass of the 6 routes in dark mode (`theme: dark`
in the toolbar) and report any visible regressions for follow-up.

If issues are found, the fix shape is one of:
- Replace a hardcoded hex with `var(--token)`
- Add a missing `[data-theme="dark"]` override in `tokens/colors.css`
- Tweak a `color-mix` percentage that's correct in light but too weak/strong
  in dark
