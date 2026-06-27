import { it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkflowParamsDialog } from './WorkflowParamsDialog';
import type { DeskWorkflowSummary } from '../types';

const WF: DeskWorkflowSummary = {
  slug: 'rmcd', title: 'RMCD', persona: 'risk_manager',
  description: '', scope: 'shared', default_mode: 'yolo', source: 'seed',
  params: [
    { name: 'portfolio', label: 'Portfolio', type: 'portfolio' },
    { name: 'start', label: 'Start date', type: 'date' },
  ],
};

it('renders one field per param', () => {
  render(<WorkflowParamsDialog open workflow={WF} portfolios={['Default']} onCancel={() => {}} onRun={() => {}} />);
  expect(screen.getByText('Portfolio')).toBeInTheDocument();
  expect(screen.getByText('Start date')).toBeInTheDocument();
});

it('disables Run until all fields are filled, then emits args', () => {
  const onRun = vi.fn();
  render(<WorkflowParamsDialog open workflow={WF} portfolios={['Default']} onCancel={() => {}} onRun={onRun} />);
  const run = screen.getByRole('button', { name: /run/i });
  expect(run).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Portfolio'), { target: { value: 'Default' } });
  fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-06-25' } });
  expect(run).not.toBeDisabled();
  fireEvent.click(run);
  expect(onRun).toHaveBeenCalledWith({ portfolio: 'Default', start: '2026-06-25' });
});
