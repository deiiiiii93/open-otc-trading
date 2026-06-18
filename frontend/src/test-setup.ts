import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import axe from 'axe-core';

if (typeof globalThis.localStorage?.clear !== 'function') {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      get length() {
        return store.size;
      },
      key(index: number) {
        return Array.from(store.keys())[index] ?? null;
      },
      getItem(key: string) {
        return store.get(key) ?? null;
      },
      setItem(key: string, value: string) {
        store.set(key, String(value));
      },
      removeItem(key: string) {
        store.delete(key);
      },
      clear() {
        store.clear();
      },
    },
  });
}

// jsdom has no ResizeObserver; provide a no-op so components that observe
// element resize (e.g. Tile's auto-fit value) render without throwing.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.removeAttribute('data-density');
  globalThis.localStorage.clear();
});

export async function expectNoA11yViolations(container: Element): Promise<void> {
  const results = await axe.run(container, {
    rules: {
      // Disable region landmark rule — small isolated test mounts often have no landmarks by design.
      region: { enabled: false },
    },
  });
  if (results.violations.length > 0) {
    const summary = results.violations
      .map((v) => `${v.id}: ${v.help} (${v.nodes.length} node${v.nodes.length === 1 ? '' : 's'})`)
      .join('\n');
    throw new Error(`a11y violations:\n${summary}`);
  }
}
