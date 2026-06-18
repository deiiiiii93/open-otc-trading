import React, { useId } from 'react';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { Button } from './Button';
import { Select } from './Select';
import './TableToolbar.css';

export type TableToolbarProps = {
  search?: { value: string; onChange: (v: string) => void; placeholder?: string };
  filters?: React.ReactNode;
  pager?: {
    page: number; pageSize: number; total: number;
    onPage: (p: number) => void; onPageSize?: (n: number) => void;
    pageSizes?: number[];
  };
  className?: string;
};

export function TableToolbar({ search, filters, pager, className = '' }: TableToolbarProps) {
  const searchId = useId();
  const totalPages = pager ? Math.max(1, Math.ceil(pager.total / pager.pageSize)) : 1;
  const safePage = pager ? Math.min(pager.page, totalPages - 1) : 0;
  const start = pager && pager.total > 0 ? safePage * pager.pageSize + 1 : 0;
  const end = pager ? Math.min(safePage * pager.pageSize + pager.pageSize, pager.total) : 0;

  return (
    <div className={`wl-table-toolbar ${className}`.trim()}>
      <div className="wl-table-toolbar__left">
        {search && (
          <label className="wl-table-toolbar__search" htmlFor={searchId}>
            <Search size={16} aria-hidden="true" />
            <input
              id={searchId}
              type="search"
              value={search.value}
              placeholder={search.placeholder ?? 'Search…'}
              onChange={(e) => search.onChange(e.target.value)}
            />
          </label>
        )}
        {filters}
      </div>
      {pager && (
        <div className="wl-table-toolbar__pager">
          <span className="wl-table-toolbar__pageinfo">{start}-{end} of {pager.total}</span>
          {pager.onPageSize && (
            <Select
              variant="inline"
              label="Rows per page"
              value={String(pager.pageSize)}
              onChange={(v) => pager.onPageSize!(Number(v))}
              options={(pager.pageSizes ?? [10, 25, 50, 100]).map((s) => ({ value: String(s), label: `${s} / page` }))}
            />
          )}
          <Button type="button" variant="ghost" iconOnly aria-label="Previous page"
            disabled={safePage === 0} onClick={() => pager.onPage(Math.max(0, safePage - 1))}>
            <ChevronLeft size={16} aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" iconOnly aria-label="Next page"
            disabled={safePage >= totalPages - 1} onClick={() => pager.onPage(Math.min(totalPages - 1, safePage + 1))}>
            <ChevronRight size={16} aria-hidden="true" />
          </Button>
        </div>
      )}
    </div>
  );
}
