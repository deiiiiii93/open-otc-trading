import { describe, expect, it } from 'vitest';
import { composeTermFormSubmission, validateTermFormValue } from './termFormModel';
import type { TermFormField } from '../types';

const fields: TermFormField[] = [
  { key: 'initial_price', label: 'Initial fixing S0', type: 'number' },
  { key: 'ko_barrier_pct', label: 'KO barrier', type: 'percent' },
  { key: 'observation_frequency', label: 'Frequency', type: 'enum',
    choices: [{ label: 'Monthly', value: 'MONTHLY' }] },
  { key: 'trade_start_date', label: 'Start', type: 'date' },
];

describe('validateTermFormValue', () => {
  it('flags required-empty', () => {
    expect(validateTermFormValue(fields[0], '')).toBe('Required');
  });
  it('flags non-numeric number/percent', () => {
    expect(validateTermFormValue(fields[1], 'abc')).toBe('Must be a number');
  });
  it('accepts percent with trailing %', () => {
    expect(validateTermFormValue(fields[1], '103%')).toBeNull();
  });
  it('flags bad date', () => {
    expect(validateTermFormValue(fields[3], '2026/05/31')).toBe('Use YYYY-MM-DD');
  });
  it('accepts ISO date', () => {
    expect(validateTermFormValue(fields[3], '2026-05-31')).toBeNull();
  });
  it('flags enum value not in choices', () => {
    expect(validateTermFormValue(fields[2], 'WEEKLY')).toBe('Pick a listed option');
  });
});

describe('composeTermFormSubmission', () => {
  it('emits a sentence + json block keyed by field key, coercing numbers', () => {
    const msg = composeTermFormSubmission(fields, {
      initial_price: '8359.56',
      ko_barrier_pct: '103%',
      observation_frequency: 'MONTHLY',
      trade_start_date: '2026-05-31',
    });
    expect(msg).toContain('booking terms');
    expect(msg).toContain('```json');
    const json = JSON.parse(msg.split('```json')[1].split('```')[0].trim());
    expect(json).toEqual({
      initial_price: 8359.56,
      ko_barrier_pct: 103,
      observation_frequency: 'MONTHLY',
      trade_start_date: '2026-05-31',
    });
  });
  it('omits blank optional values', () => {
    const msg = composeTermFormSubmission(
      [{ key: 'lockup_months', label: 'Lockup', type: 'number', required: false }],
      { lockup_months: '' },
    );
    const json = JSON.parse(msg.split('```json')[1].split('```')[0].trim());
    expect(json).toEqual({});
  });
});
