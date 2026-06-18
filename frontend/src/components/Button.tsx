import React from 'react';
import './Button.css';

export type ButtonVariant = 'primary' | 'default' | 'danger' | 'ghost';

type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  iconOnly?: boolean;
};

export function Button({ variant = 'default', iconOnly = false, className = '', children, ...rest }: Props) {
  const classes = [
    'wl-button',
    `wl-button--${variant}`,
    iconOnly ? 'wl-button--icon-only' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}
