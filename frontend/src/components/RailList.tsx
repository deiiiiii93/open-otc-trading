import React from 'react';
import './RailList.css';

type Props = {
  children: React.ReactNode;
  scroll?: boolean; // adds overflow-y:auto + min-height:0 (Skills, Tracing)
  className?: string;
};

// RailList — the one bordered container for a left-rail selection list. Rows are
// RailItems; header content (search, filter pills, eyebrow) is any non-item child
// and sits inside the border above the rows.
export function RailList({ children, scroll = false, className = '' }: Props) {
  return (
    <div className={`wl-rail${scroll ? ' wl-rail--scroll' : ''} ${className}`.trim()}>
      {children}
    </div>
  );
}
