import { Modal } from './Modal';
import { Badge, type BadgeVariant } from './Badge';
import type { ReportJob } from '../types';
import './ReportReader.css';

type Props = {
  open: boolean;
  job: ReportJob | null;
  onOpenChange: (open: boolean) => void;
};

const typeVariant: Record<string, BadgeVariant> = {
  risk: 'info',
  portfolio: 'pos',
  rfq: 'warn',
};

function artifactHref(pathValue: string): string | null {
  const trimmed = pathValue.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed) || trimmed.startsWith('/api/artifacts/')) {
    return trimmed;
  }

  const normalized = trimmed.replace(/\\/g, '/');
  const artifactMarker = '/artifacts/';
  const markerIndex = normalized.lastIndexOf(artifactMarker);
  const relativePath = markerIndex >= 0
    ? normalized.slice(markerIndex + artifactMarker.length)
    : normalized.replace(/^\.\//, '').replace(/^\/+/, '').replace(/^artifacts\//, '');

  if (!relativePath || relativePath.split('/').includes('..')) return null;
  return `/api/artifacts/${relativePath.split('/').map(encodeURIComponent).join('/')}`;
}

export function ReportReader({ open, job, onOpenChange }: Props) {
  if (!job) {
    return (
      <Modal open={open} onOpenChange={onOpenChange} title="Report" layoutKey="report-reader">
        <div />
      </Modal>
    );
  }

  const artifacts = Object.entries(job.artifact_paths ?? {}).filter(([, value]) => typeof value === 'string');
  const resultJson = JSON.stringify(job.result_payload ?? {}, null, 2);

  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Report #${job.id}`} layoutKey="report-reader">
      <div className="wl-reader">
        <header className="wl-reader__head">
          <span className="wl-reader__type">
            <Badge variant={typeVariant[job.report_type] ?? 'ink'}>{job.report_type}</Badge>
          </span>
          <span className="wl-reader__status">{job.status}</span>
        </header>

        {artifacts.length > 0 && (
          <section className="wl-reader__section">
            <h3 className="wl-reader__section-title">Artifacts</h3>
            <ul className="wl-reader__artifacts">
              {artifacts.map(([key, value]) => (
                <li key={key} className="wl-reader__artifact">
                  <span className="wl-reader__artifact-kind">{key}</span>
                  {artifactHref(String(value)) ? (
                    <a
                      className="wl-reader__artifact-path"
                      href={artifactHref(String(value)) ?? undefined}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {String(value)}
                    </a>
                  ) : (
                    <span className="wl-reader__artifact-path">{String(value)}</span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="wl-reader__section">
          <h3 className="wl-reader__section-title">Result</h3>
          <pre className="wl-reader__result">{resultJson}</pre>
        </section>
      </div>
    </Modal>
  );
}
