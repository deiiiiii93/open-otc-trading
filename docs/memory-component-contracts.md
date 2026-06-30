# Memory Console — shared frontend component contracts (preflight)

Exact prop names/variants the Memory Console compiles against (source of truth = the component files).

- **PageScaffold** (`components/templates/PageScaffold`): `{ title: string; chips?: string[]; actions?: ReactNode; feedback?: ReactNode; children }`. **chips are plain strings** (no per-chip variant) — render the disabled banner + config caption in `children`, not as chips.
- **Table** (`components/Table`): `Column<T> = { key: string; header: ReactNode; render?: (row)=>ReactNode; numeric?: boolean; width?: string }`; props `{ columns; rows; rowKey: (row)=>string|number; selectedKey?; onRowClick?; className? }`. **Use `rowKey`, NOT `getRowKey`.**
- **Tabs** (`components/Tabs`): Radix wrappers — `<Tabs value onValueChange={(v:string)=>...}><TabsList aria-label><TabsTrigger value>…`. TabsTrigger renders `role="tab"`.
- **Badge** (`components/Badge`): `{ variant?: 'pos'|'neg'|'warn'|'info'|'ink'; solid?: boolean; children }`.
- **Button** (`components/Button`): `{ variant?: 'primary'|'default'|'danger'|'ghost'; iconOnly?: boolean } & ButtonHTMLAttributes` (so `onClick`, `disabled`, `aria-label` pass through).
- **Modal** (`components/Modal`): `{ open: boolean; onOpenChange: (open)=>void; title: string; description?; children; … }` (Radix Dialog; always mounted, toggled by `open`).
- **Empty** (`components/Empty`): `{ message: string; variant?: 'empty'|'loading'|'error'; symbol?; hint?; action? }`.
- **PageToolbar** (`components/PageToolbar`): `{ children }`; plus `PageToolbarSpacer`, `PageToolbarSearch({ value, onChange })`.
- **Routing**: `Route` union in `types.ts`; `ROUTE_PATHS` in `lib/routing.ts`; nav/palette/renderer/import in `main.tsx`.
