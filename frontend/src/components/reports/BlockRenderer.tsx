import { Badge } from '../Badge';
import { ChartAsset } from '../ChartAsset';
import { GreeksByPosition } from '../GreeksByPosition';
import { MetricRow, type Metric } from '../MetricRow';
import { Table, type Column } from '../Table';
import { formatSignedNumber } from '../numberFormat';
import { Waterfall, type WaterfallData } from './Waterfall';
import type { ReportBlock } from '../../types';
import './BlockRenderer.css';

type Props = { block: ReportBlock };

type Row = Record<string, any>;

function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function scalarMetrics(data: Row, fields: string[] | null): Metric[] {
  const source: Row = data.metrics ?? data;
  return Object.entries(source)
    .filter(([key, value]) => typeof value === 'number' && (!fields || fields.includes(key)))
    .map(([key, value]) => ({
      label: humanize(key),
      value: formatSignedNumber(value as number),
      variant: ((value as number) >= 0 ? 'pos' : 'neg') as Metric['variant'],
    }));
}

/** Drives `delta_metric_row` off Tile's `delta` slot, which until now was
 *  defined but unused everywhere in the app. */
function priorMetrics(data: Row, fields: string[] | null): Metric[] {
  const source: Row = data.metrics ?? {};
  return Object.entries(source)
    .filter(([key, value]) => value && typeof value === 'object' && (!fields || fields.includes(key)))
    .map(([key, change]: [string, any]) => ({
      label: humanize(key),
      value: change.after == null ? '—' : formatSignedNumber(change.after),
      variant: ((change.change ?? 0) >= 0 ? 'pos' : 'neg') as Metric['variant'],
      delta:
        change.change == null
          ? undefined
          : `${change.change >= 0 ? '▲' : '▼'} ${formatSignedNumber(change.change)}`,
    }));
}

function autoColumns(rows: Row[]): Column<Row>[] {
  const first = rows[0] ?? {};
  return Object.keys(first).map((key) => ({
    key,
    header: humanize(key),
    numeric: typeof first[key] === 'number',
    render: (row: Row) => {
      const value = row[key];
      if (value == null) return '—';
      return typeof value === 'number' ? formatSignedNumber(value) : String(value);
    },
  }));
}

export function BlockRenderer({ block }: Props) {
  const { result, render: renderKind, fields } = block;

  // These two states are deliberately distinct. "empty" is an affirmative
  // result — the check ran and found nothing. "unavailable" is the ABSENCE of
  // a result — the check could not run. Collapsing them would let a report
  // imply a clean book when nothing was actually verified.
  if (result.status === 'empty') {
    return (
      <div className="wl-block wl-block--empty" data-testid="block-empty">
        <Badge variant="ink">none</Badge>
        <p className="wl-block__reason">{result.reason}</p>
      </div>
    );
  }

  if (result.status === 'unavailable') {
    return (
      <div className="wl-block wl-block--unavailable" data-testid="block-unavailable">
        <Badge variant="warn">not available</Badge>
        <p className="wl-block__reason">{result.reason}</p>
      </div>
    );
  }

  const data: Row = result.data ?? {};

  switch (renderKind) {
    case 'metric_row':
      return <MetricRow metrics={scalarMetrics(data, fields)} />;
    case 'delta_metric_row':
      return <MetricRow metrics={priorMetrics(data, fields)} />;
    case 'table': {
      const rows: Row[] = data.rows ?? data.added ?? [];
      if (rows.length === 0) {
        return (
          <div className="wl-block wl-block--empty" data-testid="block-empty">
            <p className="wl-block__reason">No rows.</p>
          </div>
        );
      }
      return (
        <Table
          columns={autoColumns(rows)}
          rows={rows}
          rowKey={(row) => JSON.stringify(row)}
        />
      );
    }
    case 'bar_chart':
    case 'line_chart':
      return (
        <ChartAsset
          title={block.key}
          data={{
            chart_type: renderKind === 'bar_chart' ? 'bar' : 'line',
            x_key: data.x_key ?? 'name',
            y_key: data.y_key ?? 'value',
            series: data.series ?? [],
          }}
        />
      );
    case 'callout': {
      const items: Row[] = data.items ?? [];
      if (items.length === 0) {
        return (
          <div className="wl-block wl-block--empty" data-testid="block-empty">
            <p className="wl-block__reason">Nothing to flag.</p>
          </div>
        );
      }
      return (
        <ul className="wl-block__items">
          {items.map((item, index) => (
            <li key={index} className="wl-block__item">
              <span className="wl-block__item-label">
                {item.scope_label ?? item.underlying ?? `#${item.position_id}`}
              </span>
              <span className="wl-block__item-detail">
                {item.status ?? item.nearest_barrier_kind ?? ''}
                {item.days_to_nearest != null && ` · ${item.days_to_nearest}d`}
              </span>
            </li>
          ))}
        </ul>
      );
    }
    case 'greeks_table':
      return <GreeksByPosition positions={data.positions ?? []} />;
    case 'waterfall':
      return <Waterfall data={data as WaterfallData} />;
    default:
      return (
        <div className="wl-block wl-block--unknown" data-testid="block-unknown-renderer">
          <Badge variant="warn">unknown renderer</Badge>
          <p className="wl-block__reason">
            No component is registered for renderer “{renderKind}”.
          </p>
        </div>
      );
  }
}
