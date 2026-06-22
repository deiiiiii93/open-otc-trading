import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProductTermsForm } from './ProductTermsForm';

describe('ProductTermsForm — Asian observation frequency', () => {
  it('renders an Observation Frequency picker for AsianOption with daily/weekly options', () => {
    render(
      <ProductTermsForm
        productType="AsianOption"
        productKwargs={{ strike: 100, option_type: 'CALL', averaging_frequency: 'MONTHLY' }}
        onChange={vi.fn()}
      />,
    );
    // The picker is labelled "Observation Frequency"
    expect(screen.getByText('Observation Frequency')).toBeInTheDocument();
    // Full Asian-appropriate set incl. DAILY and WEEKLY (not just snowball's set)
    expect(screen.getByRole('option', { name: 'DAILY' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'WEEKLY' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'SEMI_ANNUAL' })).toBeInTheDocument();
  });
});
