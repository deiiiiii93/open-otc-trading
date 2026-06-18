import type { TracingConfig } from '../types';

export type TraceTarget =
  | { kind: 'internal'; threadId: number }
  | { kind: 'external'; url: string }
  | { kind: 'none' };

/** Where the per-thread trace link should go for the active tracing mode. */
export function openTraceTarget(
  config: TracingConfig | null,
  threadId: number,
): TraceTarget {
  if (!config || config.mode === 'off') return { kind: 'none' };
  if (config.mode === 'langsmith') {
    return config.langsmith_url
      ? { kind: 'external', url: config.langsmith_url }
      : { kind: 'none' };
  }
  return { kind: 'internal', threadId };
}
