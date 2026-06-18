import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AnalyticsDashboard } from './AnalyticsDashboard';

describe('AnalyticsDashboard', () => {
  it('renders metrics, controls, and panels', () => {
    render(
      <AnalyticsDashboard
        title="RISK"
        metrics={[{ label: 'D', value: '1' }]}
        controls={<div>controls-strip</div>}
        columns={1}
        panels={<div>panel-x</div>}
      />,
    );
    expect(screen.getByText('RISK')).toBeInTheDocument();
    expect(screen.getByText('controls-strip')).toBeInTheDocument();
    expect(screen.getByText('panel-x')).toBeInTheDocument();
  });

  it('renders the state slot instead of panels when given', () => {
    render(<AnalyticsDashboard title="X" panels={<div>p</div>} state={<div>empty-state</div>} />);
    expect(screen.getByText('empty-state')).toBeInTheDocument();
    expect(screen.queryByText('p')).toBeNull();
  });
});
