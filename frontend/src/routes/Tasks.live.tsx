import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { PageContextReporter, Route, TaskRun } from '../types';
import { Tasks } from './Tasks';

const ACTIVE_STATUSES = new Set(['queued', 'running']);

type Props = {
  onPageContextChange?: PageContextReporter;
  onNavigate?: (route: Route) => void;
};

export function TasksLive({ onPageContextChange, onNavigate }: Props) {
  const [tasks, setTasks] = useState<TaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTasks = useCallback(async (cancelledRef?: { current: boolean }) => {
    try {
      const list = await api<TaskRun[]>('/api/tasks');
      if (cancelledRef?.current) return;
      setTasks(list);
      setError(null);
    } catch (err) {
      if (!cancelledRef?.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (!cancelledRef?.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const cancelledRef = { current: false };
    void loadTasks(cancelledRef);
    return () => { cancelledRef.current = true; };
  }, [loadTasks]);

  useEffect(() => {
    if (!tasks.some((task) => ACTIVE_STATUSES.has(task.status))) return undefined;
    const cancelledRef = { current: false };
    const timer = window.setInterval(() => {
      void loadTasks(cancelledRef);
    }, 2000);
    return () => {
      cancelledRef.current = true;
      window.clearInterval(timer);
    };
  }, [loadTasks, tasks]);

  return (
    <Tasks
      tasks={tasks}
      loading={loading}
      error={error}
      onPageContextChange={onPageContextChange}
      onOpenGreeksLandscape={() => onNavigate?.('greeks-landscape')}
    />
  );
}
