import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageScaffold } from './PageScaffold';

describe('PageScaffold', () => {
  it('renders the header title, chips, actions, feedback, and body', () => {
    render(
      <PageScaffold title="RISK" chips={['c1']} actions={<button>Run</button>} feedback={<div>saved</div>}>
        <div>body</div>
      </PageScaffold>,
    );
    expect(screen.getByText('RISK')).toBeInTheDocument();
    expect(screen.getByText('c1')).toBeInTheDocument();
    expect(screen.getByText('Run')).toBeInTheDocument();
    expect(screen.getByText('saved')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('omits the feedback region when no feedback is given', () => {
    const { container } = render(<PageScaffold title="X"><div /></PageScaffold>);
    expect(container.querySelector('.wl-scaffold__feedback')).toBeNull();
  });
});
