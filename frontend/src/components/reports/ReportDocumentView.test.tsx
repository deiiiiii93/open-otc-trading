import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportDocumentView } from './ReportDocumentView';
import type { ReportDocument } from '../../types';

const DOC: ReportDocument = {
  template: {
    slug: 'risk-manager-daily',
    title: 'Risk — Daily',
    persona: 'risk_manager',
    version: 1,
    spec: 'meta:\n',
    spec_sha256: 'sha256:abc',
  },
  params: { portfolio_id: 2, compare_to_run_id: 35 },
  generated_at: '2026-08-06T09:00:00Z',
  provenance: {
    risk_run_id: 36,
    valuation_as_of: '2026-06-24T00:00:00',
    position_set_hash: 'sha256:f69b',
    coverage: { priced: 4, total: 5 },
  },
  sections: [
    {
      id: 'limit_status',
      title: 'Limit status',
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
      narrative: 'The book is within all limits.',
      narrative_error: null,
      grounding: { checked: true, flags: [], grounded_count: 0 },
    },
    {
      id: 'stress',
      title: 'Stress',
      blocks: [
        {
          key: 'scenario.latest_grid',
          render: 'table',
          fields: null,
          result: {
            status: 'unavailable',
            reason: 'no scenario test run exists',
            data: {},
            provenance: {},
          },
        },
      ],
      narrative: null,
      narrative_error: null,
      grounding: { checked: false, flags: [], grounded_count: 0 },
    },
  ],
};

describe('ReportDocumentView', () => {
  it('renders the template title and every section', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText('Risk — Daily')).toBeInTheDocument();
    expect(screen.getByText('Limit status')).toBeInTheDocument();
    expect(screen.getByText('Stress')).toBeInTheDocument();
  });

  it('renders narrative prose beneath its section blocks', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText('The book is within all limits.')).toBeInTheDocument();
  });

  it('surfaces coverage in the header so a partial book is never silent', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText(/4\s*\/\s*5 priced/i)).toBeInTheDocument();
  });

  it('shows provenance', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText(/risk run #36/i)).toBeInTheDocument();
    expect(screen.getByText(/sha256:f69b/)).toBeInTheDocument();
  });

  it('flags a section whose narrative contains ungrounded numbers', () => {
    const flagged: ReportDocument = {
      ...DOC,
      sections: [
        {
          ...DOC.sections[0],
          narrative: 'Vega is 999.99.',
          grounding: { checked: true, flags: [{ token: 999.99, offset: 9 }], grounded_count: 0 },
        },
      ],
    };
    render(<ReportDocumentView document={flagged} />);
    expect(screen.getByTestId('grounding-warning')).toHaveTextContent(/999.99/);
  });

  it('reports a narrative failure instead of silently omitting the section', () => {
    const failed: ReportDocument = {
      ...DOC,
      sections: [{ ...DOC.sections[0], narrative: null, narrative_error: 'model timed out' }],
    };
    render(<ReportDocumentView document={failed} />);
    expect(screen.getByTestId('narrative-error')).toHaveTextContent(/model timed out/);
  });
});
