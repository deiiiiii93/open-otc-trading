import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { AuditEvent, PageContextReporter, Portfolio, RFQ } from '../types';
import { RfqApproval } from './RfqApproval';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';
import type { RfqQuoteOverrides } from '../components/RfqQuoteForm';

type Props = {
  onPageContextChange?: PageContextReporter;
};

export function RfqApprovalLive({ onPageContextChange }: Props) {
  const [rfqs, setRfqs] = useState<RFQ[]>([]);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const [list, nextPortfolios, nextAuditEvents] = await Promise.all([
        api<RFQ[]>('/api/internal/rfqs'),
        api<Portfolio[]>('/api/portfolios'),
        api<AuditEvent[]>('/api/audit/events?subject_type=rfq&limit=200'),
      ]);
      setRfqs(list);
      setPortfolios(nextPortfolios);
      setAuditEvents(nextAuditEvents);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, []);

  const handleQuote = async (id: number, overrides: RfqQuoteOverrides) => {
    await api(`/api/internal/rfq/${id}/quote`, {
      method: 'POST',
      body: JSON.stringify({ created_by: 'desk_user', ...overrides }),
    });
    await refresh();
  };

  const handleApprove = async (id: number) => {
    await api(`/api/internal/rfq/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approver: 'trader', comment: 'approved from RFQ blotter' }),
    });
    await refresh();
  };

  const handleReject = async (id: number, reason: string) => {
    await api(`/api/internal/rfq/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ approver: 'trader', comment: reason }),
    });
    await refresh();
  };

  const handleRelease = async (id: number) => {
    await api(`/api/internal/rfq/${id}/release`, {
      method: 'POST',
      body: JSON.stringify({ actor: 'trader' }),
    });
    await refresh();
  };

  const handleAccept = async (id: number) => {
    await api(`/api/internal/rfq/${id}/client-accept`, {
      method: 'POST',
      body: JSON.stringify({ actor: 'client' }),
    });
    await refresh();
  };

  const handleBook = async (id: number, portfolioId: number) => {
    await api(`/api/internal/rfq/${id}/book`, {
      method: 'POST',
      body: JSON.stringify({ portfolio_id: portfolioId, actor: 'trader' }),
    });
    await refresh();
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={300} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load RFQs: ${error}`} />;
  }

  return (
    <RfqApproval
      rfqs={rfqs}
      portfolios={portfolios}
      auditEvents={auditEvents}
      onQuote={handleQuote}
      onApprove={handleApprove}
      onReject={handleReject}
      onRelease={handleRelease}
      onAccept={handleAccept}
      onBook={handleBook}
      onPageContextChange={onPageContextChange}
    />
  );
}
