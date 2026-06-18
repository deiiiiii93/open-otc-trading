import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { SplitLayout } from '../SplitLayout';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  rail: React.ReactNode;
  children: React.ReactNode;
  railWidth?: string;
  collapsible?: boolean;
  resizableRail?: boolean;
  minRailWidth?: number;
  maxRailWidth?: number;
  railLabel?: string;
  railAs?: 'nav' | 'div';
};

export function MasterDetailPage({
  title, chips, actions, feedback, metrics, rail, children, railWidth, collapsible, resizableRail, minRailWidth, maxRailWidth, railLabel, railAs,
}: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      <SplitLayout
        rail={rail}
        railWidth={railWidth}
        collapsible={collapsible}
        resizable={resizableRail}
        minRailWidth={minRailWidth}
        maxRailWidth={maxRailWidth}
        railLabel={railLabel}
        railAs={railAs}
      >
        {children}
      </SplitLayout>
    </PageScaffold>
  );
}
