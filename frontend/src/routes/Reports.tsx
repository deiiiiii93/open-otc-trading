import { useMemo, useState } from 'react';
import type { PageContext, PageContextReporter, ReportJob } from '../types';
import { DataTablePage } from '../components/templates';
import { ReportTimeline } from '../components/ReportTimeline';
import { ReportReader } from '../components/ReportReader';
import { Skeleton } from '../components/Skeleton';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import './Reports.css';

type Props = {
  jobs: ReportJob[];
  loading: boolean;
  onPageContextChange?: PageContextReporter;
};

export function Reports({ jobs, loading, onPageContextChange }: Props) {
  const [openJob, setOpenJob] = useState<ReportJob | null>(null);
  const isOpen = openJob != null;

  const chips: string[] = [];
  const activeCount = jobs.filter((job) => job.status === 'queued' || job.status === 'running').length;
  if (loading) chips.push('Loading…');
  else chips.push(`${jobs.length} reports`);
  if (activeCount) chips.push(`${activeCount} active`);
  chips.push('All types');
  const pageContext = useMemo(() => {
    const base: PageContext = {
      route: 'reports',
      title: 'Reports',
      path: '/',
      entity_ids: { report_job_id: openJob?.id ?? null },
      snapshot: {
        report_count: jobs.length,
        jobs: jobs.slice(0, 12).map((job) => ({
          id: job.id,
          report_type: job.report_type,
          status: job.status,
          created_at: job.created_at,
          artifact_paths: job.artifact_paths,
        })),
      },
      chips,
    };
    if (!openJob) return base;
    return {
      ...base,
      title: 'Report Reader dialog',
      snapshot: {
        parent_context: base,
        report_job: openJob,
      },
      chips: ['dialog', `Report #${openJob.id}`, openJob.report_type],
    };
  }, [chips, jobs, openJob]);
  usePageContextReporter(pageContext, onPageContextChange);

  return (
    <DataTablePage
      title="REPORTS"
      chips={chips}
      body={
        <div className="wl-reports">
          {loading ? (
            <div className="wl-reports__loading">
              <Skeleton height={48} />
              <Skeleton height={48} />
              <Skeleton height={48} />
            </div>
          ) : (
            <ReportTimeline jobs={jobs} onOpen={setOpenJob} />
          )}
        </div>
      }
      overlays={
        <ReportReader
          open={isOpen}
          job={openJob}
          onOpenChange={(open) => { if (!open) setOpenJob(null); }}
        />
      }
    />
  );
}
