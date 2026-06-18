import React, { useId } from 'react';
import './Input.css';

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Input({ label, hint, error, id, className = '', ...rest }: Props) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const inputClass = [
    'wl-input',
    error ? 'wl-input--error' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <div className="wl-field">
      {label && <label className="wl-field__label" htmlFor={inputId}>{label}</label>}
      <input id={inputId} className={inputClass} {...rest} />
      {error
        ? <div className="wl-field__hint wl-field__hint--error">{error}</div>
        : hint && <div className="wl-field__hint">{hint}</div>}
    </div>
  );
}
