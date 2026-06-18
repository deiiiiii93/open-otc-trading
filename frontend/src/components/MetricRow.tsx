import React from 'react';
import { Tile, type TileVariant } from './Tile';
import { useAutoFitGroup } from '../hooks/useAutoFitText';
import './MetricRow.css';

export type Metric = {
  label: string;
  value: React.ReactNode;
  variant?: TileVariant;
  delta?: React.ReactNode;
};

type Props = {
  metrics: Metric[];
  columns?: number;
  className?: string;
};

export function MetricRow({ metrics, columns, className = '' }: Props) {
  // Hooks must run unconditionally; the empty-row early return happens after.
  const fitRef = useAutoFitGroup<HTMLDivElement>(metrics.map((m) => String(m.value)).join('|'));
  if (metrics.length === 0) return null;
  const style = columns ? ({ '--metric-cols': String(columns) } as React.CSSProperties) : undefined;
  return (
    <div ref={fitRef} className={`wl-metric-row ${className}`.trim()} style={style}>
      {metrics.map((m, i) => (
        <Tile key={`${m.label}-${i}`} label={m.label} value={m.value} variant={m.variant} delta={m.delta} />
      ))}
    </div>
  );
}

// Shared helper used by every template that accepts `metrics`. Renders one
// <MetricRow> for a flat Metric[], or one per inner array for Metric[][].
// Exported here (single source of truth) so templates don't each re-declare it.
export function MetricRows({ metrics }: { metrics?: Metric[] | Metric[][] }) {
  if (!metrics || metrics.length === 0) return null;
  const rows = Array.isArray(metrics[0]) ? (metrics as Metric[][]) : [metrics as Metric[]];
  return <>{rows.map((row, i) => <MetricRow key={i} metrics={row} />)}</>;
}
