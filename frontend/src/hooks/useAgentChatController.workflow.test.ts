import { describe, it, expect } from 'vitest';
import { parseWorkflowSse } from './useAgentChatController';

describe('workflow SSE parsing', () => {
  it('extracts step events in order', () => {
    const frames = [
      'event: workflow.start\ndata: {"slug":"x","mode":"yolo"}\n\n',
      'event: workflow.step.start\ndata: {"index":1,"prompt":"a"}\n\n',
      'event: workflow.step.end\ndata: {"index":1}\n\n',
      'event: workflow.complete\ndata: {"steps":1}\n\n',
    ].join('');
    const events = parseWorkflowSse(frames);
    expect(events.map((e) => e.type)).toEqual([
      'workflow.start',
      'workflow.step.start',
      'workflow.step.end',
      'workflow.complete',
    ]);
    expect(events[1].data.index).toBe(1);
  });
});
