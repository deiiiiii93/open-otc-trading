import React from 'react';
import './RailItem.css';

type Props = {
  children: React.ReactNode;
  active?: boolean;
  accent?: string; // a token name, e.g. '--info'; sets the always-on left bar color
  layout?: 'stack' | 'row'; // 'stack' = vertical card; 'row' = single line name+meta
  onClick?: () => void;
  className?: string;
};

// RailItem — a selectable row in a RailList. The active row gets is-active +
// aria-current. `accent` shows an always-on category bar (carries PricingParams'
// source-type stripe); `layout` picks the row axis.
export function RailItem({ children, active = false, accent, layout = 'stack', onClick, className = '' }: Props) {
  const style = accent ? ({ '--rail-item-accent': `var(${accent})` } as React.CSSProperties) : undefined;
  return (
    <button
      type="button"
      className={`wl-rail__item wl-rail__item--${layout}${active ? ' is-active' : ''} ${className}`.trim()}
      style={style}
      aria-current={active ? 'true' : undefined}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
