import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createChannel,
  createModel,
  deleteChannel,
  deleteModel,
  getAgentRegistry,
  setDefaultModel,
  updateChannel,
  updateModel,
  validateDraft,
} from '../api/client';
import type {
  AgentRegistry,
  ChannelWrite,
  ModelWrite,
  PageContextReporter,
} from '../types';
import { ModelMaintenance } from './ModelMaintenance';

type Props = { onPageContextChange?: PageContextReporter };

const VALIDATE_DEBOUNCE_MS = 500;

export function ModelMaintenanceLive({ onPageContextChange }: Props) {
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[] | null>(null);
  const debounceRef = useRef<number | null>(null);
  const validateSeq = useRef(0);

  const refresh = useCallback(async () => {
    try {
      setRegistry(await getAgentRegistry());
    } catch (error) {
      setSaveStatus(`Could not load registry: ${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => () => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
  }, []);

  // Cancel any queued/in-flight draft validation and drop stale errors — called
  // whenever the selection changes so one draft's errors can't bleed onto another.
  const handleClearValidation = useCallback(() => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    validateSeq.current += 1;
    setValidationErrors(null);
  }, []);

  const handleValidateDraft = useCallback((kind: string, payload: unknown) => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      // The debounce collapses queued validations; each dispatch still gets its
      // own generation so a slower in-flight request can't overwrite a newer one.
      const generation = ++validateSeq.current;
      void validateDraft(kind, payload)
        .then((result) => {
          if (generation === validateSeq.current) setValidationErrors(result.ok ? [] : result.errors);
        })
        .catch(() => {
          if (generation === validateSeq.current) setValidationErrors(null);
        });
    }, VALIDATE_DEBOUNCE_MS);
  }, []);

  // Every write returns the fresh registry; install it and never let the API
  // error escape (the page surfaces it as a status string).
  const runWrite = useCallback(
    async (fn: () => Promise<AgentRegistry>, verb: string): Promise<boolean> => {
      setSaving(true);
      try {
        const fresh = await fn();
        setRegistry(fresh);
        setSaveStatus(`${verb} · agent reloaded`);
        return true;
      } catch (error) {
        setSaveStatus(`${verb} failed: ${String(error)}`);
        return false;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const handleSaveChannel = useCallback(
    (name: string, write: ChannelWrite) => runWrite(() => updateChannel(name, write), 'Saved'),
    [runWrite],
  );
  const handleCreateChannel = useCallback(
    (write: ChannelWrite) => runWrite(() => createChannel(write), 'Created'),
    [runWrite],
  );
  const handleDeleteChannel = useCallback(
    async (name: string): Promise<boolean> => {
      if (!window.confirm(`Delete channel ${name} and all of its models?`)) return false;
      return runWrite(() => deleteChannel(name), 'Deleted');
    },
    [runWrite],
  );
  const handleSaveModel = useCallback(
    (channel: string, id: string, write: ModelWrite) =>
      runWrite(() => updateModel(channel, id, write), 'Saved'),
    [runWrite],
  );
  const handleCreateModel = useCallback(
    (channel: string, write: ModelWrite) => runWrite(() => createModel(channel, write), 'Created'),
    [runWrite],
  );
  const handleDeleteModel = useCallback(
    async (channel: string, id: string): Promise<boolean> => {
      if (!window.confirm(`Delete model ${id} from ${channel}?`)) return false;
      return runWrite(() => deleteModel(channel, id), 'Deleted');
    },
    [runWrite],
  );
  const handleSetDefault = useCallback(
    (channel: string, id: string) => runWrite(() => setDefaultModel(channel, id), 'Default set'),
    [runWrite],
  );

  return (
    <ModelMaintenance
      registry={registry}
      loading={loading}
      saving={saving}
      saveStatus={saveStatus}
      validationErrors={validationErrors}
      onValidateDraft={handleValidateDraft}
      onClearValidation={handleClearValidation}
      onReload={() => { void refresh(); }}
      onSaveChannel={handleSaveChannel}
      onCreateChannel={handleCreateChannel}
      onDeleteChannel={handleDeleteChannel}
      onSaveModel={handleSaveModel}
      onCreateModel={handleCreateModel}
      onDeleteModel={handleDeleteModel}
      onSetDefault={handleSetDefault}
      onPageContextChange={onPageContextChange}
    />
  );
}
