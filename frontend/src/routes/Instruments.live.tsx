import { useEffect, useMemo, useRef, useState } from 'react';
import { api, listFxRates, createFxRate, fetchFxRateAkshare, deleteFxRate, createInstrument } from '../api/client';
import type { FxRate, MarketDataProfile, PageContextReporter, UnderlyingPricingDefault } from '../types';
import { Instruments } from './Instruments';
import type { Instrument, Tab } from './Instruments';
import type { HedgeMapGroup, HedgeCandidate, CandidateFilters, QuoteInfo } from './InstrumentsAllowedHedges';
import { EMPTY_CANDIDATE_FILTERS } from './InstrumentsAllowedHedges';
import type { MarketQuote, ManualQuotePayload, RefreshResult } from './InstrumentsMarketData';
import { composeRefreshSummary } from './InstrumentsMarketData';
import type { AssumptionSet } from './InstrumentsAssumptions';

type Props = {
  onPageContextChange?: PageContextReporter;
};

type Feedback = {
  tone: 'success' | 'error';
  message: string;
};

export function InstrumentsLive({ onPageContextChange: _onPageContextChange }: Props) {
  const [rows, setRows] = useState<Instrument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [syncing, setSyncing] = useState(false);

  // Contract load task polling (ported from Hedging.live.tsx)
  const [loadInProgress, setLoadInProgress] = useState(false);
  const [loadTaskChip, setLoadTaskChip] = useState<string | null>(null);

  // Filter state (controlled — live layer owns the values, presentational just fires callbacks)
  const [kindFilter, setKindFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');

  // Tab
  const [activeTab, setActiveTab] = useState<Tab>('registry');

  // Allowed Hedges state
  const [hedgeGroups, setHedgeGroups] = useState<HedgeMapGroup[]>([]);
  const [selectedHedgeUnderlyingId, setSelectedHedgeUnderlyingId] = useState<number | null>(null);
  const [hedgeCandidates, setHedgeCandidates] = useState<HedgeCandidate[]>([]);
  const [hedgeCandidateFilters, setHedgeCandidateFilters] = useState<CandidateFilters>(EMPTY_CANDIDATE_FILTERS);
  const [quotesByInstrumentId, setQuotesByInstrumentId] = useState<Record<number, QuoteInfo>>({});

  // Market Data tab state
  const [marketQuotes, setMarketQuotes] = useState<MarketQuote[]>([]);
  const [marketQuotesLoading, setMarketQuotesLoading] = useState(false);
  const [marketQuoteHistory, setMarketQuoteHistory] = useState<MarketQuote[]>([]);
  const [marketQuoteHistoryInstrumentId, setMarketQuoteHistoryInstrumentId] = useState<number | null>(null);
  const [marketQuoteHistoryLoading, setMarketQuoteHistoryLoading] = useState(false);
  const [marketRefreshing, setMarketRefreshing] = useState(false);
  const [marketRefreshFeedback, setMarketRefreshFeedback] = useState<string | null>(null);
  const [marketProfiles, setMarketProfiles] = useState<MarketDataProfile[]>([]);
  const [marketProfilesLoading, setMarketProfilesLoading] = useState(false);
  const [fxRates, setFxRates] = useState<FxRate[]>([]);
  const [fxRatesLoading, setFxRatesLoading] = useState(false);
  const [fxFeedback, setFxFeedback] = useState<string | null>(null);
  const [fxFetching, setFxFetching] = useState(false);

  // Assumptions tab state
  const [assumptionDefaults, setAssumptionDefaults] = useState<UnderlyingPricingDefault[]>([]);
  const [assumptionSets, setAssumptionSets] = useState<AssumptionSet[]>([]);
  const [assumptionSelectedSetId, setAssumptionSelectedSetId] = useState<number | null>(null);
  const [assumptionBuilding, setAssumptionBuilding] = useState(false);
  const [assumptionRefreshing, setAssumptionRefreshing] = useState(false);
  const [assumptionBuildFeedback, setAssumptionBuildFeedback] = useState<string | null>(null);
  const [assumptionBuildUnfilled, setAssumptionBuildUnfilled] = useState<string[] | null>(null);

  const assumptionUnderlyingRoleSymbols = useMemo(
    () =>
      hedgeGroups
        .filter((group) => (group.open_position_count ?? 0) > 0)
        .map((group) => group.underlying_symbol?.trim())
        .filter((symbol): symbol is string => Boolean(symbol)),
    [hedgeGroups],
  );
  const assumptionUnderlyingRoleSymbolSet = useMemo(
    () => new Set(assumptionUnderlyingRoleSymbols.map((symbol) => symbol.toLowerCase())),
    [assumptionUnderlyingRoleSymbols],
  );

  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const load = async () => {
    setError(null);
    const params = new URLSearchParams();
    if (kindFilter) params.set('kind', kindFilter);
    if (statusFilter) params.set('status', statusFilter);
    if (search) params.set('search', search);
    const qs = params.toString();
    try {
      const data = await api<Instrument[]>(`/api/instruments${qs ? `?${qs}` : ''}`);
      if (!cancelledRef.current) setRows(data);
    } catch (err) {
      if (!cancelledRef.current) setError(errorMessage(err));
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  };

  // Re-fetch whenever filters change
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kindFilter, statusFilter, search]);

  // Hedge map on mount: feeds the Registry ROLES badges (and warms the
  // Allowed Hedges tab). Roles must be visible without visiting that tab.
  useEffect(() => {
    void loadHedgeMap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Allowed Hedges — lazy fetch on first tab activation
  // ---------------------------------------------------------------------------

  const loadHedgeMap = async () => {
    try {
      const groups = await api<HedgeMapGroup[]>('/api/hedging/map');
      if (cancelledRef.current) return;
      setHedgeGroups(groups);
      // Auto-select first group
      if (groups.length > 0 && selectedHedgeUnderlyingId === null) {
        setSelectedHedgeUnderlyingId(groups[0].underlying_id);
      }
    } catch (err) {
      console.error('Failed to load hedge map', err);
    }
  };

  const loadQuotes = async () => {
    try {
      const quotes = await api<{ instrument_id: number; price: number; age_days: number }[]>(
        '/api/market-data/quotes?latest=1',
      );
      if (cancelledRef.current) return;
      const map: Record<number, QuoteInfo> = {};
      for (const q of quotes) {
        map[q.instrument_id] = { price: q.price, age_days: q.age_days };
      }
      setQuotesByInstrumentId(map);
    } catch (err) {
      console.error('Failed to load quotes', err);
    }
  };

  const loadCandidates = async (underlyingId: number, filters: CandidateFilters) => {
    try {
      const params = new URLSearchParams({ underlying_id: String(underlyingId) });
      if (filters.family) params.set('family', filters.family);
      if (filters.optionType) params.set('option_type', filters.optionType);
      if (filters.strikeMin) params.set('strike_min', filters.strikeMin);
      if (filters.strikeMax) params.set('strike_max', filters.strikeMax);
      if (filters.search) params.set('search', filters.search);
      const candidates = await api<HedgeCandidate[]>(`/api/hedging/instruments?${params.toString()}`);
      if (!cancelledRef.current) setHedgeCandidates(candidates);
    } catch (err) {
      console.error('Failed to load hedge candidates', err);
    }
  };

  // Activate the Allowed Hedges tab — refresh map+quotes on every activation
  const activateHedgeTab = async () => {
    await Promise.all([loadHedgeMap(), loadQuotes()]);
  };

  // When tab changes to allowed-hedges, trigger lazy fetch
  useEffect(() => {
    if (activeTab === 'allowed-hedges') {
      void activateHedgeTab();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Reload candidates when selected underlying or filters change (only when tab is active)
  useEffect(() => {
    if (activeTab !== 'allowed-hedges') return;
    if (selectedHedgeUnderlyingId === null) {
      setHedgeCandidates([]);
      return;
    }
    void loadCandidates(selectedHedgeUnderlyingId, hedgeCandidateFilters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedHedgeUnderlyingId, hedgeCandidateFilters, activeTab]);

  const refreshHedgeData = async () => {
    await loadHedgeMap();
    await loadQuotes();
    if (selectedHedgeUnderlyingId !== null) {
      await loadCandidates(selectedHedgeUnderlyingId, hedgeCandidateFilters);
    }
    // Marking/unmarking/purging changes the server-derived "hedge" tag
    // (Tasks 1-2) — the Registry tab's TAGS cell now reads that tag from
    // `rows`, not from `hedgeGroups`, so it must be reloaded here too or it
    // goes stale until an unrelated filter change happens to refetch it.
    await load();
  };

  // ---------------------------------------------------------------------------
  // Market Data tab — lazy fetch on every activation
  // ---------------------------------------------------------------------------

  const loadMarketQuotes = async () => {
    setMarketQuotesLoading(true);
    try {
      const data = await api<MarketQuote[]>('/api/market-data/quotes?latest=1');
      if (!cancelledRef.current) setMarketQuotes(data);
    } catch (err) {
      console.error('Failed to load market quotes', err);
    } finally {
      if (!cancelledRef.current) setMarketQuotesLoading(false);
    }
  };

  const loadMarketProfiles = async () => {
    setMarketProfilesLoading(true);
    try {
      const data = await api<MarketDataProfile[]>('/api/market-data/profiles');
      if (!cancelledRef.current) setMarketProfiles(data);
    } catch (err) {
      console.error('Failed to load market profiles', err);
    } finally {
      if (!cancelledRef.current) setMarketProfilesLoading(false);
    }
  };

  const loadFxRates = async () => {
    setFxRatesLoading(true);
    try {
      const data = await listFxRates();
      if (!cancelledRef.current) setFxRates(data);
    } catch (err) {
      console.error('Failed to load FX rates', err);
    } finally {
      if (!cancelledRef.current) setFxRatesLoading(false);
    }
  };

  const activateMarketDataTab = async () => {
    await Promise.all([loadMarketQuotes(), loadMarketProfiles(), loadFxRates()]);
  };

  // Refresh on every market-data tab activation (T12 corrected pattern)
  useEffect(() => {
    if (activeTab === 'market-data') {
      void activateMarketDataTab();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // ---------------------------------------------------------------------------
  // Assumptions tab — lazy fetch on every activation
  // ---------------------------------------------------------------------------

  const loadAssumptionDefaults = async () => {
    try {
      const data = await api<UnderlyingPricingDefault[]>('/api/underlying-pricing-defaults');
      if (!cancelledRef.current) setAssumptionDefaults(data);
    } catch (err) {
      console.error('Failed to load assumption defaults', err);
    }
  };

  const loadAssumptionSets = async () => {
    try {
      const data = await api<AssumptionSet[]>('/api/assumptions/sets');
      if (!cancelledRef.current) setAssumptionSets(data);
    } catch (err) {
      console.error('Failed to load assumption sets', err);
    }
  };

  const activateAssumptionsTab = async () => {
    await Promise.all([loadAssumptionDefaults(), loadAssumptionSets()]);
  };

  // Refresh on every assumptions tab activation
  useEffect(() => {
    if (activeTab === 'assumptions') {
      void activateAssumptionsTab();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const onAssumptionBuild = async () => {
    setAssumptionBuilding(true);
    setAssumptionBuildFeedback(null);
    setAssumptionBuildUnfilled(null);
    try {
      const set = await api<AssumptionSet>('/api/assumptions/build', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      if (!cancelledRef.current) {
        const rowCount = set.rows.length;
        setAssumptionBuildFeedback(`Assumptions set #${set.id} built · ${rowCount} rows`);
        await loadAssumptionSets();
        setAssumptionSelectedSetId(set.id);
      }
    } catch (err) {
      if (!cancelledRef.current) {
        // Try to parse 400 JSON body: {detail, unfilled_underlyings}
        const msg = err instanceof Error ? err.message : String(err);
        try {
          // FastAPI wraps HTTPException dict detail: {"detail": {..., "unfilled_underlyings": [...]}}
          const parsed = JSON.parse(msg);
          const unfilled = parsed?.detail?.unfilled_underlyings ?? parsed?.unfilled_underlyings;
          if (Array.isArray(unfilled)) {
            setAssumptionBuildUnfilled(unfilled);
            return;
          }
        } catch {
          // not JSON — fall through
        }
        setAssumptionBuildFeedback(`Build failed: ${msg}`);
      }
    } finally {
      if (!cancelledRef.current) setAssumptionBuilding(false);
    }
  };

  const onAssumptionRefreshFromPositions = async () => {
    setAssumptionRefreshing(true);
    try {
      const data = await api<UnderlyingPricingDefault[]>(
        '/api/underlying-pricing-defaults/refresh-from-positions',
        { method: 'POST' },
      );
      if (!cancelledRef.current) setAssumptionDefaults(data);
    } catch (err) {
      console.error('Failed to refresh assumption defaults from positions', err);
    } finally {
      if (!cancelledRef.current) setAssumptionRefreshing(false);
    }
  };

  const onAssumptionUpsert = async (
    underlying: string,
    fields: { rate: number | null; dividend_yield: number | null; volatility: number | null },
  ) => {
    if (!assumptionUnderlyingRoleSymbolSet.has(underlying.trim().toLowerCase())) {
      setAssumptionBuildFeedback(`Skipped ${underlying}: not an UNDERLYING role instrument`);
      return;
    }
    try {
      await api(`/api/underlying-pricing-defaults/${encodeURIComponent(underlying)}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      if (!cancelledRef.current) await loadAssumptionDefaults();
    } catch (err) {
      console.error('Failed to upsert assumption default', err);
    }
  };

  const onAssumptionSelectSet = (id: number | null) => {
    setAssumptionSelectedSetId(id);
  };

  const onRefreshQuotes = async () => {
    setMarketRefreshing(true);
    setMarketRefreshFeedback(null);
    try {
      const result = await api<RefreshResult>('/api/market-data/quotes/refresh', { method: 'POST', body: JSON.stringify({}) });
      if (!cancelledRef.current) {
        setMarketRefreshFeedback(composeRefreshSummary(result));
        await loadMarketQuotes();
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setMarketRefreshFeedback(`Refresh failed: ${errorMessage(err)}`);
      }
    } finally {
      if (!cancelledRef.current) setMarketRefreshing(false);
    }
  };

  const onManualQuote = async (payload: ManualQuotePayload) => {
    try {
      await api('/api/market-data/quotes', { method: 'POST', body: JSON.stringify(payload) });
      if (!cancelledRef.current) await loadMarketQuotes();
    } catch (err) {
      if (!cancelledRef.current) {
        setMarketRefreshFeedback(`Failed to save quote: ${errorMessage(err)}`);
      }
    }
  };

  const onSelectQuoteHistory = async (instrumentId: number) => {
    setMarketQuoteHistoryInstrumentId(instrumentId);
    setMarketQuoteHistoryLoading(true);
    try {
      const data = await api<MarketQuote[]>(`/api/market-data/quotes?instrument_id=${instrumentId}&limit=50`);
      if (!cancelledRef.current) setMarketQuoteHistory(data);
    } catch (err) {
      console.error('Failed to load quote history', err);
    } finally {
      if (!cancelledRef.current) setMarketQuoteHistoryLoading(false);
    }
  };

  const onCloseQuoteHistory = () => {
    setMarketQuoteHistoryInstrumentId(null);
    setMarketQuoteHistory([]);
  };

  const onCreateFxRate = async (payload: Omit<FxRate, 'id'>) => {
    setFxFetching(true);
    setFxFeedback(null);
    try {
      await createFxRate(payload);
      if (!cancelledRef.current) {
        setFxFeedback(`Created ${payload.base_currency}/${payload.quote_currency} rate.`);
        await loadFxRates();
      }
    } catch (err) {
      if (!cancelledRef.current) setFxFeedback(`Failed: ${errorMessage(err)}`);
    } finally {
      if (!cancelledRef.current) setFxFetching(false);
    }
  };

  const onFetchFxRateAkshare = async (base: string, quote: string) => {
    setFxFetching(true);
    setFxFeedback(null);
    try {
      await fetchFxRateAkshare(base, quote);
      if (!cancelledRef.current) {
        setFxFeedback(`Fetched ${base}/${quote} from AKShare.`);
        await loadFxRates();
      }
    } catch (err) {
      if (!cancelledRef.current) setFxFeedback(`Failed: ${errorMessage(err)}`);
    } finally {
      if (!cancelledRef.current) setFxFetching(false);
    }
  };

  const onDeleteFxRate = async (id: number) => {
    setFxFetching(true);
    try {
      await deleteFxRate(id);
      if (!cancelledRef.current) await loadFxRates();
    } catch (err) {
      if (!cancelledRef.current) setFxFeedback(`Failed to delete: ${errorMessage(err)}`);
    } finally {
      if (!cancelledRef.current) setFxFetching(false);
    }
  };

  const onHedgeMark = async (ids: number[]) => {
    if (!ids.length) return;
    try {
      await api('/api/hedging/map/mark', { method: 'POST', body: JSON.stringify({ instrument_ids: ids }) });
      await refreshHedgeData();
    } catch (err) {
      if (!cancelledRef.current) setFeedback({ tone: 'error', message: errorMessage(err) });
    }
  };

  const onHedgeUnmark = async (ids: number[]) => {
    if (!ids.length) return;
    try {
      await api('/api/hedging/map/unmark', { method: 'POST', body: JSON.stringify({ map_entry_ids: ids }) });
      await refreshHedgeData();
    } catch (err) {
      if (!cancelledRef.current) setFeedback({ tone: 'error', message: errorMessage(err) });
    }
  };

  const onHedgePurgeStale = async (underlyingId: number) => {
    try {
      await api(`/api/hedging/map/purge-stale?underlying_id=${underlyingId}`, { method: 'POST' });
      await refreshHedgeData();
    } catch (err) {
      if (!cancelledRef.current) setFeedback({ tone: 'error', message: errorMessage(err) });
    }
  };

  const onSelectHedgeUnderlying = (id: number) => {
    setSelectedHedgeUnderlyingId(id);
    setHedgeCandidateFilters(EMPTY_CANDIDATE_FILTERS);
  };

  // ---------------------------------------------------------------------------
  // Registry tab actions
  // ---------------------------------------------------------------------------

  const onSync = async () => {
    setSyncing(true);
    setFeedback(null);
    try {
      const result = await api<{ created: number; existing: number; instruments: Instrument[] }>(
        '/api/instruments/sync-from-positions',
        { method: 'POST' },
      );
      if (!cancelledRef.current) {
        setRows(result.instruments);
        setFeedback({
          tone: 'success',
          message: `Synced ${result.created} new (${result.existing} existing).`,
        });
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setFeedback({ tone: 'error', message: errorMessage(err) });
      }
    } finally {
      if (!cancelledRef.current) setSyncing(false);
    }
  };

  const pollLoad = async (taskId: number) => {
    if (cancelledRef.current) return;
    try {
      const status = await api<{
        status: string;
        progress_current: number | null;
        progress_total: number | null;
        message: string | null;
      }>(`/api/hedging/instruments/load/${taskId}`);
      if (cancelledRef.current) return;

      const chip = [
        `task ${taskId}`,
        status.progress_current != null && status.progress_total != null
          ? `${status.progress_current}/${status.progress_total}`
          : null,
        status.message,
      ]
        .filter(Boolean)
        .join(' · ');
      setLoadTaskChip(chip);

      if (status.status === 'queued' || status.status === 'running') {
        setTimeout(() => void pollLoad(taskId), 2000);
      } else {
        setLoadInProgress(false);
        // Reload instruments after load completes
        await load();
        setLoadTaskChip(null);
      }
    } catch (err) {
      console.error('pollLoad error', err);
      if (!cancelledRef.current) {
        setLoadInProgress(false);
        setLoadTaskChip(null);
      }
    }
  };

  const onLoad = async () => {
    setLoadInProgress(true);
    setFeedback(null);
    try {
      const { task_id } = await api<{ task_id: number }>(
        '/api/hedging/instruments/load',
        { method: 'POST' },
      );
      void pollLoad(task_id);
    } catch (err) {
      setLoadInProgress(false);
      setFeedback({ tone: 'error', message: errorMessage(err) });
    }
  };

  const onSaveInstrument = async (id: number, fields: Partial<Instrument>) => {
    const updated = await api<Instrument>(`/api/instruments/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    });
    if (!cancelledRef.current) {
      setRows((current) => current.map((row) => (row.id === id ? updated : row)));
      setFeedback({ tone: 'success', message: `Saved ${updated.symbol}.` });
    }
  };

  const onSetInstrumentTags = async (id: number, tags: string[]) => {
    try {
      const updated = await api<Instrument>(`/api/instruments/${id}/tags`, {
        method: 'PUT',
        body: JSON.stringify({ tags }),
      });
      if (!cancelledRef.current) {
        setRows((current) => current.map((row) => (row.id === id ? updated : row)));
      }
    } catch (err) {
      if (!cancelledRef.current) setFeedback({ tone: 'error', message: errorMessage(err) });
    }
  };

  const onCreateInstrument = async (fields: Parameters<typeof createInstrument>[0]) => {
    const created = await createInstrument(fields);
    if (!cancelledRef.current) {
      setRows((current) => [...current, created].sort((a, b) => a.symbol.localeCompare(b.symbol)));
      setFeedback({ tone: 'success', message: `Created ${created.symbol}.` });
    }
  };

  return (
    <Instruments
      rows={rows}
      loading={loading}
      error={error}
      feedback={feedback}
      syncing={syncing}
      loadInProgress={loadInProgress}
      loadTaskChip={loadTaskChip}
      kindFilter={kindFilter}
      statusFilter={statusFilter}
      search={search}
      onKindFilterChange={setKindFilter}
      onStatusFilterChange={setStatusFilter}
      onSearchChange={setSearch}
      onSync={onSync}
      onLoad={onLoad}
      onSaveInstrument={onSaveInstrument}
      onSetInstrumentTags={onSetInstrumentTags}
      onCreateInstrument={onCreateInstrument}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      hedgeGroups={hedgeGroups}
      selectedHedgeUnderlyingId={selectedHedgeUnderlyingId}
      onSelectHedgeUnderlying={onSelectHedgeUnderlying}
      hedgeCandidates={hedgeCandidates}
      hedgeCandidateFilters={hedgeCandidateFilters}
      onHedgeCandidateFiltersChange={setHedgeCandidateFilters}
      quotesByInstrumentId={quotesByInstrumentId}
      onHedgeMark={onHedgeMark}
      onHedgeUnmark={onHedgeUnmark}
      onHedgePurgeStale={onHedgePurgeStale}
      marketQuotes={marketQuotes}
      marketQuotesLoading={marketQuotesLoading}
      marketQuoteHistory={marketQuoteHistory}
      marketQuoteHistoryInstrumentId={marketQuoteHistoryInstrumentId}
      marketQuoteHistoryLoading={marketQuoteHistoryLoading}
      marketRefreshing={marketRefreshing}
      marketRefreshFeedback={marketRefreshFeedback}
      marketProfiles={marketProfiles}
      marketProfilesLoading={marketProfilesLoading}
      fxRates={fxRates}
      fxRatesLoading={fxRatesLoading}
      fxFeedback={fxFeedback}
      fxFetching={fxFetching}
      onRefreshQuotes={onRefreshQuotes}
      onManualQuote={onManualQuote}
      onSelectQuoteHistory={onSelectQuoteHistory}
      onCloseQuoteHistory={onCloseQuoteHistory}
      onCreateFxRate={onCreateFxRate}
      onFetchFxRateAkshare={onFetchFxRateAkshare}
      onDeleteFxRate={onDeleteFxRate}
      assumptionDefaults={assumptionDefaults}
      assumptionUnderlyingRoleSymbols={assumptionUnderlyingRoleSymbols}
      assumptionSets={assumptionSets}
      assumptionSelectedSetId={assumptionSelectedSetId}
      assumptionBuilding={assumptionBuilding}
      assumptionRefreshing={assumptionRefreshing}
      assumptionBuildFeedback={assumptionBuildFeedback}
      assumptionBuildUnfilled={assumptionBuildUnfilled}
      onAssumptionBuild={() => { void onAssumptionBuild(); }}
      onAssumptionSelectSet={onAssumptionSelectSet}
      onAssumptionRefreshFromPositions={() => { void onAssumptionRefreshFromPositions(); }}
      onAssumptionUpsert={(underlying, fields) => { void onAssumptionUpsert(underlying, fields); }}
    />
  );
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
