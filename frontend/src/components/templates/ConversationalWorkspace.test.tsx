import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConversationalWorkspace } from './ConversationalWorkspace';

describe('ConversationalWorkspace', () => {
  it('renders rail, messages, composer, and context pane (3-column)', () => {
    const { container } = render(
      <ConversationalWorkspace
        title="AGENT DESK"
        rail={<div>thread-rail</div>}
        messages={<div>thread</div>}
        composer={<div>composer</div>}
        contextPane={<div>ctx</div>}
      />,
    );
    expect(screen.getByText('thread-rail')).toBeInTheDocument();
    expect(screen.getByText('thread')).toBeInTheDocument();
    expect(screen.getByText('composer')).toBeInTheDocument();
    expect(screen.getByText('ctx')).toBeInTheDocument();
    expect(container.querySelector('.wl-conversation--rail.wl-conversation--context')).not.toBeNull();
  });

  it('omits the rail and context regions when not provided', () => {
    const { container } = render(
      <ConversationalWorkspace title="X" messages={<div>m</div>} composer={<div>c</div>} />,
    );
    expect(container.querySelector('.wl-conversation__rail')).toBeNull();
    expect(container.querySelector('.wl-conversation__context')).toBeNull();
  });
});
