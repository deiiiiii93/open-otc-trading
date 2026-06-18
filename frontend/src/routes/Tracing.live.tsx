import { useCallback, useEffect, useState } from 'react';
import {
  fetchRecentTraces,
  fetchThreadTraces,
  fetchTraceRun,
  fetchTraceTree,
} from '../api/client';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';
import { Tracing } from './Tracing';

type Props = {
  threadId: number | null;
};

export function TracingLive({ threadId }: Props) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [runs, setRuns] = useState<TraceRunNode[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<TraceRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeThreadId, setActiveThreadId] = useState<number | null>(threadId);

  useEffect(() => {
    setActiveThreadId(threadId);
  }, [threadId]);

  const selectTrace = useCallback((traceId: string) => {
    setSelectedTraceId(traceId);
    setSelectedRunId(null);
    setRunDetail(null);
    setLoading(true);
    fetchTraceTree(traceId)
      .then((body) => setRuns(body.runs))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (activeThreadId == null) {
      fetchRecentTraces()
        .then((body) => {
          setTraces(body.traces);
          if (body.traces.length > 0) selectTrace(body.traces[0].trace_id);
        })
        .catch(() => setTraces([]));
      return;
    }
    fetchThreadTraces(activeThreadId)
      .then((body) => {
        setTraces(body.traces);
        if (body.traces.length > 0) selectTrace(body.traces[0].trace_id);
      })
      .catch(() => setTraces([]));
  }, [activeThreadId, selectTrace]);

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    fetchTraceRun(runId)
      .then(setRunDetail)
      .catch(() => setRunDetail(null));
  }, []);

  return (
    <Tracing
      threadId={activeThreadId}
      traces={traces}
      selectedTraceId={selectedTraceId}
      onSelectTrace={selectTrace}
      runs={runs}
      selectedRunId={selectedRunId}
      onSelectRun={selectRun}
      runDetail={runDetail}
      loading={loading}
      onThreadChange={setActiveThreadId}
    />
  );
}
