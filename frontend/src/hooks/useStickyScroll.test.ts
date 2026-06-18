import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRef } from 'react';
import { useStickyScroll, STICKY_THRESHOLD_PX } from './useStickyScroll';

function setScrollGeometry(
  node: HTMLElement,
  opts: { scrollTop: number; scrollHeight: number; clientHeight: number },
) {
  Object.defineProperty(node, 'scrollTop', {
    configurable: true,
    get: () => opts.scrollTop,
    set: () => {},
  });
  Object.defineProperty(node, 'scrollHeight', {
    configurable: true,
    get: () => opts.scrollHeight,
  });
  Object.defineProperty(node, 'clientHeight', {
    configurable: true,
    get: () => opts.clientHeight,
  });
}

function setupHook() {
  const node = document.createElement('div');
  document.body.appendChild(node);
  const { result } = renderHook(() => {
    const ref = useRef<HTMLDivElement | null>(null);
    if (!ref.current) ref.current = node;
    return useStickyScroll(ref);
  });
  return { node, result };
}

describe('useStickyScroll', () => {
  it('reports isPinned=true when scrolled to bottom', () => {
    const { node, result } = setupHook();
    setScrollGeometry(node, { scrollTop: 880, scrollHeight: 1000, clientHeight: 120 });
    act(() => {
      node.dispatchEvent(new Event('scroll'));
    });
    expect(result.current.isPinned).toBe(true);
  });

  it('reports isPinned=false when scrolled away from bottom by more than threshold', () => {
    const { node, result } = setupHook();
    setScrollGeometry(node, {
      scrollTop: 200,
      scrollHeight: 1000,
      clientHeight: 120,
    });
    act(() => {
      node.dispatchEvent(new Event('scroll'));
    });
    expect(result.current.isPinned).toBe(false);
  });

  it('respects STICKY_THRESHOLD_PX as a soft boundary', () => {
    const { node, result } = setupHook();
    setScrollGeometry(node, { scrollTop: 780, scrollHeight: 1000, clientHeight: 120 });
    act(() => {
      node.dispatchEvent(new Event('scroll'));
    });
    expect(result.current.isPinned).toBe(true);
    expect(STICKY_THRESHOLD_PX).toBe(120);
  });

  it('scrollToBottom sets scrollTop to scrollHeight', () => {
    const { node, result } = setupHook();
    let written = 0;
    Object.defineProperty(node, 'scrollTop', {
      configurable: true,
      get: () => 0,
      set: (v) => {
        written = v;
      },
    });
    Object.defineProperty(node, 'scrollHeight', { configurable: true, get: () => 1234 });
    Object.defineProperty(node, 'clientHeight', { configurable: true, get: () => 100 });
    act(() => {
      result.current.scrollToBottom();
    });
    expect(written).toBe(1234);
  });
});
