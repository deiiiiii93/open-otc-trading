import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Reports } from './Reports';
import type { ReportJob, ReportTemplate } from '../types';

const JOB: ReportJob = {
  id: 7,
  report_type: 'risk_manager',
  status: 'completed',
  template_slug: 'risk-manager-daily',
  compare_to_run_id: 35,
  request_payload: { title: 'Risk — Daily', portfolio_id: 2 },
  artifact_paths: {},
  created_at: '2026-08-06T09:00:00Z',
  result_payload: {
    template: {
      slug: 'risk-manager-daily',
      title: 'Risk — Daily',
      persona: 'risk_manager',
      version: 1,
      spec: '',
      spec_sha256: 'sha256:abc',
    },
    params: { portfolio_id: 2, compare_to_run_id: 35 },
    generated_at: '2026-08-06T09:00:00Z',
    provenance: { risk_run_id: 36 },
    sections: [
      {
        id: 's',
        title: 'Limit status',
        narrative: 'All clear.',
        narrative_error: null,
        grounding: { checked: true, flags: [], grounded_count: 0 },
        blocks: [
          {
            key: 'limits.breaches',
            render: 'callout',
            fields: null,
            result: {
              status: 'empty',
              reason: 'no limit is in breach',
              data: {},
              provenance: {},
            },
          },
        ],
      },
    ],
  },
};

const TEMPLATES: ReportTemplate[] = [
  {
    slug: 'risk-manager-daily',
    title: 'Risk — Daily',
    persona: 'risk_manager',
    description: '',
    source: 'seed',
    version: 1,
  },
];

function setup(overrides: Record<string, unknown> = {}) {
  const props = {
    jobs: [JOB],
    templates: TEMPLATES,
    loading: false,
    selectedJob: JOB as ReportJob | null,
    onSelectJob: vi.fn(),
    onGenerate: vi.fn(),
    onSaveTemplate: vi.fn(),
    onValidateTemplate: vi.fn(),
    ...overrides,
  };
  render(<Reports {...(props as any)} />);
  return props;
}

describe('Reports', () => {
  it('renders the selected report document, not a JSON dump', () => {
    setup();
    expect(screen.getByText('Limit status')).toBeInTheDocument();
    expect(screen.getByText('All clear.')).toBeInTheDocument();
    expect(screen.queryByText(/"result_payload"/)).not.toBeInTheDocument();
  });

  it('lists reports in the rail', () => {
    setup();
    expect(screen.getAllByText(/Risk — Daily/).length).toBeGreaterThan(0);
  });

  it('switches to the templates tab', () => {
    setup();
    fireEvent.click(screen.getByRole('tab', { name: /templates/i }));
    expect(screen.getByText('risk-manager-daily')).toBeInTheDocument();
  });

  it('renders a legacy report without a template as raw payload', () => {
    const legacy: ReportJob = {
      ...JOB,
      template_slug: null,
      result_payload: { risk: { totals: { market_value: 1 } } },
    };
    setup({ jobs: [legacy], selectedJob: legacy });
    expect(screen.getByTestId('legacy-payload')).toBeInTheDocument();
  });

  it('shows an empty state with no reports', () => {
    setup({ jobs: [], selectedJob: null });
    expect(screen.getByText(/no reports yet/i)).toBeInTheDocument();
  });
});
