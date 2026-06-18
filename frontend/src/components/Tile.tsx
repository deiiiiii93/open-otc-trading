import React from 'react';
import './Tile.css';

export type TileVariant = 'default' | 'pos' | 'neg';

type Props = {
  label: string;
  value: React.ReactNode;
  delta?: React.ReactNode;
  variant?: TileVariant;
  className?: string;
};

export function Tile({ label, value, delta, variant = 'default', className = '' }: Props) {
  return (
    <div className={`wl-tile wl-tile--${variant} ${className}`.trim()}>
      <div className="wl-tile__label">{label}</div>
      <div className="wl-tile__value" data-autofit>{value}</div>
      {delta && <div className="wl-tile__delta">{delta}</div>}
    </div>
  );
}
