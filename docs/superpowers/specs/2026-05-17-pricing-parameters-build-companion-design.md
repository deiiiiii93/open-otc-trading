# Pricing Parameters Build Companion UX Design

Date: 2026-05-17
Status: Draft for user review

## Goal

Improve the `PRICING PARAMETERS` page so it primarily helps a desk user build a valid daily default pricing profile, while preserving the existing profile library as the audit and selection surface.

The current page already has the right domain model: global, date-based `PricingParameterProfile` snapshots keyed by trade id, plus a global `underlying_pricing_defaults` config store for manual rate, dividend yield, and volatility assumptions. The UX problem is hierarchy. The raw defaults table dominates the first viewport, while the page does not immediately answer whether a default profile can be built, what blocks it, or where to click to fix the blocker.

The new experience adds a functional visual companion, the **Profile Build Map**, above the editable inputs. It is not decorative. It is the control surface for diagnosing and fixing the build workflow.

## Approved Direction

Use a build-first console with an interactive visual companion.

Rejected alternatives:

- **Table-only ergonomics pass**: useful but insufficient, because users still need to infer readiness by scanning rows.
- **Profile-library-first layout**: better for audit review, but weaker for the daily workflow of producing a new default profile.
- **Read-only companion graphic**: visually pleasant, but it would become another status strip above the same old table. The companion needs to drive filtering and navigation.

## Page Structure

The first viewport should contain:

1. Existing `PageHeader` with `PRICING PARAMETERS`, chips, and `Import XLSX`.
2. New `PricingBuildConsole`, replacing the current always-expanded `UnderlyingDefaultsPanel` header/table dominance.
3. Two section tabs or segmented controls:
   - `Build Inputs`
   - `Profile Library`

Default tab behavior:

- If the build is blocked, open `Build Inputs` and select the first blocking map node. Manual missing fields usually take priority over AKShare spot issues because manual entries are user-fixable without a network round trip.
- If all inputs are ready, keep `Build Inputs` visible but show the map in ready state and enable `Build Default Profile`.
- After a successful build, switch to `Profile Library`, select the newly created default profile, and highlight the `Snapshot` node.

The existing profile library list/detail table remains on the same route. Positions and Risk continue to fetch and select profiles independently; this UX pass must not add a new "load to positions" handoff.

## Visual Companion

### `ProfileBuildMap`

Render a compact left-to-right operational map:

```text
Positions universe -> Manual assumptions -> AKShare spot -> Snapshot profile
```

Each node shows:

- Label
- Count or latest object
- State: `ready`, `blocked`, `stale`, or `empty`
- Short secondary text

Suggested node summaries:

- `Positions`: number of in-scope underlyings from the defaults rows currently loaded.
- `Manual`: completed manual assumptions, for example `7/10`.
- `AKShare`: usable latest close count, for example `9/10`.
- `Snapshot`: latest `default_underlying` profile name/date if present, otherwise `none`.

The map uses the repo's compact operational styling: bordered panels, small uppercase labels, restrained color, no hero treatment, and no marketing copy.

### Node Behavior

Nodes are clickable and update the lower section:

- `Positions`: switch to `Build Inputs`, show all in-scope underlying rows.
- `Manual`: switch to `Build Inputs`, filter to rows missing `rate`, `dividend_yield`, or `volatility`.
- `AKShare`: switch to `Build Inputs`, filter to rows with missing or stale latest AKShare close.
- `Snapshot`: switch to `Profile Library`, select the latest `default_underlying` profile if one exists.

The active node should be visibly selected, and the selected node should be reflected in the page context snapshot for the floating agent.

## Build Console

`PricingBuildConsole` owns the top-level readiness and actions.

It should show:

- Primary readiness result: `Ready to build`, `Build blocked`, `Spot data stale`, or `No inputs yet`.
- Short reason text.
- `Build Default Profile` as the primary action.
- Secondary actions:
  - `Refresh from positions`
  - `Refresh all AKShare spots`
- The `ProfileBuildMap`.
- `BuildBlockerPanel` when blocked or stale.

Build button rules:

- Disabled while building.
- Disabled when there are no defaults rows.
- Disabled when manual assumptions are missing.
- Not disabled solely because the latest cached AKShare close is missing or stale. The backend build fetches fresh spot and remains authoritative. Missing or stale cached spot should be shown as attention state, not a hard UI block.

The existing backend build remains authoritative. UI readiness is a guide, not a replacement for server-side validation.

## Blocker Panel

`BuildBlockerPanel` lists the concrete underlyings that need attention.

Chip behavior:

- Clicking a manual-missing chip switches to `Build Inputs`, filters to that underlying, and opens the row in edit mode.
- Clicking an AKShare-missing or stale chip switches to `Build Inputs`, filters to that underlying, and keeps the spot cell/action visible.

Chip grouping:

- `Missing manual assumptions`
- `No cached AKShare close`
- `Stale cached AKShare close`

Each group should cap visible chips and expose a compact `+N more` affordance when needed, to avoid turning the first viewport into another long table.

## Build Inputs

`BuildInputsTable` replaces the current raw table rendering inside `UnderlyingDefaultsPanel`. It can reuse the same data and update handlers, but should make row state and editing more direct.

Required behavior:

- Filter modes: `all`, `manual`, `akshare`, and `underlying:<symbol>`.
- Row state styling for `ready`, `blocked`, `stale`, and `empty`.
- Inline edit remains available for manual fields.
- Clicking a manual blocker chip opens edit mode for that row.
- Save success returns the row to read mode and keeps the current filter active.
- Save failure keeps the row in edit mode and shows an inline error message.
- Delete remains available, but it should be icon-sized and secondary. If the existing app does not ask for confirmation on this endpoint today, do not introduce a modal in this UX pass.

Column recommendations:

- `UNDERLYING`
- `AKSHARE CLOSE`
- `RATE`
- `DIV YIELD`
- `VOL`
- `NOTES`
- `LAST EDITED`
- compact actions

The `RATE`, `DIV YIELD`, and `VOL` columns should visually read as manual input columns. Missing values should render as `missing`, not just a dash, when the active filter is manual.

## Profile Library

The existing profile list/detail surface becomes the `Profile Library` tab.

Keep:

- Source filter chips: `ALL`, `DEFAULT`, `XLSX`, `SPOT`
- Profile list with source tag
- Selected profile tiles
- Paged row table
- Import XLSX modal

Improve:

- Add or preserve a `Source type` tile in the selected profile summary if not already present.
- Avoid showing origin badges next to blank numeric values; `- XLSX` on empty cells adds noise.
- Keep the literal `YYYY-MM-DD` prefix for profile date labels to avoid timezone date shifts.
- Selecting the `Snapshot` map node should select the latest `default_underlying` profile, not merely filter to default profiles.

## Data Model In Frontend

No new backend endpoints are required for the first implementation pass.

Derive a `buildReadiness` model in `PricingParameters.tsx` or a small local helper module:

```ts
type BuildNodeState = 'ready' | 'blocked' | 'stale' | 'empty';

type BuildInputFilter =
  | { kind: 'all' }
  | { kind: 'manual' }
  | { kind: 'akshare' }
  | { kind: 'underlying'; underlying: string };

type PricingBuildReadiness = {
  positionsCount: number;
  manualCompleteCount: number;
  manualMissing: UnderlyingPricingDefault[];
  spotReadyCount: number;
  spotMissing: UnderlyingPricingDefault[];
  spotStale: UnderlyingPricingDefault[];
  latestDefaultProfile: PricingParameterProfile | null;
  overallState: BuildNodeState;
};
```

Freshness rule:

- Compare `latest_akshare_close.fetched_at` or the best available date field to the app's accounting date.
- If `PricingParametersLive` does not currently receive `accountingDate`, pass it from `main.tsx` to `PricingParametersLive` and then to `PricingParameters`.
- When only a timestamp exists, compare the `YYYY-MM-DD` date prefix to the accounting date. Do not round-trip through local timezone conversion for profile or freshness labels.

The backend remains the source of truth for actual build success or failure.

## Component Boundaries

Proposed frontend units:

- `PricingParameters`: route orchestration, selected tab/profile/filter state, derived readiness model.
- `PricingBuildConsole`: readiness headline, action buttons, companion map, blocker panel.
- `ProfileBuildMap`: visual node rendering and node-click callbacks.
- `BuildBlockerPanel`: grouped chips and chip-click callbacks.
- `BuildInputsTable`: editable defaults rows and filter-aware display.
- `ProfileLibrary`: existing profile list/detail extracted from the current route body.

This keeps `PricingParameters.tsx` from growing into one large mixed workflow component.

## Error Handling

Inline errors are preferred for this workflow.

| Scenario | UX behavior |
|---|---|
| Missing manual assumptions | `Manual` node is `blocked`; blocker panel lists underlyings; build action disabled. |
| No cached AKShare close | `AKShare` node is `empty`; blocker panel lists underlyings and keeps refresh action visible. Build can still be attempted because the backend fetches fresh spot. |
| Stale cached AKShare close | `AKShare` node is `stale`; blocker panel lists underlyings; user can refresh all spots or attempt build. |
| Build endpoint returns `unfilled_underlyings` | Stay in `Build Inputs`; activate `Manual`; show returned underlyings as chips. |
| Build endpoint returns `failed_akshare_underlyings` | Stay in `Build Inputs`; activate `AKShare`; show returned underlyings as chips. |
| Build succeeds | Switch to `Profile Library`; select new profile; set `Snapshot` active; show row-count success feedback. |
| Defaults load fails | Preserve existing page-level error behavior. |

Do not add modal error dialogs for build blockers.

## Testing

Frontend unit/regression coverage:

- Readiness model derives `ready`, `blocked`, `stale`, and `empty` states from profiles/defaults.
- Manual node click filters to rows missing rate/dividend yield/volatility.
- AKShare node click filters to rows missing or stale spot.
- Positions node click resets to all build-input rows.
- Snapshot node click switches to profile library and selects latest `default_underlying` profile.
- Blocker chip click filters to one underlying and opens edit mode for manual blockers.
- Build success selects the returned profile and switches to profile library.
- Build failure activates the correct node and renders returned blocker chips.
- Profile date labels keep the literal `YYYY-MM-DD` prefix.

Browser verification:

- Open `http://localhost:5173`, navigate to `Pricing Parameters`.
- First viewport shows the build console and visual companion before the long defaults table.
- Build action is visible without scrolling.
- Clicking companion nodes changes the lower section.
- Narrow viewport keeps node labels and action buttons readable without text overlap.

## Scope Boundaries

In scope:

- Frontend build-first UX on the existing `Pricing Parameters` route.
- Interactive Profile Build Map.
- Build blocker chips.
- Build Inputs / Profile Library segmented surface.
- Existing build/import/refresh endpoint integration.
- Optional `accountingDate` prop wiring from `main.tsx` into the Pricing Parameters route.

Out of scope:

- New backend endpoints.
- Pricing math changes.
- Scheduled builds.
- Volatility derivation from market data.
- Curve or surface management.
- Changing how Positions or Risk consume pricing profiles.
- Reintroducing any direct "load to positions" action.

## Implementation Notes

- Keep visual style consistent with existing management pages: compact, operational, bordered, and data-dense.
- Prefer lucide icons for compact row actions if icons are introduced.
- Avoid nesting cards inside cards; the build console can be one panel with unframed internal sections.
- Keep table dimensions stable so editing one row does not shift the page.
- Keep `.superpowers/` ignored; visual companion artifacts are not part of the product commit.
