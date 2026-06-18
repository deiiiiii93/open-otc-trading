import React from 'react';
import { PageContextChips } from './PageContextChips';
import './PageHeader.css';

type Props = {
  title: string;
  chips: string[];
  action?: React.ReactNode;
};

export function PageHeader({ title, chips, action }: Props) {
  return (
    <header className="wl-pageheader">
      <div className="wl-pageheader__main">
        <h1 className="wl-pageheader__title">{title}</h1>
        <PageContextChips chips={chips} />
      </div>
      {action && <div className="wl-pageheader__action">{action}</div>}
    </header>
  );
}
