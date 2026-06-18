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
