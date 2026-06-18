import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChartAsset } from './ChartAsset';

describe('ChartAsset', () => {
  it('renders a bar chart from series data', () => {
    const data = {
      chart_type: 'bar' as const,
      x_key: 'name',
      y_key: 'value',
      series: [
        { name: 'delta', value: 100 },
        { name: 'gamma', value: 0.5 },
      ],
    };
    const { container } = render(<ChartAsset data={data} title="Risk" />);
    expect(container.querySelector('.wl-chart-asset')).not.toBeNull();
    expect(screen.getByText('Risk')).toBeInTheDocument();
  });

  it('renders a line chart', () => {
    const data = {
      chart_type: 'line' as const,
      x_key: 'day',
      y_key: 'pnl',
      series: [
        { day: 'Mon', pnl: 10 },
        { day: 'Tue', pnl: 20 },
      ],
    };
    const { container } = render(<ChartAsset data={data} title="P&L" />);
    expect(container.querySelector('.wl-chart-asset')).not.toBeNull();
  });
});
