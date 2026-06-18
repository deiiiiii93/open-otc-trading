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
