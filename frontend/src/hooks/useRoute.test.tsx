import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRoute } from './useRoute';

function setUrl(url: string) {
  window.history.replaceState(null, '', url);
}

describe('useRoute', () => {
  beforeEach(() => {
    setUrl('/positions');
  });

  it('derives the route from the initial pathname', () => {
    setUrl('/risk');
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('risk');
  });

  it('canonicalizes / to /positions on mount (replace, no extra history entry)', () => {
    setUrl('/');
    const before = window.history.length;
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('positions');
    expect(window.location.pathname).toBe('/positions');
    expect(window.history.length).toBe(before); // replaceState, not push
  });

  it('canonicalizes an unknown path to /positions on mount', () => {
    setUrl('/nope');
    renderHook(() => useRoute());
    expect(window.location.pathname).toBe('/positions');
  });

  it('preserves the query string during mount canonicalization', () => {
    // pathname is already canonical for /risk; query must survive untouched.
    setUrl('/risk?portfolio=7');
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('risk');
    expect(window.location.search).toBe('?portfolio=7');
  });

  it('navigate() pushes the path and updates the route', () => {
    const { result } = renderHook(() => useRoute());
    act(() => result.current.navigate('chat'));
    expect(window.location.pathname).toBe('/agent-desk');
    expect(result.current.route).toBe('chat');
  });

  it('updates the route on popstate', () => {
    const { result } = renderHook(() => useRoute());
    act(() => {
      window.history.pushState(null, '', '/hedging');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.route).toBe('hedging');
  });

  it('canonicalizes a stale path on popstate', () => {
    const { result } = renderHook(() => useRoute());
    act(() => {
      window.history.pushState(null, '', '/nope');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.route).toBe('positions');
    expect(window.location.pathname).toBe('/positions');
  });
});
