import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { formatSignedNumber } from '../numberFormat';
import './Waterfall.css';

export type WaterfallData = {
  buckets: Record<string, number>;
  explained: number;
  actual: number;
  residual: number;
  residual_ratio: number | null;
  residual_exceeds_threshold: boolean;
};

type Props = { data: WaterfallData };

const BUCKET_LABELS: Record<string, string> = {
  delta: 'Delta',
  gamma: 'Gamma',
  vega: 'Vega',
  theta: 'Theta',
  rho: 'Rho',
  rho_q: 'Rho (q)',
};

type Row = { name: string; value: number; kind: 'bucket' | 'residual' | 'total' };

/** recharts renders SVG, so `fill` must be a value, not a class — the house
 *  pattern (see ChartAsset) passes the token inline. Still token-only. */
function barFill(row: Row): string {
  if (row.kind === 'total') return 'var(--ink-2)';
  if (row.kind === 'residual') return 'var(--warn)';
  return row.value >= 0 ? 'var(--pos)' : 'var(--neg)';
}

export function Waterfall({ data }: Props) {
  // A bucket that contributed nothing is noise on a decomposition chart.
  const bars: Row[] = Object.entries(data.buckets)
    .filter(([, value]) => Number.isFinite(value) && value !== 0)
    .map(([key, value]) => ({ name: BUCKET_LABELS[key] ?? key, value, kind: 'bucket' }));

  const rows: Row[] = [
    ...bars,
    { name: 'Residual', value: data.residual, kind: 'residual' },
    { name: 'Actual', value: data.actual, kind: 'total' },
  ];

  return (
    <div className="wl-waterfall">
      <div className="wl-waterfall__chart">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
            <XAxis
              dataKey="name"
              tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }}
            />
            <YAxis
              width={72}
              tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--paper)',
                border: '1px solid var(--ink)',
                fontFamily: 'var(--font-numeric)',
                fontSize: 12,
              }}
            />
            <Bar dataKey="value">
              {rows.map((row) => (
                <Cell key={row.name} fill={barFill(row)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* The contributions are also listed, not only plotted: the chart is a
          shape, the list is the evidence. It also keeps the decomposition
          readable to a screen reader and at narrow widths. */}
      <dl className="wl-waterfall__legend">
        {rows.map((row) => (
          <div key={row.name} className={`wl-waterfall__entry wl-waterfall__entry--${row.kind}`}>
            <dt className="wl-waterfall__term">{row.name}</dt>
            <dd className="wl-waterfall__value">{formatSignedNumber(row.value)}</dd>
          </div>
        ))}
      </dl>

      {data.residual_exceeds_threshold && (
        <p className="wl-waterfall__warn" role="note">
          The attribution does not fully explain this move
          {data.residual_ratio != null &&
            ` — ${(data.residual_ratio * 100).toFixed(1)}% unexplained`}
          .
        </p>
      )}
    </div>
  );
}
