import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScenarioGrid, type ScenarioCell } from './ScenarioGrid';

describe('ScenarioGrid', () => {
  it('renders the spec grid layout (shift columns + vol rows)', () => {
    render(<ScenarioGrid cells={null} onPromoteToReport={() => {}} />);
    expect(screen.getByText('-3%')).toBeInTheDocument();
    expect(screen.getByText('+2%')).toBeInTheDocument();
    expect(screen.getByText('-2v')).toBeInTheDocument();
    expect(screen.getByText('+2v')).toBeInTheDocument();
  });

  it('renders deferred-state overlay', () => {
    render(<ScenarioGrid cells={null} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/scenario engine deferred/i)).toBeInTheDocument();
  });

  it('disables promote button while deferred', () => {
    render(<ScenarioGrid cells={null} onPromoteToReport={() => {}} />);
    expect(screen.getByRole('button', { name: /promote/i })).toBeDisabled();
  });

  it('does not call onPromoteToReport when disabled button clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<ScenarioGrid cells={null} onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).not.toHaveBeenCalled();
  });

  it('renders pnl values when cells provided', () => {
    const cells: ScenarioCell[][] = [
      [
        { spot_shift_pct: -3, vol_shift_abs: -0.02, pnl: -1200 },
        { spot_shift_pct: 0, vol_shift_abs: -0.02, pnl: -50 },
      ],
      [
        { spot_shift_pct: -3, vol_shift_abs: 0, pnl: -800 },
        { spot_shift_pct: 0, vol_shift_abs: 0, pnl: 0 },
      ],
    ];
    render(<ScenarioGrid cells={cells} onPromoteToReport={() => {}} />);
    expect(screen.getByText('-1,200')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.queryByText(/deferred/i)).not.toBeInTheDocument();
  });

  it('renders deferred overlay when cells null', () => {
    render(<ScenarioGrid cells={null} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/deferred/i)).toBeInTheDocument();
  });
});
