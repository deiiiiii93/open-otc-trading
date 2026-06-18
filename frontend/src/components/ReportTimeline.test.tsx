import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportTimeline } from './ReportTimeline';
import type { ReportJob } from '../types';

const jobs: ReportJob[] = [
  { id: 1, report_type: 'risk',      status: 'completed', request_payload: {}, result_payload: {}, artifact_paths: {}, created_at: '2026-05-07T11:00:00Z' },
  { id: 2, report_type: 'portfolio', status: 'completed', request_payload: {}, result_payload: {}, artifact_paths: {}, created_at: '2026-05-06T17:02:00Z' },
];

describe('ReportTimeline', () => {
  it('renders one card per job', () => {
    render(<ReportTimeline jobs={jobs} onOpen={() => {}} />);
    expect(screen.getByText(/Risk report #1/i)).toBeInTheDocument();
    expect(screen.getByText(/Portfolio report #2/i)).toBeInTheDocument();
  });

  it('shows empty state when no jobs', () => {
    render(<ReportTimeline jobs={[]} onOpen={() => {}} />);
    expect(screen.getByText(/no reports yet/i)).toBeInTheDocument();
  });

  it('forwards onOpen click to the underlying card', async () => {
    const onOpen = vi.fn();
    render(<ReportTimeline jobs={jobs} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /Risk report #1/i }));
    expect(onOpen).toHaveBeenCalledWith(jobs[0]);
  });
});
