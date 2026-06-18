import React from 'react';
import { PageScaffold } from './PageScaffold';
import './ConversationalWorkspace.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  rail?: React.ReactNode;        // left column (e.g. AgentDesk ThreadRail)
  messages: React.ReactNode;
  composer: React.ReactNode;
  contextPane?: React.ReactNode; // right column (e.g. AgentDesk AssetsPane)
};

export function ConversationalWorkspace({ title, chips, actions, rail, messages, composer, contextPane }: Props) {
  const cls = [
    'wl-conversation',
    rail ? 'wl-conversation--rail' : '',
    contextPane ? 'wl-conversation--context' : '',
  ].filter(Boolean).join(' ');
  return (
    <PageScaffold title={title} chips={chips} actions={actions}>
      <div className={cls}>
        {/* Plain wrapper — the rail content (e.g. AgentDesk ThreadRail) owns its
            own landmark/label, so we don't nest a second labelled landmark here. */}
        {rail && <div className="wl-conversation__rail">{rail}</div>}
        <section className="wl-conversation__chat">
          <div className="wl-conversation__messages">{messages}</div>
          <div className="wl-conversation__composer">{composer}</div>
        </section>
        {contextPane && <aside className="wl-conversation__context">{contextPane}</aside>}
      </div>
    </PageScaffold>
  );
}
