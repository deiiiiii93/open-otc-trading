import { describe, expect, it } from 'vitest';
import { shortProfileDate } from './pricingBuildReadiness';

describe('shortProfileDate', () => {
  it('preserves the literal YYYY-MM-DD prefix when shortening profile dates', () => {
    expect(shortProfileDate('2026-05-17T23:59:59+08:00')).toBe('2026-05-17');
    expect(shortProfileDate('2026-05')).toBe('2026-05');
  });
});
