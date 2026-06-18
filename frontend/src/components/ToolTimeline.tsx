import type { ToolEvent } from '../types';
import './ToolTimeline.css';

type Mode = 'compact' | 'detailed';

type Props = {
  events: ToolEvent[] | string[];
  mode: Mode;
  defaultOpen?: boolean;
};

export function ToolTimeline({ events, mode, defaultOpen = true }: Props) {
  if (!events || events.length === 0) return null;

  if (typeof events[0] === 'string') {
    return (
      <details className="wl-tool-timeline-box" open={defaultOpen}>
        <summary className="wl-tool-timeline-box__summary">
          <ToolTimelineSummary
            label="Tool use"
            total={events.length}
            running={0}
            errors={0}
            durationMs={0}
          />
        </summary>
        <ol className="wl-tool-timeline wl-tool-timeline--legacy">
          {(events as string[]).map((s, i) => (
            <li key={`${i}-${s}`}>{s}</li>
          ))}
        </ol>
      </details>
    );
  }

  const typedEvents = events as ToolEvent[];
  const running = typedEvents.filter((ev) => ev.status === 'running').length;
  const errors = typedEvents.filter((ev) => ev.status === 'error').length;
  const durationMs = typedEvents.reduce(
    (sum, ev) => sum + (ev.status === 'running' ? 0 : (ev.duration_ms ?? 0)),
    0,
  );

  return (
    <details className="wl-tool-timeline-box" open={defaultOpen}>
      <summary className="wl-tool-timeline-box__summary">
        <ToolTimelineSummary
          label="Tool use"
          total={typedEvents.length}
          running={running}
          errors={errors}
          durationMs={durationMs}
        />
      </summary>
      <ol className="wl-tool-timeline">
        {typedEvents.map((ev) => (
          <ToolEventRow key={ev.id} event={ev} mode={mode} />
        ))}
      </ol>
    </details>
  );
}

function ToolTimelineSummary({
  label,
  total,
  running,
  errors,
  durationMs,
}: {
  label: string;
  total: number;
  running: number;
  errors: number;
  durationMs: number;
}) {
  return (
    <span className="wl-tool-timeline-box__summary-inner">
      <span className="wl-tool-timeline-box__title">{label}</span>
      <span className="wl-tool-timeline-box__meta">
        {total} {total === 1 ? 'call' : 'calls'}
        {running > 0 && ` · ${running} running`}
        {errors > 0 && ` · ${errors} ${errors === 1 ? 'error' : 'errors'}`}
        {durationMs > 0 && ` · ${formatDuration(durationMs)}`}
      </span>
    </span>
  );
}

function ToolEventRow({ event, mode }: { event: ToolEvent; mode: Mode }) {
  const icon = event.status === 'running' ? '↻' : event.status === 'error' ? '✕' : '✓';
  const summary = (
    <span className="wl-tool-timeline__summary">
      <span className={`wl-tool-timeline__icon wl-tool-timeline__icon--${event.status}`}>
        {icon}
      </span>
      <span className="wl-tool-timeline__name">{event.name}</span>
      {event.status === 'running' ? (
        <span className="wl-tool-timeline__timing">running...</span>
      ) : (
        <span className="wl-tool-timeline__timing">{event.duration_ms ?? 0}ms</span>
      )}
      {event.error && <span className="wl-tool-timeline__error">{event.error}</span>}
    </span>
  );

  if (mode === 'compact' || (event.args == null && event.output == null && !event.error)) {
    return <li data-status={event.status}>{summary}</li>;
  }

  return (
    <li data-status={event.status}>
      <details open>
        <summary>{summary}</summary>
        {event.args != null && (
          <div className="wl-tool-timeline__detail">
            <span className="wl-tool-timeline__detail-label">args</span>
            <pre className="wl-tool-timeline__detail-body">
              {JSON.stringify(event.args, null, 2)}
            </pre>
          </div>
        )}
        {event.output != null && (
          <div className="wl-tool-timeline__detail">
            <span className="wl-tool-timeline__detail-label">-&gt;</span>
            <pre className="wl-tool-timeline__detail-body">
              {JSON.stringify(event.output, null, 2)}
            </pre>
          </div>
        )}
      </details>
    </li>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder > 0 ? `${minutes}m ${remainder}s` : `${minutes}m`;
}
