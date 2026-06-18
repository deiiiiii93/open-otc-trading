import { useMemo, useState } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import type {
  PageContext,
  PageContextReporter,
  SkillCatalog,
  SkillFile,
  SkillFileSummary,
  SkillFrontmatter,
  SkillTier,
  SkillValidateResult,
} from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { SplitLayout } from '../components/SplitLayout';
import { PageToolbar, PageToolbarSpacer, PageToolbarSearch } from '../components/PageToolbar';
import { RailItem } from '../components/RailItem';
import { Button } from '../components/Button';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Empty } from '../components/Empty';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { SkillsWorkflowForm, type WorkflowDraft } from './SkillsWorkflowForm';
import './Skills.css';

export type SkillSelection = { tier: SkillTier; path: string };
export type SkillTab = 'workflows' | 'references' | 'meta';

type Props = {
  catalog: SkillCatalog | null;
  loading: boolean;
  selected: SkillSelection | null;
  file: SkillFile | null;
  validation: SkillValidateResult | null;
  saving: boolean;
  reloadStatus: string;
  saveStatus: string | null;
  onSelect: (selection: SkillSelection) => void;
  onDraftChange: (draft: WorkflowDraft) => void;
  onSaveWorkflow: (draft: WorkflowDraft) => void;
  onSaveRaw: (selection: SkillSelection, content: string) => void;
  onCreate: (draft: WorkflowDraft) => void;
  onDelete: (selection: SkillSelection, name: string) => void;
  onReload: () => void;
  onPageContextChange?: PageContextReporter;
};

const CREATE_TEMPLATE_BODY =
  '## When to use\n\n- \n\n## Procedure\n\n1. \n\n## Example\n\nUser: \nAssistant: \n';

const TABS: { id: SkillTab; label: string }[] = [
  { id: 'workflows', label: 'Workflows' },
  { id: 'references', label: 'References' },
  { id: 'meta', label: 'Meta' },
];

function blankDraft(domain: string): WorkflowDraft {
  return {
    frontmatter: {
      name: '',
      description: '',
      domain,
      workflow_type: 'read',
      allowed_envelopes: ['desk_workflow'],
      may_escalate_to: [],
      required_context: [],
      optional_context: [],
      write_actions: false,
      confirmation_required: false,
      success_criteria: [],
      routing: [],
    },
    body: CREATE_TEMPLATE_BODY,
  };
}

function lintBadgeVariant(entry: SkillFileSummary): BadgeVariant {
  const errors = entry.lint.filter((issue) => issue.severity === 'error').length;
  if (errors) return 'neg';
  if (entry.lint.length > 0) return 'warn';
  return 'pos';
}

function lintBadgeLabel(entry: SkillFileSummary): string {
  const errors = entry.lint.filter((issue) => issue.severity === 'error').length;
  const warnings = entry.lint.length - errors;
  if (errors) return `${errors} err`;
  if (warnings) return `${warnings} warn`;
  return 'clean';
}

// Skill names are kebab-case but users type natural phrases ("scenario test"),
// so matching folds away separators and also searches descriptions.
function foldFilterText(value: string): string {
  return value.toLowerCase().replace(/[\s_-]+/g, '');
}

function matchesFilter(entry: SkillFileSummary, filter: string): boolean {
  const needle = foldFilterText(filter);
  if (!needle) return true;
  if (foldFilterText(entry.name).includes(needle)) return true;
  const description = entry.frontmatter?.description;
  return typeof description === 'string' && foldFilterText(description).includes(needle);
}

export function Skills({
  catalog, loading, selected, file, validation, saving, reloadStatus, saveStatus,
  onSelect, onDraftChange, onSaveWorkflow, onSaveRaw, onCreate, onDelete, onReload,
  onPageContextChange,
}: Props) {
  const [activeTab, setActiveTab] = useState<SkillTab>(selected?.tier ?? 'workflows');
  const [filter, setFilter] = useState('');
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  const [draftPath, setDraftPath] = useState<string | null>(null);
  const [rawText, setRawText] = useState<string | null>(null);

  const workflowCount = catalog?.workflows.length ?? 0;
  const referenceCount = catalog?.references.length ?? 0;
  const metaCount = catalog?.meta.length ?? 0;

  const pageContext = useMemo<PageContext>(() => ({
    route: 'skills',
    title: 'Skills',
    path: '/',
    entity_ids: { skill_path: selected?.path ?? null },
    snapshot: {
      workflow_count: workflowCount,
      selected: selected?.path ?? null,
    },
    chips: ['Skills', `${workflowCount} workflows`],
  }), [workflowCount, selected]);
  usePageContextReporter(pageContext, onPageContextChange);

  const isWorkflowFile = file != null && file.tier === 'workflows' && file.frontmatter != null;
  if (file && file.path !== draftPath) {
    setDraftPath(file.path);
    setRawText(file.content);
    setCreating(false);
    setDraft(
      file.tier === 'workflows' && file.frontmatter
        ? { frontmatter: file.frontmatter as unknown as SkillFrontmatter, body: file.body ?? '' }
        : null,
    );
  }

  const matches = (entry: SkillFileSummary) => matchesFilter(entry, filter);

  const workflowsByDomain = useMemo(() => {
    const grouped = new Map<string, SkillFileSummary[]>();
    for (const entry of catalog?.workflows ?? []) {
      if (matchesFilter(entry, filter)) {
        const domain = entry.domain ?? 'unknown';
        grouped.set(domain, [...(grouped.get(domain) ?? []), entry]);
      }
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [catalog, filter]);

  const changeDraft = (next: WorkflowDraft) => {
    setDraft(next);
    onDraftChange(next);
  };

  const startCreate = () => {
    setCreating(true);
    const initial = blankDraft(catalog?.domains[0] ?? '');
    setDraft(initial);
    setDraftPath(null);
    onDraftChange(initial);
  };

  const handleTabChange = (tab: SkillTab) => {
    setActiveTab(tab);
    if (tab !== 'workflows') {
      setCreating(false);
    }
  };

  const renderEntry = (entry: SkillFileSummary) => (
    <RailItem
      key={`${entry.tier}:${entry.path}`}
      layout="row"
      className="wl-skills__entry"
      active={selected?.path === entry.path}
      onClick={() => {
        setCreating(false);
        setDraftPath(null);
        onSelect({ tier: entry.tier, path: entry.path });
      }}
    >
      <span className="wl-skills__entry-name wl-rail__title">{entry.name}</span>
      <Badge variant={lintBadgeVariant(entry)}>{lintBadgeLabel(entry)}</Badge>
    </RailItem>
  );

  const errorCount = useMemo(() => {
    let count = 0;
    for (const entry of catalog?.workflows ?? []) {
      count += entry.lint.filter((issue) => issue.severity === 'error').length;
    }
    for (const entry of catalog?.references ?? []) {
      count += entry.lint.filter((issue) => issue.severity === 'error').length;
    }
    for (const entry of catalog?.meta ?? []) {
      count += entry.lint.filter((issue) => issue.severity === 'error').length;
    }
    return count;
  }, [catalog]);

  const tabCount = (id: SkillTab) =>
    id === 'workflows' ? workflowCount : id === 'references' ? referenceCount : metaCount;

  const renderWorkflowList = () => (
    <div className="wl-skills__list">
      {workflowsByDomain.length === 0 ? (
        <Empty message="No workflows match this filter." symbol="∅" />
      ) : (
        workflowsByDomain.map(([domain, entries]) => (
          <div key={domain} className="wl-skills__domain">
            <span className="wl-skills__domain-name">{domain}</span>
            {entries.map(renderEntry)}
          </div>
        ))
      )}
    </div>
  );

  const renderReferenceList = () => (
    <div className="wl-skills__list">
      {(catalog?.references ?? []).filter(matches).map(renderEntry)}
    </div>
  );

  const renderMetaList = () => (
    <div className="wl-skills__list">
      {(catalog?.meta ?? []).filter(matches).map(renderEntry)}
    </div>
  );

  const renderEditor = () => {
    if (loading) return <Empty message="Loading catalog…" symbol="◎" />;
    if (creating && draft) {
      return (
        <SkillsWorkflowForm
          draft={draft}
          domains={catalog?.domains ?? []}
          mode="create"
          issues={validation?.issues ?? []}
          bodyTokens={validation?.body_tokens ?? null}
          saving={saving}
          onChange={changeDraft}
          onSave={() => onCreate(draft)}
        />
      );
    }
    if (selected?.tier !== activeTab || !file) {
      return <Empty message="Select a skill to view or edit it." symbol="◈" />;
    }
    if (isWorkflowFile && draft) {
      return (
        <SkillsWorkflowForm
          draft={draft}
          domains={catalog?.domains ?? []}
          mode="edit"
          issues={validation?.issues ?? file!.lint}
          bodyTokens={validation?.body_tokens ?? file!.body_tokens}
          saving={saving}
          onChange={changeDraft}
          onSave={() => onSaveWorkflow(draft)}
          onDelete={() => onDelete(selected!, file!.name)}
        />
      );
    }
    if (rawText != null) {
      return (
        <div className="wl-skills__raw">
          <span className="wl-skills__label">
            {file.tier}/{file.path} (raw editor — schema differs from workflows)
          </span>
          <textarea
            aria-label="raw content"
            rows={24}
            className="wl-skills__body"
            value={rawText}
            onChange={(event) => setRawText(event.currentTarget.value)}
          />
          {(validation?.issues ?? file.lint).map((issue, index) => (
            <p key={index} className={`wl-skills__issue wl-skills__issue--${issue.severity}`}>
              {issue.severity === 'error' ? '✕' : '⚠'} {issue.code}: {issue.message}
            </p>
          ))}
          <div className="wl-skills__actions">
            <Button type="button" disabled={saving} onClick={() => onSaveRaw(selected!, rawText)}>
              Save & reload agent
            </Button>
          </div>
        </div>
      );
    }
    return <Empty message="Select a skill to view or edit it." symbol="◈" />;
  };

  const renderTabContent = () => {
    const list =
      activeTab === 'workflows'
        ? renderWorkflowList()
        : activeTab === 'references'
          ? renderReferenceList()
          : renderMetaList();
    return (
      <SplitLayout rail={list} railLabel={TABS.find((t) => t.id === activeTab)?.label ?? 'Skills'}>
        <section className="wl-skills__editor">{renderEditor()}</section>
      </SplitLayout>
    );
  };

  const feedbackNode = saveStatus ? (
    <p className="wl-skills__save-status">{saveStatus}</p>
  ) : undefined;

  return (
    <PageScaffold title="Skills" chips={pageContext.chips} feedback={feedbackNode}>
      <Tabs value={activeTab} onValueChange={(v: string) => handleTabChange(v as SkillTab)}>
        <TabsList aria-label="Skills tabs">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
              <span className="wl-skills__tab-count">{tabCount(tab.id)}</span>
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <PageToolbar>
        {activeTab === 'workflows' && (
          <Button type="button" variant="ghost" onClick={startCreate}>
            <Plus size={14} aria-hidden="true" />
            <span>New</span>
          </Button>
        )}
        <Button type="button" onClick={onReload}>
          <RefreshCw size={16} aria-hidden="true" />
          <span>Reload</span>
        </Button>
        {errorCount > 0 && (
          <span className="wl-skills__lint-chip" aria-live="polite">
            {errorCount} lint error{errorCount === 1 ? '' : 's'}
          </span>
        )}
        <PageToolbarSpacer />
        <PageToolbarSearch
          value={filter}
          onChange={setFilter}
          placeholder="Filter skills…"
          aria-label="Filter skills"
        />
      </PageToolbar>
      {renderTabContent()}
    </PageScaffold>
  );
}
