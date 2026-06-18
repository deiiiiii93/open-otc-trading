import { useLayoutEffect, useRef } from 'react';

// Fits a group of single-line text elements to their columns *as a set*: it
// finds the one font-size at which the longest value still fits, then applies
// that size uniformly to every element in the group so a row of KPI cards keeps
// a consistent visual weight instead of each card shrinking on its own.
//
// Returns a ref to attach to the container. Members are the descendants of that
// container matching `selector`; the shared size is published as the
// `--autofit-size` CSS variable, which members read via their stylesheet.
//
// `depKey` should encode the rendered values (e.g. a joined string) so the fit
// re-runs on content change; width-driven refits are handled by the
// ResizeObserver. It is a single value so the effect's dependency list keeps a
// constant length across renders.
export function useAutoFitGroup<T extends HTMLElement>(
  depKey: unknown,
  selector = '[data-autofit]',
  minSize = 11,
) {
  const ref = useRef<T | null>(null);
  const lastWidth = useRef(-1);

  useLayoutEffect(() => {
    const container = ref.current;
    if (!container) return undefined;

    const fit = () => {
      const members = container.querySelectorAll<HTMLElement>(selector);
      if (members.length === 0) return;

      // Clear the shared override so every member is measured at its stylesheet
      // base; this also lets the group grow back when columns widen.
      container.style.removeProperty('--autofit-size');

      const base = parseFloat(getComputedStyle(members[0]).fontSize);
      if (!Number.isFinite(base) || base <= 0) return;

      let minScale = 1;
      members.forEach((el) => {
        const available = el.clientWidth;
        const content = el.scrollWidth;
        if (available > 0 && content > available) {
          minScale = Math.min(minScale, available / content);
        }
      });

      if (minScale < 1) {
        const next = Math.max(minSize, base * minScale);
        container.style.setProperty('--autofit-size', `${next}px`);
      }
    };

    fit();

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? -1;
      // Applying the shared size changes member heights and re-fires the
      // observer; only refit when the container width actually changed.
      if (width !== lastWidth.current) {
        lastWidth.current = width;
        fit();
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [selector, minSize, depKey]);

  return ref;
}
