import { describe, it, expect } from 'vitest';
import { parseWorkflowSlug } from './Workflows.live';

describe('parseWorkflowSlug', () => {
  it('reads the slug from the meta name', () => {
    const script = 'meta = {\n  "name": "risk-day",\n  "title": "T",\n}\nawait step("go")\n';
    expect(parseWorkflowSlug(script)).toBe('risk-day');
  });

  it('returns empty string when meta has no name', () => {
    expect(parseWorkflowSlug('meta = {\n  "title": "T",\n}\n')).toBe('');
  });

  it('returns empty string when there is no meta block', () => {
    expect(parseWorkflowSlug('await step("go")\n')).toBe('');
  });

  it('is not shadowed by a "name": inside a later step prompt', () => {
    const script =
      'meta = {\n  "name": "real-slug",\n}\n' +
      'await step("set the \\"name\\": \\"decoy\\" field")\n';
    expect(parseWorkflowSlug(script)).toBe('real-slug');
  });
});
