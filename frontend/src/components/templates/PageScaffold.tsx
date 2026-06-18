import React from 'react';
import { PageHeader } from '../PageHeader';
import './PageScaffold.css';

export type PageScaffoldProps = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

export function PageScaffold({ title, chips = [], actions, feedback, children, className = '' }: PageScaffoldProps) {
  return (
    <div className={`wl-scaffold ${className}`.trim()}>
      <PageHeader title={title} chips={chips} action={actions} />
      {feedback && (
        <div className="wl-scaffold__feedback" role="status" aria-live="polite">{feedback}</div>
      )}
      <div className="wl-scaffold__body">{children}</div>
    </div>
  );
}
