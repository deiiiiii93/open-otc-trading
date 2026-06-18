import { describe, expect, it } from 'vitest';
import { colorForProvider, PROVIDER_COLOR, PROVIDER_COLOR_DEFAULT } from './providerColors';

describe('colorForProvider', () => {
  it('returns the mapped color for known providers', () => {
    expect(colorForProvider('anthropic')).toBe(PROVIDER_COLOR.anthropic);
    expect(colorForProvider('openai')).toBe(PROVIDER_COLOR.openai);
    expect(colorForProvider('deepseek')).toBe(PROVIDER_COLOR.deepseek);
  });

  it('falls back to default for unknown providers', () => {
    expect(colorForProvider('mystery-vendor')).toBe(PROVIDER_COLOR_DEFAULT);
    expect(colorForProvider(undefined)).toBe(PROVIDER_COLOR_DEFAULT);
  });
});
