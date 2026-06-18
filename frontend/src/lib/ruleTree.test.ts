import { describe, expect, it } from 'vitest';
import {
  ALLOWED_FIELDS,
  ALLOWED_OPS,
  parseDsl,
  serializeDsl,
  validateRule,
} from './ruleTree';

describe('ruleTree validate', () => {
  it('accepts simple eq', () => {
    expect(validateRule({ op: 'eq', field: 'product_type', value: 'Snowball' })).toEqual([]);
  });
  it('rejects unknown op', () => {
    const errs = validateRule({ op: 'matches', field: 'underlying', value: 'AAPL' } as any);
    expect(errs.some(e => e.includes('matches'))).toBe(true);
  });
  it('rejects unknown field', () => {
    const errs = validateRule({ op: 'eq', field: 'color', value: 'blue' } as any);
    expect(errs.some(e => e.includes('color'))).toBe(true);
  });
});

describe('ruleTree DSL', () => {
  it('parses simple eq', () => {
    expect(parseDsl('product_type = Snowball')).toEqual({
      op: 'eq', field: 'product_type', value: 'Snowball',
    });
  });
  it('roundtrips AND tree', () => {
    const tree = {
      op: 'and' as const,
      children: [
        { op: 'eq' as const, field: 'product_type', value: 'Snowball' },
        { op: 'in' as const, field: 'underlying', value: ['AAPL', 'TSLA'] },
      ],
    };
    expect(parseDsl(serializeDsl(tree))).toEqual(tree);
  });
});
