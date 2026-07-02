import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Audit, type AuditProps } from './Audit';
import type { AuditAction } from '../types';

const ROW: AuditAction = {
  id: 1,
  kind: 'execution',
  status: 'ok',
  deny_reason: null,
  tool_name: 'book_position',
  tool_class: 'domain_write',
  tool_call_id: 'c1',
  audit_ref: null,
  mode: 'yolo',
  envelope: null,
  actor: 'desk_user',
  model: null,
  persona: 'trader',
  thread_id: 7,
  workflow_id: null,
  session_id: null,
  task_id: null,
  message_id: null,
  desk_workflow_slug: null,
  args_json: { qty: 1 },
  redacted: false,
  result_preview: null,
  error: null,
  occurred_at: '2026-07-02T10:00:00',
  completed_at: '2026-07-02T10:00:01',
};

function props(overrides: Partial<AuditProps> = {}): AuditProps {
  return {
    items: [ROW],
    total: 1,
    summary: null,
    loading: false,
    error: null,
    search: '',
    statusFilter: '',
    classFilter: '',
    modeFilter: '',
    detail: null,
    onSearch: vi.fn(),
    onStatusFilter: vi.fn(),
    onClassFilter: vi.fn(),
    onModeFilter: vi.fn(),
    onRowClick: vi.fn(),
    onCloseDetail: vi.fn(),
    onLoadMore: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
}

describe('Audit', () => {
  it('renders rows with tool, status and yolo mode badge', () => {
    render(<Audit {...props()} />);
    expect(screen.getByText('book_position')).toBeInTheDocument();
    const okBadges = screen
      .getAllByText('ok')
      .filter((el) => el.classList.contains('wl-badge'));
    expect(okBadges).toHaveLength(1);
    const yoloBadges = screen
      .getAllByText('yolo')
      .filter((el) => el.classList.contains('wl-badge'));
    expect(yoloBadges).toHaveLength(1);
    const classBadges = screen
      .getAllByText('domain_write')
      .filter((el) => el.classList.contains('wl-badge'));
    expect(classBadges).toHaveLength(1);
  });

  it('renders empty state when no records', () => {
    render(<Audit {...props({ items: [], total: 0 })} />);
    expect(screen.getByText('No audit records')).toBeInTheDocument();
  });

  it('renders denied status with deny reason in detail modal', () => {
    render(
      <Audit
        {...props({
          detail: {
            ...ROW,
            status: 'denied',
            deny_reason: 'capability',
            related: [],
          },
        })}
      />,
    );
    expect(screen.getByText('capability')).toBeInTheDocument();
  });

  it('renders detail modal with args and related action chain', () => {
    render(
      <Audit
        {...props({
          detail: {
            ...ROW,
            related: [
              { ...ROW, id: 2, kind: 'hitl_decision', status: 'approved' },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText(/"qty": 1/)).toBeInTheDocument();
    expect(screen.getByText('hitl_decision')).toBeInTheDocument();
    const approvedBadges = screen
      .getAllByText('approved')
      .filter((el) => el.classList.contains('wl-badge'));
    expect(approvedBadges).toHaveLength(1);
  });

  it('shows load-more when items < total', () => {
    render(<Audit {...props({ total: 5 })} />);
    expect(screen.getByText('Load more')).toBeInTheDocument();
  });
});
