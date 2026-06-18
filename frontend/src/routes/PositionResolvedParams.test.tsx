import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ResolvedPricingParamsBlock, resolvedParamProvenance } from './Positions';
import type { ResolvedPricingParams } from '../types';

describe('resolvedParamProvenance', () => {
  it('labels a market_quote with age and quote source', () => {
    expect(resolvedParamProvenance({
      value: 6412.55,
      source: 'market_quote',
      age_days: 2.4,
      quote_source: 'xlsx_import',
    })).toBe('quote · 2d · xlsx_import');
  });

  it('labels a pricing_parameter_profile with profile id and trade id', () => {
    expect(resolvedParamProvenance({
      value: 0.023,
      source: 'pricing_parameter_profile',
      profile_id: 7,
      source_trade_id: 'T-0142',
    })).toBe('profile #7 · T-0142');
  });

  it('labels an assumption_set with its set id', () => {
    expect(resolvedParamProvenance({
      value: 0.018,
      source: 'assumption_set',
      assumption_set_id: 4,
      assumption_row_id: 9,
    })).toBe('assumptions #4');
  });

  it('labels a missing field', () => {
    expect(resolvedParamProvenance({ value: null, source: 'missing' })).toBe('missing');
    expect(resolvedParamProvenance(undefined)).toBe('missing');
  });
});

describe('ResolvedPricingParamsBlock', () => {
  const allFour: ResolvedPricingParams = {
    spot: { value: 6412.55, source: 'market_quote', age_days: 2, quote_source: 'xlsx_import' },
    rate: { value: 0.023, source: 'pricing_parameter_profile', profile_id: 7, source_trade_id: 'T-0142' },
    dividend_yield: { value: 0.018, source: 'assumption_set', assumption_set_id: 4, assumption_row_id: 9 },
    volatility: { value: null, source: 'missing' },
  };

  it('renders all four fields with their provenance labels', () => {
    render(<ResolvedPricingParamsBlock resolvedParams={allFour} />);
    expect(screen.getByText('Spot')).toBeInTheDocument();
    expect(screen.getByText(/quote · 2d · xlsx_import/)).toBeInTheDocument();
    expect(screen.getByText(/profile #7 · T-0142/)).toBeInTheDocument();
    expect(screen.getByText(/assumptions #4/)).toBeInTheDocument();
    expect(screen.getByText(/missing/)).toBeInTheDocument();
    // formatted values present
    expect(screen.getByText(/6,412\.55/)).toBeInTheDocument();
  });

  it('shows a loading placeholder while params are null and loading', () => {
    render(<ResolvedPricingParamsBlock resolvedParams={null} loading />);
    expect(screen.getByText(/Resolving pricing params/)).toBeInTheDocument();
  });

  it('shows an empty placeholder when there are no resolved params', () => {
    render(<ResolvedPricingParamsBlock resolvedParams={null} />);
    expect(screen.getByText(/No resolved pricing params/)).toBeInTheDocument();
  });
});
