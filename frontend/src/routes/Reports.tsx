import { useMemo, useState } from 'react';
import type {
  PageContext,
  PageContextReporter,
  ReportDocument,
  ReportJob,
  ReportTemplate,
} from '../types';
import { MasterDetailPage } from '../components/templates';
import { ReportDocumentView } from '../components/reports/ReportDocumentView';
import { TemplateEditor } from '../components/reports/TemplateEditor';
import { Badge } from '../components/Badge';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import './Reports.css';

type Props = {
  jobs: ReportJob[];
  templates: ReportTemplate[];
  loading: boolean;
  selectedJob: ReportJob | null;
  onSelectJob: (job: ReportJob) => void;
  onGenerate: (slug: string) => void;
  onSaveTemplate: (slug: string, specYaml: string) => Promise<void>;
  onValidateTemplate: (specYaml: string) => Promise<{ ok: boolean; errors: string[] }>;
  onPageContextChange?: PageContextReporter;
};

type Tab = 'reports' | 'templates';

function jobTitle(job: ReportJob): string {
  const fromDoc = (job.result_payload as Partial<ReportDocument> | undefined)?.template?.title;
  if (fromDoc) return fromDoc;
  const fromRequest = job.request_payload?.title;
  return typeof fromRequest === 'string' && fromRequest ? fromRequest : `Report #${job.id}`;
}

/** A templated report carries a slug AND a sections array. Legacy jobs have
 *  neither, and still render — their payload is shown raw rather than lost. */
function isTemplated(job: ReportJob): boolean {
  return Boolean(job.template_slug) && Boolean((job.result_payload as any)?.sections);
}

export function Reports({
  jobs,
  templates,
  loading,
  selectedJob,
  onSelectJob,
  onGenerate,
  onSaveTemplate,
  onValidateTemplate,
  onPageContextChange,
}: Props) {
  const [tab, setTab] = useState<Tab>('reports');
  const [selectedTemplate, setSelectedTemplate] = useState<ReportTemplate | null>(null);

  const chips = useMemo(
    () => [loading ? 'Loading…' : `${jobs.length} reports`, `${templates.length} templates`],
    [loading, jobs.length, templates.length],
  );

  const pageContext = useMemo<PageContext>(
    () => ({
      route: 'reports',
      title: 'Reports',
      path: '/reports',
      entity_ids: { report_job_id: selectedJob?.id ?? null },
      snapshot: {
        report_count: jobs.length,
        templates: templates.map((template) => template.slug),
        selected: selectedJob
          ? { id: selectedJob.id, template_slug: selectedJob.template_slug ?? null }
          : null,
      },
      chips,
    }),
    [chips, jobs, templates, selectedJob],
  );
  usePageContextReporter(pageContext, onPageContextChange);

  const rail = (
    <div className="wl-reports__rail">
      <div className="wl-reports__tabs" role="tablist">
        {(['reports', 'templates'] as Tab[]).map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            aria-selected={tab === name}
            className={`wl-reports__tab ${tab === name ? 'wl-reports__tab--active' : ''}`.trim()}
            onClick={() => setTab(name)}
          >
            {name === 'reports' ? 'Reports' : 'Templates'}
          </button>
        ))}
      </div>

      {tab === 'reports' ? (
        <ul className="wl-reports__list">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                type="button"
                className={`wl-reports__item ${
                  selectedJob?.id === job.id ? 'wl-reports__item--active' : ''
                }`.trim()}
                onClick={() => onSelectJob(job)}
              >
                <span className="wl-reports__item-title">{jobTitle(job)}</span>
                <span className="wl-reports__item-meta">
                  #{job.id} · {job.created_at.slice(5, 16).replace('T', ' ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="wl-reports__list">
          {templates.map((template) => (
            <li key={template.slug}>
              <button
                type="button"
                className={`wl-reports__item ${
                  selectedTemplate?.slug === template.slug ? 'wl-reports__item--active' : ''
                }`.trim()}
                onClick={() => setSelectedTemplate(template)}
              >
                <span className="wl-reports__item-title">{template.slug}</span>
                <span className="wl-reports__item-meta">
                  <Badge variant="ink">{template.persona}</Badge>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  let body: React.ReactNode;
  if (loading) {
    body = (
      <div className="wl-reports__loading">
        <Skeleton height={48} />
        <Skeleton height={48} />
        <Skeleton height={48} />
      </div>
    );
  } else if (tab === 'templates') {
    body = selectedTemplate ? (
      <TemplateEditor
        template={selectedTemplate}
        onSave={onSaveTemplate}
        onValidate={onValidateTemplate}
      />
    ) : (
      <div className="wl-reports__generate">
        <Empty
          message="Select a template to view its spec, or generate a report from one."
          symbol="◫"
        />
        <div className="wl-reports__generate-actions">
          {templates.map((template) => (
            <button
              key={template.slug}
              type="button"
              className="wl-reports__generate-btn"
              onClick={() => onGenerate(template.slug)}
            >
              Generate {template.title}
            </button>
          ))}
        </div>
      </div>
    );
  } else if (!selectedJob) {
    body = <Empty message="No reports yet — generate one from a template." symbol="◌" />;
  } else if (isTemplated(selectedJob)) {
    body = (
      <ReportDocumentView document={selectedJob.result_payload as unknown as ReportDocument} />
    );
  } else {
    body = (
      <pre className="wl-reports__legacy" data-testid="legacy-payload">
        {JSON.stringify(selectedJob.result_payload ?? {}, null, 2)}
      </pre>
    );
  }

  return (
    <MasterDetailPage title="REPORTS" chips={chips} rail={rail} railWidth="18rem">
      {body}
    </MasterDetailPage>
  );
}
