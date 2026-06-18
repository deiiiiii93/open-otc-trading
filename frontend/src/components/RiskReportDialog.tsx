import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Modal } from './Modal';
import { Skeleton } from './Skeleton';
import { GreeksSummary, type CurrencyGreeks, type GreeksTotals } from './GreeksSummary';
import { PnlAttribution, type AttributionPosition } from './PnlAttribution';
import './RiskReportDialog.css';

export type RiskReportRun = {
  id: number;
  portfolio_id: number;
  method: string;
  status: string;
  created_at: string;
  metrics: {
    totals?: GreeksTotals | null;
    by_currency?: Record<string, CurrencyGreeks> | null;
    positions?: AttributionPosition[];
  };
};

type Props = {
  riskRunId: number | null;
  open: boolean;
  onClose: () => void;
};

export function RiskReportDialog({ riskRunId, open, onClose }: Props) {
  const [run, setRun] = useState<RiskReportRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || riskRunId == null) return undefined;
    let cancelled = false;
    setRun(null);
    setError(null);
    api<RiskReportRun>(`/api/risk/runs/${riskRunId}`)
      .then((result) => { if (!cancelled) setRun(result); })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [open, riskRunId]);

  return (
    <Modal
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title={riskRunId != null ? `RISK REPORT · RUN #${riskRunId}` : 'RISK REPORT'}
      layoutKey="risk-report"
    >
      <div className="wl-risk-report">
        {error ? (
          <p className="wl-risk-report__error">Could not load risk run: {error}</p>
        ) : !run ? (
          <Skeleton height={220} />
        ) : (
          <>
            <div className="wl-risk-report__meta">
              <span>Portfolio #{run.portfolio_id}</span>
              <span>{run.method}</span>
              <span>{run.status.replaceAll('_', ' ')}</span>
              <span>{formatDateTime(run.created_at)}</span>
            </div>
            <GreeksSummary
              totals={run.metrics.totals ?? null}
              byCurrency={run.metrics.by_currency ?? null}
            />
            <PnlAttribution positions={run.metrics.positions ?? []} />
          </>
        )}
      </div>
    </Modal>
  );
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
