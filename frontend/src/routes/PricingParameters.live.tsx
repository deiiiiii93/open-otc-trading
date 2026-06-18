import { useEffect, useRef, useState } from 'react';
import { api, uploadForm } from '../api/client';
import type { PageContextReporter, PricingParameterProfile } from '../types';
import { PricingParameters, type PricingParameterFeedback } from './PricingParameters';

type ImportPayload = {
  file: File;
  name: string;
  valuationDate: string;
  sheetName: string;
};

type Props = {
  onPageContextChange?: PageContextReporter;
};

function errorMessage(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(message);
    const detail = parsed?.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail?.detail === 'string') return detail.detail;
  } catch {
    // Use the raw message when the backend did not return JSON.
  }
  return message;
}

export function PricingParametersLive({ onPageContextChange }: Props) {
  const [profiles, setProfiles] = useState<PricingParameterProfile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<PricingParameterFeedback | null>(null);
  const profileLoadGeneration = useRef(0);

  const loadProfiles = async (preferredSelectedId?: number) => {
    const generation = profileLoadGeneration.current + 1;
    profileLoadGeneration.current = generation;
    let list: PricingParameterProfile[];
    try {
      list = await api<PricingParameterProfile[]>('/api/pricing-parameter-profiles');
    } catch (err) {
      if (generation !== profileLoadGeneration.current) return null;
      throw err;
    }
    if (generation !== profileLoadGeneration.current) return null;
    setProfiles(list);
    setSelectedId((current) => {
      if (preferredSelectedId != null && list.some((p) => p.id === preferredSelectedId)) {
        return preferredSelectedId;
      }
      if (current != null && list.some((p) => p.id === current)) return current;
      return list[0]?.id ?? null;
    });
    return list;
  };

  const load = async () => {
    setError(null);
    try {
      await loadProfiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onImport = async (payload: ImportPayload) => {
    const form = new FormData();
    form.append('file', payload.file);
    if (payload.name.trim()) form.append('name', payload.name.trim());
    if (payload.valuationDate.trim()) form.append('valuation_date', payload.valuationDate.trim());
    if (payload.sheetName.trim()) form.append('sheet_name', payload.sheetName.trim());

    setImporting(true);
    setFeedback(null);
    try {
      const profile = await uploadForm<PricingParameterProfile>(
        '/api/pricing-parameter-profiles/import',
        form,
      );
      setFeedback({ tone: 'success', message: `Imported ${profile.summary?.row_count ?? profile.rows.length} rows.` });
      setSelectedId(profile.id);
      await loadProfiles(profile.id);
    } catch (err) {
      setFeedback({ tone: 'error', message: `Could not import: ${errorMessage(err)}` });
    } finally {
      setImporting(false);
    }
  };

  return (
    <PricingParameters
      profiles={profiles}
      selectedId={selectedId}
      loading={loading}
      error={error}
      importing={importing}
      feedback={feedback}
      onSelectProfile={setSelectedId}
      onImport={onImport}
      onPageContextChange={onPageContextChange}
    />
  );
}
