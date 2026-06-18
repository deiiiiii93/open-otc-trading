import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TableToolbar } from './TableToolbar';

describe('TableToolbar', () => {
  it('fires onChange when typing in search', () => {
    const onChange = vi.fn();
    render(<TableToolbar search={{ value: '', onChange, placeholder: 'Search…' }} />);
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'abc' } });
    expect(onChange).toHaveBeenCalledWith('abc');
  });

  it('renders the filters slot and pager info', () => {
    render(
      <TableToolbar
        filters={<button>All</button>}
        pager={{ page: 0, pageSize: 25, total: 60, onPage: () => {} }}
      />,
    );
    expect(screen.getByText('All')).toBeInTheDocument();
    expect(screen.getByText('1-25 of 60')).toBeInTheDocument();
  });

  it('advances the page when Next is clicked', () => {
    const onPage = vi.fn();
    render(<TableToolbar pager={{ page: 0, pageSize: 25, total: 60, onPage }} />);
    fireEvent.click(screen.getByLabelText('Next page'));
    expect(onPage).toHaveBeenCalledWith(1);
  });

  it('disables Next on the last page', () => {
    render(<TableToolbar pager={{ page: 2, pageSize: 25, total: 60, onPage: () => {} }} />);
    expect(screen.getByLabelText('Next page')).toBeDisabled();
  });
});
