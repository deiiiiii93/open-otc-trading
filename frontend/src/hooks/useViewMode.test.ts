import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useViewMode, VIEW_MODE_STORAGE_KEY } from './useViewMode';

describe('useViewMode', () => {
  it('defaults to compact when localStorage is empty', () => {
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('compact');
  });

  it('hydrates from localStorage on mount', () => {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, 'detailed');
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('detailed');
  });

  it('writes through to localStorage on setMode', () => {
    const { result } = renderHook(() => useViewMode());
    act(() => result.current[1]('detailed'));
    expect(result.current[0]).toBe('detailed');
    expect(localStorage.getItem(VIEW_MODE_STORAGE_KEY)).toBe('detailed');
  });

  it('ignores invalid values in localStorage and defaults to compact', () => {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, 'gibberish');
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('compact');
  });
});
