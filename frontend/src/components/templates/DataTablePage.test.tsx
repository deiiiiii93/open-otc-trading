import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DataTablePage } from './DataTablePage';
import type { Column } from '../Table';

type Row = { id: number; name: string };
const columns: Column<Row>[] = [{ key: 'name', header: 'NAME' }];

describe('DataTablePage', () => {
  it('renders header, metrics, table rows, and overlays', () => {
    render(
      <DataTablePage<Row>
        title="POSITIONS"
        metrics={[{ label: 'NAV', value: '10' }]}
        table={{ columns, rows: [{ id: 1, name: 'alpha' }], rowKey: (r) => r.id }}
        overlays={<div>my-modal</div>}
      />,
    );
    expect(screen.getByText('POSITIONS')).toBeInTheDocument();
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('my-modal')).toBeInTheDocument();
  });

  it('shows the empty slot when there are no rows', () => {
    render(
      <DataTablePage<Row>
        title="X" table={{ columns, rows: [], rowKey: (r) => r.id }}
        empty={<div>nothing here</div>}
      />,
    );
    expect(screen.getByText('nothing here')).toBeInTheDocument();
  });

  it('renders two metric rows when metrics is an array of arrays', () => {
    const { container } = render(
      <DataTablePage<Row>
        title="X"
        metrics={[[{ label: 'A', value: '1' }], [{ label: 'B', value: '2' }]]}
        table={{ columns, rows: [{ id: 1, name: 'a' }], rowKey: (r) => r.id }}
      />,
    );
    expect(container.querySelectorAll('.wl-metric-row')).toHaveLength(2);
  });

  it('renders the body slot for list pages that have no Table', () => {
    render(<DataTablePage title="REPORTS" body={<div>timeline-list</div>} />);
    expect(screen.getByText('timeline-list')).toBeInTheDocument();
  });
});
