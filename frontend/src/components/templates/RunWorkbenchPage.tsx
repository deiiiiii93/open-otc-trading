import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { SplitLayout } from '../SplitLayout';
import './RunWorkbenchPage.css';
import './WorkbenchPage.css';

type Props = {
  title: string;
  chips?: string[];
  /** Run-config controls rendered in the page header (top). */
  runConfig: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  /** Run-history list rendered in the left rail. */
  runHistory: React.ReactNode;
  /** Selected-run detail rendered in the right workspace. */
  runDetail: React.ReactNode;
  railWidth?: string;
};

export function RunWorkbenchPage({
  title,
  chips,
  runConfig,
  feedback,
  metrics,
  runHistory,
  runDetail,
  railWidth,
}: Props) {
  return (
    <PageScaffold className="wl-run-workbench" title={title} chips={chips} actions={runConfig} feedback={feedback}>
      <MetricRows metrics={metrics} />
      <SplitLayout rail={runHistory} railWidth={railWidth} railLabel="Run history">
        {runDetail}
      </SplitLayout>
    </PageScaffold>
  );
}
