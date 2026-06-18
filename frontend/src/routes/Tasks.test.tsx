import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tasks } from './Tasks';
import type { TaskRun } from '../types';

const tasks: TaskRun[] = [
  {
    id: 1,
    kind: 'risk_run',
    status: 'running',
    portfolio_id: 10,
    risk_run_id: 20,
    report_job_id: null,
    progress_current: 3,
    progress_total: 5,
    message: 'Pricing positions',
    error: null,
    created_at: '2026-05-11T08:00:00Z',
    started_at: '2026-05-11T08:00:10Z',
  },
  {
    id: 2,
    kind: 'report_job',
    status: 'completed',
    portfolio_id: 10,
    risk_run_id: null,
    report_job_id: 30,
    progress_current: 3,
    progress_total: 3,
    message: 'Report generated',
    error: null,
    created_at: '2026-05-11T08:01:00Z',
    started_at: '2026-05-11T08:01:05Z',
    finished_at: '2026-05-11T08:02:00Z',
  },
  {
    id: 3,
    kind: 'risk_run',
    status: 'failed',
    portfolio_id: 11,
    risk_run_id: 21,
    report_job_id: null,
    progress_current: 1,
    progress_total: 4,
    message: 'Risk run failed',
    error: 'interrupted',
    created_at: '2026-05-11T08:03:00Z',
    started_at: '2026-05-11T08:03:01Z',
  },
  {
    id: 4,
    kind: 'risk_run',
    status: 'completed_with_errors',
    portfolio_id: 12,
    risk_run_id: 22,
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
    created_at: '2026-05-11T08:04:00Z',
    started_at: '2026-05-11T08:04:01Z',
    finished_at: '2026-05-11T08:04:30Z',
  },
];

describe('Tasks', () => {
  it('lists running, completed, and failed task rows with linked ids', () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);

    expect(screen.getByText('#1 Risk run')).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(screen.getByText('Risk #20')).toBeInTheDocument();
    expect(screen.getByText('#2 Report job')).toBeInTheDocument();
    expect(screen.getByText('Report #30')).toBeInTheDocument();
    expect(screen.getByText('#3 Risk run')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'STARTED' })).toBeInTheDocument();
  });

  it('shows a View errors button only for failed and completed_with_errors rows', () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    expect(buttons).toHaveLength(2);
  });

  it('opens a dialog with the exception text for a failed task', async () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    await userEvent.click(buttons[0]); // task #3 (failed) is the first error row
    expect(await screen.findByText('interrupted')).toBeInTheDocument();
  });

  it('opens a dialog listing failing positions for a completed_with_errors task', async () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    await userEvent.click(buttons[1]); // task #4 (completed_with_errors)
    expect(await screen.findByText(/510050\.SH snowball/)).toBeInTheDocument();
    expect(screen.getByText(/missing vol/)).toBeInTheDocument();
  });
});

function mockFetch(payload: unknown, ok = true) {
  const fetchMock = vi.fn(async (_input?: RequestInfo | URL, _init?: RequestInit) => ({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

describe('Tasks — batch_pricing label', () => {
  it('labels batch_pricing tasks', () => {
    const batchTask: TaskRun = {
      id: 5,
      kind: 'batch_pricing',
      status: 'completed',
      portfolio_id: null,
      risk_run_id: null,
      report_job_id: null,
      progress_current: 10,
      progress_total: 10,
      message: 'Batch pricing done',
      error: null,
      created_at: '2026-06-06T08:00:00Z',
      started_at: '2026-06-06T08:00:01Z',
      finished_at: '2026-06-06T08:00:30Z',
    };
    render(<Tasks tasks={[batchTask]} loading={false} error={null} />);
    expect(screen.getByText('#5 Batch pricing')).toBeInTheDocument();
  });
});

describe('Tasks — Greeks landscape link', () => {
  it('labels and opens a Greeks landscape task', async () => {
    const onOpen = vi.fn();
    render(<Tasks tasks={[{
      id: 8,
      kind: 'greeks_landscape',
      status: 'completed',
      portfolio_id: 1,
      greeks_landscape_run_id: 4,
      progress_current: 2,
      progress_total: 2,
      created_at: '2026-06-12T00:00:00Z',
    }]} loading={false} error={null} onOpenGreeksLandscape={onOpen} />);
    expect(screen.getByText('#8 Greeks landscape')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Landscape #4' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });
});

describe('Tasks — risk link opens RiskReportDialog', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens the risk report dialog when the risk link is clicked', async () => {
    const riskRun = {
      id: 2,
      portfolio_id: 1,
      method: 'summary',
      status: 'completed',
      created_at: '2026-06-06T08:00:00Z',
      metrics: {
        totals: {
          market_value: 1500.5,
          pnl: 250.25,
          delta_proxy: 0,
          gross_notional: 0,
          one_day_var_proxy: 0,
        },
        by_currency: null,
        positions: [],
      },
    };
    mockFetch(riskRun);

    const taskWithRisk: TaskRun = {
      id: 10,
      kind: 'risk_run',
      status: 'completed',
      portfolio_id: 1,
      risk_run_id: 2,
      report_job_id: null,
      progress_current: 5,
      progress_total: 5,
      message: 'Done',
      error: null,
      created_at: '2026-06-06T08:00:00Z',
      started_at: '2026-06-06T08:00:01Z',
      finished_at: '2026-06-06T08:00:30Z',
    };
    render(<Tasks tasks={[taskWithRisk]} loading={false} error={null} />);

    await userEvent.click(screen.getByRole('button', { name: 'Risk #2' }));
    await waitFor(() => expect(screen.getByText('Market Value (PV)')).toBeInTheDocument());
  });

  it('renders no risk button for tasks without risk_run_id', () => {
    const taskNoRisk: TaskRun = {
      id: 11,
      kind: 'report_job',
      status: 'completed',
      portfolio_id: 1,
      risk_run_id: null,
      report_job_id: 5,
      progress_current: 1,
      progress_total: 1,
      message: 'Done',
      error: null,
      created_at: '2026-06-06T08:00:00Z',
      started_at: '2026-06-06T08:00:01Z',
      finished_at: '2026-06-06T08:00:30Z',
    };
    render(<Tasks tasks={[taskNoRisk]} loading={false} error={null} />);
    expect(screen.queryByRole('button', { name: /Risk #/ })).toBeNull();
  });
});
