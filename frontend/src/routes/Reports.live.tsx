import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { PageContextReporter, ReportJob } from '../types';
import { Reports } from './Reports';
import { Empty } from '../components/Empty';

type Props = {
  onPageContextChange?: PageContextReporter;
};

const ACTIVE_STATUSES = new Set(['queued', 'running']);

export function ReportsLive({ onPageContextChange }: Props) {
  const [jobs, setJobs] = useState<ReportJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadReports = useCallback(async (cancelledRef?: { current: boolean }) => {
    try {
      const list = await api<ReportJob[]>('/api/reports/jobs');
      if (cancelledRef?.current) return;
      setJobs(list);
      setError(null);
    } catch (e) {
      if (!cancelledRef?.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!cancelledRef?.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const cancelledRef = { current: false };
    void loadReports(cancelledRef);
    return () => { cancelledRef.current = true; };
  }, [loadReports]);

  useEffect(() => {
    if (!jobs.some((job) => ACTIVE_STATUSES.has(job.status))) return undefined;
    const cancelledRef = { current: false };
    const timer = window.setInterval(() => {
      void loadReports(cancelledRef);
    }, 2000);
    return () => {
      cancelledRef.current = true;
      window.clearInterval(timer);
    };
  }, [jobs, loadReports]);

  if (error) {
    return <Empty message={`Could not load reports: ${error}`} />;
  }

  return <Reports jobs={jobs} loading={loading} onPageContextChange={onPageContextChange} />;
}
