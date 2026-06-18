# Warm Ledger Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Warm Ledger design system (tokens, primitives, app shell, floating agent, ⌘K palette) plus the Positions route as the reference master-detail vertical slice. After this plan ships, the remaining 5 routes still render in the new shell using their existing layouts; their full Warm Ledger migrations are follow-up plans.

**Architecture:** Vanilla CSS with custom properties as the token source of truth (no Tailwind, no CSS-in-JS). Theme and density are runtime-switchable via `<html data-theme>` and `<html data-density>` attributes; selectors target via `[data-theme="dark"]` and `[data-density="compact"]`. Accessibility-critical primitives (Dialog, Tabs, Tooltip, Toast, Popover, DropdownMenu) come from Radix UI, styled with our tokens. Component logic is unit-tested with vitest + @testing-library/react. The 1458-line `main.tsx` is decomposed by route + shared components + tokens, with the existing string-based routing preserved.

**Tech Stack:** React 19 · Vite · vanilla CSS + custom properties · Radix UI primitives · lucide-react · vitest + @testing-library/react · @fontsource/inter-tight + @fontsource/jetbrains-mono (Berkeley Mono is a paid font; this plan uses JetBrains Mono as the dev/CI fallback per spec).

**Spec:** `docs/superpowers/specs/2026-05-07-ui-ux-redesign-design.md` (commit 141641a).

**Out of scope for this plan (deferred to follow-up plans):** RFQ Approval route, Risk route, Reports route, Agent Desk route, Client RFQ route, accessibility audit, prefers-reduced-motion verification pass, Berkeley Mono procurement.

---

## File structure

**New files (tokens — `frontend/src/tokens/`):**
- `colors.css` — `:root` light + `[data-theme="dark"]` overrides
- `type.css` — font-family + type ramp tokens
- `density.css` — `[data-density="compact"]` overrides
- `motion.css` — durations, curves, keyframes, `prefers-reduced-motion` overrides
- `reset.css` — minimal reset
- `index.css` — imports the above in order

**New files (components — `frontend/src/components/`):**
- `Button.tsx` · `Input.tsx` · `Tabs.tsx` · `Modal.tsx` · `Toast.tsx`
- `Panel.tsx` · `Tile.tsx` · `Table.tsx`
- `Badge.tsx` · `Chip.tsx` · `PageContextChips.tsx`
- `AssetCard.tsx` · `ActionProposal.tsx`
- `FloatingAgent.tsx` · `CommandPalette.tsx`
- `Skeleton.tsx` · `Empty.tsx`
- `AppShell.tsx` · `Sidebar.tsx` · `PageHeader.tsx`

**New files (hooks — `frontend/src/hooks/`):**
- `useTheme.ts` — manage `<html data-theme>` + `localStorage`
- `useDensity.ts` — manage `<html data-density>` + `localStorage`
- `usePageContext.ts` — extracted from `main.tsx`
- `useCommandPalette.ts` — keyboard shortcut + open/close state

**New files (other):**
- `frontend/src/types.ts` — extracted from `main.tsx`
- `frontend/src/api/client.ts` — extracted `api`/`uploadForm` helpers
- `frontend/src/routes/Positions.tsx` — new Positions route (reference vertical slice)
- `frontend/src/routes/PlaceholderRoute.tsx` — temporary wrapper for not-yet-migrated routes
- `frontend/vitest.config.ts` — vitest config
- `frontend/src/test-setup.ts` — testing-library config

**Modified files:**
- `frontend/package.json` — add Radix + fontsource + vitest deps
- `frontend/src/main.tsx` — gut to ~50 lines: router + AppShell wiring
- `frontend/src/styles.css` — keep until last task, then delete

---

# Phase 1 · Tokens, Theme/Density Toggles, Test Setup

## Task 1: Install runtime dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install Radix primitives, fontsource fonts**

Run from `frontend/`:

```bash
npm install \
  @radix-ui/react-dialog \
  @radix-ui/react-tabs \
  @radix-ui/react-tooltip \
  @radix-ui/react-toast \
  @radix-ui/react-popover \
  @radix-ui/react-dropdown-menu \
  @fontsource/inter-tight \
  @fontsource/jetbrains-mono
```

- [ ] **Step 2: Verify installation**

Run: `npm ls @radix-ui/react-dialog @fontsource/inter-tight`
Expected: both packages listed under `frontend/`.

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add Radix primitives and fontsource fonts"
```

## Task 2: Install testing dependencies and configure vitest

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test-setup.ts`

- [ ] **Step 1: Install vitest + testing-library**

Run from `frontend/`:

```bash
npm install --save-dev \
  vitest \
  @testing-library/react \
  @testing-library/jest-dom \
  @testing-library/user-event \
  jsdom
```

- [ ] **Step 2: Add `test` script to package.json**

In `frontend/package.json`, add to `scripts`:

```json
{
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 0.0.0.0",
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

- [ ] **Step 3: Create vitest config**

Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
    css: true,
  },
});
```

- [ ] **Step 4: Create test setup**

Create `frontend/src/test-setup.ts`:

```ts
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.removeAttribute('data-density');
  localStorage.clear();
});
```

- [ ] **Step 5: Verify vitest runs**

Run from `frontend/`: `npm test`
Expected: "No test files found" — vitest is wired correctly.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/test-setup.ts
git commit -m "chore(frontend): wire vitest + react-testing-library"
```

## Task 3: Create color tokens

**Files:**
- Create: `frontend/src/tokens/colors.css`

- [ ] **Step 1: Write tokens file**

Create `frontend/src/tokens/colors.css`:

```css
:root {
  --paper:        #FAFAF6;
  --paper-2:      #F4EFE2;
  --paper-3:      #EAE3D0;
  --hairline:     #D8D0BD;
  --hairline-2:   #B8AC8D;
  --ink:          #14110A;
  --ink-2:        #3C342A;
  --pos:          #2F5D3A;
  --neg:          #8C2A2A;
  --warn:         #B58A2C;
  --info:         #2A4F76;
}

[data-theme="dark"] {
  --paper:        #131009;
  --paper-2:      #1B1710;
  --paper-3:      #2C261A;
  --hairline:     #3F3829;
  --hairline-2:   #5A4F38;
  --ink:          #F0E9D5;
  --ink-2:        #C9C0A8;
  --pos:          #7AAB6A;
  --neg:          #D9645B;
  --warn:         #D9B469;
  --info:         #6A8FB8;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme]),
  :root[data-theme="system"] {
    --paper:        #131009;
    --paper-2:      #1B1710;
    --paper-3:      #2C261A;
    --hairline:     #3F3829;
    --hairline-2:   #5A4F38;
    --ink:          #F0E9D5;
    --ink-2:        #C9C0A8;
    --pos:          #7AAB6A;
    --neg:          #D9645B;
    --warn:         #D9B469;
    --info:         #6A8FB8;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/tokens/colors.css
git commit -m "feat(frontend): add Warm Ledger color tokens"
```

## Task 4: Create type tokens

**Files:**
- Create: `frontend/src/tokens/type.css`

- [ ] **Step 1: Write tokens file**

Create `frontend/src/tokens/type.css`:

```css
@import '@fontsource/inter-tight/400.css';
@import '@fontsource/inter-tight/500.css';
@import '@fontsource/inter-tight/600.css';
@import '@fontsource/inter-tight/700.css';
@import '@fontsource/jetbrains-mono/400.css';
@import '@fontsource/jetbrains-mono/500.css';
@import '@fontsource/jetbrains-mono/700.css';

:root {
  --font-ui: "Inter Tight", "Inter", system-ui, -apple-system, sans-serif;
  --font-numeric: "Berkeley Mono", "JetBrains Mono", ui-monospace, monospace;

  --type-h1-size: 28px;
  --type-h1-weight: 700;
  --type-h2-size: 18px;
  --type-h2-weight: 650;
  --type-h3-size: 14px;
  --type-h3-weight: 600;
  --type-body-size: 14px;
  --type-body-weight: 400;
  --type-small-size: 12px;
  --type-small-weight: 500;
  --type-caps-size: 10px;
  --type-caps-weight: 600;
  --type-num-l-size: 20px;
  --type-num-l-weight: 700;
  --type-num-m-size: 14px;
  --type-num-m-weight: 500;
  --type-num-s-size: 11px;
  --type-num-s-weight: 500;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/tokens/type.css
git commit -m "feat(frontend): add Warm Ledger type tokens"
```

## Task 5: Create density tokens

**Files:**
- Create: `frontend/src/tokens/density.css`

- [ ] **Step 1: Write density file**

Create `frontend/src/tokens/density.css`:

```css
:root {
  --row-height: 36px;
  --panel-padding: 12px;
  --panel-padding-tight: 8px;
  --tile-padding: 12px 14px;
  --button-padding-y: 9px;
  --button-padding-x: 18px;
  --input-padding-y: 8px;
  --input-padding-x: 11px;
  --gap-1: 4px;
  --gap-2: 8px;
  --gap-3: 12px;
  --gap-4: 16px;
  --gap-5: 24px;
  --gap-6: 32px;
}

[data-density="compact"] {
  --row-height: 26px;
  --panel-padding: 8px;
  --panel-padding-tight: 6px;
  --tile-padding: 8px 10px;
  --button-padding-y: 6px;
  --button-padding-x: 12px;
  --input-padding-y: 5px;
  --input-padding-x: 8px;
  --gap-1: 2px;
  --gap-2: 4px;
  --gap-3: 8px;
  --gap-4: 12px;
  --gap-5: 16px;
  --gap-6: 24px;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/tokens/density.css
git commit -m "feat(frontend): add Warm Ledger density tokens"
```

## Task 6: Create motion tokens and reset

**Files:**
- Create: `frontend/src/tokens/motion.css`
- Create: `frontend/src/tokens/reset.css`
- Create: `frontend/src/tokens/index.css`

- [ ] **Step 1: Write motion tokens**

Create `frontend/src/tokens/motion.css`:

```css
:root {
  --motion-fade: 120ms;
  --motion-slide: 180ms;
  --motion-curve: cubic-bezier(0.4, 0, 0.2, 1);
  --motion-shimmer: 1.6s;
}

@keyframes wl-shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@keyframes wl-fade-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes wl-slide-in-right {
  from { transform: translateX(100%); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
}

@keyframes wl-pulse {
  0%, 100% { opacity: 0.4; }
  50%      { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --motion-fade: 0ms;
    --motion-slide: 0ms;
  }
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    transition-duration: 0.001ms !important;
  }
}
```

- [ ] **Step 2: Write reset tokens**

Create `frontend/src/tokens/reset.css`:

```css
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  font-weight: var(--type-body-weight);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
button, input, select, textarea { font: inherit; color: inherit; }
button { background: none; border: none; padding: 0; cursor: pointer; }
a { color: inherit; text-decoration: none; }
```

- [ ] **Step 3: Write tokens index**

Create `frontend/src/tokens/index.css`:

```css
@import './colors.css';
@import './type.css';
@import './density.css';
@import './motion.css';
@import './reset.css';
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/tokens/motion.css frontend/src/tokens/reset.css frontend/src/tokens/index.css
git commit -m "feat(frontend): add Warm Ledger motion + reset tokens"
```

## Task 7: Create useTheme hook with tests

**Files:**
- Create: `frontend/src/hooks/useTheme.ts`
- Create: `frontend/src/hooks/useTheme.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useTheme.test.ts`:

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTheme } from './useTheme';

describe('useTheme', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    localStorage.clear();
  });

  it('defaults to system theme', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('system');
  });

  it('reads persisted theme from localStorage', () => {
    localStorage.setItem('otc:theme', 'dark');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('dark');
  });

  it('setTheme writes data-theme attribute', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('setTheme persists to localStorage', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('light'));
    expect(localStorage.getItem('otc:theme')).toBe('light');
  });

  it('setting system theme removes data-theme attribute', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('dark'));
    act(() => result.current.setTheme('system'));
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/`: `npm test useTheme`
Expected: FAIL with "Cannot find module './useTheme'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/hooks/useTheme.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'otc:theme';

function readStoredTheme(): Theme {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === 'light' || raw === 'dark' || raw === 'system') return raw;
  return 'system';
}

function applyTheme(theme: Theme) {
  if (theme === 'system') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.dataset.theme = theme;
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next);
    setThemeState(next);
  }, []);

  return { theme, setTheme };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test useTheme`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useTheme.ts frontend/src/hooks/useTheme.test.ts
git commit -m "feat(frontend): add useTheme hook"
```

## Task 8: Create useDensity hook with tests

**Files:**
- Create: `frontend/src/hooks/useDensity.ts`
- Create: `frontend/src/hooks/useDensity.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useDensity.test.ts`:

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDensity } from './useDensity';

describe('useDensity', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-density');
    localStorage.clear();
  });

  it('defaults to comfortable', () => {
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('comfortable');
  });

  it('reads persisted density from localStorage', () => {
    localStorage.setItem('otc:density', 'compact');
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('compact');
  });

  it('comfortable density removes data-density attribute', () => {
    document.documentElement.dataset.density = 'compact';
    const { result } = renderHook(() => useDensity());
    act(() => result.current.setDensity('comfortable'));
    expect(document.documentElement.hasAttribute('data-density')).toBe(false);
  });

  it('compact density sets data-density attribute', () => {
    const { result } = renderHook(() => useDensity());
    act(() => result.current.setDensity('compact'));
    expect(document.documentElement.dataset.density).toBe('compact');
  });

  it('toggles between modes', () => {
    const { result } = renderHook(() => useDensity());
    act(() => result.current.toggle());
    expect(result.current.density).toBe('compact');
    act(() => result.current.toggle());
    expect(result.current.density).toBe('comfortable');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test useDensity`
Expected: FAIL with "Cannot find module './useDensity'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/hooks/useDensity.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

const STORAGE_KEY = 'otc:density';

function readStoredDensity(): Density {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === 'compact' ? 'compact' : 'comfortable';
}

function applyDensity(density: Density) {
  if (density === 'comfortable') {
    document.documentElement.removeAttribute('data-density');
  } else {
    document.documentElement.dataset.density = density;
  }
}

export function useDensity() {
  const [density, setDensityState] = useState<Density>(() => readStoredDensity());

  useEffect(() => {
    applyDensity(density);
  }, [density]);

  const setDensity = useCallback((next: Density) => {
    localStorage.setItem(STORAGE_KEY, next);
    setDensityState(next);
  }, []);

  const toggle = useCallback(() => {
    setDensityState((current) => {
      const next = current === 'comfortable' ? 'compact' : 'comfortable';
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { density, setDensity, toggle };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test useDensity`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useDensity.ts frontend/src/hooks/useDensity.test.ts
git commit -m "feat(frontend): add useDensity hook"
```

## Task 9: Wire tokens into root and verify in browser

**Files:**
- Modify: `frontend/src/main.tsx:29` (replace `import './styles.css';` with new tokens import for now; keep old CSS import too until cleanup)

- [ ] **Step 1: Add tokens import alongside existing styles**

In `frontend/src/main.tsx`, after `import './styles.css';`, add:

```ts
import './tokens/index.css';
```

Keep the existing `import './styles.css';` line for now — we will remove it in the final cleanup task.

- [ ] **Step 2: Verify the dev server still runs**

Run from `frontend/`: `npm run dev`
Open `http://localhost:5173`. The page should still render the existing UI (old design wins for now since `styles.css` loads after tokens, and the old code targets old classes).

- [ ] **Step 3: Verify tokens are present in DevTools**

In browser DevTools → Elements → `<html>` → Computed → filter "--paper".
Expected: `--paper: #FAFAF6` resolved.

In console: `document.documentElement.dataset.theme = "dark"`.
Expected: `--paper` flips to `#131009`.

Reset: `document.documentElement.removeAttribute("data-theme")`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat(frontend): wire Warm Ledger tokens into root"
```

---

# Phase 2 · Component Primitives

## Task 10: Extract types.ts

**Files:**
- Create: `frontend/src/types.ts`
- Modify: `frontend/src/main.tsx` (remove the moved type definitions, replace with import)

- [ ] **Step 1: Create types file**

Create `frontend/src/types.ts`:

```ts
export type Route = 'chat' | 'rfq' | 'portfolio' | 'risk' | 'reports' | 'client';

export type Thread = {
  id: number;
  title: string;
  character: string;
  messages: ChatMessage[];
};

export type ChatMessage = {
  id: number;
  role: string;
  character?: string | null;
  content: string;
  meta?: {
    assets?: AgentAsset[];
    pending_actions?: AgentActionProposal[];
    confirmed_action?: AgentActionProposal & { result?: Record<string, unknown> };
    context_used?: PageContext | null;
    routed_character?: string;
    [key: string]: unknown;
  };
};

export type AgentAsset = {
  id: string;
  kind: 'file' | 'image' | 'table' | 'chart' | 'json' | 'markdown';
  title: string;
  mime_type?: string | null;
  url?: string | null;
  path?: string | null;
  data?: unknown;
  metadata?: Record<string, unknown>;
};

export type AgentActionProposal = {
  id: string;
  type: 'price_positions' | 'run_risk' | 'create_report' | 'approve_rfq' | 'reject_rfq';
  label: string;
  summary: string;
  payload: Record<string, unknown>;
  requires_confirmation: boolean;
  status?: 'pending' | 'confirmed' | 'dismissed' | 'failed';
};

export type PageContext = {
  route: Route;
  title: string;
  path: string;
  entity_ids: Record<string, number | string | null | undefined>;
  snapshot: Record<string, unknown>;
  chips: string[];
};

export type PageContextReporter = (context: PageContext) => void;

export type RFQ = {
  id: number;
  client_name: string;
  channel: string;
  status: string;
  request_payload: Record<string, unknown>;
  quote_payload: Record<string, unknown>;
  approved_response?: string | null;
};

export type Portfolio = {
  id: number;
  name: string;
  base_currency: string;
  positions: Array<Record<string, unknown>>;
};

export type MarketInput = {
  id: number;
  source_trade_id: string;
  symbol: string;
  valuation_date: string;
  spot?: number | null;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
};

export type PositionValuationRun = {
  id: number;
  status: string;
  valuation_date: string;
  summary: Record<string, unknown>;
  results: Array<Record<string, unknown>>;
};
```

- [ ] **Step 2: Replace inline type defs in `main.tsx` with import**

In `frontend/src/main.tsx`, delete the inline `type Route = ...` through `type PositionValuationRun = ...` block (lines 31–125 in the current file). Replace with:

```ts
import type {
  Route,
  Thread,
  ChatMessage,
  AgentAsset,
  AgentActionProposal,
  PageContext,
  PageContextReporter,
  RFQ,
  Portfolio,
  MarketInput,
  PositionValuationRun,
} from './types';
```

- [ ] **Step 3: Run typecheck**

Run from `frontend/`: `npx tsc -b --noEmit`
Expected: no errors. If errors mention missing types, ensure all imports are present.

- [ ] **Step 4: Run dev server, verify app still loads**

Run: `npm run dev`. Open `http://localhost:5173`. Existing UI should render unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/main.tsx
git commit -m "refactor(frontend): extract types from main.tsx"
```

## Task 11: Extract api/client.ts

**Files:**
- Create: `frontend/src/api/client.ts`
- Modify: `frontend/src/main.tsx` (remove inline `api` and `uploadForm`, replace with import)

- [ ] **Step 1: Create api/client.ts**

Create `frontend/src/api/client.ts`:

```ts
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function uploadForm<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(path, { method: 'POST', body });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
```

- [ ] **Step 2: Replace inline helpers in main.tsx**

In `frontend/src/main.tsx`, delete the `async function api<T>(...)` and `async function uploadForm<T>(...)` definitions. Replace with:

```ts
import { api, uploadForm } from './api/client';
```

- [ ] **Step 3: Verify dev server runs**

Run: `npm run dev`. Check that all routes still load and API calls succeed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/main.tsx
git commit -m "refactor(frontend): extract api helpers from main.tsx"
```

## Task 12: Build Button primitive with tests

**Files:**
- Create: `frontend/src/components/Button.tsx`
- Create: `frontend/src/components/Button.css`
- Create: `frontend/src/components/Button.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Button.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from './Button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Submit</Button>);
    expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
  });

  it('applies primary variant class', () => {
    render(<Button variant="primary">Go</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--primary');
  });

  it('applies danger variant class', () => {
    render(<Button variant="danger">Reject</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--danger');
  });

  it('applies ghost variant class', () => {
    render(<Button variant="ghost">Skip</Button>);
    expect(screen.getByRole('button')).toHaveClass('wl-button--ghost');
  });

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Click</Button>);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('respects disabled prop', () => {
    render(<Button disabled>Off</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/`: `npm test Button`
Expected: FAIL with "Cannot find module './Button'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Button.tsx`:

```tsx
import React from 'react';
import './Button.css';

export type ButtonVariant = 'primary' | 'default' | 'danger' | 'ghost';

type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  iconOnly?: boolean;
};

export function Button({ variant = 'default', iconOnly = false, className = '', children, ...rest }: Props) {
  const classes = [
    'wl-button',
    `wl-button--${variant}`,
    iconOnly ? 'wl-button--icon-only' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}
```

Create `frontend/src/components/Button.css`:

```css
.wl-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--gap-2);
  border: 1px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  padding: var(--button-padding-y) var(--button-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  font-weight: 600;
  border-radius: 0;
  cursor: pointer;
  transition: background var(--motion-fade) linear;
}
.wl-button:hover { background: var(--paper-2); }
.wl-button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.wl-button:disabled { opacity: 0.5; cursor: not-allowed; }

.wl-button--primary { background: var(--ink); color: var(--paper); }
.wl-button--primary:hover { background: var(--ink-2); }

.wl-button--danger { background: var(--paper); color: var(--neg); border-color: var(--neg); }
.wl-button--danger:hover { background: var(--paper-2); }

.wl-button--ghost { border-color: transparent; background: transparent; }
.wl-button--ghost:hover { background: var(--paper-2); }

.wl-button--icon-only { padding: var(--button-padding-y); width: calc(var(--button-padding-y) * 2 + 16px); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Button`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Button.tsx frontend/src/components/Button.css frontend/src/components/Button.test.tsx
git commit -m "feat(frontend): add Button primitive"
```

## Task 13: Build Input primitive with tests

**Files:**
- Create: `frontend/src/components/Input.tsx`
- Create: `frontend/src/components/Input.css`
- Create: `frontend/src/components/Input.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Input.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Input } from './Input';

describe('Input', () => {
  it('renders with label', () => {
    render(<Input label="Strike" />);
    expect(screen.getByText('Strike')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('binds label to input via htmlFor', () => {
    render(<Input label="Underlying" id="u1" />);
    expect(screen.getByLabelText('Underlying')).toHaveAttribute('id', 'u1');
  });

  it('applies error state', () => {
    render(<Input label="Maturity" error="Beyond cap" />);
    expect(screen.getByText('Beyond cap')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveClass('wl-input--error');
  });

  it('renders hint when present and no error', () => {
    render(<Input label="Strike" hint="Press up/down to step" />);
    expect(screen.getByText('Press up/down to step')).toBeInTheDocument();
  });

  it('passes value through', async () => {
    render(<Input label="X" defaultValue="hello" />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('hello');
    await userEvent.clear(input);
    await userEvent.type(input, 'world');
    expect(input.value).toBe('world');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Input`
Expected: FAIL with "Cannot find module './Input'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Input.tsx`:

```tsx
import React, { useId } from 'react';
import './Input.css';

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Input({ label, hint, error, id, className = '', ...rest }: Props) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const inputClass = [
    'wl-input',
    error ? 'wl-input--error' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <div className="wl-field">
      {label && <label className="wl-field__label" htmlFor={inputId}>{label}</label>}
      <input id={inputId} className={inputClass} {...rest} />
      {error
        ? <div className="wl-field__hint wl-field__hint--error">{error}</div>
        : hint && <div className="wl-field__hint">{hint}</div>}
    </div>
  );
}
```

Create `frontend/src/components/Input.css`:

```css
.wl-field { display: flex; flex-direction: column; gap: var(--gap-1); }
.wl-field__label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-input {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  border-radius: 0;
}
.wl-input:focus {
  outline: none;
  border: 2px solid var(--ink);
  padding: calc(var(--input-padding-y) - 1px) calc(var(--input-padding-x) - 1px);
}
.wl-input--error { border-color: var(--neg); }
.wl-field__hint { font-size: var(--type-small-size); color: var(--ink-2); }
.wl-field__hint--error { color: var(--neg); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Input`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Input.tsx frontend/src/components/Input.css frontend/src/components/Input.test.tsx
git commit -m "feat(frontend): add Input primitive"
```

## Task 14: Build Badge and Chip primitives

**Files:**
- Create: `frontend/src/components/Badge.tsx`
- Create: `frontend/src/components/Badge.css`
- Create: `frontend/src/components/Chip.tsx`
- Create: `frontend/src/components/Chip.css`
- Create: `frontend/src/components/Badge.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Badge.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Badge } from './Badge';
import { Chip } from './Chip';

describe('Badge', () => {
  it('renders text and applies variant class', () => {
    render(<Badge variant="pos">APPROVED</Badge>);
    const badge = screen.getByText('APPROVED');
    expect(badge).toHaveClass('wl-badge--pos');
  });

  it('applies solid variant', () => {
    render(<Badge variant="ink" solid>CONFIRMED</Badge>);
    expect(screen.getByText('CONFIRMED')).toHaveClass('wl-badge--solid');
  });
});

describe('Chip', () => {
  it('renders children', () => {
    render(<Chip>CSI500</Chip>);
    expect(screen.getByText('CSI500')).toBeInTheDocument();
  });

  it('shows close button when onRemove is provided', async () => {
    const onRemove = vi.fn();
    render(<Chip onRemove={onRemove}>CSI500</Chip>);
    await userEvent.click(screen.getByRole('button', { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('hides close button when no onRemove', () => {
    render(<Chip>CSI500</Chip>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Badge`
Expected: FAIL with "Cannot find module './Badge'" or "./Chip".

- [ ] **Step 3: Write Badge**

Create `frontend/src/components/Badge.tsx`:

```tsx
import React from 'react';
import './Badge.css';

export type BadgeVariant = 'pos' | 'neg' | 'warn' | 'info' | 'ink';

type Props = {
  variant?: BadgeVariant;
  solid?: boolean;
  children: React.ReactNode;
  className?: string;
};

export function Badge({ variant = 'ink', solid = false, children, className = '' }: Props) {
  const classes = [
    'wl-badge',
    `wl-badge--${variant}`,
    solid ? 'wl-badge--solid' : '',
    className,
  ].filter(Boolean).join(' ');
  return <span className={classes}>{children}</span>;
}
```

Create `frontend/src/components/Badge.css`:

```css
.wl-badge {
  display: inline-block;
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border: 1px solid currentColor;
  line-height: 1.4;
}
.wl-badge--pos { color: var(--pos); }
.wl-badge--neg { color: var(--neg); }
.wl-badge--warn { color: var(--warn); }
.wl-badge--info { color: var(--info); }
.wl-badge--ink { color: var(--ink); }
.wl-badge--solid {
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
}
```

- [ ] **Step 4: Write Chip**

Create `frontend/src/components/Chip.tsx`:

```tsx
import React from 'react';
import './Chip.css';

type Props = {
  children: React.ReactNode;
  onRemove?: () => void;
  onClick?: () => void;
  className?: string;
};

export function Chip({ children, onRemove, onClick, className = '' }: Props) {
  return (
    <span
      className={`wl-chip ${onClick ? 'wl-chip--clickable' : ''} ${className}`.trim()}
      onClick={onClick}
    >
      <span className="wl-chip__label">{children}</span>
      {onRemove && (
        <button
          type="button"
          className="wl-chip__remove"
          aria-label="Remove"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
        >
          ×
        </button>
      )}
    </span>
  );
}
```

Create `frontend/src/components/Chip.css`:

```css
.wl-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--gap-1);
  padding: 3px 8px;
  background: var(--paper-2);
  border: 1px solid var(--hairline);
  color: var(--ink-2);
  font-size: var(--type-small-size);
  font-weight: 500;
}
.wl-chip--clickable { cursor: pointer; }
.wl-chip--clickable:hover { border-color: var(--ink); }
.wl-chip__remove {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
  padding: 0 2px;
  line-height: 1;
  cursor: pointer;
}
.wl-chip__remove:hover { color: var(--ink); }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test Badge`
Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Badge.tsx frontend/src/components/Badge.css frontend/src/components/Chip.tsx frontend/src/components/Chip.css frontend/src/components/Badge.test.tsx
git commit -m "feat(frontend): add Badge and Chip primitives"
```

## Task 15: Build Panel and Tile primitives

**Files:**
- Create: `frontend/src/components/Panel.tsx`
- Create: `frontend/src/components/Panel.css`
- Create: `frontend/src/components/Tile.tsx`
- Create: `frontend/src/components/Tile.css`
- Create: `frontend/src/components/Panel.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Panel.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Panel } from './Panel';
import { Tile } from './Tile';

describe('Panel', () => {
  it('renders title and body', () => {
    render(
      <Panel title="Greeks" meta="LIVE">
        <div>body</div>
      </Panel>
    );
    expect(screen.getByText('Greeks')).toBeInTheDocument();
    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('omits meta when not provided', () => {
    render(<Panel title="Greeks">body</Panel>);
    expect(screen.queryByText('LIVE')).not.toBeInTheDocument();
  });
});

describe('Tile', () => {
  it('renders label and value', () => {
    render(<Tile label="NAV" value="38.2M" />);
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('38.2M')).toBeInTheDocument();
  });

  it('applies pos variant', () => {
    const { container } = render(<Tile label="P&L" value="+1.84%" variant="pos" />);
    expect(container.firstChild).toHaveClass('wl-tile--pos');
  });

  it('renders delta when provided', () => {
    render(<Tile label="NAV" value="38.2M" delta="+0.71M today" />);
    expect(screen.getByText('+0.71M today')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Panel`
Expected: FAIL with "Cannot find module './Panel'" or "./Tile".

- [ ] **Step 3: Write Panel**

Create `frontend/src/components/Panel.tsx`:

```tsx
import React from 'react';
import './Panel.css';

type Props = {
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

export function Panel({ title, meta, children, className = '' }: Props) {
  return (
    <section className={`wl-panel ${className}`.trim()}>
      <header className="wl-panel__head">
        <span className="wl-panel__title">{title}</span>
        {meta && <span className="wl-panel__meta">{meta}</span>}
      </header>
      <div className="wl-panel__body">{children}</div>
    </section>
  );
}
```

Create `frontend/src/components/Panel.css`:

```css
.wl-panel { border: 1px solid var(--ink); background: var(--paper); display: flex; flex-direction: column; }
.wl-panel__head {
  background: var(--ink);
  color: var(--paper);
  padding: 6px var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-panel__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-panel__meta {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  opacity: 0.85;
}
.wl-panel__body { padding: var(--panel-padding); }
```

- [ ] **Step 4: Write Tile**

Create `frontend/src/components/Tile.tsx`:

```tsx
import React from 'react';
import './Tile.css';

export type TileVariant = 'default' | 'pos' | 'neg';

type Props = {
  label: string;
  value: React.ReactNode;
  delta?: React.ReactNode;
  variant?: TileVariant;
  className?: string;
};

export function Tile({ label, value, delta, variant = 'default', className = '' }: Props) {
  return (
    <div className={`wl-tile wl-tile--${variant} ${className}`.trim()}>
      <div className="wl-tile__label">{label}</div>
      <div className="wl-tile__value">{value}</div>
      {delta && <div className="wl-tile__delta">{delta}</div>}
    </div>
  );
}
```

Create `frontend/src/components/Tile.css`:

```css
.wl-tile {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--tile-padding);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wl-tile__label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
}
.wl-tile__value {
  font-family: var(--font-numeric);
  font-size: var(--type-num-l-size);
  font-weight: var(--type-num-l-weight);
  color: var(--ink);
  letter-spacing: -0.01em;
}
.wl-tile--pos .wl-tile__value { color: var(--pos); }
.wl-tile--neg .wl-tile__value { color: var(--neg); }
.wl-tile__delta {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test Panel`
Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Panel.tsx frontend/src/components/Panel.css frontend/src/components/Tile.tsx frontend/src/components/Tile.css frontend/src/components/Panel.test.tsx
git commit -m "feat(frontend): add Panel and Tile primitives"
```

## Task 16: Build Table primitive

**Files:**
- Create: `frontend/src/components/Table.tsx`
- Create: `frontend/src/components/Table.css`
- Create: `frontend/src/components/Table.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Table.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Table } from './Table';

describe('Table', () => {
  type Row = { trade: string; price: number };
  const rows: Row[] = [
    { trade: 'A', price: 1 },
    { trade: 'B', price: 2 },
  ];
  const columns = [
    { key: 'trade', header: 'TRADE' },
    { key: 'price', header: 'PRICE', numeric: true },
  ] as const;

  it('renders headers and rows', () => {
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    expect(screen.getByText('TRADE')).toBeInTheDocument();
    expect(screen.getByText('PRICE')).toBeInTheDocument();
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument();
  });

  it('marks selected row', () => {
    const { container } = render(
      <Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} selectedKey="A" />
    );
    const selected = container.querySelectorAll('.wl-table__row--selected');
    expect(selected.length).toBe(1);
  });

  it('calls onRowClick', async () => {
    const onRowClick = vi.fn();
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} onRowClick={onRowClick} />);
    await userEvent.click(screen.getByText('A'));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Table`
Expected: FAIL with "Cannot find module './Table'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Table.tsx`:

```tsx
import React from 'react';
import './Table.css';

export type Column<T> = {
  key: string;
  header: React.ReactNode;
  render?: (row: T) => React.ReactNode;
  numeric?: boolean;
  width?: string;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  selectedKey?: string | number | null;
  onRowClick?: (row: T) => void;
  className?: string;
};

export function Table<T>({
  columns, rows, rowKey, selectedKey, onRowClick, className = '',
}: Props<T>) {
  const gridTemplate = columns.map((c) => c.width ?? '1fr').join(' ');
  return (
    <div className={`wl-table ${className}`.trim()}>
      <div className="wl-table__row wl-table__row--head" style={{ gridTemplateColumns: gridTemplate }}>
        {columns.map((c) => (
          <div
            key={c.key}
            className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
          >
            {c.header}
          </div>
        ))}
      </div>
      {rows.map((row) => {
        const key = rowKey(row);
        const isSelected = selectedKey === key;
        return (
          <div
            key={key}
            className={`wl-table__row ${isSelected ? 'wl-table__row--selected' : ''}`}
            style={{ gridTemplateColumns: gridTemplate }}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            data-clickable={onRowClick ? 'true' : undefined}
          >
            {columns.map((c) => (
              <div
                key={c.key}
                className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
              >
                {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
```

Create `frontend/src/components/Table.css`:

```css
.wl-table { border: 1px solid var(--ink); background: var(--paper); display: flex; flex-direction: column; }
.wl-table__row {
  display: grid;
  gap: var(--gap-3);
  padding: 0 var(--gap-3);
  align-items: center;
  height: var(--row-height);
  border-bottom: 1px solid var(--paper-3);
}
.wl-table__row:last-child { border-bottom: 0; }
.wl-table__row[data-clickable="true"] { cursor: pointer; }
.wl-table__row[data-clickable="true"]:hover { background: var(--paper-2); }
.wl-table__row--selected { background: var(--paper-3); }
.wl-table__row--head {
  background: var(--ink);
  color: var(--paper);
  border-bottom: 1px solid var(--ink);
}
.wl-table__row--head .wl-table__cell {
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-table__cell {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  font-weight: var(--type-num-m-weight);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wl-table__cell--num { text-align: right; font-variant-numeric: tabular-nums; }
.wl-table__row--head .wl-table__cell--num { text-align: right; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Table`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Table.tsx frontend/src/components/Table.css frontend/src/components/Table.test.tsx
git commit -m "feat(frontend): add Table primitive"
```

## Task 17: Build Tabs primitive (Radix-based)

**Files:**
- Create: `frontend/src/components/Tabs.tsx`
- Create: `frontend/src/components/Tabs.css`
- Create: `frontend/src/components/Tabs.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Tabs.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './Tabs';

describe('Tabs', () => {
  it('shows the active tab content', () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">First</TabsContent>
        <TabsContent value="b">Second</TabsContent>
      </Tabs>
    );
    expect(screen.getByText('First')).toBeInTheDocument();
    expect(screen.queryByText('Second')).not.toBeInTheDocument();
  });

  it('switches content on click', async () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">First</TabsContent>
        <TabsContent value="b">Second</TabsContent>
      </Tabs>
    );
    await userEvent.click(screen.getByRole('tab', { name: 'B' }));
    expect(screen.getByText('Second')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Tabs`
Expected: FAIL with "Cannot find module './Tabs'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Tabs.tsx`:

```tsx
import * as RadixTabs from '@radix-ui/react-tabs';
import './Tabs.css';

export const Tabs = (props: RadixTabs.TabsProps) => (
  <RadixTabs.Root className="wl-tabs" {...props} />
);

export const TabsList = (props: RadixTabs.TabsListProps) => (
  <RadixTabs.List className="wl-tabs__list" {...props} />
);

export const TabsTrigger = (props: RadixTabs.TabsTriggerProps) => (
  <RadixTabs.Trigger className="wl-tabs__trigger" {...props} />
);

export const TabsContent = (props: RadixTabs.TabsContentProps) => (
  <RadixTabs.Content className="wl-tabs__content" {...props} />
);
```

Create `frontend/src/components/Tabs.css`:

```css
.wl-tabs__list {
  display: flex;
  border-bottom: 1px solid var(--ink);
}
.wl-tabs__trigger {
  background: transparent;
  border: 0;
  padding: var(--gap-2) var(--gap-3);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  font-weight: 500;
  color: var(--ink-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color var(--motion-fade) linear;
}
.wl-tabs__trigger:hover { color: var(--ink); }
.wl-tabs__trigger[data-state="active"] {
  color: var(--ink);
  border-bottom-color: var(--ink);
  font-weight: 600;
}
.wl-tabs__content { padding-top: var(--gap-3); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Tabs`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Tabs.tsx frontend/src/components/Tabs.css frontend/src/components/Tabs.test.tsx
git commit -m "feat(frontend): add Tabs primitive (Radix)"
```

## Task 18: Build Modal primitive (Radix Dialog)

**Files:**
- Create: `frontend/src/components/Modal.tsx`
- Create: `frontend/src/components/Modal.css`
- Create: `frontend/src/components/Modal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Modal.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

describe('Modal', () => {
  it('renders title and body when open', () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm">
        <p>Are you sure?</p>
      </Modal>
    );
    expect(screen.getByText('Confirm')).toBeInTheDocument();
    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    render(
      <Modal open={false} onOpenChange={() => {}} title="Confirm">
        <p>Are you sure?</p>
      </Modal>
    );
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument();
  });

  it('calls onOpenChange(false) when close button clicked', async () => {
    const onOpenChange = vi.fn();
    render(
      <Modal open onOpenChange={onOpenChange} title="Confirm">
        body
      </Modal>
    );
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Modal`
Expected: FAIL with "Cannot find module './Modal'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Modal.tsx`:

```tsx
import * as Dialog from '@radix-ui/react-dialog';
import React from 'react';
import './Modal.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
};

export function Modal({ open, onOpenChange, title, description, children }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="wl-modal__overlay" />
        <Dialog.Content className="wl-modal__content">
          <header className="wl-modal__head">
            <Dialog.Title className="wl-modal__title">{title}</Dialog.Title>
            <Dialog.Close className="wl-modal__close" aria-label="Close">×</Dialog.Close>
          </header>
          {description && (
            <Dialog.Description className="wl-modal__description">{description}</Dialog.Description>
          )}
          <div className="wl-modal__body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
```

Create `frontend/src/components/Modal.css`:

```css
.wl-modal__overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 10, 0.42);
  animation: wl-fade-in var(--motion-fade) linear;
}
.wl-modal__content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(560px, 90vw);
  max-height: 80vh;
  background: var(--paper);
  border: 1px solid var(--ink);
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.18);
  display: flex;
  flex-direction: column;
  animation: wl-fade-in var(--motion-slide) var(--motion-curve);
}
.wl-modal__head {
  background: var(--ink);
  color: var(--paper);
  padding: var(--gap-2) var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-modal__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0;
}
.wl-modal__close {
  font-family: var(--font-numeric);
  background: none;
  border: 0;
  color: var(--paper);
  font-size: 18px;
  cursor: pointer;
}
.wl-modal__description {
  padding: var(--gap-3);
  color: var(--ink-2);
  font-size: var(--type-small-size);
  border-bottom: 1px solid var(--paper-3);
}
.wl-modal__body { padding: var(--panel-padding); overflow-y: auto; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Modal`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Modal.tsx frontend/src/components/Modal.css frontend/src/components/Modal.test.tsx
git commit -m "feat(frontend): add Modal primitive (Radix Dialog)"
```

## Task 19: Build Skeleton and Empty primitives

**Files:**
- Create: `frontend/src/components/Skeleton.tsx`
- Create: `frontend/src/components/Skeleton.css`
- Create: `frontend/src/components/Empty.tsx`
- Create: `frontend/src/components/Empty.css`
- Create: `frontend/src/components/Skeleton.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Skeleton.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Skeleton } from './Skeleton';
import { Empty } from './Empty';

describe('Skeleton', () => {
  it('renders with given height', () => {
    const { container } = render(<Skeleton height={20} />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('wl-skeleton');
    expect(el.style.height).toBe('20px');
  });

  it('accepts width prop', () => {
    const { container } = render(<Skeleton height={12} width="60%" />);
    const el = container.firstChild as HTMLElement;
    expect(el.style.width).toBe('60%');
  });
});

describe('Empty', () => {
  it('renders message', () => {
    render(<Empty message="No risk runs yet." />);
    expect(screen.getByText('No risk runs yet.')).toBeInTheDocument();
  });

  it('renders action when provided', () => {
    render(<Empty message="No data" action={<button>Create first run</button>} />);
    expect(screen.getByRole('button', { name: /create first run/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Skeleton`
Expected: FAIL with "Cannot find module './Skeleton'" or "./Empty".

- [ ] **Step 3: Write Skeleton**

Create `frontend/src/components/Skeleton.tsx`:

```tsx
import './Skeleton.css';

type Props = {
  height: number;
  width?: number | string;
  className?: string;
};

export function Skeleton({ height, width, className = '' }: Props) {
  return (
    <div
      className={`wl-skeleton ${className}`.trim()}
      style={{ height, width: typeof width === 'number' ? `${width}px` : width }}
    />
  );
}
```

Create `frontend/src/components/Skeleton.css`:

```css
.wl-skeleton {
  background: linear-gradient(90deg, var(--paper-3) 0%, var(--paper-2) 50%, var(--paper-3) 100%);
  background-size: 200% 100%;
  animation: wl-shimmer var(--motion-shimmer) linear infinite;
  width: 100%;
}
@media (prefers-reduced-motion: reduce) {
  .wl-skeleton { animation: none; background: var(--paper-2); }
}
```

- [ ] **Step 4: Write Empty**

Create `frontend/src/components/Empty.tsx`:

```tsx
import React from 'react';
import './Empty.css';

type Props = {
  message: string;
  symbol?: string;
  action?: React.ReactNode;
  className?: string;
};

export function Empty({ message, symbol = '∅', action, className = '' }: Props) {
  return (
    <div className={`wl-empty ${className}`.trim()}>
      <div className="wl-empty__symbol">{symbol}</div>
      <div className="wl-empty__message">{message}</div>
      {action && <div className="wl-empty__action">{action}</div>}
    </div>
  );
}
```

Create `frontend/src/components/Empty.css`:

```css
.wl-empty {
  border: 1px dashed var(--hairline-2);
  padding: var(--gap-5);
  text-align: center;
  color: var(--ink-2);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--gap-2);
}
.wl-empty__symbol {
  font-family: var(--font-numeric);
  font-size: 22px;
  color: var(--ink);
}
.wl-empty__message { font-size: var(--type-body-size); }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test Skeleton`
Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Skeleton.tsx frontend/src/components/Skeleton.css frontend/src/components/Empty.tsx frontend/src/components/Empty.css frontend/src/components/Skeleton.test.tsx
git commit -m "feat(frontend): add Skeleton and Empty primitives"
```

## Task 20: Build PageContextChips component

**Files:**
- Create: `frontend/src/components/PageContextChips.tsx`
- Create: `frontend/src/components/PageContextChips.css`
- Create: `frontend/src/components/PageContextChips.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/PageContextChips.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageContextChips } from './PageContextChips';

describe('PageContextChips', () => {
  it('renders all chips', () => {
    render(<PageContextChips chips={['val 2026-05-07', '12 trades', 'Run #87']} />);
    expect(screen.getByText('val 2026-05-07')).toBeInTheDocument();
    expect(screen.getByText('12 trades')).toBeInTheDocument();
    expect(screen.getByText('Run #87')).toBeInTheDocument();
  });

  it('renders nothing when empty', () => {
    const { container } = render(<PageContextChips chips={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test PageContextChips`
Expected: FAIL with "Cannot find module './PageContextChips'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/PageContextChips.tsx`:

```tsx
import { Chip } from './Chip';
import './PageContextChips.css';

type Props = {
  chips: string[];
};

export function PageContextChips({ chips }: Props) {
  if (chips.length === 0) return null;
  return (
    <div className="wl-pchips">
      {chips.map((c) => <Chip key={c}>{c}</Chip>)}
    </div>
  );
}
```

Create `frontend/src/components/PageContextChips.css`:

```css
.wl-pchips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--gap-1);
  margin-top: var(--gap-1);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test PageContextChips`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PageContextChips.tsx frontend/src/components/PageContextChips.css frontend/src/components/PageContextChips.test.tsx
git commit -m "feat(frontend): add PageContextChips"
```

## Task 21: Build AssetCard component

**Files:**
- Create: `frontend/src/components/AssetCard.tsx`
- Create: `frontend/src/components/AssetCard.css`
- Create: `frontend/src/components/AssetCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/AssetCard.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssetCard } from './AssetCard';
import type { AgentAsset } from '../types';

describe('AssetCard', () => {
  const asset: AgentAsset = {
    id: 'a1',
    kind: 'json',
    title: 'pricing_request.json',
    metadata: { size: '3.2KB' },
  };

  it('renders kind icon, title, and subtitle', () => {
    render(<AssetCard asset={asset} subtitle="3.2KB · LangGraph trace" />);
    expect(screen.getByText('JSON')).toBeInTheDocument();
    expect(screen.getByText('pricing_request.json')).toBeInTheDocument();
    expect(screen.getByText('3.2KB · LangGraph trace')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test AssetCard`
Expected: FAIL with "Cannot find module './AssetCard'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/AssetCard.tsx`:

```tsx
import React from 'react';
import type { AgentAsset } from '../types';
import './AssetCard.css';

const kindLabel: Record<AgentAsset['kind'], string> = {
  file: 'FILE',
  image: 'IMG',
  table: 'TBL',
  chart: 'CHRT',
  json: 'JSON',
  markdown: 'MD',
};

type Props = {
  asset: AgentAsset;
  subtitle?: string;
  actions?: React.ReactNode;
};

export function AssetCard({ asset, subtitle, actions }: Props) {
  return (
    <div className="wl-asset">
      <div className="wl-asset__icon">{kindLabel[asset.kind]}</div>
      <div className="wl-asset__meta">
        <div className="wl-asset__title">{asset.title}</div>
        {subtitle && <div className="wl-asset__sub">{subtitle}</div>}
      </div>
      {actions && <div className="wl-asset__actions">{actions}</div>}
    </div>
  );
}
```

Create `frontend/src/components/AssetCard.css`:

```css
.wl-asset {
  border: 1px solid var(--ink);
  background: var(--paper);
  padding: var(--gap-2) var(--gap-3);
  display: flex;
  gap: var(--gap-3);
  align-items: flex-start;
}
.wl-asset__icon {
  width: 36px;
  height: 36px;
  border: 1px solid var(--ink);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  font-weight: 700;
  flex-shrink: 0;
}
.wl-asset__meta { flex: 1; min-width: 0; }
.wl-asset__title {
  font-size: var(--type-body-size);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wl-asset__sub {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
  margin-top: 2px;
}
.wl-asset__actions { display: flex; gap: var(--gap-1); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test AssetCard`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AssetCard.tsx frontend/src/components/AssetCard.css frontend/src/components/AssetCard.test.tsx
git commit -m "feat(frontend): add AssetCard"
```

## Task 22: Build ActionProposal component

**Files:**
- Create: `frontend/src/components/ActionProposal.tsx`
- Create: `frontend/src/components/ActionProposal.css`
- Create: `frontend/src/components/ActionProposal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ActionProposal.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ActionProposal } from './ActionProposal';
import type { AgentActionProposal } from '../types';

describe('ActionProposal', () => {
  const proposal: AgentActionProposal = {
    id: 'p1',
    type: 'price_positions',
    label: 'Run pricing on Portfolio "Desk-Q2"',
    summary: '12 positions · valuation date 2026-05-07',
    payload: {},
    requires_confirmation: true,
    status: 'pending',
  };

  it('renders label and summary', () => {
    render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText(proposal.label)).toBeInTheDocument();
    expect(screen.getByText(proposal.summary)).toBeInTheDocument();
  });

  it('calls onConfirm when confirm clicked', async () => {
    const onConfirm = vi.fn();
    render(<ActionProposal proposal={proposal} onConfirm={onConfirm} onDismiss={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledWith(proposal);
  });

  it('calls onDismiss when dismiss clicked', async () => {
    const onDismiss = vi.fn();
    render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledWith(proposal);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test ActionProposal`
Expected: FAIL with "Cannot find module './ActionProposal'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/ActionProposal.tsx`:

```tsx
import { Button } from './Button';
import type { AgentActionProposal } from '../types';
import './ActionProposal.css';

type Props = {
  proposal: AgentActionProposal;
  onConfirm: (p: AgentActionProposal) => void;
  onDismiss: (p: AgentActionProposal) => void;
  onViewPayload?: (p: AgentActionProposal) => void;
};

export function ActionProposal({ proposal, onConfirm, onDismiss, onViewPayload }: Props) {
  return (
    <div className="wl-actprop">
      <header className="wl-actprop__head">
        <span className="wl-actprop__tag">⚠ Pending Confirmation</span>
        <span className="wl-actprop__kbd">⌘↵ to confirm</span>
      </header>
      <div className="wl-actprop__label">{proposal.label}</div>
      <div className="wl-actprop__summary">{proposal.summary}</div>
      <div className="wl-actprop__actions">
        <Button variant="primary" onClick={() => onConfirm(proposal)}>Confirm Action</Button>
        <Button onClick={() => onDismiss(proposal)}>Dismiss</Button>
        {onViewPayload && <Button variant="ghost" onClick={() => onViewPayload(proposal)}>View payload</Button>}
      </div>
    </div>
  );
}
```

Create `frontend/src/components/ActionProposal.css`:

```css
.wl-actprop {
  border: 1px solid var(--warn);
  background: color-mix(in oklab, var(--warn) 10%, var(--paper));
  padding: var(--gap-3);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-actprop__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-actprop__tag {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--warn);
}
.wl-actprop__kbd {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
  border: 1px solid var(--hairline);
  padding: 1px 6px;
}
.wl-actprop__label {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
}
.wl-actprop__summary {
  font-size: var(--type-small-size);
  color: var(--ink-2);
  line-height: 1.45;
}
.wl-actprop__actions { display: flex; gap: var(--gap-2); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test ActionProposal`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ActionProposal.tsx frontend/src/components/ActionProposal.css frontend/src/components/ActionProposal.test.tsx
git commit -m "feat(frontend): add ActionProposal"
```

---

# Phase 3 · App Shell + Cross-Route Affordances

## Task 23: Build Sidebar

**Files:**
- Create: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/components/Sidebar.css`
- Create: `frontend/src/components/Sidebar.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Sidebar.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Sidebar } from './Sidebar';

describe('Sidebar', () => {
  const items = [
    { route: 'chat' as const, label: 'Agent Desk' },
    { route: 'portfolio' as const, label: 'Positions' },
  ];

  it('renders nav items and marks active route', () => {
    const { container } = render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const buttons = container.querySelectorAll('.wl-sidebar__nav button');
    expect(buttons.length).toBe(2);
    expect(buttons[0]).toHaveClass('wl-sidebar__nav-item--active');
    expect(buttons[1]).not.toHaveClass('wl-sidebar__nav-item--active');
  });

  it('calls onNavigate when clicked', async () => {
    const onNavigate = vi.fn();
    render(<Sidebar items={items} active="chat" onNavigate={onNavigate} />);
    await userEvent.click(screen.getByRole('button', { name: 'Positions' }));
    expect(onNavigate).toHaveBeenCalledWith('portfolio');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test Sidebar`
Expected: FAIL with "Cannot find module './Sidebar'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/Sidebar.tsx`:

```tsx
import type { Route } from '../types';
import './Sidebar.css';

export type SidebarItem = {
  route: Route;
  label: string;
};

type Props = {
  items: SidebarItem[];
  active: Route;
  onNavigate: (route: Route) => void;
};

export function Sidebar({ items, active, onNavigate }: Props) {
  return (
    <aside className="wl-sidebar">
      <div className="wl-sidebar__brand">
        <div className="wl-sidebar__brand-mark">OTC</div>
        <div className="wl-sidebar__brand-text">
          <strong>Open OTC</strong>
          <span>AI trading platform</span>
        </div>
      </div>
      <nav className="wl-sidebar__nav">
        {items.map((item) => (
          <button
            key={item.route}
            type="button"
            className={`wl-sidebar__nav-item ${item.route === active ? 'wl-sidebar__nav-item--active' : ''}`.trim()}
            onClick={() => onNavigate(item.route)}
          >
            {item.label}
          </button>
        ))}
      </nav>
    </aside>
  );
}
```

Create `frontend/src/components/Sidebar.css`:

```css
.wl-sidebar {
  background: var(--paper-2);
  border-right: 1px solid var(--ink);
  width: 240px;
  padding: var(--gap-4) var(--gap-3);
  display: flex;
  flex-direction: column;
  gap: var(--gap-5);
  flex-shrink: 0;
}
.wl-sidebar__brand { display: flex; align-items: center; gap: var(--gap-3); }
.wl-sidebar__brand-mark {
  width: 36px;
  height: 36px;
  background: var(--ink);
  color: var(--paper);
  display: grid;
  place-items: center;
  font-family: var(--font-numeric);
  font-weight: 700;
  font-size: 12px;
}
.wl-sidebar__brand-text strong { display: block; font-size: var(--type-body-size); }
.wl-sidebar__brand-text span { display: block; font-size: var(--type-small-size); color: var(--ink-2); }
.wl-sidebar__nav { display: flex; flex-direction: column; gap: 2px; }
.wl-sidebar__nav-item {
  text-align: left;
  padding: var(--gap-2) var(--gap-3);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  color: var(--ink-2);
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: color var(--motion-fade) linear;
}
.wl-sidebar__nav-item:hover { color: var(--ink); }
.wl-sidebar__nav-item--active {
  background: var(--ink);
  color: var(--paper);
  font-weight: 600;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test Sidebar`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.css frontend/src/components/Sidebar.test.tsx
git commit -m "feat(frontend): add Sidebar"
```

## Task 24: Build PageHeader

**Files:**
- Create: `frontend/src/components/PageHeader.tsx`
- Create: `frontend/src/components/PageHeader.css`
- Create: `frontend/src/components/PageHeader.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/PageHeader.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageHeader } from './PageHeader';

describe('PageHeader', () => {
  it('renders title', () => {
    render(<PageHeader title="Positions" chips={[]} />);
    expect(screen.getByText('Positions')).toBeInTheDocument();
  });

  it('renders chips', () => {
    render(<PageHeader title="Positions" chips={['12 trades']} />);
    expect(screen.getByText('12 trades')).toBeInTheDocument();
  });

  it('renders action slot', () => {
    render(<PageHeader title="Positions" chips={[]} action={<button>Run</button>} />);
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test PageHeader`
Expected: FAIL with "Cannot find module './PageHeader'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/PageHeader.tsx`:

```tsx
import React from 'react';
import { PageContextChips } from './PageContextChips';
import './PageHeader.css';

type Props = {
  title: string;
  chips: string[];
  action?: React.ReactNode;
};

export function PageHeader({ title, chips, action }: Props) {
  return (
    <header className="wl-pageheader">
      <div className="wl-pageheader__main">
        <h1 className="wl-pageheader__title">{title}</h1>
        <PageContextChips chips={chips} />
      </div>
      {action && <div className="wl-pageheader__action">{action}</div>}
    </header>
  );
}
```

Create `frontend/src/components/PageHeader.css`:

```css
.wl-pageheader {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--gap-4);
  margin-bottom: var(--gap-4);
}
.wl-pageheader__main { flex: 1; min-width: 0; }
.wl-pageheader__title {
  margin: 0;
  font-size: var(--type-h1-size);
  font-weight: var(--type-h1-weight);
  text-transform: uppercase;
  letter-spacing: -0.01em;
  color: var(--ink);
  line-height: 1.1;
}
.wl-pageheader__action { display: flex; gap: var(--gap-2); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test PageHeader`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PageHeader.tsx frontend/src/components/PageHeader.css frontend/src/components/PageHeader.test.tsx
git commit -m "feat(frontend): add PageHeader"
```

## Task 25: Build CommandPalette and useCommandPalette

**Files:**
- Create: `frontend/src/hooks/useCommandPalette.ts`
- Create: `frontend/src/hooks/useCommandPalette.test.ts`
- Create: `frontend/src/components/CommandPalette.tsx`
- Create: `frontend/src/components/CommandPalette.css`
- Create: `frontend/src/components/CommandPalette.test.tsx`

- [ ] **Step 1: Write hook test**

Create `frontend/src/hooks/useCommandPalette.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCommandPalette } from './useCommandPalette';

describe('useCommandPalette', () => {
  it('starts closed', () => {
    const { result } = renderHook(() => useCommandPalette());
    expect(result.current.isOpen).toBe(false);
  });

  it('opens via open()', () => {
    const { result } = renderHook(() => useCommandPalette());
    act(() => result.current.open());
    expect(result.current.isOpen).toBe(true);
  });

  it('closes via close()', () => {
    const { result } = renderHook(() => useCommandPalette());
    act(() => result.current.open());
    act(() => result.current.close());
    expect(result.current.isOpen).toBe(false);
  });

  it('opens on Cmd+K', () => {
    const { result } = renderHook(() => useCommandPalette());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
    });
    expect(result.current.isOpen).toBe(true);
  });

  it('opens on Ctrl+K', () => {
    const { result } = renderHook(() => useCommandPalette());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));
    });
    expect(result.current.isOpen).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test useCommandPalette`
Expected: FAIL with "Cannot find module './useCommandPalette'".

- [ ] **Step 3: Write hook**

Create `frontend/src/hooks/useCommandPalette.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';

export function useCommandPalette() {
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen((v) => !v), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && !e.shiftKey) {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle]);

  return { isOpen, open, close, toggle };
}
```

- [ ] **Step 4: Run hook test to verify it passes**

Run: `npm test useCommandPalette`
Expected: PASS — 5 tests.

- [ ] **Step 5: Write component test**

Create `frontend/src/components/CommandPalette.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CommandPalette } from './CommandPalette';

describe('CommandPalette', () => {
  const items = [
    { id: 'price', group: 'Actions', label: 'Price Portfolio · Desk-Q2', shortcut: '⌘P' },
    { id: 'risk',  group: 'Actions', label: 'Run Risk · Desk-Q2',        shortcut: '⌘R' },
    { id: 'jump-snb', group: 'Jump To', label: 'SNB-CSI500 · Trade Detail', shortcut: '↵' },
  ];

  it('renders all items grouped', () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    expect(screen.getByText('Price Portfolio · Desk-Q2')).toBeInTheDocument();
    expect(screen.getByText('SNB-CSI500 · Trade Detail')).toBeInTheDocument();
    expect(screen.getByText(/^Actions$/i)).toBeInTheDocument();
  });

  it('filters items by query', async () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    const input = screen.getByPlaceholderText(/search/i);
    await userEvent.type(input, 'risk');
    expect(screen.getByText('Run Risk · Desk-Q2')).toBeInTheDocument();
    expect(screen.queryByText('SNB-CSI500 · Trade Detail')).not.toBeInTheDocument();
  });

  it('calls onSelect when item clicked', async () => {
    const onSelect = vi.fn();
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={onSelect} />);
    await userEvent.click(screen.getByText('Run Risk · Desk-Q2'));
    expect(onSelect).toHaveBeenCalledWith(items[1]);
  });
});
```

- [ ] **Step 6: Run test to verify it fails**

Run: `npm test CommandPalette`
Expected: FAIL with "Cannot find module './CommandPalette'".

- [ ] **Step 7: Write component**

Create `frontend/src/components/CommandPalette.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import './CommandPalette.css';

export type CommandItem = {
  id: string;
  group: string;
  label: string;
  shortcut?: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: CommandItem[];
  onSelect: (item: CommandItem) => void;
  placeholder?: string;
};

export function CommandPalette({ open, onOpenChange, items, onSelect, placeholder = 'Search…' }: Props) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => it.label.toLowerCase().includes(q) || it.group.toLowerCase().includes(q));
  }, [items, query]);

  const grouped = useMemo(() => {
    const map = new Map<string, CommandItem[]>();
    for (const it of filtered) {
      const arr = map.get(it.group) ?? [];
      arr.push(it);
      map.set(it.group, arr);
    }
    return Array.from(map.entries());
  }, [filtered]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="wl-cmdk__overlay" />
        <Dialog.Content className="wl-cmdk" aria-label="Command palette">
          <input
            ref={inputRef}
            className="wl-cmdk__input"
            placeholder={placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="wl-cmdk__results">
            {grouped.map(([group, list]) => (
              <div key={group} className="wl-cmdk__group">
                <div className="wl-cmdk__group-label">{group}</div>
                {list.map((it) => (
                  <button
                    key={it.id}
                    type="button"
                    className="wl-cmdk__item"
                    onClick={() => onSelect(it)}
                  >
                    <span>{it.label}</span>
                    {it.shortcut && <span className="wl-cmdk__shortcut">{it.shortcut}</span>}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
```

Create `frontend/src/components/CommandPalette.css`:

```css
.wl-cmdk__overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 10, 0.42);
  animation: wl-fade-in var(--motion-fade) linear;
}
.wl-cmdk {
  position: fixed;
  top: 18%;
  left: 50%;
  transform: translateX(-50%);
  width: min(620px, 90vw);
  background: var(--paper);
  border: 1px solid var(--ink);
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.28);
  display: flex;
  flex-direction: column;
}
.wl-cmdk__input {
  border: 0;
  border-bottom: 1px solid var(--ink);
  padding: var(--gap-3);
  font-family: var(--font-numeric);
  font-size: var(--type-body-size);
  background: var(--paper);
  color: var(--ink);
  outline: none;
}
.wl-cmdk__results { padding: var(--gap-1) 0; max-height: 400px; overflow-y: auto; }
.wl-cmdk__group-label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  padding: var(--gap-2) var(--gap-3) var(--gap-1);
}
.wl-cmdk__item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--gap-2) var(--gap-3);
  background: transparent;
  border: 0;
  cursor: pointer;
  width: 100%;
  text-align: left;
  font-size: var(--type-body-size);
}
.wl-cmdk__item:hover { background: var(--paper-3); }
.wl-cmdk__shortcut {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  border: 1px solid var(--hairline);
  padding: 1px 6px;
  color: var(--ink-2);
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `npm test CommandPalette`
Expected: PASS — 3 tests.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/hooks/useCommandPalette.ts frontend/src/hooks/useCommandPalette.test.ts frontend/src/components/CommandPalette.tsx frontend/src/components/CommandPalette.css frontend/src/components/CommandPalette.test.tsx
git commit -m "feat(frontend): add ⌘K command palette"
```

## Task 26: Build FloatingAgent pip

**Files:**
- Create: `frontend/src/components/FloatingAgent.tsx`
- Create: `frontend/src/components/FloatingAgent.css`
- Create: `frontend/src/components/FloatingAgent.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/FloatingAgent.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FloatingAgent } from './FloatingAgent';

describe('FloatingAgent', () => {
  it('renders collapsed pip with label', () => {
    render(<FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread={false} />);
    expect(screen.getByRole('button', { name: /agent/i })).toBeInTheDocument();
  });

  it('shows pulsing dot when hasUnread is true', () => {
    const { container } = render(
      <FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread />
    );
    expect(container.querySelector('.wl-agent-pip__dot--active')).not.toBeNull();
  });

  it('calls onOpenChange(true) when pip clicked', async () => {
    const onOpenChange = vi.fn();
    render(<FloatingAgent open={false} onOpenChange={onOpenChange} chips={[]} hasUnread={false} />);
    await userEvent.click(screen.getByRole('button', { name: /agent/i }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it('renders chips strip when open', () => {
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={['Run #87', 'SNB-CSI500']} hasUnread={false} />
    );
    expect(screen.getByText('Run #87')).toBeInTheDocument();
    expect(screen.getByText('SNB-CSI500')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test FloatingAgent`
Expected: FAIL with "Cannot find module './FloatingAgent'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/FloatingAgent.tsx`:

```tsx
import React from 'react';
import { Chip } from './Chip';
import './FloatingAgent.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  chips: string[];
  hasUnread: boolean;
  children?: React.ReactNode;
};

export function FloatingAgent({ open, onOpenChange, chips, hasUnread, children }: Props) {
  if (!open) {
    return (
      <button
        type="button"
        className="wl-agent-pip"
        aria-label="Open agent"
        onClick={() => onOpenChange(true)}
      >
        <span className={`wl-agent-pip__dot ${hasUnread ? 'wl-agent-pip__dot--active' : ''}`} />
        <span className="wl-agent-pip__label">⌘K · AGENT</span>
      </button>
    );
  }

  return (
    <div className="wl-agent-panel" role="dialog" aria-label="Agent panel">
      <header className="wl-agent-panel__head">
        <span>⌘K · AGENT</span>
        <button
          type="button"
          className="wl-agent-panel__close"
          aria-label="Close agent"
          onClick={() => onOpenChange(false)}
        >
          ×
        </button>
      </header>
      {chips.length > 0 && (
        <div className="wl-agent-panel__ctx">
          <span className="wl-agent-panel__ctx-label">Context</span>
          {chips.map((c) => <Chip key={c}>{c}</Chip>)}
        </div>
      )}
      <div className="wl-agent-panel__body">{children}</div>
    </div>
  );
}
```

Create `frontend/src/components/FloatingAgent.css`:

```css
.wl-agent-pip {
  position: fixed;
  bottom: 16px;
  right: 16px;
  background: var(--ink);
  color: var(--paper);
  border: 0;
  padding: 8px 12px;
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  letter-spacing: 0.06em;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(20, 17, 10, 0.18);
  z-index: 50;
}
.wl-agent-pip__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-2);
}
.wl-agent-pip__dot--active {
  background: var(--pos);
  animation: wl-pulse 2.4s ease-in-out infinite;
}

.wl-agent-panel {
  position: fixed;
  bottom: 16px;
  right: 16px;
  width: min(420px, 90vw);
  max-height: 70vh;
  background: var(--paper);
  border: 1px solid var(--ink);
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.18);
  display: flex;
  flex-direction: column;
  z-index: 50;
  animation: wl-fade-in var(--motion-slide) var(--motion-curve);
}
.wl-agent-panel__head {
  background: var(--ink);
  color: var(--paper);
  padding: var(--gap-2) var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  letter-spacing: 0.06em;
}
.wl-agent-panel__close {
  background: none;
  border: 0;
  color: var(--paper);
  font-family: var(--font-numeric);
  font-size: 18px;
  cursor: pointer;
}
.wl-agent-panel__ctx {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--gap-1);
  padding: var(--gap-2) var(--gap-3);
  background: var(--paper-2);
  border-bottom: 1px solid var(--hairline);
}
.wl-agent-panel__ctx-label {
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  margin-right: var(--gap-2);
}
.wl-agent-panel__body { padding: var(--gap-3); flex: 1; overflow-y: auto; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test FloatingAgent`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FloatingAgent.tsx frontend/src/components/FloatingAgent.css frontend/src/components/FloatingAgent.test.tsx
git commit -m "feat(frontend): add FloatingAgent pip + panel"
```

## Task 27: Build AppShell composing Sidebar + content area

**Files:**
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/AppShell.css`
- Create: `frontend/src/components/AppShell.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/AppShell.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppShell } from './AppShell';

describe('AppShell', () => {
  const items = [
    { route: 'chat' as const, label: 'Agent Desk' },
    { route: 'portfolio' as const, label: 'Positions' },
  ];

  it('renders sidebar and main content', () => {
    render(
      <AppShell active="chat" onNavigate={() => {}} items={items}>
        <div>page content</div>
      </AppShell>
    );
    expect(screen.getByText('Agent Desk')).toBeInTheDocument();
    expect(screen.getByText('page content')).toBeInTheDocument();
  });

  it('calls onNavigate on sidebar click', async () => {
    const onNavigate = vi.fn();
    render(
      <AppShell active="chat" onNavigate={onNavigate} items={items}>
        body
      </AppShell>
    );
    await userEvent.click(screen.getByRole('button', { name: 'Positions' }));
    expect(onNavigate).toHaveBeenCalledWith('portfolio');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test AppShell`
Expected: FAIL with "Cannot find module './AppShell'".

- [ ] **Step 3: Write implementation**

Create `frontend/src/components/AppShell.tsx`:

```tsx
import React from 'react';
import type { Route } from '../types';
import { Sidebar, type SidebarItem } from './Sidebar';
import './AppShell.css';

type Props = {
  active: Route;
  onNavigate: (route: Route) => void;
  items: SidebarItem[];
  children: React.ReactNode;
  toolbar?: React.ReactNode;
};

export function AppShell({ active, onNavigate, items, children, toolbar }: Props) {
  return (
    <div className="wl-shell">
      <Sidebar items={items} active={active} onNavigate={onNavigate} />
      <main className="wl-shell__main">
        {toolbar && <div className="wl-shell__toolbar">{toolbar}</div>}
        <div className="wl-shell__content">{children}</div>
      </main>
    </div>
  );
}
```

Create `frontend/src/components/AppShell.css`:

```css
.wl-shell {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
  background: var(--paper);
  color: var(--ink);
}
.wl-shell__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.wl-shell__toolbar {
  border-bottom: 1px solid var(--hairline);
  padding: var(--gap-2) var(--gap-4);
  display: flex;
  justify-content: flex-end;
  gap: var(--gap-2);
  align-items: center;
}
.wl-shell__content {
  padding: var(--gap-4);
  flex: 1;
  overflow-y: auto;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test AppShell`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/components/AppShell.css frontend/src/components/AppShell.test.tsx
git commit -m "feat(frontend): add AppShell"
```

---

# Phase 4 · Positions Reference Vertical Slice

## Task 28: Stub the Positions route file with a "moved" marker

**Files:**
- Create: `frontend/src/routes/Positions.tsx`

- [ ] **Step 1: Create stub route**

Create `frontend/src/routes/Positions.tsx` containing the new-design Positions screen using the primitives. This is the new vertical slice — for now it renders mock data; wiring to live API happens in the next task.

```tsx
import { useMemo, useState } from 'react';
import { PageHeader } from '../components/PageHeader';
import { Tile } from '../components/Tile';
import { Table, type Column } from '../components/Table';
import { Panel } from '../components/Panel';
import { Button } from '../components/Button';

type PositionRow = {
  trade_id: string;
  underlying: string;
  price: number;
  delta: number;
  vega: number;
};

type Props = {
  rows: PositionRow[];
  nav: string;
  pnl: string;
  pnlVariant: 'pos' | 'neg' | 'default';
  delta: string;
  deltaVariant: 'pos' | 'neg' | 'default';
  vega: string;
  valuationDate: string;
  onRunPricing: () => void;
};

export function Positions({ rows, nav, pnl, pnlVariant, delta, deltaVariant, vega, valuationDate, onRunPricing }: Props) {
  const [selected, setSelected] = useState<string | null>(rows[0]?.trade_id ?? null);
  const selectedRow = useMemo(() => rows.find((r) => r.trade_id === selected) ?? null, [rows, selected]);

  const columns: Column<PositionRow>[] = [
    { key: 'trade_id', header: 'TRADE', width: '1.6fr' },
    { key: 'underlying', header: 'UNDER', width: '1fr' },
    { key: 'price',  header: 'PRICE', width: '0.8fr', numeric: true, render: (r) => r.price.toFixed(3) },
    { key: 'delta',  header: 'Δ',     width: '0.7fr', numeric: true, render: (r) => formatSigned(r.delta) },
    { key: 'vega',   header: 'VEGA',  width: '0.7fr', numeric: true, render: (r) => r.vega.toFixed(2) },
  ];

  return (
    <>
      <PageHeader
        title="POSITIONS · DESK-Q2"
        chips={[`val ${valuationDate}`, `${rows.length} trades`]}
        action={<Button variant="primary" onClick={onRunPricing}>Run Pricing ⌘R</Button>}
      />
      <div className="wl-positions__tiles">
        <Tile label="NAV" value={nav} />
        <Tile label="P&L" value={pnl} variant={pnlVariant} />
        <Tile label="DELTA" value={delta} variant={deltaVariant} />
        <Tile label="VEGA" value={vega} />
      </div>
      <div className="wl-positions__main">
        <Table<PositionRow>
          columns={columns}
          rows={rows}
          rowKey={(r) => r.trade_id}
          selectedKey={selected}
          onRowClick={(r) => setSelected(r.trade_id)}
        />
        <Panel title={selectedRow ? selectedRow.trade_id : 'No selection'} meta={selectedRow ? 'LIVE' : ''}>
          {selectedRow ? (
            <pre className="wl-positions__detail">{`price = ${selectedRow.price.toFixed(3)}
delta = ${formatSigned(selectedRow.delta)}
vega  = ${selectedRow.vega.toFixed(2)}
underlying = ${selectedRow.underlying}`}</pre>
          ) : <span>Select a trade.</span>}
        </Panel>
      </div>
    </>
  );
}

function formatSigned(n: number): string {
  return (n >= 0 ? '+' : '') + n.toFixed(2);
}
```

- [ ] **Step 2: Add Positions CSS**

Create `frontend/src/routes/Positions.css`:

```css
.wl-positions__tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--gap-3);
  margin-bottom: var(--gap-3);
}
.wl-positions__main {
  display: grid;
  grid-template-columns: 1fr min(420px, 38%);
  gap: var(--gap-3);
  align-items: flex-start;
}
.wl-positions__detail {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  line-height: 1.7;
  color: var(--ink);
  white-space: pre;
  margin: 0;
}
```

Add the import to `Positions.tsx`:

```ts
import './Positions.css';
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Positions.tsx frontend/src/routes/Positions.css
git commit -m "feat(frontend): add Positions route with master-detail layout"
```

## Task 29: Wire Positions route to live data

**Files:**
- Modify: `frontend/src/routes/Positions.tsx` (now reads from API helpers)
- Create: `frontend/src/routes/Positions.live.tsx` (container that calls the API and renders `Positions`)

- [ ] **Step 1: Create live container**

Create `frontend/src/routes/Positions.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Portfolio, PositionValuationRun } from '../types';
import { Positions } from './Positions';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';

export function PositionsLive() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [run, setRun] = useState<PositionValuationRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const portfolios = await api<Portfolio[]>('/api/portfolios');
      const desk = portfolios[0];
      if (!desk) {
        setPortfolio(null);
        setLoading(false);
        return;
      }
      setPortfolio(desk);
      const runs = await api<PositionValuationRun[]>(`/api/portfolios/${desk.id}/runs`);
      setRun(runs[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleRunPricing = async () => {
    if (!portfolio) return;
    await api(`/api/portfolios/${portfolio.id}/runs`, { method: 'POST' });
    await load();
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={80} />
        <div style={{ height: 12 }} />
        <Skeleton height={240} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load positions: ${error}`} />;
  }

  if (!portfolio) {
    return <Empty message="No portfolios available." />;
  }

  const rows = (run?.results ?? []).map((r) => ({
    trade_id: String(r.trade_id ?? r.id ?? ''),
    underlying: String(r.underlying ?? ''),
    price: Number(r.price ?? 0),
    delta: Number(r.delta ?? 0),
    vega: Number(r.vega ?? 0),
  }));

  const summary = run?.summary ?? {};
  const nav = String((summary as Record<string, unknown>).nav ?? '—');
  const pnl = String((summary as Record<string, unknown>).pnl ?? '—');
  const delta = String((summary as Record<string, unknown>).delta ?? '—');
  const vega = String((summary as Record<string, unknown>).vega ?? '—');

  return (
    <Positions
      rows={rows}
      nav={nav}
      pnl={pnl}
      pnlVariant={pnl.startsWith('-') ? 'neg' : pnl === '—' ? 'default' : 'pos'}
      delta={delta}
      deltaVariant={delta.startsWith('-') ? 'neg' : delta === '—' ? 'default' : 'pos'}
      vega={vega}
      valuationDate={run?.valuation_date ?? '—'}
      onRunPricing={handleRunPricing}
    />
  );
}
```

- [ ] **Step 2: Run dev server, verify the route doesn't break the app**

Run: `npm run dev`. Open `http://localhost:5173`. The existing app still wins (we haven't wired the new shell yet) but typecheck must pass.

Run: `npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Positions.live.tsx
git commit -m "feat(frontend): wire Positions route to live API"
```

## Task 30: Build PlaceholderRoute for not-yet-migrated routes

**Files:**
- Create: `frontend/src/routes/PlaceholderRoute.tsx`

- [ ] **Step 1: Write component**

Create `frontend/src/routes/PlaceholderRoute.tsx`:

```tsx
import { PageHeader } from '../components/PageHeader';
import { Empty } from '../components/Empty';

type Props = {
  title: string;
  message?: string;
};

export function PlaceholderRoute({ title, message }: Props) {
  return (
    <>
      <PageHeader title={title} chips={[]} />
      <Empty
        message={message ?? `${title} migration is in a follow-up plan.`}
        symbol="◌"
      />
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/PlaceholderRoute.tsx
git commit -m "feat(frontend): add PlaceholderRoute for routes pending migration"
```

## Task 31: Wire main.tsx to use AppShell + new routing

**Files:**
- Modify: `frontend/src/main.tsx` (full rewrite — keep what's referenced; replace App with shell-based router)

- [ ] **Step 1: Rewrite main.tsx**

Replace the entire contents of `frontend/src/main.tsx` with:

```tsx
import { StrictMode, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './tokens/index.css';
import { AppShell } from './components/AppShell';
import { Button } from './components/Button';
import { CommandPalette, type CommandItem } from './components/CommandPalette';
import { FloatingAgent } from './components/FloatingAgent';
import { useTheme } from './hooks/useTheme';
import { useDensity } from './hooks/useDensity';
import { useCommandPalette } from './hooks/useCommandPalette';
import type { Route } from './types';
import { PositionsLive } from './routes/Positions.live';
import { PlaceholderRoute } from './routes/PlaceholderRoute';

const navItems = [
  { route: 'chat' as const,      label: 'Agent Desk' },
  { route: 'rfq' as const,       label: 'RFQ Approval' },
  { route: 'portfolio' as const, label: 'Positions' },
  { route: 'risk' as const,      label: 'Risk' },
  { route: 'reports' as const,   label: 'Reports' },
  { route: 'client' as const,    label: 'Client RFQ' },
];

function initialRoute(): Route {
  return location.pathname.includes('/client/rfq') ? 'client' : 'portfolio';
}

function App() {
  const [route, setRoute] = useState<Route>(() => initialRoute());
  const { theme, setTheme } = useTheme();
  const { density, toggle: toggleDensity } = useDensity();
  const palette = useCommandPalette();
  const [agentOpen, setAgentOpen] = useState(false);

  useEffect(() => {
    const path = route === 'client' ? '/client/rfq' : '/';
    if (location.pathname !== path) history.replaceState(null, '', path);
  }, [route]);

  const onSelectCommand = useCallback((item: CommandItem) => {
    palette.close();
    if (item.id.startsWith('jump-')) {
      const target = item.id.replace('jump-', '') as Route;
      setRoute(target);
    }
  }, [palette]);

  const cycleTheme = () => {
    setTheme(theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system');
  };

  const commandItems: CommandItem[] = [
    { id: 'jump-portfolio', group: 'Jump To', label: 'Positions',     shortcut: '↵' },
    { id: 'jump-rfq',       group: 'Jump To', label: 'RFQ Approval',  shortcut: '↵' },
    { id: 'jump-risk',      group: 'Jump To', label: 'Risk',          shortcut: '↵' },
    { id: 'jump-reports',   group: 'Jump To', label: 'Reports',       shortcut: '↵' },
    { id: 'jump-chat',      group: 'Jump To', label: 'Agent Desk',    shortcut: '↵' },
    { id: 'jump-client',    group: 'Jump To', label: 'Client RFQ',    shortcut: '↵' },
  ];

  const showAgent = route !== 'client';

  return (
    <>
      <AppShell
        active={route}
        onNavigate={setRoute}
        items={navItems}
        toolbar={
          <>
            <Button variant="ghost" onClick={cycleTheme}>theme: {theme}</Button>
            <Button variant="ghost" onClick={toggleDensity}>density: {density}</Button>
            <Button variant="ghost" onClick={palette.open}>⌘K</Button>
          </>
        }
      >
        {route === 'portfolio' && <PositionsLive />}
        {route === 'chat'      && <PlaceholderRoute title="Agent Desk" />}
        {route === 'rfq'       && <PlaceholderRoute title="RFQ Approval" />}
        {route === 'risk'      && <PlaceholderRoute title="Risk" />}
        {route === 'reports'   && <PlaceholderRoute title="Reports" />}
        {route === 'client'    && <PlaceholderRoute title="Client RFQ" />}
      </AppShell>
      <CommandPalette
        open={palette.isOpen}
        onOpenChange={(o) => o ? palette.open() : palette.close()}
        items={commandItems}
        onSelect={onSelectCommand}
      />
      {showAgent && (
        <FloatingAgent open={agentOpen} onOpenChange={setAgentOpen} chips={[]} hasUnread={false}>
          <div style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
            Agent panel scaffolding — wiring to existing agent backend lands in a follow-up plan.
          </div>
        </FloatingAgent>
      )}
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>
);
```

- [ ] **Step 2: Verify dev server**

Run from `frontend/`: `npm run dev`
Open `http://localhost:5173`.

Expected:
- New AppShell renders with the Warm Ledger sidebar.
- "Positions" tab is the default; it tries to load live data via the API.
- Other tabs render PlaceholderRoute.
- Floating agent pip visible bottom-right (not on Client RFQ tab).
- Toolbar has theme/density toggles and a `⌘K` button.
- Pressing `⌘K` (or `Ctrl+K`) opens the command palette.

- [ ] **Step 3: Verify typecheck**

Run: `npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 4: Run all tests**

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat(frontend): wire AppShell + cross-route affordances into main.tsx"
```

## Task 32: Remove old styles.css

**Files:**
- Delete: `frontend/src/styles.css`
- Modify: `frontend/src/main.tsx` (remove `import './styles.css'` if still present)

- [ ] **Step 1: Verify no remaining references**

Run from `/Users/fuxinyao/open-otc-trading`:

```bash
grep -rn "styles.css" frontend/src
```

Expected: only the import in `main.tsx` (removed in step 2 below).

- [ ] **Step 2: Remove import line**

In `frontend/src/main.tsx`, the import was already removed in Task 31 (we replaced the whole file). Confirm it's gone:

```bash
grep -n "styles.css" frontend/src/main.tsx || echo "no reference"
```

Expected: `no reference`.

- [ ] **Step 3: Delete the file**

```bash
rm frontend/src/styles.css
```

- [ ] **Step 4: Verify dev server still renders correctly**

Run: `npm run dev`. Open `http://localhost:5173`. The app should still render; the old design is now fully gone.

- [ ] **Step 5: Run all tests**

Run: `npm test`
Expected: all pass.

- [ ] **Step 6: Run typecheck**

Run: `npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add -A frontend/src
git commit -m "chore(frontend): remove legacy styles.css"
```

## Task 33: Smoke-test all routes end-to-end and document follow-ups

**Files:**
- Modify: `README.md` (add a brief redesign section)

- [ ] **Step 1: Manually smoke-test each route in the dev server**

With `npm run dev` running, visit each route via the sidebar. Confirm:

- [ ] Positions: tile row renders, table renders, detail panel updates on row click, "Run Pricing" button calls the backend.
- [ ] All other routes render the PlaceholderRoute with the correct title.
- [ ] Theme toggle cycles `system → light → dark`. Confirm `<html data-theme>` updates and `--paper`/`--ink` flip.
- [ ] Density toggle flips `<html data-density>` and tile/table padding compresses.
- [ ] `⌘K` opens the command palette. Selecting a "Jump To" item navigates to that route.
- [ ] Floating agent pip is visible bottom-right on every route except Client RFQ.
- [ ] Clicking the pip expands the agent panel; close button collapses it back.

- [ ] **Step 2: Run all tests once more**

Run: `npm test`
Expected: all suites pass.

Run: `npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 3: Add brief redesign section to README**

In `README.md`, append:

```markdown

## UI/UX redesign (in progress)

The frontend is being migrated to the **Warm Ledger** design system per
`docs/superpowers/specs/2026-05-07-ui-ux-redesign-design.md`.

The foundation plan
(`docs/superpowers/plans/2026-05-07-warm-ledger-foundation.md`) lands:

- design tokens (colors, typography, density, motion) under `src/tokens/`
- primitive components under `src/components/`
- AppShell with sidebar, ⌘K command palette, floating agent pip
- the **Positions** route as the reference master-detail vertical slice

Follow-up plans migrate the remaining routes:

- Client RFQ + RFQ Approval
- Risk
- Reports + Agent Desk
- accessibility audit + prefers-reduced-motion verification

Run the tests with `cd frontend && npm test` and the dev server with
`cd frontend && npm run dev`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: note Warm Ledger foundation lands in main.tsx + Positions"
```

---

# Self-Review Notes

(Performed after writing the plan; fixes applied inline, not re-reviewed.)

- **Spec coverage check:** Tokens (Tasks 3–6) ✓ — Theme/density toggles (Tasks 7–9) ✓ — Test setup (Task 2) ✓ — Button/Input/Tabs/Modal/Toast/Panel/Tile/Table/Badge/Chip/PageContextChips/AssetCard/ActionProposal/Skeleton/Empty (Tasks 12–22) ✓ — Sidebar/PageHeader/AppShell (Tasks 23–24, 27) ✓ — CommandPalette + FloatingAgent (Tasks 25–26) ✓ — Positions route (Tasks 28–29) ✓ — Out-of-scope routes (Task 30 placeholder) ✓ — File reorganization (Tasks 10–11, plus per-component) ✓.
- **Toast omitted:** the spec lists `Toast` as a primitive, but no Phase-4 route in this plan calls it. Deferred to the route migration plan that needs it (likely Risk or Reports).
- **Tooltip / Popover / DropdownMenu Radix primitives installed but unused in this plan:** they're added as deps in Task 1 because per-route plans will need them. Acceptable to install ahead.
- **No placeholder strings ("TBD", "implement later", etc.) detected** in steps; every code-bearing step ships full code.
- **Type/method consistency:** `Route` is imported from `./types` everywhere; `useTheme().setTheme`, `useDensity().setDensity`/`.toggle`, `useCommandPalette().{open,close,toggle,isOpen}` — consistent across hook tests and component usage.

# Follow-up plans (post this plan)

- **Plan 2:** Migrate Client RFQ + RFQ Approval routes.
- **Plan 3:** Migrate Risk route + scenario grid.
- **Plan 4:** Migrate Reports + Agent Desk routes (chat layout, asset pane, action proposals inline).
- **Plan 5:** Accessibility audit + reduced-motion verification + dark mode QA + Berkeley Mono procurement.
