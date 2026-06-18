import React from 'react';
import './PanelGrid.css';

type Props = {
  columns?: 1 | 2 | 3;
  children: React.ReactNode;
  className?: string;
};

export function PanelGrid({ columns = 1, children, className = '' }: Props) {
  return (
    <div className={`wl-panel-grid ${className}`.trim()} style={{ '--panel-cols': String(columns) } as React.CSSProperties}>
      {children}
    </div>
  );
}
