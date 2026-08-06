import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BlockRenderer } from './BlockRenderer';
import type { ReportBlock } from '../../types';

function block(
  partial: Partial<ReportBlock> & { result: ReportBlock['result'] },
): ReportBlock {
  return { key: 'risk.totals', render: 'metric_row', fields: null, ...partial };
}

describe('BlockRenderer', () => {
  it('renders scalars as metric tiles', () => {
    render(
      <BlockRenderer
        block={block({
          result: {
            status: 'ok',
            reason: null,
            provenance: {},
            data: { metrics: { delta_cash: 57334.67, vega: 276.43 } },
          },
        })}
      />,
    );
    expect(screen.getByText(/delta cash/i)).toBeInTheDocument();
  });

  it('honours the fields filter', () => {
    render(
      <BlockRenderer
        block={block({
          fields: ['vega'],
          result: {
            status: 'ok',
            reason: null,
            provenance: {},
            data: { metrics: { delta_cash: 1, vega: 276.43 } },
          },
        })}
      />,
    );
    expect(screen.getByText(/vega/i)).toBeInTheDocument();
    expect(screen.queryByText(/delta cash/i)).not.toBeInTheDocument();
  });

  it('renders an EMPTY block as an affirmative result', () => {
    render(
      <BlockRenderer
        block={block({
          key: 'limits.breaches',
          render: 'callout',
          result: {
            status: 'empty',
            reason: 'no limit is in breach on the latest monitoring run',
            data: {},
            provenance: {},
          },
        })}
      />,
    );
    expect(screen.getByTestId('block-empty')).toHaveTextContent(/no limit is in breach/i);
  });

  it('renders an UNAVAILABLE block as a check that did not run', () => {
    render(
      <BlockRenderer
        block={block({
          key: 'scenario.latest_grid',
          render: 'table',
          result: {
            status: 'unavailable',
            reason: 'no scenario test run exists for this portfolio',
            data: {},
            provenance: {},
          },
        })}
      />,
    );
    const panel = screen.getByTestId('block-unavailable');
    expect(panel).toHaveTextContent(/not available/i);
    expect(panel).toHaveTextContent(/no scenario test run exists/i);
  });

  it('gives empty and unavailable different test ids so they can never render alike', () => {
    const { rerender } = render(
      <BlockRenderer
        block={block({
          result: { status: 'empty', reason: 'nothing to report', data: {}, provenance: {} },
        })}
      />,
    );
    expect(screen.queryByTestId('block-unavailable')).not.toBeInTheDocument();
    rerender(
      <BlockRenderer
        block={block({
          result: { status: 'unavailable', reason: 'could not run', data: {}, provenance: {} },
        })}
      />,
    );
    expect(screen.queryByTestId('block-empty')).not.toBeInTheDocument();
  });

  it('renders rows as a table', () => {
    render(
      <BlockRenderer
        block={block({
          key: 'pnl.by_position',
          render: 'table',
          result: {
            status: 'ok',
            reason: null,
            provenance: {},
            data: { rows: [{ position_id: 1, underlying: 'AAPL', change: 2000 }] },
          },
        })}
      />,
    );
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('renders a waterfall', () => {
    render(
      <BlockRenderer
        block={block({
          key: 'pnl.explain',
          render: 'waterfall',
          result: {
            status: 'ok',
            reason: null,
            provenance: {},
            data: {
              buckets: { delta: 2000 },
              explained: 2000,
              actual: 2000,
              residual: 0,
              residual_ratio: 0,
              residual_exceeds_threshold: false,
            },
          },
        })}
      />,
    );
    expect(screen.getByText('Delta')).toBeInTheDocument();
  });

  it('falls back visibly for an unknown renderer rather than rendering nothing', () => {
    render(
      <BlockRenderer
        block={block({
          render: 'hologram',
          result: { status: 'ok', reason: null, provenance: {}, data: { metrics: {} } },
        })}
      />,
    );
    expect(screen.getByTestId('block-unknown-renderer')).toHaveTextContent(/hologram/);
  });
});
