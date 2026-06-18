import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MetricRow } from './MetricRow';

describe('MetricRow', () => {
  it('renders one tile per metric with label and value', () => {
    render(<MetricRow metrics={[
      { label: 'NAV', value: '1,000' },
      { label: 'PnL', value: '-20', variant: 'neg' },
    ]} />);
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('1,000')).toBeInTheDocument();
    expect(screen.getByText('-20')).toBeInTheDocument();
  });

  it('applies the negative variant class to the tile', () => {
    const { container } = render(<MetricRow metrics={[{ label: 'PnL', value: '-1', variant: 'neg' }]} />);
    expect(container.querySelector('.wl-tile--neg')).not.toBeNull();
  });

  it('renders nothing when metrics is empty', () => {
    const { container } = render(<MetricRow metrics={[]} />);
    expect(container.querySelector('.wl-metric-row')).toBeNull();
  });
});
