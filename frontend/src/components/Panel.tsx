import React from 'react';
import './Panel.css';

type Props = {
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

export function Panel({ title, meta, children, className = '' }: Props) {
  return (
    <section className={`wl-panel ${className}`.trim()}>
      <header className="wl-panel__head">
        <span className="wl-panel__title">{title}</span>
        {meta && <span className="wl-panel__meta">{meta}</span>}
      </header>
      <div className="wl-panel__body">{children}</div>
    </section>
  );
}
