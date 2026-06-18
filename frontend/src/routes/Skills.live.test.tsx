import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SkillsLive } from './Skills.live';

const catalog = {
  domains: ['market-data'],
  workflows: [
    {
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
      name: 'fetch-market-data',
      domain: 'market-data',
      frontmatter: { name: 'fetch-market-data' },
      frontmatter_error: null,
      lint: [],
      body_tokens: 42,
    },
  ],
  references: [],
  meta: [],
};

const skillFile = {
  ...catalog.workflows[0],
  frontmatter: {
    name: 'fetch-market-data',
    description: 'Fetch market snapshots.',
    domain: 'market-data',
    workflow_type: 'read',
    allowed_envelopes: ['desk_workflow'],
    may_escalate_to: [],
    required_context: [],
    optional_context: [],
    write_actions: false,
    confirmation_required: false,
    success_criteria: ['done'],
    routing: [],
  },
  content: '---\nname: fetch-market-data\n---\n\n## Body',
  body: '## Body',
};

function stubFetch() {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } });
    if (url === '/api/skills/catalog') return json(catalog);
    if (url.endsWith('/SKILL.md') && method === 'GET') return json(skillFile);
    if (url.endsWith('/SKILL.md') && method === 'PUT') {
      return json({ saved: true, reloaded: true, reload_error: null, lint: [] });
    }
    if (url === '/api/skills/validate') {
      return json({ issues: [], body_tokens: 42, blocking: false });
    }
    if (url === '/api/skills/reload') return json({ reloaded: true, error: null });
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock, calls };
}

afterEach(() => { vi.restoreAllMocks(); });

describe('SkillsLive', () => {
  it('loads the catalog and shows the tree', async () => {
    stubFetch();
    render(<SkillsLive />);
    expect(await screen.findByText('fetch-market-data')).toBeInTheDocument();
  });

  it('selecting a skill loads the file and saving PUTs it', async () => {
    const { calls } = stubFetch();
    render(<SkillsLive />);
    await userEvent.click(await screen.findByText('fetch-market-data'));
    await screen.findByDisplayValue('Fetch market snapshots.');
    await userEvent.click(screen.getByRole('button', { name: 'Save & reload agent' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'PUT' && c.url.includes('fetch-market-data'))).toBe(true);
    });
    expect(await screen.findByText(/Saved · agent reloaded/)).toBeInTheDocument();
  });

  it('reload button posts to the reload endpoint', async () => {
    const { calls } = stubFetch();
    render(<SkillsLive />);
    await screen.findByText('fetch-market-data');
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/skills/reload' && c.method === 'POST')).toBe(true);
    });
  });
});
