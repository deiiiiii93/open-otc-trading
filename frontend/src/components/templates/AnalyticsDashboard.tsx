import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { PanelGrid } from '../PanelGrid';
import './AnalyticsDashboard.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  controls?: React.ReactNode;
  panels: React.ReactNode;
  columns?: 1 | 2 | 3;
  state?: React.ReactNode;
};

export function AnalyticsDashboard({
  title, chips, actions, feedback, metrics, controls, panels, columns = 1, state,
}: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      {controls && <div className="wl-analytics__controls">{controls}</div>}
      {state ?? <PanelGrid columns={columns}>{panels}</PanelGrid>}
    </PageScaffold>
  );
}
