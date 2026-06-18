import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ActionProposal } from './ActionProposal';
import type { AgentActionProposal } from '../types';

describe('ActionProposal', () => {
  const proposal: AgentActionProposal = {
    id: 'p1',
    tool_name: 'run_batch_pricing',
    label: 'Run pricing on Portfolio "Desk-Q2"',
    summary: '12 positions · valuation date 2026-05-07',
    payload: {},
    requires_confirmation: true,
    status: 'pending',
  };

  it('renders label and summary', () => {
    render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText(proposal.label)).toBeInTheDocument();
    expect(screen.getByText(proposal.summary)).toBeInTheDocument();
  });

  it('renders the tool_name in a code element', () => {
    render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText('run_batch_pricing')).toBeInTheDocument();
  });

  it('renders with tool_name field and persona chip', () => {
    const fullProposal: AgentActionProposal = {
      id: 'intr-1:0',
      tool_name: 'run_batch_pricing',
      label: 'Run batch pricing (valuations + risk)',
      summary: 'Run summary risk for portfolio #7',
      payload: { portfolio_id: 7 },
      persona: 'risk_manager',
      risk_level: 'write',
      status: 'pending',
    };

    render(<ActionProposal proposal={fullProposal} onConfirm={vi.fn()} onDismiss={vi.fn()} />);

    expect(screen.getByText('Run batch pricing (valuations + risk)')).toBeInTheDocument();
    expect(screen.getByText('run_batch_pricing')).toBeInTheDocument();
    expect(screen.getByText(/risk manager/i)).toBeInTheDocument();
  });

  it('falls back to legacy type field when tool_name is missing', () => {
    const legacy = {
      id: 'old',
      type: 'approve_rfq',
      label: 'Approve RFQ',
      summary: '...',
    } as unknown as AgentActionProposal;

    render(<ActionProposal proposal={legacy} onConfirm={vi.fn()} onDismiss={vi.fn()} />);

    expect(screen.getByText('approve_rfq')).toBeInTheDocument();
  });

  it('calls onConfirm when confirm clicked', async () => {
    const onConfirm = vi.fn();
    render(<ActionProposal proposal={proposal} onConfirm={onConfirm} onDismiss={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledWith(proposal);
  });

  it('calls onDismiss when dismiss clicked', async () => {
    const onDismiss = vi.fn();
    render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledWith(proposal);
  });

  it('renders confirmed as a terminal disabled state', () => {
    render(<ActionProposal proposal={{ ...proposal, status: 'confirmed' }} onConfirm={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText('Confirmed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /dismiss/i })).toBeDisabled();
  });

  it('shows an indeterminate progress strip while a confirmed action is executing', () => {
    render(
      <ActionProposal
        proposal={{ ...proposal, status: 'confirmed' }}
        executing
        onConfirm={() => {}}
        onDismiss={() => {}}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Running run_batch_pricing');
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows determinate task progress for confirmed background work', () => {
    render(
      <ActionProposal
        proposal={{ ...proposal, status: 'confirmed', task_id: 21 }}
        task={{
          id: 21,
          kind: 'risk_run',
          status: 'running',
          progress_current: 12,
          progress_total: 64,
          message: 'Pricing scenarios',
          created_at: '2026-05-27T00:00:00Z',
        }}
        onConfirm={() => {}}
        onDismiss={() => {}}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Risk run running');
    expect(screen.getByText('12/64')).toBeInTheDocument();
    expect(screen.getByText('Pricing scenarios')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '12');
  });

  it('renders dismissed as a terminal disabled state', () => {
    render(<ActionProposal proposal={{ ...proposal, status: 'dismissed' }} onConfirm={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText('Dismissed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /dismiss/i })).toBeDisabled();
  });

  it('has no a11y violations', async () => {
    const { container } = render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
