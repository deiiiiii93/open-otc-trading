import { test, expect } from 'vitest';
import { composeImportSummary } from './pricingImportSummary';

test('joins all four segments when every count is non-zero', () => {
  expect(
    composeImportSummary({
      rows_applied: 12,
      rows_dormant: 3,
      quotes_emitted: 9,
      dormant_trade_ids: ['T-1', 'T-2', 'T-3'],
      spot_conflicts: [{ symbol: '000905.SH', count: 2, resolution: 'last row wins' }],
    }),
  ).toBe('12 applied · 3 dormant (T-1, T-2, T-3) · 9 quotes emitted · 1 spot conflict (last row wins)');
});

test('omits zero-valued segments', () => {
  expect(
    composeImportSummary({
      rows_applied: 5,
      rows_dormant: 0,
      quotes_emitted: 0,
      dormant_trade_ids: [],
      spot_conflicts: [],
    }),
  ).toBe('5 applied');
});

test('shows dormant ids inline only when five or fewer', () => {
  expect(
    composeImportSummary({
      rows_applied: 0,
      rows_dormant: 6,
      dormant_trade_ids: ['A', 'B', 'C', 'D', 'E', 'F'],
    }),
  ).toBe('6 dormant');

  expect(
    composeImportSummary({
      rows_applied: 0,
      rows_dormant: 2,
      dormant_trade_ids: ['A', 'B'],
    }),
  ).toBe('2 dormant (A, B)');
});

test('pluralizes spot conflicts', () => {
  expect(
    composeImportSummary({
      spot_conflicts: [
        { symbol: 'X', count: 2, resolution: 'last row wins' },
        { symbol: 'Y', count: 3, resolution: 'last row wins' },
      ],
    }),
  ).toBe('2 spot conflicts (last row wins)');
});

test('returns empty string for null summary', () => {
  expect(composeImportSummary(null)).toBe('');
  expect(composeImportSummary(undefined)).toBe('');
  expect(composeImportSummary({})).toBe('');
});
