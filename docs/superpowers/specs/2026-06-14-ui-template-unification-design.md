# UI Template Unification — Design

**Date:** 2026-06-14
**Status:** Approved (architecture), pending implementation
**Scope:** `frontend/` route pages and their layout scaffolding

## Problem

The frontend has ~20 route pages. Each one hand-rolls its own page-level layout
scaffolding below the shared `PageHeader`: KPI tile rows (`wl-{page}__tiles`),
search/filter/pager toolbars (`wl-{page}__toolbar`), panel grids
(`wl-{page}__grid`), and rail/workspace splits (`wl-{page}__layout`,
`hedging-body`). The same handful of region shapes are re-expressed in bespoke
CSS on every page, producing:

- **Visual drift** — tile rows, toolbars, and splits have slightly different
  gaps, breakpoints, and alignment per page.
- **Duplicated CSS** — the same grid/flex region rules are copied with small
  variations across ~20 stylesheets.
- **Inconsistent behavior** — search, pagination, rail collapse, and empty
  states are reimplemented rather than shared.

The only shared layout primitive today is `PageHeader` (imported by ~16 pages).
Everything below it is per-page.

## Goal

Classify every page by its **on-screen structural skeleton**, then express each
skeleton as a reusable **template component** built from a small set of shared
**region primitives**. After migration, a page's layout is declared by choosing
a template and filling its slots; page-specific CSS shrinks to genuinely
page-specific styling.

This is a **layout/structure unification**, achieved with **zero visual
regression intent** — the templates reproduce each page's current arrangement,
just from shared code. Token-only styling (per `UI_STYLE_GUIDE.md`) is preserved
throughout; this refactor is also an opportunity to bring any lingering literal
values in the touched regions into token compliance.

## Non-goals

- No redesign of page content, no new features, no data-flow changes. **Data
  loading logic is untouched.** Where a page has a presentational/`.live.tsx`
  split (e.g. Positions, Risk, Hedging), only the presentational component
  changes. Several pages are *combined* components with no separate
  presentational layer — **Booking** (only `Booking.live.tsx`), **EngineConfigs**,
  **ScenarioTest**, **Backtest** (each only `X.tsx` with state+effects+render in
  one component). For these, the template is applied **in place** to the existing
  component, editing only its render/layout JSX — its data-fetching hooks, state,
  and effects are left intact. No new file extraction is performed.
- No change to routing, `PageContext` reporting, or agent action declarations.
- No change to the existing primitives (`Tile`, `Table`, `Panel`, `Modal`,
  `Tabs`, `Button`, `Input`, `Badge`, `Chip`, `PageHeader`). Templates *compose*
  them; they are not modified.
- Not migrating embedded/leaf components that already have clean local layout
  (e.g. `GreeksSummary`, `PnlAttribution`) beyond wrapping them in a template's
  panel grid.

## Taxonomy (locked)

Six template families. Page → template mapping:

| Template | Pages |
|---|---|
| **A · DataTablePage** | Positions, Portfolios, Tasks, Reports |
| **B · AnalyticsDashboard** | Risk, HedgeStrategy*, GreeksLandscape |
| **C · MasterDetailPage** | Skills, EngineConfigs, Instruments†, Hedging, Tracing, RfqApproval, PricingParameters‡, TrySolve |
| **D · WorkbenchPage** | ScenarioTest, Backtest |
| **E · ConversationalWorkspace** | AgentDesk |
| **F · WizardPage** | ClientRfq, Booking |

> **Reclassifications from review gate 1 (verified against source):**
> - `TrySolve` moved **F → C**. `TrySolve.tsx:560` is a `Panel`-wrapped request
>   queue (rail) + editor workspace; the `wl-try-solve__step` markers at
>   `TrySolve.tsx:1128-1132` are a small inline progress strip *inside* the
>   detail, not a wizard that drives the page. It is master-detail.
> - `PricingParameters` moved **A → C**. `PricingParameters.tsx:183` renders
>   `ProfileLibrary`, whose body (`ProfileLibrary.tsx:123,159`) is an
>   `<aside>` profile list (rail) + `<section>` detail (tiles + table). The page
>   is header + master-detail.
> - `ProfileLibrary` is **removed as a standalone entry** — it is the *body* of
>   `PricingParameters`'s C split (rail = profile list, detail = its
>   tiles+toolbar+table), sharing the `wl-pricing-params__` namespace. It has no
>   `PageHeader` of its own and is never routed independently.

\* `HedgeStrategy` is not a top-level route — it renders inside `Hedging`'s
workspace pane. It is an AnalyticsDashboard-shaped *body* (KPI tiles + panels)
composed with B's region primitives, not a standalone template instance. Its
`hedge-strategy__*` / `hedging-*` BEM block names are **compliant** feature-level
names per `UI_STYLE_GUIDE.md` §8 — no rename required; only the bespoke region
layout (KPI row, panel grid) migrates to `MetricRow`/`PanelGrid`.

‡ `PricingParameters` C body is supplied by `ProfileLibrary` (see reclassification
note above).

† `Instruments` (1154 lines) is a **composite**: a tabbed master-detail whose
tabs (`InstrumentsMarketData`, `InstrumentsAssumptions`, `InstrumentsAllowedHedges`,
`InstrumentsPager`) are themselves sub-pages. It uses `MasterDetailPage` for its
outer shell; its inner tab bodies are migrated opportunistically to region
primitives but may retain bespoke layout where they don't match a family.

## Architecture — two layers

### Layer 1: shared region primitives (`frontend/src/components/`)

Each is a small component with one co-located `.css`, `wl-` prefixed, BEM-named,
token-only. These are the reuse units.

```tsx
// MetricRow.tsx — a row (or rows) of KPI tiles. Replaces wl-{page}__tiles.
type Metric = { label: string; value: React.ReactNode; variant?: TileVariant; delta?: React.ReactNode };
type MetricRowProps = { metrics: Metric[]; columns?: number; className?: string };
// Renders <Tile> per metric in a responsive grid; `columns` sets the target
// per-row count (auto-fit fallback). Multiple <MetricRow>s stack for grouped tiles.

// TableToolbar.tsx — search + filter + pager strip. Replaces wl-{page}__toolbar.
type TableToolbarProps = {
  search?: { value: string; onChange: (v: string) => void; placeholder?: string };
  filters?: React.ReactNode;   // arbitrary toggle/select cluster (left)
  pager?: { page: number; pageSize: number; total: number;
            onPage: (p: number) => void; onPageSize?: (n: number) => void;
            pageSizes?: number[] };
  className?: string;
};

// PanelGrid.tsx — responsive grid of <Panel>s. Replaces wl-{page}__grid.
type PanelGridProps = { columns?: 1 | 2 | 3; children: React.ReactNode; className?: string };

// SplitLayout.tsx — rail + workspace. Replaces hedging-body / wl-{page}__layout.
type SplitLayoutProps = {
  rail: React.ReactNode; children: React.ReactNode;   // children = workspace
  railWidth?: string;          // see rail-width token note below
  collapsible?: boolean;       // shows a collapse toggle; collapse state is internal
  railLabel?: string;          // a11y label for the rail nav region
  className?: string;
};
// rail-width token: SplitLayout's default rail basis is a new token
// `--rail-width` (defined in density.css, e.g. 280px, shrinking under compact).
// `railWidth` overrides must pass a token (`var(--rail-width)`) or an acceptable
// non-design geometry value per UI_STYLE_GUIDE.md §5 (a fixed min/basis width is
// geometry, not spacing rhythm) — never a raw spacing-rhythm literal.

// Stepper.tsx — wizard progress indicator.
type Step = { label: string; status: 'done' | 'active' | 'todo' };
type StepperProps = { steps: Step[]; className?: string };
```

### Layer 2: templates (`frontend/src/components/templates/`)

All six share a private `PageScaffold` (PageHeader + optional feedback banner +
a vertical section stack with consistent `--gap` rhythm). Templates are
*opinionated compositions* of the region primitives. A page that doesn't fit a
template can drop to the primitive layer directly — no page is ever trapped.

```tsx
// PageScaffold.tsx (private base; not used directly by pages)
type PageScaffoldProps = {
  title: string; chips?: string[]; actions?: React.ReactNode;
  feedback?: React.ReactNode; children: React.ReactNode;
};

// A · DataTablePage.tsx
type DataTablePageProps<T> = {
  title: string; chips?: string[]; actions?: React.ReactNode; feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];          // one or more MetricRows
  toolbar?: TableToolbarProps;              // structured search/filter/pager
  // Provide EITHER `table` (renders the Table primitive) OR `body` (escape hatch
  // for list pages whose body is not a <Table>, e.g. Reports' <ReportTimeline>).
  table?: { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number;
            selectedKey?: string | number | null; onRowClick?: (r: T) => void };
  body?: React.ReactNode;                   // non-Table body (alternative to table)
  mobileCards?: React.ReactNode;            // optional responsive card list
  empty?: React.ReactNode;                  // shown when table rows are empty
  overlays?: React.ReactNode;               // page-owned modals/dialogs (page owns
                                            // open state). May be a fragment of
                                            // MULTIPLE modals — e.g. Positions
                                            // passes both its Import dialog and its
                                            // Position-detail modal here.
};

// B · AnalyticsDashboard.tsx
type AnalyticsDashboardProps = {
  title: string; chips?: string[]; actions?: React.ReactNode; feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  controls?: React.ReactNode;               // optional always-visible config strip
                                            // rendered between metrics and panels
                                            // (e.g. GreeksLandscape's run-config form).
  panels: React.ReactNode;                  // wrapped in PanelGrid (columns prop)
  columns?: 1 | 2 | 3;                      // PanelGrid columns; see per-page notes
  state?: React.ReactNode;                  // empty/loading replacement for panels
};

// C · MasterDetailPage.tsx
type MasterDetailPageProps = {
  title: string; chips?: string[]; actions?: React.ReactNode; feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];          // optional KPI row(s) above the split
                                            // (Skills uses this); omit for pages
                                            // with no KPI row (Tracing, Hedging…).
  rail: React.ReactNode; children: React.ReactNode;   // children = detail/editor
  railWidth?: string; collapsible?: boolean; railLabel?: string;
};

// D · WorkbenchPage.tsx  (SplitLayout(config) + results pane = MetricRow + panels/table)
type WorkbenchPageProps = {
  title: string; chips?: string[]; actions?: React.ReactNode; feedback?: React.ReactNode;
  config: React.ReactNode;                  // left config panel (rail)
  metrics?: Metric[] | Metric[][];          // results KPI row(s)
  results: React.ReactNode;                 // run history + result panels
  railWidth?: string;
};

// E · ConversationalWorkspace.tsx  (up to THREE columns: rail | chat | context)
type ConversationalWorkspaceProps = {
  title: string; chips?: string[]; actions?: React.ReactNode;
  rail?: React.ReactNode;                   // left column — AgentDesk's ThreadRail
  messages: React.ReactNode; composer: React.ReactNode;
  contextPane?: React.ReactNode;            // right column — AgentDesk's AssetsPane
};
// AgentDesk is genuinely three-column (ThreadRail | chat | AssetsPane); the
// rail/context columns render only when their slot is provided.

// F · WizardPage.tsx
type WizardPageProps = {
  title: string; chips?: string[]; actions?: React.ReactNode; feedback?: React.ReactNode;
  steps?: Step[];                           // Stepper; omit for tabbed wizards
  tabs?: React.ReactNode;                   // alternative to steps (e.g. ClientRfq NL/structured)
  children: React.ReactNode;                // active step/tab body
  footer?: React.ReactNode;                 // back / submit actions
};
```

**Design rule — data for uniform, slots for variable.** Structured props
(`metrics`, `steps`, `toolbar`, `table`) are used where the content is uniform
across pages; `ReactNode` slots (`panels`, `detail`, `children`, `config`,
`results`) are used where content genuinely varies. This keeps the prop surface
bounded.

## Migration approach (big-bang, internally ordered)

Per the approved scope, all pages migrate in one body of work merged together.
Internal build order (required so the tree compiles and is reviewable):

1. **Region primitives** (Layer 1) — built test-first, each with unit tests
   covering render + interaction (search change, pager click, rail collapse,
   stepper status). No page changes yet.
2. **Templates** (Layer 2) — built test-first over the primitives. Snapshot/region
   presence tests.
3. **Page migrations**, grouped by family (A→F). Each page: swap its top-level
   scaffold to the template, delete the now-dead `wl-{page}__{region}` CSS,
   keep page-specific styles. Page's existing `.test.tsx` must still pass; update
   only selector-coupled assertions where a region class legitimately changed,
   preserving the behavior they assert.

## Per-page migration notes

- **Positions** — A; two `MetricRow`s (risk tiles + position-summary tiles), a
  `TableToolbar` (search + All/Live toggle as `filters`, pager), `mobileCards` =
  the responsive card list. `overlays` carries **both** modals (Import dialog +
  Position-detail). The page keeps its local `useMediaQuery` and passes
  `mobileCards` only when the breakpoint matches — responsive ownership stays on
  the page; the template just renders the slot.
- **Portfolios** — A; header + tiles + table, with its rule/tag/position editors
  passed through `overlays` (detail). Borderline C, but the dominant surface is
  the list+table, so A with an editor overlay.
- **Tasks** — A; `table` (existing Tasks table), no metrics row.
- **Reports** — A via the `body` slot (it renders `<ReportTimeline>`, not a
  `<Table>`); `overlays` = the `<ReportReader>` modal. No metrics row.
- **PricingParameters** — C; header + `ProfileLibrary` body (rail = profile list,
  detail = profile tiles + toolbar + table). `ProfileLibrary` is migrated as the
  C body; its `wl-pricing-params__list/__detail` become the SplitLayout slots.
- **Risk** — B, `columns={1}` (panels stack vertically as today); `actions` =
  portfolio/profile/engine selects + Run button; `panels` = GreeksSummary +
  PnlAttribution + ScenarioGrid; `state` = the existing empty/running messages.
- **HedgeStrategy** — B-shaped body inside Hedging's workspace; uses MetricRow +
  PanelGrid, not a route template. Feature BEM names retained (see note *).
- **GreeksLandscape** — B, `columns={1}`; the large run-config selects strip goes
  in the new `controls?` slot (not `actions`); `panels` = the single chart panel.
  Chart series colors are JS literals — out of scope (see Risks).
- **Skills** — C; `metrics` (workflow/reference/meta/lint tiles) render above the
  split via PageScaffold; rail = skill list, children = `SkillsWorkflowForm`
  (the editor) or reference viewer. `SkillsWorkflowForm` is unchanged content in
  the detail slot.
- **EngineConfigs / RfqApproval** — C; rail = config list / RFQ inbox,
  children = editor / RFQ detail.
- **Tracing** — C. **Intentional change:** Tracing currently has *no* `PageHeader`
  (`Tracing.tsx` has none); migrating to C adds one with title `TRACING`,
  matching every other page's all-caps header. This is the single deliberate
  layout addition in the refactor, justified by cross-page consistency; called
  out so it is not mistaken for a regression.
- **Hedging** — C; rail = underlying cards, workspace = `HedgeStrategyLive`.
- **Instruments** — C composite (see Taxonomy note †). Its custom tab strip is the
  `rail`; `collapsible={false}` (a tab strip never collapses); inner
  `RegistryTab`/sub-tabs keep bespoke layout.
- **TrySolve** — C; rail = request-queue `Panel`, children = solve workspace. The
  inline `wl-try-solve__step` progress strip inside the workspace MAY adopt the
  `Stepper` primitive but is not the page driver.
- **ScenarioTest / Backtest** — D; `config` = scenario/config panel,
  `results` = runs list + result panels. ScenarioTest has no results KPI row
  (`metrics` omitted). Backtest's KPI cards live inside its `RunReport`, which is
  passed whole as `results`; they remain inside that slot rather than being
  hoisted to the `metrics` prop (converting `wl-backtest__kpis` to `MetricRow` is
  a nice-to-have, not required — flagged so the plan can scope it explicitly).
- **AgentDesk** — E (three-column); `rail` = ThreadRail, `messages` + `composer`
  = chat column, `contextPane` = AssetsPane.
- **ClientRfq** — F via `tabs` (NL / Structured); **Booking** — F (combined
  `Booking.live.tsx`, edited in place; stepped form).

### Resolved design detail — metrics above a split

Skills (C) shows a `MetricRow` *above* its master-detail split. `MasterDetailPage`
therefore accepts an optional `metrics?` prop rendered by `PageScaffold` before
the `SplitLayout`. This keeps C uniform rather than forcing Skills into a bespoke
shape.

## Testing strategy

- **Primitives & templates:** new co-located `.test.tsx` (vitest + Testing
  Library), test-first. Cover region presence, slot rendering, and interactions.
- **Pages:** existing `*.test.tsx` and `*.live.test.tsx` are the regression net.
  They must pass. Where a test asserts a removed bespoke class
  (`wl-positions__tiles`), update the assertion to the template's region class
  while preserving the *behavior* being asserted (do not weaken the test).
- **Both themes + compact density:** verified per `UI_STYLE_GUIDE.md` §9 for the
  new primitives/templates.
- **Phantom-token sweep:** run the `comm -23` check from the style guide; must be
  empty.
- **Full suite:** `vitest run` green before each review gate.

## Risks

- **Big-bang review surface.** Migrating ~20 pages in one change is large and
  harder to review than a phased rollout. Mitigation: the two-layer split makes
  the diff reviewable in slices (primitives, templates, then per-family page
  groups), and the existing per-page test suites pin behavior. A standalone
  review subagent gates spec, plan, and implementation in place of human review.
- **Test/selector coupling.** Some page tests assert bespoke region classes.
  Mitigation: update assertions to template region classes without weakening
  them; never delete a behavioral assertion to make it pass.
- **Instruments composite.** Largest, least-uniform page. Mitigation: migrate its
  outer shell only to C; leave non-conforming inner tab layout as-is rather than
  forcing a fit.
- **Concurrent agents on shared repo.** Work proceeds in an isolated git
  worktree (`worktree-ui-unification`) with symlinked `node_modules`.
- **JS chart colors escape the CSS token sweep.** `GreeksLandscape.tsx`'s
  `COLORS` array and Backtest chart strokes are hex literals passed to Recharts
  props — JavaScript, not CSS, so the `comm -23` `.css` sweep cannot catch them.
  They are **out of scope** for this layout refactor (pre-existing, and series
  colors are a separate concern); the spec notes them so "token compliance" is
  not mistaken for covering them. They are not introduced or worsened here.

## Out of scope

- AppShell / Sidebar / theme / density systems (unchanged).
- Backend, routing, PageContext, agent tooling.
- Any visual redesign beyond reproducing current layouts from shared code.
