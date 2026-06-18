import React from 'react';
import './Badge.css';

export type BadgeVariant = 'pos' | 'neg' | 'warn' | 'info' | 'ink';

type Props = {
  variant?: BadgeVariant;
  solid?: boolean;
  children: React.ReactNode;
  className?: string;
};

export function Badge({ variant = 'ink', solid = false, children, className = '' }: Props) {
  const classes = [
    'wl-badge',
    `wl-badge--${variant}`,
    solid ? 'wl-badge--solid' : '',
    className,
  ].filter(Boolean).join(' ');
  return <span className={classes}>{children}</span>;
}
