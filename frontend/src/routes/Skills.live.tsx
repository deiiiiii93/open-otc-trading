import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createWorkflowSkill,
  deleteWorkflowSkill,
  getSkillFile,
  listSkillsCatalog,
  reloadSkills,
  saveRawSkillFile,
  saveWorkflowSkill,
  validateRawSkillFile,
  validateWorkflowSkill,
} from '../api/client';
import type {
  PageContextReporter,
  SkillCatalog,
  SkillFile,
  SkillValidateResult,
} from '../types';
import { Skills, type SkillSelection } from './Skills';
import type { WorkflowDraft } from './SkillsWorkflowForm';

type Props = { onPageContextChange?: PageContextReporter };

const VALIDATE_DEBOUNCE_MS = 500;

export function SkillsLive({ onPageContextChange }: Props) {
  const [catalog, setCatalog] = useState<SkillCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SkillSelection | null>(null);
  const [file, setFile] = useState<SkillFile | null>(null);
  const [validation, setValidation] = useState<SkillValidateResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [reloadStatus, setReloadStatus] = useState('agent in sync');
  const debounceRef = useRef<number | null>(null);
  const selectSeq = useRef(0);
  const validateSeq = useRef(0);

  const refreshCatalog = useCallback(async () => {
    try {
      setCatalog(await listSkillsCatalog());
    } catch (error) {
      setSaveStatus(`Could not load catalog: ${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refreshCatalog(); }, [refreshCatalog]);

  const handleSelect = useCallback(async (selection: SkillSelection) => {
    // Rapid re-selection races the fetches; only the latest selection's
    // response may install a file, or the editor could save the previously
    // selected skill's content onto the highlighted path.
    const seq = ++selectSeq.current;
    // Cancel any pending draft validation — it belongs to the previous selection.
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    setSelected(selection);
    setValidation(null);
    setSaveStatus(null);
    // Clear the current file first so the page's derived-state reset always
    // fires when the (possibly same-path) file lands.
    setFile(null);
    try {
      const loaded = await getSkillFile(selection.tier, selection.path);
      if (seq === selectSeq.current) setFile(loaded);
    } catch (error) {
      if (seq === selectSeq.current) {
        setSaveStatus(`Could not load file: ${String(error)}`);
        setFile(null);
      }
    }
  }, []);

  const handleDraftChange = useCallback((draft: WorkflowDraft) => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    // A validation result is only meaningful for the selection it was typed
    // in; drop responses that land after the user switched skills.
    const seq = selectSeq.current;
    debounceRef.current = window.setTimeout(() => {
      // The debounce only collapses *queued* validations — once a request is
      // in flight, a newer one can still overtake it, so each dispatch also
      // gets its own generation and only the latest may write state.
      const generation = ++validateSeq.current;
      const isCurrent = () =>
        seq === selectSeq.current && generation === validateSeq.current;
      void validateWorkflowSkill(draft.frontmatter, draft.body)
        .then((result) => {
          if (isCurrent()) setValidation(result);
        })
        .catch(() => {
          if (isCurrent()) setValidation(null);
        });
    }, VALIDATE_DEBOUNCE_MS);
  }, []);

  useEffect(() => () => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
  }, []);

  const finishWrite = useCallback(
    (result: { reloaded: boolean; reload_error: string | null }, verb: string) => {
      if (result.reloaded) {
        setSaveStatus(`${verb} · agent reloaded`);
        setReloadStatus('agent in sync');
      } else {
        setSaveStatus(
          `${verb} — agent still on old prompt: ${result.reload_error ?? 'reload failed'}. Fix and press Reload.`,
        );
        setReloadStatus('agent stale');
      }
      void refreshCatalog();
    },
    [refreshCatalog],
  );

  const handleSaveWorkflow = useCallback(async (draft: WorkflowDraft) => {
    if (!selected) return;
    setSaving(true);
    try {
      const result = await saveWorkflowSkill(selected.path, draft.frontmatter, draft.body);
      finishWrite(result, 'Saved');
    } catch (error) {
      setSaveStatus(`Save failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [selected, finishWrite]);

  const handleSaveRaw = useCallback(async (selection: SkillSelection, content: string) => {
    setSaving(true);
    try {
      const result = await saveRawSkillFile(selection.tier, selection.path, content);
      finishWrite(result, 'Saved');
    } catch (error) {
      try {
        setValidation(await validateRawSkillFile(selection.tier, content));
      } catch { /* validation refresh is best-effort */ }
      setSaveStatus(`Save failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite]);

  const handleCreate = useCallback(async (draft: WorkflowDraft) => {
    setSaving(true);
    try {
      const result = await createWorkflowSkill(
        draft.frontmatter.domain,
        draft.frontmatter.name,
        draft.frontmatter,
        draft.body,
      );
      finishWrite(result, 'Created');
      await handleSelect({
        tier: 'workflows',
        path: `${draft.frontmatter.domain}/${draft.frontmatter.name}/SKILL.md`,
      });
    } catch (error) {
      setSaveStatus(`Create failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite, handleSelect]);

  const handleDelete = useCallback(async (selection: SkillSelection, name: string) => {
    const domain = selection.path.split('/')[0];
    if (!window.confirm(`Delete workflow skill ${name}? Git history keeps the file.`)) return;
    // Cancel any pending draft validation — the draft's file is going away.
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    setSaving(true);
    try {
      const result = await deleteWorkflowSkill(domain, name);
      const warningText = result.warnings.length ? ` — Warnings: ${result.warnings.join('; ')}` : '';
      finishWrite(result, `Deleted${warningText}`);
      setSelected(null);
      setFile(null);
    } catch (error) {
      setSaveStatus(`Delete failed: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }, [finishWrite]);

  const handleReload = useCallback(async () => {
    try {
      const result = await reloadSkills();
      setReloadStatus(result.reloaded ? 'agent in sync' : `reload failed: ${result.error}`);
    } catch (error) {
      setReloadStatus(`reload failed: ${String(error)}`);
    }
  }, []);

  return (
    <Skills
      catalog={catalog}
      loading={loading}
      selected={selected}
      file={file}
      validation={validation}
      saving={saving}
      reloadStatus={reloadStatus}
      saveStatus={saveStatus}
      onSelect={(selection) => { void handleSelect(selection); }}
      onDraftChange={handleDraftChange}
      onSaveWorkflow={(draft) => { void handleSaveWorkflow(draft); }}
      onSaveRaw={(selection, content) => { void handleSaveRaw(selection, content); }}
      onCreate={(draft) => { void handleCreate(draft); }}
      onDelete={(selection, name) => { void handleDelete(selection, name); }}
      onReload={() => { void handleReload(); }}
      onPageContextChange={onPageContextChange}
    />
  );
}
