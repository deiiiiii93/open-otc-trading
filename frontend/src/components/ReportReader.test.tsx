import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReportReader } from './ReportReader';
import type { ReportJob } from '../types';

const job: ReportJob = {
  id: 42,
  report_type: 'risk',
  status: 'completed',
  request_payload: { title: 'Risk Run · Desk-Q2' },
  result_payload: { summary: 'Risk priced 12 positions.' },
  artifact_paths: { html: '/artifacts/risk-42.html', excel: '/artifacts/risk-42.xlsx' },
  created_at: '2026-05-07T13:44:00Z',
};

describe('ReportReader', () => {
  it('renders modal title with report id when open', () => {
    render(<ReportReader open job={job} onOpenChange={() => {}} />);
    expect(screen.getByText(/Report #42/i)).toBeInTheDocument();
  });

  it('renders artifact paths as openable links', () => {
    render(<ReportReader open job={job} onOpenChange={() => {}} />);
    expect(screen.getByRole('link', { name: '/artifacts/risk-42.html' }))
      .toHaveAttribute('href', '/api/artifacts/risk-42.html');
    expect(screen.getByRole('link', { name: '/artifacts/risk-42.xlsx' }))
      .toHaveAttribute('href', '/api/artifacts/risk-42.xlsx');
  });

  it('shows result_payload as monospaced JSON', () => {
    render(<ReportReader open job={job} onOpenChange={() => {}} />);
    const code = document.querySelector('pre');
    expect(code?.textContent).toContain('Risk priced 12 positions');
  });

  it('does not render when job is null', () => {
    render(<ReportReader open={true} job={null} onOpenChange={() => {}} />);
    expect(screen.queryByText(/Report #/i)).not.toBeInTheDocument();
  });

  it('calls onOpenChange(false) when close button clicked', async () => {
    const onOpenChange = vi.fn();
    render(<ReportReader open job={job} onOpenChange={onOpenChange} />);
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
