/**
 * InstrumentsPager — shared client-side pagination for the Instruments page tables.
 *
 * The page hosts many independent tables (registry, hedge map, candidates,
 * quotes, fetch events, FX rates, assumption defaults, set view); each gets its
 * own usePagination() state plus an <InstrumentsPager /> row under the table.
 *
 * Follows the Positions/Hedging pager idiom: "{start}-{end} of {n}", a
 * rows-per-page select, and ghost chevron buttons. The pager hides itself when
 * the row count can never overflow the smallest page size.
 */

import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '../components/Button';
import { Select } from '../components/Select';
import './InstrumentsPager.css';

export const PAGE_SIZES = [10, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = 25;

export type PaginationState<T> = {
  /** Rows for the current page. */
  pagedRows: T[];
  /** Clamped 0-based page index. */
  page: number;
  pageSize: number;
  totalPages: number;
  total: number;
  setPage: (updater: (current: number) => number) => void;
  setPageSize: (size: number) => void;
};

/**
 * Client-side pagination over an already-filtered row array.
 * `resetKey` — any value derived from the active filters; when it changes the
 * pager snaps back to page 0 ("new query, start from the top").
 */
export function usePagination<T>(rows: T[], resetKey: unknown = null): PaginationState<T> {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [resetKey, pageSize]);

  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pagedRows = useMemo(
    () => rows.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [rows, safePage, pageSize],
  );

  return {
    pagedRows,
    page: safePage,
    pageSize,
    totalPages,
    total: rows.length,
    setPage,
    setPageSize,
  };
}

type Props<T> = {
  pagination: PaginationState<T>;
  /** Short table name for unique aria-labels, e.g. "quotes", "candidates". */
  label: string;
};

export function InstrumentsPager<T>({ pagination, label }: Props<T>) {
  const { page, pageSize, totalPages, total, setPage, setPageSize } = pagination;

  // Tables that can never overflow the smallest page size don't need a pager.
  if (total <= PAGE_SIZES[0]) return null;

  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, total);

  return (
    <div className="wl-ipager">
      <span className="wl-ipager__info">
        {start}-{end} of {total}
      </span>
      <Select
        variant="inline"
        label={`Rows per page (${label})`}
        value={String(pageSize)}
        onChange={(v) => setPageSize(Number(v))}
        options={PAGE_SIZES.map((size) => ({ value: String(size), label: `${size} / page` }))}
      />
      <Button
        type="button"
        variant="ghost"
        iconOnly
        aria-label={`Previous page (${label})`}
        disabled={page === 0}
        onClick={() => setPage((current) => Math.max(0, current - 1))}
      >
        <ChevronLeft size={16} aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        iconOnly
        aria-label={`Next page (${label})`}
        disabled={page >= totalPages - 1}
        onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}
      >
        <ChevronRight size={16} aria-hidden="true" />
      </Button>
    </div>
  );
}
