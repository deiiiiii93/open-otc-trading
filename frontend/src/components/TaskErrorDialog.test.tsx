import { render, screen } from '@testing-library/react';
import { test, expect } from 'vitest';
import { TaskErrorDialog } from './TaskErrorDialog';
import type { TaskRun } from '../types';

const baseTask: TaskRun = {
  id: 7,
  kind: 'risk_run',
  status: 'completed_with_errors',
  portfolio_id: 1,
  risk_run_id: 2,
  report_job_id: null,
  progress_current: 2,
  progress_total: 2,
  message: 'Completed with 1 position issue',
  error: null,
  result_payload: {
    errors: {
      kind: 'risk_run',
      failed_count: 1,
      positions: [
        {
          position_id: 99,
          underlying: '510050.SH',
          product_type: 'snowball',
          pricing_ok: false,
          pricing_error: 'Pricing profile extraction failed: missing vol',
          greeks_ok: true,
          greeks_error: null,
        },
      ],
    },
  },
  created_at: '2026-06-03T01:00:00Z',
};

test('renders nothing when closed', () => {
  render(<TaskErrorDialog task={baseTask} open={false} onClose={() => {}} />);
  expect(screen.queryByText(/510050\.SH/)).not.toBeInTheDocument();
});

test('lists failing positions with reasons for completed_with_errors', () => {
  render(<TaskErrorDialog task={baseTask} open onClose={() => {}} />);
  expect(screen.getByText(/510050\.SH snowball/)).toBeInTheDocument();
  expect(screen.getByText(/missing vol/)).toBeInTheDocument();
});

test('shows exception text for a failed task', () => {
  const failed: TaskRun = {
    ...baseTask,
    status: 'failed',
    error: 'ValueError: boom',
    result_payload: null,
  };
  render(<TaskErrorDialog task={failed} open onClose={() => {}} />);
  expect(screen.getByText(/ValueError: boom/)).toBeInTheDocument();
});

test('falls back to message when payload is absent', () => {
  const noPayload: TaskRun = { ...baseTask, result_payload: null };
  render(<TaskErrorDialog task={noPayload} open onClose={() => {}} />);
  expect(screen.getByText(/Completed with 1 position issue/)).toBeInTheDocument();
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});
