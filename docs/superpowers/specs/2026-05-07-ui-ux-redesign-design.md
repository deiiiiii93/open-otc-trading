# UI/UX Redesign — Warm Ledger

**Date:** 2026-05-07
**Status:** Design approved, awaiting implementation plan
**Brief:** "Refinitiv/Bloomberg terminal style, but more zen aesthetic" — applied to a 6-route OTC derivatives platform (Agent Desk, RFQ Approval, Positions, Risk, Reports, Client RFQ).

## Design philosophy

The redesign is a single unified design system across all six routes, light and dark first-class. The Bloomberg lineage is honored at the **component level** (monospace tabular numerics, color-coded P&L, dense tables, master-detail layouts). The zen lineage is honored at the **chrome level** (warm-tinted neutrals, generous breathing room above data, restrained motion, ruthless removal of decorative chrome).

The system commits to one tonal world — **Warm Ledger** — Bauhaus rigor (square corners, strict 4pt grid, table-first thinking) softened by warm-tinted neutrals (paper-white, ink-black, sage and oxblood as restrained accents). This is the single concession that makes "Bloomberg + zen" coherent rather than contradictory.

## Tokens

### Color · light (default)

```
--paper:        #FAFAF6   /* dominant surface */
--paper-2:      #F4EFE2   /* row hover, secondary surface */
--paper-3:      #EAE3D0   /* row selected, tertiary surface */
--hairline:     #D8D0BD   /* fine dividers */
--hairline-2:   #B8AC8D   /* input borders */
--ink:          #14110A   /* primary text, primary border, button-primary bg */
--ink-2:        #3C342A   /* secondary text */
--pos:          #2F5D3A   /* P&L positive, success */
--neg:          #8C2A2A   /* P&L negative, destructive */
--warn:         #B58A2C   /* pending confirmation, awaiting action */
--info:         #2A4F76   /* informational */
```

### Color · dark

```
--paper:        #131009
--paper-2:      #1B1710
--paper-3:      #2C261A
--hairline:     #3F3829
--hairline-2:   #5A4F38
--ink:          #F0E9D5
--ink-2:        #C9C0A8
--pos:          #7AAB6A
--neg:          #D9645B
--warn:         #D9B469
--info:         #6A8FB8
```

Dark is a true mirror — semantic colors lighten to maintain WCAG AA contrast against the warm-dark surface. Both modes use the same token names; only values change.

### Typography

```
--font-ui:        "Inter Tight", "Inter", system-ui, -apple-system, sans-serif
--font-numeric:   "Berkeley Mono", "JetBrains Mono", ui-monospace, monospace
```

- **UI font:** Inter Tight — free, OFL, near-Söhne quality at UI sizes.
- **Numerics font:** Berkeley Mono — paid ($75/year/dev). Carries the brand identity in the most-visible typographic surface (tickers, prices, Greeks). CI/dev environments fall back to JetBrains Mono so the build doesn't gate on a license.

Type ramp:

| Token | Size | Weight | Use |
|-------|------|--------|-----|
| `--type-h1` | 28px | 700 | Page hero (e.g. RFQ #1042 detail) |
| `--type-h2` | 18px | 650 | Panel titles |
| `--type-h3` | 14px | 600 | Section labels (uppercase, letter-spacing 0.04em) |
| `--type-body` | 14px | 400 | Body |
| `--type-small` | 12px | 500 | Hints, audit lines |
| `--type-caps` | 10px | 600 | Labels (uppercase, letter-spacing 0.08em) |
| `--type-num-l` | 20px | 700 | Tile values |
| `--type-num-m` | 14px | 500 | Table cells |
| `--type-num-s` | 11px | 500 | Greeks at min legibility |

### Density

Two modes, switchable per-user via `<html data-density>`:

- **Comfortable** (default): row height 36px, body 14px, panel padding 12px
- **Compact** (trader mode): row height 26px, body 12px, panel padding 8px

The toggle persists in localStorage. CSS targets via `[data-density="compact"]` selectors.

### Theme

Three values for `<html data-theme>`: `system` (default, follows `prefers-color-scheme`), `light`, `dark`. Persisted in localStorage.

### Motion

Three patterns, no more:

| Pattern | Duration | Curve | Use |
|---------|----------|-------|-----|
| Fade | 120ms | linear | State changes (selected, hover, focus) |
| Slide | 180ms | cubic-bezier(0.4, 0, 0.2, 1) | Panels arriving (agent open, modal in, sheet) |
| Shimmer | 1.6s loop | linear | Loading skeletons only |

**Stillness is a fourth pattern.** Numerics, table cells, and tile values have zero motion. Numbers change instantly because they are data, not animation. Bloomberg never animates ticks; the Warm Ledger system inherits this.

`prefers-reduced-motion: reduce` collapses Fade and Slide to instant. Skeletons stay shimmer (it conveys "loading" — replacing it with stillness misleads the user that data has arrived).

## Component vocabulary

All components are built from the tokens above. No additional decorative properties.

### Forms

- **Button** — square corners, 1px ink border. Variants: `primary` (ink fill, paper text), `default` (paper fill, ink border), `danger` (paper fill, neg border + text), `ghost` (no border). Keyboard hints rendered as inline `<kbd>` chips.
- **Input** — square corners, 1px hairline-2 border. Focus = 2px ink border (compensating padding inward). Label rendered as `--type-caps` above field.
- **Select / Textarea** — same chrome as Input.
- **Tabs** — underline only, never boxed. Active = `--ink` color and 2px bottom border in `--ink`. No background.

### Status & Context

- **Badge** — outline by default (1px currentColor border), uppercase Berkeley Mono at `--type-caps`. Variants: `pos`, `neg`, `warn`, `info`, `ink`. `solid` variant (ink fill, paper text) reserved for "confirmed" / "live" terminal states.
- **Chip** — filled (`--paper-2` background, hairline border). Used for filter values, page-context entries, removable tags.
- **PageContextChips** — first-class header strip below page title on every internal route. Renders `PageContext.chips` from existing app state. Same component is read by the floating agent on open, so the user sees what the agent sees.

### Data display

- **Panel** — 1px ink border. Header is `--ink` background + `--paper` text + `--type-h3`. Body is `--paper` background.
- **Tile** — 1px ink border. Label uppercase `--type-caps` + value Berkeley Mono `--type-num-l`. Optional delta line below in `--type-small`. Variants `pos` and `neg` color the value.
- **Table** — 1px ink border. Header row uses ink fill + paper text + caps. Body cells use Berkeley Mono `--type-num-m`. Numerics right-aligned (`text-align: right`), labels left-aligned. Hover row = `--paper-2`. Selected row = `--paper-3`. No zebra striping.

### Agent affordances

- **AssetCard** — for `AgentAsset` (file/image/table/chart/json/markdown). 36px square type icon (Berkeley Mono kind label), title + subtitle, action buttons on the right.
- **ActionProposal** — for `AgentActionProposal` (confirmable agent action). `--warn` border, pale-warn background, "Pending Confirmation" label uppercase. Always shows action name in monospace (e.g. `price_positions(portfolio=Desk-Q2)`), summary including downstream effects, primary "Confirm" button + secondary "Dismiss" + ghost "View payload". `⌘↵` confirms when focused.
- **FloatingAgentPip** — bottom-right of every internal route. Collapsed: ink chip with pulsing `--pos` dot when there's an unread message. Expanded: 70%-width panel with `PageContextChips` strip at top, message log, action proposals inline, input at bottom. Open/close via slide 180ms.
- **CommandPalette** — separate from agent. `⌘K` opens (`⌘⇧K` opens agent panel as overlay alternative). Sections: Actions, Jump To, Recent. Berkeley Mono input. Items show keyboard shortcut on the right.

### States

- **Skeleton** — paper-3 → paper-2 shimmer 1.6s linear infinite. Always content-shaped (heights match the type they replace). Never spinners.
- **Empty** — 1px dashed hairline-2 border, centered glyph (`∅` or shape) in Berkeley Mono, message in `--type-body`, optional CTA button.
- **Toast** — bottom-right, ink fill, paper text. Auto-dismiss 4s. Error variant = `--neg` fill, persists until clicked.

### Detail-view pattern

Two patterns, used disjointly:

- **Master-detail** for browsing list-heavy routes (Positions, RFQ Approval, Risk drilldowns). List on left, detail panel on right, both always visible. Click row → right panel updates instantly. No modal trip required.
- **Modal** for irreversible confirmations only. Centered overlay, dims background. Used for: rejecting an RFQ, deleting a position, sending a quote to client.

This split honors the Modal pick (centered overlay for the moments that demand attention) without paying its browsing cost (Modal-on-every-row would be exhausting for traders).

## Per-route layouts

All six routes share the same chrome: left sidebar nav (240px, Inter Tight, ink-on-paper-2), top page header (page title + `PageContextChips` strip + page-level actions), main work area, floating agent pip (bottom-right except Client RFQ).

### 1. Agent Desk (`/chat`)

Split layout, 60% chat / 40% assets pane.

- **Chat column:** message log, action proposals inline, input pinned bottom. User messages = `--paper-2` background + 2px `--ink` left border. Agent messages = `--paper` background + 2px `--info` left border.
- **Assets pane:** vertical stack of `AssetCard`s. Streams in as the agent generates outputs. Each asset's "open" action expands to its own viewer (table → table viewer, chart → chart viewer, json → code block).

### 2. RFQ Approval (`/rfq`)

Tri-column master-detail.

- **Inbox** (30% width) — list of pending RFQs. Selected row highlighted with `--paper-3`.
- **Detail** (45%) — selected RFQ. Panel shows underlying, structure, terms, computed price + KO rate, badge for status. Two action buttons: `APPROVE & SEND` (primary, sends to client) and `REJECT…` (opens irreversible-confirm modal).
- **Audit** (25%) — chronological log of state transitions in Berkeley Mono.

### 3. Positions (`/portfolio`)

Master-detail, table-first.

- **Header:** page title + page-context chips + `RUN PRICING ⌘R` action.
- **Tile row:** four tiles — NAV, P&L, Delta, Vega.
- **Master table** (62% width) — dense Greeks table, all numerics right-aligned in Berkeley Mono. Sortable headers (click to sort, shift+click to add). Compact-mode toggle changes row height to 26px.
- **Trade-detail panel** (38% width, sticky) — selected trade's full Greeks dump in monospace + audit footer. Updates instantly on row click.

### 4. Risk (`/risk`)

Dashboard grid: two columns on the top row, full-width row below.

- **Top-left:** Greeks summary panel (Δ, Γ, V, θ, ρ).
- **Top-right:** P&L attribution panel (horizontal bars per attribution source: spot, vol, theta, etc.).
- **Bottom (spans both columns):** scenario grid — shift × vol matrix, color-coded `--pos`/`--neg` per cell.
- Every panel header has `↗` "promote to Report" affordance that creates a Reports entry from the panel's current data.

### 5. Reports (`/reports`)

Vertical document timeline.

- **Header:** filter chips (date range, type, portfolio) + `NEW REPORT` action.
- **Timeline:** chronological list of `ReportCard`s. Each card: 38px date column on left, card body on right with title + subtitle + generator (agent name or human). Clicking opens the report in a Reader view.
- New reports animate in at the top via slide 180ms + skeleton shimmer until loaded.

### 6. Client RFQ (`/client`)

Single-column centered intake. **Only route without the floating agent pip** — clients don't need an internal agent.

- **Intake card** (75% width, centered): tabs for `Natural Language` and `Structured`. NL = textarea + submit button. Structured = field set per product type (EuropeanVanilla, Snowball, Phoenix), submit button.
- **Status card** (below intake): displays the most recent submitted RFQ — id, status badge, key terms in **bold-monospaced** inline (e.g. **KO_rate = 0.20**, **price = 10.04**), released approval text from desk.

## Engineering decisions

### CSS architecture

Vanilla CSS with custom properties as the source of truth for tokens. No Tailwind, no CSS-in-JS, no CSS Modules.

```
frontend/src/tokens/
  colors.css       /* :root + [data-theme="dark"] */
  type.css         /* font tokens, ramp */
  density.css      /* [data-density="compact"] overrides */
  motion.css       /* keyframes, durations, curves */
  reset.css        /* normalize + reset */

frontend/src/styles/
  components.css   /* primitive components */
  routes.css       /* route-specific overrides only */
```

Theme and density switch at runtime by setting attributes on `<html>`:

```js
document.documentElement.dataset.theme = "dark"      // or "light", "system"
document.documentElement.dataset.density = "compact" // or "comfortable"
```

CSS targets via attribute selectors:

```css
:root { --paper: #FAFAF6; }
[data-theme="dark"] { --paper: #131009; }
[data-density="compact"] .panel { padding: 8px; }
```

User preference persisted in `localStorage` under `otc:theme` and `otc:density`. Defaults: `theme=system`, `density=comfortable`.

### Component primitives

Adopt **Radix UI primitives** for accessibility-critical pieces, style them with our CSS tokens. Specifically:

- `@radix-ui/react-dialog` — irreversible-confirmation modals
- `@radix-ui/react-tabs` — Agent Desk, Client RFQ, Risk panels
- `@radix-ui/react-tooltip` — Greek explanations, button hints
- `@radix-ui/react-toast` — toasts
- `@radix-ui/react-popover` — command palette container, agent pip expansion
- `@radix-ui/react-dropdown-menu` — sort menus, action menus

All other primitives (Button, Input, Tile, Panel, Table, Badge, Chip, AssetCard, ActionProposal, etc.) are built from scratch. They have no behavior beyond styling and don't need a library.

Skip shadcn/ui — Tailwind-coupled, conflicts with our CSS-properties approach.

### File organization

Split the 1458-line `main.tsx` into:

```
frontend/src/
  main.tsx                          /* router setup, root render */
  routes/
    AgentDesk.tsx
    RfqApproval.tsx
    Positions.tsx
    Risk.tsx
    Reports.tsx
    ClientRfq.tsx
  components/
    Button.tsx
    Input.tsx
    Tabs.tsx
    Panel.tsx
    Tile.tsx
    Table.tsx
    Badge.tsx
    Chip.tsx
    PageContextChips.tsx
    AssetCard.tsx
    ActionProposal.tsx
    FloatingAgent.tsx
    CommandPalette.tsx
    Toast.tsx
    Skeleton.tsx
    Empty.tsx
  hooks/
    useTheme.ts
    useDensity.ts
    usePageContext.ts
    useCommandPalette.ts
  api/
    client.ts                       /* existing fetch helpers */
  types.ts                          /* extracted from main.tsx */
```

Keep React 19 + Vite + Lucide icons. Remove no dependencies; add `@radix-ui/react-*` and that's it.

## Migration sequencing

Detailed sequencing belongs in the implementation plan, but the design implies this rough order:

1. **Tokens layer** (colors, type, density, motion CSS files). No visual change yet.
2. **Component primitives** (Button, Input, Panel, Tile, Table, Badge, Chip). Visual change in isolation.
3. **App shell** (sidebar, top header, page-context chips, theme/density toggles). All routes visible in new shell.
4. **Floating agent + command palette** (cross-route).
5. **Per-route layouts** in priority order: Positions → Risk → RFQ Approval → Reports → Agent Desk → Client RFQ. Each route ships independently after its primitives are ready.
6. **Polish pass** (motion, prefers-reduced-motion, accessibility audit, dark mode pass).

## Out of scope (explicitly)

- Internationalization beyond mixed-language CN/EN labels in the existing data. No i18n framework added.
- Charting library choice (the `chart` AssetCard kind is a placeholder; charting selection is its own design exercise).
- Mobile / tablet responsive behavior. The system is desktop-first; minimum supported width is 1280px. Sub-1280 falls back to a scrollable horizontal layout, not a redesigned mobile UX.
- Replacing the existing FastAPI backend, the QuantArk pricing library, or the LangGraph agent topology. The redesign is purely the React frontend.
- Migration of the demo client portal to a separate domain or build. Same SPA, same router.

## Open questions

- **Berkeley Mono licensing path:** confirm purchase mechanism and per-developer count before shipping. Until then, dev/CI uses JetBrains Mono fallback.
- **`PageContext.chips` schema:** the existing schema covers most chip needs, but the floating-agent integration may want chip values typed (currently strings). Defer until floating-agent backend integration.
- **Compact-mode discoverability:** density toggle lives in the user menu by default. If traders aren't finding it, add a keyboard shortcut (candidate: `⌘\\`); defer until usage data exists.
