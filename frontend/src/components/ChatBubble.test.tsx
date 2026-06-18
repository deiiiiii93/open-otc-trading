import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatBubble } from './ChatBubble';
import type { ChatMessage as ChatMessageType, ToolEvent } from '../types';

const userMsg: ChatMessageType = {
  id: 1,
  role: 'user',
  character: null,
  content: 'Quote a CSI500 snowball.',
  meta: {},
};

const agentMsg: ChatMessageType = {
  id: 2,
  role: 'assistant',
  character: 'trader',
  content: 'Pricing CSI500 snowball at 10.04.',
  meta: {},
};

const agentWithAction: ChatMessageType = {
  id: 3,
  role: 'assistant',
  character: 'trader',
  content: 'Confirm to run risk.',
  meta: {
    pending_actions: [
      {
        id: 'p1',
        tool_name: 'run_batch_pricing',
        label: 'Run risk on Desk-Q2',
        summary: '12 positions, summary method',
        payload: {},
        requires_confirmation: true,
        status: 'pending',
      },
    ],
  },
};

const events: ToolEvent[] = [
  { id: 'r1', name: 'get_positions', status: 'done', duration_ms: 80 },
];

const agentWithEvents: ChatMessageType = {
  id: 4,
  role: 'assistant',
  character: 'trader',
  content: 'Done.',
  meta: { process_events: events },
};

describe('ChatBubble', () => {
  it('renders user role with user alignment class', () => {
    const { container } = render(
      <ChatBubble
        message={userMsg}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(container.querySelector('.wl-chat-bubble--user')).toBeTruthy();
    expect(container.querySelector('.wl-chat-bubble--assistant')).toBeFalsy();
    expect(screen.getByText('Quote a CSI500 snowball.')).toBeInTheDocument();
  });

  it('renders assistant role with assistant alignment class and character header', () => {
    const { container } = render(
      <ChatBubble
        message={agentMsg}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(container.querySelector('.wl-chat-bubble--assistant')).toBeTruthy();
    expect(screen.getByText('Trader')).toBeInTheDocument();
  });

  it('renders ToolTimeline when assistant message has process_events', () => {
    render(
      <ChatBubble
        message={agentWithEvents}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(screen.getByText('Tool use')).toBeInTheDocument();
    expect(screen.getByText(/1 call/)).toBeInTheDocument();
    expect(document.querySelector('.wl-tool-timeline-box')).not.toHaveAttribute('open');
  });

  it('keeps short tool-backed answers in the normal body', () => {
    const { container } = render(
      <ChatBubble
        message={agentWithEvents}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    const body = container.querySelector('.wl-chat-bubble__body:not(.wl-chat-bubble__reasoning-body)');
    expect(body).toHaveTextContent('Done.');
    expect(container.querySelector('.wl-chat-bubble__reasoning')).toBeFalsy();
  });

  it('keeps tool use expanded while an assistant message is streaming', () => {
    render(
      <ChatBubble
        message={agentWithEvents}
        viewMode="compact"
        isStreaming
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(document.querySelector('.wl-tool-timeline-box')).toHaveAttribute('open');
  });

  it('folds verbose process reasoning separately from tool use', () => {
    const verboseReasoning = [
      'The Snowballs portfolio is id=6.',
      'Let me check the query_snowball_ko_from_spot tool.',
      'I need to read the snowball skill first.',
      'Now I have the positions data.',
      'Let me fetch the latest spot prices.',
      'I see the issue and need to pass a compact payload instead.',
      'The sandbox cannot access the filesystem directly.',
      'Let me run the Python analysis again with the compact rows.',
    ].join(' '.repeat(24));

    render(
      <ChatBubble
        message={{
          ...agentWithEvents,
          content: `${verboseReasoning} ${verboseReasoning}`,
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    const reasoning = document.querySelector('.wl-chat-bubble__reasoning');
    expect(reasoning).toBeTruthy();
    expect(reasoning).not.toHaveAttribute('open');
    expect(screen.getByText('Reasoning')).toBeInTheDocument();
  });

  it('keeps report content visible when folding a process preamble', () => {
    const content = [
      'Delegating to risk_manager with the `snowball-risk-explain` skill to assess exposures.',
      'I have 2 of the 3 positions from the risk run. Let me check for the third position.',
      'I now have the full picture. Let me run the risk analytics.',
      'I have all the data I need. Let me now compose the structured risk and hedging report.',
      'The engine\'s delta-only check says net delta is near zero on CSI300, but gamma, vega, CSI500 delta, and near-KO autocall exposure require detailed hedging guidance.',
      'The skill confirms the final answer should return a structured risk snapshot, key exposures, and concrete hedge recommendations for the portfolio.',
      'Let me keep the public report below intact rather than mixing it into the process trace.',
      'Here is the full structured output:',
      '',
      '---',
      '',
      '## Portfolio "X" (id=4) -- Snowball Risk & Hedging Report',
      '',
      '**Risk run:** #20 | Completed 2026-06-04 01:30 UTC',
      '',
      '### (a) Risk Snapshot -- Greeks by Position',
    ].join('\n');

    const { container } = render(
      <ChatBubble
        message={{
          ...agentWithEvents,
          content,
          meta: {
            process_events: [
              ...events,
              { id: 'r2', name: 'read_file', status: 'done', duration_ms: 5 },
              { id: 'r3', name: 'get_latest_risk_run', status: 'done', duration_ms: 25 },
              { id: 'r4', name: 'query_snowball_ko_from_spot', status: 'done', duration_ms: 25 },
              { id: 'r5', name: 'run_python', status: 'done', duration_ms: 100 },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    const body = container.querySelector('.wl-chat-bubble__body:not(.wl-chat-bubble__reasoning-body)');
    const reasoningBody = container.querySelector('.wl-chat-bubble__reasoning-body');

    expect(body).toHaveTextContent('Portfolio "X" (id=4) -- Snowball Risk & Hedging Report');
    expect(body).toHaveTextContent('Risk run: #20');
    expect(reasoningBody).toHaveTextContent('Delegating to risk_manager');
    expect(reasoningBody).not.toHaveTextContent('Portfolio "X" (id=4) -- Snowball Risk & Hedging Report');
  });

  it('renders ActionProposal for pending actions', () => {
    render(
      <ChatBubble
        message={agentWithAction}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(screen.getByText('Run risk on Desk-Q2')).toBeInTheDocument();
  });

  it('marks a confirmed action as running when its confirmation request is in flight', () => {
    render(
      <ChatBubble
        message={{
          ...agentWithAction,
          meta: {
            pending_actions: [
              {
                ...agentWithAction.meta!.pending_actions![0],
                status: 'confirmed',
              },
            ],
          },
        }}
        viewMode="compact"
        confirmingActionIds={new Set(['3:p1'])}
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Running run_batch_pricing');
  });

  it('shows task progress for a confirmed action with a tracked task id', () => {
    render(
      <ChatBubble
        message={{
          ...agentWithAction,
          meta: {
            pending_actions: [
              {
                ...agentWithAction.meta!.pending_actions![0],
                status: 'confirmed',
                task_id: 21,
              },
            ],
          },
        }}
        viewMode="compact"
        taskRunsById={{
          21: {
            id: 21,
            kind: 'risk_run',
            status: 'running',
            progress_current: 20,
            progress_total: 64,
            message: 'Completed 20 positions',
            created_at: '2026-05-27T00:00:00Z',
          },
        }}
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Risk run running');
    expect(screen.getByText('20/64')).toBeInTheDocument();
  });

  it('turns generated html asset paths into openable links', () => {
    render(
      <ChatBubble
        message={{
          id: 8,
          role: 'assistant',
          character: 'trader',
          content: 'Chart ready: /trading_desk/charts/candle_000852_SH.html',
          meta: {
            assets: [{
              id: 'html-1',
              kind: 'html',
              title: 'candle_000852_SH.html',
              path: '/trading_desk/charts/candle_000852_SH.html',
              url: '/api/artifacts/agent/thread-1/trading_desk/charts/candle_000852_SH.html',
            }],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    const link = screen.getByRole('link', { name: '/trading_desk/charts/candle_000852_SH.html' });
    expect(link).toHaveAttribute('href', '/api/artifacts/agent/thread-1/trading_desk/charts/candle_000852_SH.html');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('turns generated csv asset paths into openable links', () => {
    render(
      <ChatBubble
        message={{
          id: 9,
          role: 'assistant',
          character: 'trader',
          content: 'CSV exported: /trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv',
          meta: {
            assets: [{
              id: 'csv-1',
              kind: 'file',
              title: 'snowballs_ko_proximity_2026-05-26.csv',
              path: '/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv',
              url: '/api/artifacts/agent/thread-30/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv',
              mime_type: 'text/csv',
            }],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );

    const link = screen.getByRole('link', {
      name: '/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv',
    });
    expect(link).toHaveAttribute(
      'href',
      '/api/artifacts/agent/thread-30/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv',
    );
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('confirms an action through the callback', async () => {
    const onConfirm = vi.fn();
    render(
      <ChatBubble
        message={agentWithAction}
        viewMode="compact"
        onConfirmAction={onConfirm}
        onDismissAction={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledWith(3, 'p1');
  });

  it('shows the streaming cursor only when isStreaming', () => {
    const { container, rerender } = render(
      <ChatBubble
        message={agentMsg}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(container.querySelector('.wl-chat-bubble__cursor')).toBeFalsy();
    rerender(
      <ChatBubble
        message={agentMsg}
        viewMode="compact"
        isStreaming
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(container.querySelector('.wl-chat-bubble__cursor')).toBeTruthy();
  });

  it('hides pending_actions while streaming', () => {
    render(
      <ChatBubble
        message={agentWithAction}
        viewMode="compact"
        isStreaming
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(screen.queryByText('Run risk on Desk-Q2')).not.toBeInTheDocument();
  });

  it('renders pickable reply options from the latest enabled assistant choice list', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 9,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed with repricing all 104 positions (~47s)?',
            '',
            "- **Yes** -> I'll reprice the book, then run the full risk report end-to-end",
            "- **No** -> I'll proceed with stored data and note the staleness",
          ].join('\n'),
          meta: {},
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    expect(screen.getByText(/Do you want to proceed/i)).toBeInTheDocument();
    expect(screen.getByText(/stored data and note the staleness/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /No.*stored data/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(9, 'No');
  });

  it('renders pickable reply options from icon-prefixed choice lines', async () => {
    const onSelectReplyOption = vi.fn();
    const clipboard = String.fromCodePoint(0x1F4CB);
    const refresh = String.fromCodePoint(0x1F504);
    const page = String.fromCodePoint(0x1F4C4);
    const dash = String.fromCharCode(8212);
    render(
      <ChatBubble
        message={{
          id: 12,
          role: 'assistant',
          character: 'risk_manager',
          content: [
            'You have three options:',
            '',
            `${clipboard} Use what exists ${dash} Compile from stored data.`,
            `${refresh} Fresh risk run first, then report ${dash} Re-run risk on 104 positions.`,
            `${page} Full governance-grade report via create_report ${dash} Clean risk run plus persisted report.`,
            'Which way would you like to go?',
          ].join('\n'),
          meta: {},
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    expect(screen.getByText(/Which way would you like to go/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Fresh risk run first.*104 positions/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(12, 'Fresh risk run first, then report');
  });

  it('renders structured reply_options from meta and leaves content untouched', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 20,
          role: 'assistant',
          character: 'trader',
          content: 'Do you want to reprice the book before the risk run?',
          meta: {
            reply_options: [
              { label: 'Yes' },
              { label: 'No', description: 'Use stored prices' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    expect(screen.getByText(/Do you want to reprice the book/i)).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /suggested replies/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /No.*Use stored prices/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(20, 'No');
  });

  it('ignores stale deepagents reply_options on completed messages without tool trace', () => {
    render(
      <ChatBubble
        message={{
          id: 27,
          role: 'assistant',
          character: 'trader',
          content: 'Position 113 booked.',
          meta: {
            agent_graph: 'deepagents',
            agent_phase: 'completed',
            pending_actions: [],
            reply_options: [
              { label: 'Quote it first', description: 'Price before booking.' },
              { label: 'Book as-is', description: 'Book at stated terms.' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
        replyOptionsEnabled
      />,
    );

    expect(screen.getByText('Position 113 booked.')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /suggested replies/i })).not.toBeInTheDocument();
  });

  it('uses meta.reply_options[].value when present', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 21,
          role: 'assistant',
          character: 'trader',
          content: 'Pick a path.',
          meta: {
            reply_options: [
              {
                label: 'Fresh risk',
                description: 'Re-run risk first, then the report',
                value: 'Yes, re-run risk first, then write the report',
              },
              { label: 'Use stored' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /Fresh risk/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(
      21,
      'Yes, re-run risk first, then write the report',
    );
  });

  it('opens an inline input for reply options that need extra detail', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 25,
          role: 'assistant',
          character: 'orchestrator',
          content: 'Which portfolio should I screen?',
          meta: {
            reply_options: [
              {
                label: 'All portfolios',
                description: 'Screen every portfolio.',
              },
              {
                label: 'Specify a portfolio',
                description: "I'll name a specific portfolio or portfolio ID.",
              },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /Specify a portfolio/i }));
    const input = screen.getByPlaceholderText('Portfolio name or ID');
    await userEvent.type(input, 'Snowballs');
    await userEvent.click(screen.getByRole('button', { name: /^Send$/ }));

    expect(onSelectReplyOption).toHaveBeenCalledWith(25, 'Specify a portfolio: Snowballs');
  });

  it('supports input templates in structured reply option values', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 26,
          role: 'assistant',
          character: 'orchestrator',
          content: 'Which portfolio should I screen?',
          meta: {
            reply_options: [
              { label: 'All portfolios' },
              {
                label: 'Specify a portfolio',
                description: 'Provide a portfolio name.',
                value: 'Use portfolio {input}',
              },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /Specify a portfolio/i }));
    await userEvent.type(screen.getByPlaceholderText('Portfolio name or ID'), '6');
    await userEvent.click(screen.getByRole('button', { name: /^Send$/ }));

    expect(onSelectReplyOption).toHaveBeenCalledWith(26, 'Use portfolio 6');
  });

  it('structured options win over heuristic and content is verbatim', () => {
    render(
      <ChatBubble
        message={{
          id: 22,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed?',
            '',
            '- **Yes** -> ignored bullet',
            '- **No** -> ignored bullet',
          ].join('\n'),
          meta: {
            reply_options: [
              { label: 'A' },
              { label: 'B' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
        replyOptionsEnabled
      />,
    );

    // Buttons reflect structured options, not heuristic ones.
    expect(screen.getByRole('button', { name: /^A$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^B$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Yes$/ })).not.toBeInTheDocument();
    // Content is shown verbatim (heuristic NOT stripping): both ignored bullets remain.
    expect(screen.getAllByText(/ignored bullet/i)).toHaveLength(2);
  });

  it('falls back to heuristic when meta.reply_options is missing', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 23,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed?',
            '',
            '- **Yes** -> Go ahead',
            '- **No** -> Stop',
          ].join('\n'),
          meta: {},
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /^Yes/ }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(23, 'Yes');
  });

  it('suppresses structured reply options when pending actions are present', () => {
    render(
      <ChatBubble
        message={{
          id: 24,
          role: 'assistant',
          character: 'trader',
          content: 'Confirm to run.',
          meta: {
            reply_options: [
              { label: 'Yes' },
              { label: 'No' },
            ],
            pending_actions: [
              {
                id: 'p2',
                tool_name: 'run_batch_pricing',
                label: 'Run risk',
                summary: 'demo',
                payload: {},
                requires_confirmation: true,
                status: 'pending',
              },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
        replyOptionsEnabled
      />,
    );

    expect(screen.queryByRole('group', { name: /suggested replies/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Run risk/i)).toBeInTheDocument(); // ActionProposal still renders
  });

  it('does not render reply option buttons when disabled or when actions are pending', () => {
    const content = [
      'Do you want to proceed?',
      '- **Yes**: Run it',
      '- **No**: Stop here',
    ].join('\n');

    const { rerender } = render(
      <ChatBubble
        message={{ id: 10, role: 'assistant', character: 'trader', content, meta: {} }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
      />,
    );
    expect(screen.queryByRole('group', { name: /suggested replies/i })).not.toBeInTheDocument();

    rerender(
      <ChatBubble
        message={{ ...agentWithAction, content }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
        replyOptionsEnabled
      />,
    );
    expect(screen.queryByRole('group', { name: /suggested replies/i })).not.toBeInTheDocument();
  });

  it('renders a term-collection card from meta.term_form and submits a composed string', () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 77,
          role: 'assistant',
          character: 'trader',
          content: 'Fill in the missing terms to book.',
          meta: {
            term_form: {
              title: 'Finish booking',
              submit_label: 'Review & book',
              fields: [
                { key: 'observation_frequency', label: 'Frequency', type: 'enum',
                  choices: [{ label: 'Monthly', value: 'MONTHLY' }] },
              ],
            },
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /monthly/i }));
    fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
    expect(onSelectReplyOption).toHaveBeenCalledTimes(1);
    expect(onSelectReplyOption.mock.calls[0][0]).toBe(77);
    expect(onSelectReplyOption.mock.calls[0][1]).toContain('"observation_frequency": "MONTHLY"');
  });
});

describe('ChatBubble model chip', () => {
  const channels = [
    {
      name: 'zenmux',
      label: 'Zenmux',
      type: 'zenmux' as const,
      healthy: true,
      models: [
        {
          channel: 'zenmux',
          provider: 'anthropic',
          model: 'anthropic/claude-sonnet-4-6',
          label: 'Claude Sonnet 4.6',
        },
      ],
    },
  ];

  it('shows resolved catalog labels for assistant messages with model_selection', () => {
    render(
      <ChatBubble
        message={{
          id: 1,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
          },
        }}
        viewMode="detailed"
        channels={channels}
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText(/Claude Sonnet 4\.6 · Zenmux/)).toBeInTheDocument();
  });

  it('renders fallback note when model_selection_fallback is true', () => {
    render(
      <ChatBubble
        message={{
          id: 2,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
            model_selection_fallback: true,
          },
        }}
        viewMode="compact"
        channels={channels}
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText(/fell back to default/i)).toBeInTheDocument();
  });

  it('omits chip on user messages', () => {
    render(
      <ChatBubble
        message={{ id: 3, role: 'user', content: 'hello' }}
        viewMode="detailed"
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.queryByTestId('chat-bubble-model-chip')).not.toBeInTheDocument();
  });

  it('falls back to readable model text with missing badge when channel is gone', () => {
    render(
      <ChatBubble
        message={{
          id: 4,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'old-channel', provider: 'old', model: 'old-model' },
          },
        }}
        viewMode="compact"
        channels={channels}
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText('Old Model · Old Channel')).toBeInTheDocument();
    expect(screen.getByText('?')).toBeInTheDocument();
  });

  it('does not show missing badge when only the model id changed inside a known channel', () => {
    render(
      <ChatBubble
        message={{
          id: 5,
          role: 'assistant',
          character: 'trader',
          content: 'hi',
          meta: {
            model_selection: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
          },
        }}
        viewMode="compact"
        channels={channels}
        onConfirmAction={() => {}}
        onDismissAction={() => {}}
      />
    );
    expect(screen.getByText('Claude Sonnet 4.6 · Zenmux')).toBeInTheDocument();
    expect(screen.queryByText('?')).not.toBeInTheDocument();
  });
});
