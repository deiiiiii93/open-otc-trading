import React from 'react';
import './HeaderControls.css';

type Props = {
  children: React.ReactNode;
  // 'end' (default) right-aligns the controls — right for a page-header action row.
  // 'start' left-aligns — right for an in-body controls strip.
  justify?: 'start' | 'end';
  className?: string;
};

// HeaderControls — a single row of filter pickers + action buttons that all share
// one control height, so a Select sitting next to a Button never disagrees on size.
// Replaces the per-page `--{page}-action-height` blocks that drifted apart.
export function HeaderControls({ children, justify = 'end', className = '' }: Props) {
  return (
    <div className={`wl-header-controls wl-header-controls--${justify} ${className}`.trim()}>
      {children}
    </div>
  );
}
