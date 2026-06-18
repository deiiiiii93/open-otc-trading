import React from 'react';
import './Empty.css';

type Props = {
  message: string;
  symbol?: string;
  action?: React.ReactNode;
  variant?: 'empty' | 'loading' | 'error';
  hint?: React.ReactNode;
  className?: string;
};

export function Empty({ message, symbol = '∅', action, variant = 'empty', hint, className = '' }: Props) {
  const cls = ['wl-empty', variant !== 'empty' ? `wl-empty--${variant}` : '', className]
    .filter(Boolean).join(' ');
  return (
    <div className={cls}>
      {variant !== 'loading' && <div className="wl-empty__symbol">{symbol}</div>}
      <div className="wl-empty__message">{message}</div>
      {hint && <div className="wl-empty__hint">{hint}</div>}
      {action && <div className="wl-empty__action">{action}</div>}
    </div>
  );
}
