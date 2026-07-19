import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import * as client from '../api/client';
import type {
  AuditAction,
  AuditActionDetail,
  AuditSummary,
} from '../types';
import { AuditLive } from './Audit.live';

const TARGET_REF = 'limit:incident:81:waived';

function action(
  id: number,
  auditRef: string | null,
  overrides: Partial<AuditAction> = {},
): AuditAction {
  return {
    id,
    kind: 'execution',
    status: 'ok',
    deny_reason: null,
    tool_name: `limit_action_${id}`,
    tool_class: 'domain_write',
    tool_call_id: `call-${id}`,
    audit_ref: auditRef,
    mode: 'interactive',
    envelope: 'interactive',
    actor: 'risk_user',
    model: null,
    persona: 'limit-manager',
    thread_id: null,
    workflow_id: null,
    session_id: null,
    task_id: null,
    message_id: null,
    desk_workflow_slug: null,
    args_json: { incident_id: 81 },
    redacted: false,
    result_preview: null,
    error: null,
    occurred_at: '2026-07-18T09:00:00Z',
    completed_at: '2026-07-18T09:00:01Z',
    ...overrides,
  };
}

function detail(row: AuditAction, marker: string): AuditActionDetail {
  return { ...row, result_preview: marker, related: [] };
}

const SUMMARY: AuditSummary = {
  by_status: { ok: 1 },
  by_class: { domain_write: 1 },
  by_mode: { interactive: 1 },
  fail_closed_refusals: { persisted: 0, unpersisted: 0 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.history.replaceState(
    null,
    '',
    `/audit?audit_ref=${encodeURIComponent(TARGET_REF)}`,
  );
  vi.spyOn(client, 'fetchAuditSummary').mockResolvedValue(SUMMARY);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/audit');
});

describe('AuditLive deep links', () => {
  it('filters by audit_ref and automatically opens the exact first match', async () => {
    const row = action(7, TARGET_REF);
    vi.spyOn(client, 'listAuditActions').mockResolvedValue({
      items: [row],
      total: 1,
    });
    vi.spyOn(client, 'getAuditAction').mockResolvedValue(
      detail(row, 'exact audit detail'),
    );

    render(<AuditLive />);

    await waitFor(() => {
      expect(screen.getByText('exact audit detail')).toBeInTheDocument();
    });
    expect(client.listAuditActions).toHaveBeenCalledWith(expect.objectContaining({
      audit_ref: TARGET_REF,
      limit: 25,
      offset: 0,
    }));
    expect(client.getAuditAction).toHaveBeenCalledWith(7);
  });

  it('does not open a row when a response does not exactly match the reference', async () => {
    vi.spyOn(client, 'listAuditActions').mockResolvedValue({
      items: [action(8, 'different:audit:ref')],
      total: 1,
    });
    const getSpy = vi.spyOn(client, 'getAuditAction');

    render(<AuditLive />);

    await waitFor(() => {
      expect(client.listAuditActions).toHaveBeenCalled();
    });
    expect(getSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('ignores a stale response after Back or Forward changes audit_ref', async () => {
    const stale = deferred<{ items: AuditAction[]; total: number }>();
    const nextRef = 'limit:incident:82:resolved';
    const staleRow = action(9, TARGET_REF);
    const nextRow = action(10, nextRef);
    vi.spyOn(client, 'listAuditActions').mockImplementation((params) => {
      if (params?.audit_ref === TARGET_REF) return stale.promise;
      return Promise.resolve({ items: [nextRow], total: 1 });
    });
    const getSpy = vi.spyOn(client, 'getAuditAction').mockImplementation(
      async (id) => detail(id === nextRow.id ? nextRow : staleRow, `detail-${id}`),
    );

    render(<AuditLive />);
    await waitFor(() => {
      expect(client.listAuditActions).toHaveBeenCalledWith(
        expect.objectContaining({ audit_ref: TARGET_REF }),
      );
    });

    act(() => {
      window.history.pushState(
        null,
        '',
        `/audit?audit_ref=${encodeURIComponent(nextRef)}`,
      );
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    await waitFor(() => {
      expect(screen.getByText('detail-10')).toBeInTheDocument();
    });

    await act(async () => {
      stale.resolve({ items: [staleRow], total: 1 });
      await stale.promise;
    });

    expect(getSpy).not.toHaveBeenCalledWith(staleRow.id);
    expect(screen.getByText('detail-10')).toBeInTheDocument();
  });
});
