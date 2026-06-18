import { Fragment, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, CircleDashed } from 'lucide-react';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';
import { MasterDetailPage } from '../components/templates';
import { RailList } from '../components/RailList';
import { RailItem } from '../components/RailItem';
import { Empty } from '../components/Empty';
import './Tracing.css';

type Props = {
  threadId: number | null;
  traces: TraceSummary[];
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  runs: TraceRunNode[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  runDetail: TraceRunDetail | null;
  loading: boolean;
  onThreadChange?: (threadId: number | null) => void;
};

function statusIcon(status: TraceSummary['status']) {
  if (status === 'error') return <AlertCircle size={14} aria-label="error" />;
  if (status === 'running') return <CircleDashed size={14} aria-label="running" />;
  return <CheckCircle2 size={14} aria-label="success" />;
}

function durationMs(start: string, end: string | null): string {
  if (!end) return '—';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function depthOf(run: TraceRunNode): number {
  return run.dotted_order.split('.').length - 1;
}

function JsonBlock({ raw, label }: { raw: string | null; label: string }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!raw) return null;

  let parsed: unknown;
  let isJson = false;
  try {
    parsed = JSON.parse(raw);
    isJson = true;
  } catch {
    parsed = raw;
  }

  return (
    <div className="wl-tracing__payload">
      <button
        type="button"
        className="wl-tracing__payload-head"
        onClick={() => setCollapsed((v) => !v)}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span className="wl-tracing__eyebrow">{label}</span>
        {isJson && <span className="wl-tracing__payload-badge">JSON</span>}
      </button>
      {!collapsed && (
        <pre className="wl-tracing__payload-body">
          {isJson ? <JsonValue val={parsed} depth={0} /> : String(parsed)}
        </pre>
      )}
    </div>
  );
}

function JsonValue({ val, depth }: { val: unknown; depth: number }) {
  if (val === null) return <span className="wl-json-null">null</span>;
  if (typeof val === 'boolean') return <span className="wl-json-bool">{String(val)}</span>;
  if (typeof val === 'number') return <span className="wl-json-number">{String(val)}</span>;
  if (typeof val === 'string') return <span className="wl-json-string">"{val}"</span>;

  if (Array.isArray(val)) {
    if (val.length === 0) return <span className="wl-json-punct">[]</span>;
    return (
      <>
        <span className="wl-json-punct">[</span>
        <span className="wl-json-indent">
          {val.map((item, i) => (
            <Fragment key={i}>
              <JsonValue val={item} depth={depth + 1} />
              {i < val.length - 1 && <span className="wl-json-punct">,</span>}
              {'\n'}{' '.repeat((depth + 1) * 2)}
            </Fragment>
          ))}
        </span>
        <span className="wl-json-punct">]</span>
      </>
    );
  }

  if (typeof val === 'object') {
    const entries = Object.entries(val as Record<string, unknown>);
    if (entries.length === 0) return <span className="wl-json-punct">{'{}'}</span>;
    return (
      <>
        <span className="wl-json-punct">{'{'}</span>
        <span className="wl-json-indent">
          {entries.map(([k, v], i) => (
            <Fragment key={k}>
              <span className="wl-json-key">"{k}"</span>
              <span className="wl-json-punct">: </span>
              <JsonValue val={v} depth={depth + 1} />
              {i < entries.length - 1 && <span className="wl-json-punct">,</span>}
              {'\n'}{' '.repeat((depth + 1) * 2)}
            </Fragment>
          ))}
        </span>
        <span className="wl-json-punct">{'}'}</span>
      </>
    );
  }

  return <>{String(val)}</>;
}

export function Tracing({
  threadId, traces, selectedTraceId, onSelectTrace,
  runs, selectedRunId, onSelectRun, runDetail, loading,
  onThreadChange,
}: Props) {
  const orderedRuns = useMemo(
    () => [...runs].sort((a, b) => a.dotted_order.localeCompare(b.dotted_order)),
    [runs],
  );

  const rail = (
    <RailList scroll className="wl-tracing__list">
        <div className="wl-tracing__head">
          <div className="wl-tracing__eyebrow">Traces</div>
          {threadId != null ? (
            <span className="wl-tracing__filter-chip">
              Thread #{threadId}
              {onThreadChange && (
                <button
                  className="wl-tracing__clear-thread"
                  onClick={() => onThreadChange(null)}
                  aria-label="Clear thread filter"
                >
                  ×
                </button>
              )}
            </span>
          ) : onThreadChange && (
            <input
              className="wl-tracing__thread-input"
              type="number"
              placeholder="Thread ID…"
              aria-label="Filter by thread ID"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const val = parseInt(e.currentTarget.value, 10);
                  if (!isNaN(val)) onThreadChange(val);
                }
              }}
            />
          )}
        </div>
        {traces.length === 0 ? (
          <Empty message="No traces recorded yet — run an agent turn." />
        ) : (
          traces.map((trace) => (
            <RailItem
              key={trace.id}
              layout="row"
              className="wl-tracing__trace-card"
              active={trace.id === selectedTraceId}
              onClick={() => onSelectTrace(trace.trace_id)}
            >
              <span className="wl-tracing__status">{statusIcon(trace.status)}</span>
              <span className="wl-tracing__trace-name wl-rail__title">{trace.name}</span>
              <span className="wl-tracing__meta wl-rail__meta">
                {new Date(trace.start_time).toLocaleString()} · {durationMs(trace.start_time, trace.end_time)}
                {trace.total_tokens != null ? ` · ${trace.total_tokens} tok` : ''}
              </span>
            </RailItem>
          ))
        )}
    </RailList>
  );

  return (
    <MasterDetailPage title="TRACING" rail={rail} railLabel="Traces">
      <div className="wl-tracing__workspace">
      <div className="wl-tracing__tree" aria-label="Span tree">
        {loading ? (
          <Empty variant="loading" message="Loading…" />
        ) : orderedRuns.length === 0 ? (
          <Empty message="Select a trace to inspect its spans." />
        ) : (
          orderedRuns.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`wl-tracing__span${run.id === selectedRunId ? ' is-active' : ''}`}
              style={{ ['--wl-span-depth' as string]: depthOf(run) }}
              onClick={() => onSelectRun(run.id)}
            >
              <span className="wl-tracing__status">{statusIcon(run.status)}</span>
              <span className={`wl-tracing__span-type wl-tracing__span-type--${run.run_type}`}>
                {run.run_type}
              </span>
              <span className="wl-tracing__span-name">{run.name}</span>
              <span className="wl-tracing__meta">
                {durationMs(run.start_time, run.end_time)}
                {run.total_tokens != null ? ` · ${run.total_tokens} tok` : ''}
              </span>
            </button>
          ))
        )}
      </div>

      <aside className="wl-tracing__detail" aria-label="Span detail">
        {runDetail === null ? (
          <Empty message="Select a span to see its payloads." />
        ) : (
          <>
            <div className="wl-tracing__detail-head">
              <span className="wl-tracing__detail-name">{runDetail.name}</span>
              <span className={`wl-tracing__detail-status wl-tracing__detail-status--${runDetail.status}`}>
                {runDetail.status}
              </span>
            </div>
            <div className="wl-tracing__detail-meta">
              <span>type: <b>{runDetail.run_type}</b></span>
              <span>id: <code>{runDetail.id.slice(0, 24)}…</code></span>
              {runDetail.parent_run_id && (
                <span>parent: <code>{runDetail.parent_run_id.slice(0, 24)}…</code></span>
              )}
              <span>duration: <b>{durationMs(runDetail.start_time, runDetail.end_time)}</b></span>
              {runDetail.total_tokens != null && (
                <span>tokens: <b>{runDetail.total_tokens}</b> ({runDetail.prompt_tokens ?? '?'} → {runDetail.completion_tokens ?? '?'})</span>
              )}
              <span>start: <code>{new Date(runDetail.start_time).toLocaleTimeString()}</code></span>
            </div>
            {runDetail.error && (
              <div className="wl-tracing__error" role="alert">
                <div className="wl-tracing__error-label">Error</div>
                <pre className="wl-tracing__error-body">{runDetail.error}</pre>
              </div>
            )}
            <JsonBlock raw={runDetail.inputs} label="Inputs" />
            <JsonBlock raw={runDetail.outputs} label="Outputs" />
          </>
        )}
      </aside>
      </div>
    </MasterDetailPage>
  );
}
