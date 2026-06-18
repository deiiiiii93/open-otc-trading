import React from 'react';
import './Table.css';

export type Column<T> = {
  key: string;
  header: React.ReactNode;
  render?: (row: T) => React.ReactNode;
  numeric?: boolean;
  width?: string;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  selectedKey?: string | number | null;
  onRowClick?: (row: T) => void;
  className?: string;
};

export function Table<T>({
  columns, rows, rowKey, selectedKey, onRowClick, className = '',
}: Props<T>) {
  const gridTemplate = columns.map((c) => c.width ?? '1fr').join(' ');
  return (
    <div className={`wl-table ${className}`.trim()} role="table">
      <div
        className="wl-table__row wl-table__row--head"
        role="row"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        {columns.map((c) => (
          <div
            key={c.key}
            role="columnheader"
            className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
          >
            {c.header}
          </div>
        ))}
      </div>
      {rows.map((row) => {
        const key = rowKey(row);
        const isSelected = selectedKey === key;
        return (
          <div
            key={key}
            className={`wl-table__row ${isSelected ? 'wl-table__row--selected' : ''}`}
            role="row"
            style={{ gridTemplateColumns: gridTemplate }}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            data-clickable={onRowClick ? 'true' : undefined}
          >
            {columns.map((c) => (
              <div
                key={c.key}
                role="cell"
                className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
              >
                {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
