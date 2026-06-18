import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Table } from './Table';

describe('Table', () => {
  type Row = { trade: string; price: number };
  const rows: Row[] = [
    { trade: 'A', price: 1 },
    { trade: 'B', price: 2 },
  ];
  const columns = [
    { key: 'trade', header: 'TRADE' },
    { key: 'price', header: 'PRICE', numeric: true },
  ] as const;

  it('renders headers and rows', () => {
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    expect(screen.getByText('TRADE')).toBeInTheDocument();
    expect(screen.getByText('PRICE')).toBeInTheDocument();
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument();
  });

  it('marks selected row', () => {
    const { container } = render(
      <Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} selectedKey="A" />
    );
    const selected = container.querySelectorAll('.wl-table__row--selected');
    expect(selected.length).toBe(1);
  });

  it('calls onRowClick', async () => {
    const onRowClick = vi.fn();
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} onRowClick={onRowClick} />);
    await userEvent.click(screen.getByText('A'));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  it('exposes semantic table roles', () => {
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    // 1 head row + N body rows
    expect(screen.getAllByRole('row').length).toBe(rows.length + 1);
    expect(screen.getAllByRole('columnheader').length).toBe(columns.length);
    expect(screen.getAllByRole('cell').length).toBe(rows.length * columns.length);
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
