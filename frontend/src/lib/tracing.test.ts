import { describe, expect, it } from 'vitest';
import { openTraceTarget } from './tracing';
import type { TracingConfig } from '../types';

const cfg = (mode: TracingConfig['mode']): TracingConfig => ({
  mode,
  langsmith_url: 'https://smith.langchain.com',
});

describe('openTraceTarget', () => {
  it('routes internally for local mode', () => {
    expect(openTraceTarget(cfg('local'), 7)).toEqual({ kind: 'internal', threadId: 7 });
  });
  it('routes internally for both mode', () => {
    expect(openTraceTarget(cfg('both'), 7)).toEqual({ kind: 'internal', threadId: 7 });
  });
  it('opens LangSmith externally for langsmith mode', () => {
    expect(openTraceTarget(cfg('langsmith'), 7)).toEqual({
      kind: 'external',
      url: 'https://smith.langchain.com',
    });
  });
  it('hides the link when tracing is off or config missing', () => {
    expect(openTraceTarget(cfg('off'), 7)).toEqual({ kind: 'none' });
    expect(openTraceTarget(null, 7)).toEqual({ kind: 'none' });
  });
});
