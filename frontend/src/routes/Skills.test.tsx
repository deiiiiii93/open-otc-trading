import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Skills } from './Skills';
import type { SkillCatalog, SkillFile } from '../types';

const catalog: SkillCatalog = {
  domains: ['market-data', 'risk'],
  workflows: [
    {
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
      name: 'fetch-market-data',
      domain: 'market-data',
      frontmatter: {
        name: 'fetch-market-data',
        description: 'Pull spot and vol snapshots for an underlying.',
      },
      frontmatter_error: null,
      lint: [],
      body_tokens: 42,
    },
    {
      tier: 'workflows',
      path: 'risk/run-risk/SKILL.md',
      name: 'run-risk',
      domain: 'risk',
      frontmatter: { name: 'run-risk' },
      frontmatter_error: null,
      lint: [{ code: 'missing_example', message: 'm', detail: '', severity: 'warning' }],
      body_tokens: 10,
    },
  ],
  references: [],
  meta: [
    {
      tier: 'meta',
      path: 'clarification-policy.md',
      name: 'clarification-policy',
      domain: null,
      frontmatter: { name: 'clarification-policy' },
      frontmatter_error: null,
      lint: [],
      body_tokens: null,
    },
  ],
};

const metaFile: SkillFile = {
  ...catalog.meta[0],
  content: '---\nname: clarification-policy\n---\n\n## Clarification\n\nAsk.',
  body: '## Clarification\n\nAsk.',
};

const baseProps = {
  catalog,
  loading: false,
  selected: null,
  file: null,
  validation: null,
  saving: false,
  reloadStatus: 'agent in sync',
  saveStatus: null,
  onSelect: vi.fn(),
  onDraftChange: vi.fn(),
  onSaveWorkflow: vi.fn(),
  onSaveRaw: vi.fn(),
  onCreate: vi.fn(),
  onDelete: vi.fn(),
  onReload: vi.fn(),
};

describe('Skills', () => {
  it('renders tree groups with lint badges', () => {
    render(<Skills {...baseProps} />);
    expect(screen.getByText('Workflows')).toBeInTheDocument();
    expect(screen.getByText('Meta')).toBeInTheDocument();
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.getByText('run-risk')).toBeInTheDocument();
    // run-risk carries a warning badge
    expect(screen.getByText('1 warn')).toBeInTheDocument();
  });

  it('filters the tree', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.type(screen.getByPlaceholderText('Filter skills…'), 'fetch');
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.queryByText('run-risk')).not.toBeInTheDocument();
  });

  it('filter treats spaces and hyphens as interchangeable', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.type(screen.getByPlaceholderText('Filter skills…'), 'market data');
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.queryByText('run-risk')).not.toBeInTheDocument();
  });

  it('filter matches frontmatter descriptions', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.type(screen.getByPlaceholderText('Filter skills…'), 'vol snapshots');
    expect(screen.getByText('fetch-market-data')).toBeInTheDocument();
    expect(screen.queryByText('run-risk')).not.toBeInTheDocument();
  });

  it('fires onSelect when an entry is clicked', async () => {
    const onSelect = vi.fn();
    render(<Skills {...baseProps} onSelect={onSelect} />);
    await userEvent.click(screen.getByText('fetch-market-data'));
    expect(onSelect).toHaveBeenCalledWith({
      tier: 'workflows',
      path: 'market-data/fetch-market-data/SKILL.md',
    });
  });

  it('renders a raw editor for meta files and saves through onSaveRaw', async () => {
    const onSaveRaw = vi.fn();
    render(
      <Skills
        {...baseProps}
        selected={{ tier: 'meta', path: 'clarification-policy.md' }}
        file={metaFile}
        onSaveRaw={onSaveRaw}
      />,
    );
    const editor = screen.getByLabelText('raw content');
    expect(editor).toHaveValue(metaFile.content);
    await userEvent.click(screen.getByRole('button', { name: /Save/ }));
    expect(onSaveRaw).toHaveBeenCalled();
  });

  it('opens a blank create form from the New button', async () => {
    render(<Skills {...baseProps} />);
    await userEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('button', { name: 'Create & reload agent' })).toBeInTheDocument();
  });

  it('reload button fires onReload', async () => {
    const onReload = vi.fn();
    render(<Skills {...baseProps} onReload={onReload} />);
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(onReload).toHaveBeenCalled();
  });
});
