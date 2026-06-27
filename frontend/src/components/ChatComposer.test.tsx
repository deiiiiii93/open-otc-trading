import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatComposer } from './ChatComposer';
import type { DeskWorkflowSummary } from '../types';

describe('ChatComposer', () => {
  it('renders textarea and send button', () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
  });

  it('calls onSend with current text', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'price snowball');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).toHaveBeenCalledWith('price snowball');
  });

  it('clears input after send', async () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i) as HTMLTextAreaElement;
    await userEvent.type(textarea, 'hello');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(textarea.value).toBe('');
  });

  it('sends on Enter', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, 'price snowball');
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('price snowball');
  });

  it('inserts a newline instead of sending on Shift+Enter', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, 'line one');
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('does not send on Enter while an IME composition is active', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, '雪球');
    // While composing CJK input, Enter confirms a candidate and must not send.
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('does not send on Enter when sending is already in flight', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending />);
    const textarea = screen.getByLabelText(/ask anything/i);
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('disables send when sending=true', () => {
    render(<ChatComposer onSend={() => {}} sending />);
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
  });

  it('does not call onSend when text is empty', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('shows Streaming… when streaming prop is true', () => {
    render(<ChatComposer onSend={() => {}} sending streaming />);
    expect(screen.getByRole('button', { name: /streaming/i })).toBeInTheDocument();
  });

  it('shows a stop button while streaming when stop handler is provided', async () => {
    const onStopStreaming = vi.fn();
    render(<ChatComposer onSend={() => {}} sending streaming onStopStreaming={onStopStreaming} />);

    await userEvent.click(screen.getByRole('button', { name: /stop/i }));

    expect(onStopStreaming).toHaveBeenCalledTimes(1);
  });

  it('renders the three execution modes with AUTO active by default', () => {
    render(
      <ChatComposer
        onSend={() => {}}
        sending={false}
        onChangeMode={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /interactive/i })).toBeInTheDocument();
    const auto = screen.getByRole('button', { name: /^auto$/i });
    const yolo = screen.getByRole('button', { name: /yolo/i });
    expect(auto).toHaveAttribute('aria-pressed', 'true');
    expect(yolo).toHaveAttribute('aria-pressed', 'false');
  });

  it('marks the supplied executionMode as active', () => {
    render(
      <ChatComposer
        onSend={() => {}}
        sending={false}
        executionMode="yolo"
        onChangeMode={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /yolo/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /^auto$/i })).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onChangeMode with the selected mode', async () => {
    const onChangeMode = vi.fn();
    render(
      <ChatComposer
        onSend={() => {}}
        sending={false}
        executionMode="auto"
        onChangeMode={onChangeMode}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /interactive/i }));
    expect(onChangeMode).toHaveBeenCalledWith('interactive');

    await userEvent.click(screen.getByRole('button', { name: /yolo/i }));
    expect(onChangeMode).toHaveBeenCalledWith('yolo');
  });

  it('disables the mode control while streaming', () => {
    render(
      <ChatComposer
        onSend={() => {}}
        sending
        streaming
        executionMode="yolo"
        onChangeMode={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /yolo/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^auto$/i })).toBeDisabled();
  });

  describe('slash-command discovery', () => {
    it('surfaces the built-in /goal command when typing a slash (no workflows needed)', async () => {
      render(<ChatComposer onSend={() => {}} sending={false} />);
      await userEvent.type(screen.getByLabelText(/ask anything/i), '/');
      expect(screen.getByRole('option', { name: /\/goal/ })).toBeInTheDocument();
    });

    it('matches /goal by prefix as you type', async () => {
      render(<ChatComposer onSend={() => {}} sending={false} />);
      await userEvent.type(screen.getByLabelText(/ask anything/i), '/go');
      expect(screen.getByRole('option', { name: /\/goal/ })).toBeInTheDocument();
    });

    it('clicking the /goal option fills the composer with "/goal " instead of sending', async () => {
      const onSend = vi.fn();
      render(<ChatComposer onSend={onSend} sending={false} />);
      const box = screen.getByLabelText(/ask anything/i) as HTMLTextAreaElement;
      await userEvent.type(box, '/goal');
      await userEvent.click(screen.getByRole('option', { name: /\/goal/ }));
      expect(box.value).toBe('/goal ');
      expect(onSend).not.toHaveBeenCalled();
    });

    it('pressing Enter on a bare /goal prompts for the description rather than sending', async () => {
      const onSend = vi.fn();
      render(<ChatComposer onSend={onSend} sending={false} />);
      const box = screen.getByLabelText(/ask anything/i) as HTMLTextAreaElement;
      await userEvent.type(box, '/goal{Enter}');
      expect(onSend).not.toHaveBeenCalled();
      expect(box.value).toBe('/goal ');
    });

    it('sends a fully-typed /goal command through onSend', async () => {
      const onSend = vi.fn();
      render(<ChatComposer onSend={onSend} sending={false} />);
      await userEvent.type(screen.getByLabelText(/ask anything/i), '/goal refresh risk{Enter}');
      expect(onSend).toHaveBeenCalledWith('/goal refresh risk');
    });
  });

  describe('slash-command keyboard navigation', () => {
    const workflow = {
      slug: 'run-risk',
      title: 'Run risk',
      persona: 'risk_manager' as const,
      description: '',
      scope: 'local' as const,
      default_mode: 'auto' as const,
      source: 'seed' as const,
    };

    it('highlights the first option by default when the menu opens', async () => {
      render(
        <ChatComposer
          onSend={() => {}}
          sending={false}
          workflows={[workflow]}
          onLaunchWorkflow={() => {}}
        />,
      );
      await userEvent.type(screen.getByLabelText(/ask anything/i), '/');
      expect(screen.getByRole('option', { name: /\/goal/ })).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByRole('option', { name: /\/run-risk/ })).toHaveAttribute('aria-selected', 'false');
    });

    it('moves the highlight down with ArrowDown', async () => {
      render(
        <ChatComposer
          onSend={() => {}}
          sending={false}
          workflows={[workflow]}
          onLaunchWorkflow={() => {}}
        />,
      );
      const box = screen.getByLabelText(/ask anything/i);
      await userEvent.type(box, '/');
      fireEvent.keyDown(box, { key: 'ArrowDown' });
      expect(screen.getByRole('option', { name: /\/run-risk/ })).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByRole('option', { name: /\/goal/ })).toHaveAttribute('aria-selected', 'false');
    });

    it('wraps the highlight to the last option with ArrowUp from the top', async () => {
      render(
        <ChatComposer
          onSend={() => {}}
          sending={false}
          workflows={[workflow]}
          onLaunchWorkflow={() => {}}
        />,
      );
      const box = screen.getByLabelText(/ask anything/i);
      await userEvent.type(box, '/');
      fireEvent.keyDown(box, { key: 'ArrowUp' });
      expect(screen.getByRole('option', { name: /\/run-risk/ })).toHaveAttribute('aria-selected', 'true');
    });

    it('Enter selects the highlighted option, not the first one', async () => {
      const onLaunchWorkflow = vi.fn();
      const box = renderAndOpen(onLaunchWorkflow, workflow);
      fireEvent.keyDown(box, { key: 'ArrowDown' });
      fireEvent.keyDown(box, { key: 'Enter' });
      expect(onLaunchWorkflow).toHaveBeenCalledWith('run-risk', 'auto');
    });

    it('Tab autocompletes the highlighted slug without launching it', async () => {
      const onLaunchWorkflow = vi.fn();
      const box = renderAndOpen(onLaunchWorkflow, workflow) as HTMLTextAreaElement;
      fireEvent.keyDown(box, { key: 'ArrowDown' });
      fireEvent.keyDown(box, { key: 'Tab' });
      expect(box.value).toBe('/run-risk');
      expect(onLaunchWorkflow).not.toHaveBeenCalled();
    });

    function renderAndOpen(
      onLaunchWorkflow: (slug: string, mode: 'auto' | 'yolo') => void,
      wf: typeof workflow,
    ) {
      render(
        <ChatComposer
          onSend={() => {}}
          sending={false}
          workflows={[wf]}
          onLaunchWorkflow={onLaunchWorkflow}
        />,
      );
      const box = screen.getByLabelText(/ask anything/i);
      fireEvent.change(box, { target: { value: '/' } });
      return box;
    }
  });

  describe('command-token highlighting in the input', () => {
    const workflow = {
      slug: 'run-risk',
      title: 'Run risk',
      persona: 'risk_manager' as const,
      description: '',
      scope: 'local' as const,
      default_mode: 'auto' as const,
      source: 'seed' as const,
    };

    it('wraps a leading built-in command in a highlighted token, even with trailing args', () => {
      const { container } = render(<ChatComposer onSend={() => {}} sending={false} />);
      fireEvent.change(screen.getByLabelText(/ask anything/i), {
        target: { value: '/goal create a risk report for portfolio "Default"' },
      });
      const token = container.querySelector('.wl-composer__cmd-token');
      expect(token?.textContent).toBe('/goal');
    });

    it('highlights a recognized workflow slug typed into the input', () => {
      const { container } = render(
        <ChatComposer
          onSend={() => {}}
          sending={false}
          workflows={[workflow]}
          onLaunchWorkflow={() => {}}
        />,
      );
      fireEvent.change(screen.getByLabelText(/ask anything/i), { target: { value: '/run-risk' } });
      expect(container.querySelector('.wl-composer__cmd-token')?.textContent).toBe('/run-risk');
    });

    it('does not highlight ordinary prose or an unrecognized slash token', () => {
      const { container } = render(<ChatComposer onSend={() => {}} sending={false} />);
      const box = screen.getByLabelText(/ask anything/i);
      fireEvent.change(box, { target: { value: 'price a snowball' } });
      expect(container.querySelector('.wl-composer__cmd-token')).toBeNull();
      fireEvent.change(box, { target: { value: '/notacommand do things' } });
      expect(container.querySelector('.wl-composer__cmd-token')).toBeNull();
    });
  });

  const _wfBase: Omit<DeskWorkflowSummary, 'slug' | 'params'> = {
    title: 'T', persona: 'trader', description: '', scope: 'local',
    default_mode: 'auto', source: 'user',
  };
  const plainWf: DeskWorkflowSummary = { ..._wfBase, slug: 'plain' };
  const paramWf: DeskWorkflowSummary = {
    ..._wfBase, slug: 'needs', params: [{ name: 'p', label: 'P', type: 'string' }],
  };

  it('requests params (not launch) for a parameterized workflow', () => {
    const onLaunch = vi.fn();
    const onRequestParams = vi.fn();
    render(
      <ChatComposer
        onSend={() => {}} sending={false}
        workflows={[paramWf]} onLaunchWorkflow={onLaunch} onRequestParams={onRequestParams}
      />,
    );
    fireEvent.change(screen.getByLabelText('Ask anything'), { target: { value: '/needs' } });
    fireEvent.click(screen.getByRole('option', { name: /\/needs/ }));
    expect(onRequestParams).toHaveBeenCalledWith(paramWf);
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('launches a zero-param workflow directly', () => {
    const onLaunch = vi.fn();
    const onRequestParams = vi.fn();
    render(
      <ChatComposer
        onSend={() => {}} sending={false}
        workflows={[plainWf]} onLaunchWorkflow={onLaunch} onRequestParams={onRequestParams}
      />,
    );
    fireEvent.change(screen.getByLabelText('Ask anything'), { target: { value: '/plain' } });
    fireEvent.click(screen.getByRole('option', { name: /\/plain/ }));
    expect(onLaunch).toHaveBeenCalledWith('plain', 'auto');
    expect(onRequestParams).not.toHaveBeenCalled();
  });
});
