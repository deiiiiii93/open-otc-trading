import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { TableToolbar, type TableToolbarProps } from '../TableToolbar';
import { Table, type Column } from '../Table';
import './DataTablePage.css';

type Props<T> = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  toolbar?: TableToolbarProps;
  // Provide EITHER `table` (renders the Table primitive) OR `body` (escape hatch
  // for list pages whose body is not a <Table>, e.g. Reports' <ReportTimeline>).
  table?: {
    columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number;
    selectedKey?: string | number | null; onRowClick?: (r: T) => void;
    className?: string;
  };
  body?: React.ReactNode;
  mobileCards?: React.ReactNode;
  empty?: React.ReactNode;
  overlays?: React.ReactNode;
};

export function DataTablePage<T>({
  title, chips, actions, feedback, metrics, toolbar, table, body, mobileCards, empty, overlays,
}: Props<T>) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      {toolbar && <TableToolbar {...toolbar} />}
      <div className="wl-data-table-page__main">
        {table && (
          <Table<T>
            columns={table.columns}
            rows={table.rows}
            rowKey={table.rowKey}
            selectedKey={table.selectedKey}
            onRowClick={table.onRowClick}
            className={table.className}
          />
        )}
        {body}
        {mobileCards}
        {table && table.rows.length === 0 && empty}
      </div>
      {overlays}
    </PageScaffold>
  );
}
