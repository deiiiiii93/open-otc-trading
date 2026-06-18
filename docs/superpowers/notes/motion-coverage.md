# Motion coverage (Plan 5a)

Audited: 2026-05-08

All animations and transitions in `frontend/src` route through one of:
- `var(--motion-fade)` / `var(--motion-slide)` — set to `0ms` under `prefers-reduced-motion: reduce`
- The catch-all `*, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }`

Inventory at audit time (excluding `tokens/motion.css` itself):

```
Tabs.css:16            transition: color var(--motion-fade) linear
CommandPalette.css:5   animation:  wl-fade-in var(--motion-fade) linear
FloatingAgent.css:27   animation:  wl-pulse 2.4s ease-in-out infinite
FloatingAgent.css:42   animation:  wl-fade-in var(--motion-slide) var(--motion-curve)
Skeleton.css:4         animation:  wl-shimmer var(--motion-shimmer) linear infinite
Skeleton.css:8         .wl-skeleton { animation: none; background: var(--paper-2); }   ← inside reduced-motion media
Button.css:15          transition: background var(--motion-fade) linear
Modal.css:5            animation:  wl-fade-in var(--motion-fade) linear
Modal.css:19           animation:  wl-fade-in var(--motion-slide) var(--motion-curve)
Sidebar.css:35         transition: color var(--motion-fade) linear
```

The catch-all in `tokens/motion.css` neutralises duration on every `animation:` and
`transition:` declaration regardless of authoring style, so all of the entries above
are covered. The token reset (`--motion-fade: 0ms`, `--motion-slide: 0ms`) is a
belt-and-braces redundancy — components that interpolate the token still get 0ms
even if the `*` selector were ever scoped or overridden.

Skeleton has its own per-component override (`Skeleton.css:8`) that replaces the
shimmer with a static `var(--paper-2)` background — visually clearer than freezing
the gradient at any random offset.

`wl-pulse` (FloatingAgent dot, 2.4s infinite) is the only animation NOT routed
through `var(--motion-...)` — but it's still caught by the `*` rule.

## When adding new animations

1. Use `var(--motion-fade)` / `var(--motion-slide)` for durations whenever possible.
2. The catch-all will neutralise any new `animation:` or `transition:` declaration
   automatically — no per-component opt-in is required.
3. If freezing the animation at a static state would be misleading (e.g. a progress
   bar freezing partway), add a component-specific reduced-motion override that
   swaps the animated state for an explicit static one (see `Skeleton.css:8`).
