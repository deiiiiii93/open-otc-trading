import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { SplitLayout } from '../SplitLayout';
import './WorkbenchPage.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  config: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  results: React.ReactNode;
  railWidth?: string;
};

export function WorkbenchPage({ title, chips, actions, feedback, config, metrics, results, railWidth }: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <SplitLayout rail={config} railWidth={railWidth} railLabel="Configuration">
        <div className="wl-workbench__results">
          <MetricRows metrics={metrics} />
          {results}
        </div>
      </SplitLayout>
    </PageScaffold>
  );
}
