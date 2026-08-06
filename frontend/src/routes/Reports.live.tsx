import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { PageContextReporter, ReportJob, ReportTemplate } from '../types';
import { Reports } from './Reports';
import { Empty } from '../components/Empty';

type Props = {
  onPageContextChange?: PageContextReporter;
};

const ACTIVE_STATUSES = new Set(['queued', 'running']);

export function ReportsLive({ onPageContextChange }: Props) {
  const [jobs, setJobs] = useState<ReportJob[]>([]);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [selectedJob, setSelectedJob] = useState<ReportJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (cancelledRef?: { current: boolean }) => {
    try {
      const [jobList, templateList] = await Promise.all([
        api<ReportJob[]>('/api/reports/jobs'),
        api<ReportTemplate[]>('/api/reports/templates'),
      ]);
      if (cancelledRef?.current) return;
      setJobs(jobList);
      setTemplates(templateList);
      setSelectedJob((current) => current ?? jobList[0] ?? null);
      setError(null);
    } catch (e) {
      if (!cancelledRef?.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!cancelledRef?.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const cancelledRef = { current: false };
    void load(cancelledRef);
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  useEffect(() => {
    if (!jobs.some((job) => ACTIVE_STATUSES.has(job.status))) return undefined;
    const cancelledRef = { current: false };
    const timer = window.setInterval(() => {
      void load(cancelledRef);
    }, 2000);
    return () => {
      cancelledRef.current = true;
      window.clearInterval(timer);
    };
  }, [jobs, load]);

  const onGenerate = useCallback(
    async (slug: string) => {
      const portfolioId = jobs[0]?.request_payload?.portfolio_id ?? 1;
      const job = await api<ReportJob>('/api/reports/generate', {
        method: 'POST',
        body: JSON.stringify({ template_slug: slug, portfolio_id: portfolioId }),
      });
      setSelectedJob(job);
      await load();
    },
    [jobs, load],
  );

  const onValidateTemplate = useCallback(
    (specYaml: string) =>
      api<{ ok: boolean; errors: string[] }>('/api/reports/templates/validate', {
        method: 'POST',
        body: JSON.stringify({ spec_yaml: specYaml }),
      }),
    [],
  );

  const onSaveTemplate = useCallback(
    async (slug: string, specYaml: string) => {
      await api<ReportTemplate>(`/api/reports/templates/${encodeURIComponent(slug)}`, {
        method: 'PUT',
        body: JSON.stringify({ spec_yaml: specYaml }),
      });
      await load();
    },
    [load],
  );

  if (error) {
    return <Empty message={`Could not load reports: ${error}`} />;
  }

  return (
    <Reports
      jobs={jobs}
      templates={templates}
      loading={loading}
      selectedJob={selectedJob}
      onSelectJob={setSelectedJob}
      onGenerate={onGenerate}
      onSaveTemplate={onSaveTemplate}
      onValidateTemplate={onValidateTemplate}
      onPageContextChange={onPageContextChange}
    />
  );
}
