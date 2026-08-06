import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Waterfall } from './Waterfall';

const DATA = {
  buckets: { delta: 2000, gamma: 80, vega: -150, theta: -81.6, rho: 0, rho_q: 0 },
  explained: 1848.4,
  actual: 1500,
  residual: -348.4,
  residual_ratio: 0.232,
  residual_exceeds_threshold: true,
};

describe('Waterfall', () => {
  it('renders a bar for every non-zero bucket', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByText('Delta')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
    expect(screen.getByText('Vega')).toBeInTheDocument();
    expect(screen.getByText('Theta')).toBeInTheDocument();
  });

  it('shows the actual move and the residual', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByText('Actual')).toBeInTheDocument();
    expect(screen.getByText('Residual')).toBeInTheDocument();
  });

  it('warns when the residual exceeds the threshold', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByRole('note')).toHaveTextContent(/does not fully explain/i);
  });

  it('does not warn when the residual is within tolerance', () => {
    render(
      <Waterfall
        data={{ ...DATA, residual: -10, residual_ratio: 0.006, residual_exceeds_threshold: false }}
      />,
    );
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });

  it('omits buckets that contributed nothing', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.queryByText('Rho')).not.toBeInTheDocument();
  });
});
