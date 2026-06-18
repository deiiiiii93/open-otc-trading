import React from 'react';
import './Chip.css';

type Props = {
  children: React.ReactNode;
  onRemove?: () => void;
  onClick?: () => void;
  className?: string;
};

export function Chip({ children, onRemove, onClick, className = '' }: Props) {
  return (
    <span
      className={`wl-chip ${onClick ? 'wl-chip--clickable' : ''} ${className}`.trim()}
      onClick={onClick}
    >
      <span className="wl-chip__label">{children}</span>
      {onRemove && (
        <button
          type="button"
          className="wl-chip__remove"
          aria-label="Remove"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
        >
          ×
        </button>
      )}
    </span>
  );
}
