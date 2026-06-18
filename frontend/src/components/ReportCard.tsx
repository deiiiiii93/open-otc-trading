import type { ReportJob } from '../types';
import { Badge, type BadgeVariant } from './Badge';
import './ReportCard.css';

type Props = {
  job: ReportJob;
  onOpen: (job: ReportJob) => void;
};

const typeVariant: Record<string, BadgeVariant> = {
  risk: 'info',
  portfolio: 'pos',
  rfq: 'warn',
};

function readTitle(job: ReportJob): string {
  const fromPayload = job.request_payload?.title;
  if (typeof fromPayload === 'string' && fromPayload.length > 0) return fromPayload;
  return `${capitalize(job.report_type)} report #${job.id}`;
}

function capitalize(s: string): string {
  return s.length === 0 ? s : s[0].toUpperCase() + s.slice(1);
}

function formatDate(iso: string): { day: string; time: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: '—', time: '' };
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return { day: `${month}-${day}`, time: `${hh}:${mm}` };
}

export function ReportCard({ job, onOpen }: Props) {
  const title = readTitle(job);
  const date = formatDate(job.created_at);
  const variant = typeVariant[job.report_type] ?? 'ink';

  return (
    <article className="wl-report-card">
      <div className="wl-report-card__date">
        <div className="wl-report-card__date-day">{date.day}</div>
        <div className="wl-report-card__date-time">{date.time}</div>
      </div>
      <button
        type="button"
        className="wl-report-card__body"
        onClick={() => onOpen(job)}
        aria-label={`Open ${title}`}
      >
        <div className="wl-report-card__head">
          <span className="wl-report-card__title">{title}</span>
          <Badge variant={variant}>{job.report_type}</Badge>
        </div>
        <div className="wl-report-card__meta">
          <span className="wl-report-card__id">#{job.id}</span>
          <span className="wl-report-card__status">· {job.status}</span>
          {job.task_id != null && <span className="wl-report-card__status">· task #{job.task_id}</span>}
        </div>
      </button>
    </article>
  );
}
