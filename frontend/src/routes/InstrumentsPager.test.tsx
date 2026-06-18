/**
 * Tests for InstrumentsPager + usePagination — shared client-side pagination
 * used by every table on the Instruments page.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { InstrumentsPager, usePagination, DEFAULT_PAGE_SIZE } from './InstrumentsPager';

function Harness({ rows, resetKey = null }: { rows: string[]; resetKey?: unknown }) {
  const pagination = usePagination(rows, resetKey);
  return (
    <div>
      <ul>
        {pagination.pagedRows.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <InstrumentsPager pagination={pagination} label="test" />
    </div>
  );
}

const items = (n: number) => Array.from({ length: n }, (_, i) => `item-${i + 1}`);

describe('usePagination + InstrumentsPager', () => {
  it('shows the first page by default and reports the range', () => {
    render(<Harness rows={items(30)} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(DEFAULT_PAGE_SIZE);
    expect(screen.getByText('1-25 of 30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /previous page \(test\)/i })).toBeDisabled();
  });

  it('next page shows the remaining rows and disables at the end', async () => {
    const user = userEvent.setup();
    render(<Harness rows={items(30)} />);
    await user.click(screen.getByRole('button', { name: /next page \(test\)/i }));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText('item-26')).toBeInTheDocument();
    expect(screen.getByText('26-30 of 30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /next page \(test\)/i })).toBeDisabled();
  });

  it('changing page size resets to the first page', async () => {
    const user = userEvent.setup();
    render(<Harness rows={items(30)} />);
    await user.click(screen.getByRole('button', { name: /next page \(test\)/i }));
    await user.selectOptions(
      screen.getByRole('combobox', { name: /rows per page \(test\)/i }),
      '10',
    );
    expect(screen.getByText('1-10 of 30')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(10);
  });

  it('hides itself when rows can never overflow the smallest page size', () => {
    render(<Harness rows={items(10)} />);
    expect(screen.queryByRole('button', { name: /next page/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(10);
  });

  it('resetKey change snaps back to page 0', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness rows={items(30)} resetKey="a" />);
    await user.click(screen.getByRole('button', { name: /next page \(test\)/i }));
    expect(screen.getByText('26-30 of 30')).toBeInTheDocument();
    rerender(<Harness rows={items(30)} resetKey="b" />);
    expect(screen.getByText('1-25 of 30')).toBeInTheDocument();
  });
});
