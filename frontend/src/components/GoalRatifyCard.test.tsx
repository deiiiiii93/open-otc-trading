import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { GoalRatifyCard } from './GoalRatifyCard';
import type { GoalContract, GoalRunState } from '../lib/goalApi';

const CONTRACT: GoalContract = {
  schema_version: 'goal_contract.v1',
  goal_text: 'Get latest risk run onto Control',
  summary: 'Refresh risk on the Control portfolio and confirm via the ledger.',
  domain_write_policy: 'allowed_by_mode',
  criteria: [
    {
      id: 'C1',
      text: 'Latest risk run used the Control portfolio.',
      required: true,
      check: { type: 'ledger_predicate', tool: 'get_latest_risk_run' },
    },
    {
      id: 'C2',
      text: 'A durable risk report artifact exists.',
      required: true,
      check: { type: 'artifact_exists', kind: 'report' },
    },
  ],
};

const stateWith = (status: GoalRunState['status']): GoalRunState => ({
  schema_version: 'goal_run_state.v1',
  goal_run_id: '7',
  status,
  mode: 'auto',
  contract_hash: status === 'awaiting_ratification' ? null : 'abc',
});

describe('GoalRatifyCard', () => {
  it('shows the summary and every criterion', () => {
    render(
      <GoalRatifyCard contract={CONTRACT} state={stateWith('awaiting_ratification')} onRatify={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByText(/Refresh risk on the Control portfolio/)).toBeInTheDocument();
    expect(screen.getByText('Latest risk run used the Control portfolio.')).toBeInTheDocument();
    expect(screen.getByText('A durable risk report artifact exists.')).toBeInTheDocument();
  });

  it('fires onRatify and onCancel while awaiting ratification', () => {
    const onRatify = vi.fn();
    const onCancel = vi.fn();
    render(
      <GoalRatifyCard contract={CONTRACT} state={stateWith('awaiting_ratification')} onRatify={onRatify} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /accept/i }));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onRatify).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('hides the accept button once the run is running', () => {
    render(
      <GoalRatifyCard contract={CONTRACT} state={stateWith('running')} onRatify={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.queryByRole('button', { name: /accept/i })).toBeNull();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it('disables the buttons while busy', () => {
    render(
      <GoalRatifyCard contract={CONTRACT} state={stateWith('awaiting_ratification')} onRatify={vi.fn()} onCancel={vi.fn()} busy />,
    );
    expect(screen.getByRole('button', { name: /accept/i })).toBeDisabled();
  });

  it('surfaces escalation reason and failing criteria when stuck', () => {
    render(
      <GoalRatifyCard
        contract={CONTRACT}
        state={{
          ...stateWith('stuck_needs_human'),
          terminal_reason: 'max_iterations_reached',
          failing_criteria: [{ id: 'C2', status: 'failed', reason: 'no durable report artifact' }],
        }}
        onRatify={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/needs you/i)).toBeInTheDocument();
    expect(screen.getByText(/no durable report artifact/)).toBeInTheDocument();
  });
});
