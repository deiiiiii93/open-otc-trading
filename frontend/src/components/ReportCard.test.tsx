import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportCard } from './ReportCard';
import type { ReportJob } from '../types';

const job: ReportJob = {
  id: 42,
  report_type: 'risk',
  status: 'completed',
  request_payload: {},
  result_payload: {},
  artifact_paths: { html: '/artifacts/risk-42.html' },
  created_at: '2026-05-07T13:44:00Z',
};

describe('ReportCard', () => {
  it('renders title from request_payload', () => {
    render(<ReportCard job={{ ...job, request_payload: { title: 'Daily Risk Run · Desk-Q2' } }} onOpen={() => {}} />);
    expect(screen.getByText('Daily Risk Run · Desk-Q2')).toBeInTheDocument();
  });

  it('falls back to default title when none provided', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText(/Risk report #42/i)).toBeInTheDocument();
  });

  it('renders the formatted date', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText('05-07')).toBeInTheDocument();
  });

  it('renders the report type label', () => {
    render(<ReportCard job={job} onOpen={() => {}} />);
    expect(screen.getByText(/^risk$/)).toBeInTheDocument();
  });

  it('calls onOpen with job on click', async () => {
    const onOpen = vi.fn();
    render(<ReportCard job={job} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /open/i }));
    expect(onOpen).toHaveBeenCalledWith(job);
  });
});
