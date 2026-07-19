import { useEffect, useMemo, useRef, useState } from 'react';
import { FileClock, ShieldAlert } from 'lucide-react';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { HeaderControls } from '../components/HeaderControls';
import { Input } from '../components/Input';
import { MetricRows } from '../components/MetricRow';
import { Modal } from '../components/Modal';
import { Panel } from '../components/Panel';
import { PanelGrid } from '../components/PanelGrid';
import { RailItem } from '../components/RailItem';
import { RailList } from '../components/RailList';
import { Select } from '../components/Select';
import { Skeleton } from '../components/Skeleton';
import { SplitLayout } from '../components/SplitLayout';
import { Table, type Column } from '../components/Table';
import { TableToolbar } from '../components/TableToolbar';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../components/Tabs';
import { PageScaffold } from '../components/templates/PageScaffold';
import type {
  EngineConfigVariant,
  LimitActionInput,
  LimitCategory,
  LimitComparator,
  LimitCreateInput,
  LimitDashboard,
  LimitEvaluation,
  LimitIncident,
  LimitIncidentAssignInput,
  LimitIncidentCommentInput,
  LimitIncidentWaiveInput,
  LimitMetadataPatchInput,
  LimitMetricKind,
  LimitMonitoringRun,
  LimitScopeType,
  LimitSourcePolicy,
  LimitVersion,
  LimitVersionCreateInput,
  LimitVersionInput,
  MarketSnapshot,
  Portfolio,
  PricingParameterProfile,
  RiskLimit,
} from '../types';
import { parseServerDateTime } from './limitsDateTime';
import './Limits.css';

export type LimitsTab =
  | 'monitor'
  | 'definitions'
  | 'breaches'
  | 'schedules'
  | 'reports';

export type LimitsProps = {
  activeTab: LimitsTab;
  onTabChange: (tab: LimitsTab) => void;
  portfolios: Portfolio[];
  pricingProfiles: PricingParameterProfile[];
  engineConfigs: EngineConfigVariant[];
  marketSnapshots: MarketSnapshot[];
  selectedPortfolioId: number | null;
  selectedPricingProfileId: number | null;
  selectedEngineConfigId: number | null;
  selectedMarketSnapshotId: number | null;
  effectiveMarketEvidenceId: string;
  valuationAsOf: string;
  sourcePolicy: LimitSourcePolicy;
  maxSourceAgeSeconds: number | null;
  sourceInputsText: string;
  onSelectPortfolio: (id: number) => void;
  onSelectPricingProfile: (id: number | null) => void;
  onSelectEngineConfig: (id: number | null) => void;
  onSelectMarketSnapshot: (id: number | null) => void;
  onEffectiveMarketEvidenceChange: (value: string) => void;
  onValuationAsOfChange: (value: string) => void;
  onSourcePolicyChange: (value: LimitSourcePolicy) => void;
  onMaxSourceAgeSecondsChange: (value: number | null) => void;
  onSourceInputsTextChange: (value: string) => void;
  dashboard: LimitDashboard | null;
  selectedRun: LimitMonitoringRun | null;
  selectedRunExplicit?: boolean;
  selectedEvaluationId?: number | null;
  onCloseEvaluation?: () => void;
  onEvidenceFocus?: (id: number | null) => void;
  evaluations: LimitEvaluation[];
  definitions: RiskLimit[];
  selectedDefinitionId: number | null;
  onSelectDefinition: (id: number) => void;
  incidents: LimitIncident[];
  selectedIncidentId: number | null;
  selectedIncident: LimitIncident | null;
  onSelectIncident: (id: number) => void;
  loading: boolean;
  running: boolean;
  mutationPending: boolean;
  error: string | null;
  mutationFeedback: string | null;
  onRunNow: () => void | Promise<void>;
  onAskLimitManager?: () => void;
  onCreateDefinition: (body: LimitCreateInput) => boolean | Promise<boolean>;
  onUpdateDefinition: (
    id: number,
    body: LimitMetadataPatchInput,
  ) => boolean | Promise<boolean>;
  onCreateDefinitionVersion: (
    id: number,
    body: LimitVersionCreateInput,
  ) => boolean | Promise<boolean>;
  onActivateDefinitionVersion: (
    id: number,
    versionId: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onDeactivateDefinition: (
    id: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onRetireDefinition: (
    id: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onAcknowledgeIncident: (
    id: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onAssignIncident: (
    id: number,
    body: LimitIncidentAssignInput,
  ) => boolean | Promise<boolean>;
  onCommentIncident: (
    id: number,
    body: LimitIncidentCommentInput,
  ) => boolean | Promise<boolean>;
  onWaiveIncident: (
    id: number,
    body: LimitIncidentWaiveInput,
  ) => boolean | Promise<boolean>;
  onResolveIncident: (
    id: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onReopenIncident: (
    id: number,
    body: LimitActionInput,
  ) => boolean | Promise<boolean>;
  onOpenEvaluation?: (id: number) => void;
  onOpenAudit?: (auditRef: string) => void;
};

export function Limits(props: LimitsProps) {
  const selectedPortfolio =
    props.portfolios.find((item) => item.id === props.selectedPortfolioId) ?? null;
  const chips = [
    selectedPortfolio?.name,
    props.activeTab === 'monitor' && props.selectedRun
      ? `RUN ${props.selectedRun.id}`
      : null,
    props.activeTab === 'breaches' && props.selectedIncident
      ? `INCIDENT ${props.selectedIncident.id}`
      : null,
  ].filter((value): value is string => Boolean(value));

  const actions = (
    <HeaderControls>
      {props.activeTab === 'monitor' ? (
        <Button
          variant="primary"
          onClick={props.onRunNow}
          disabled={
            props.running
            || props.selectedPortfolioId == null
            || (
              props.selectedMarketSnapshotId == null
              && !props.effectiveMarketEvidenceId.trim()
            )
          }
        >
          {props.running ? 'Running…' : 'Run now'}
        </Button>
      ) : null}
      <Button disabled title="Analysis reports arrive with the evidence-report API.">
        Generate analysis
      </Button>
      <Button
        disabled
        onClick={props.onAskLimitManager}
        title="The Limit Manager persona is enabled in a later checkpoint."
      >
        Ask Limit Manager
      </Button>
    </HeaderControls>
  );

  const feedback = props.error ? (
    <div className="limits-feedback limits-feedback--error" role="alert">
      <ShieldAlert size={16} aria-hidden="true" />
      <span>{props.error}</span>
    </div>
  ) : props.mutationFeedback ? (
    <div className="limits-feedback" role="status">
      <FileClock size={16} aria-hidden="true" />
      <span>{props.mutationFeedback}</span>
    </div>
  ) : undefined;

  return (
    <PageScaffold
      title="LIMITS"
      chips={chips}
      actions={actions}
      feedback={feedback}
      className="limits"
    >
      <div className="limits-mast">
        <div>
          <span className="limits-mast__eyebrow">RISK CONTROL LEDGER</span>
          <p className="limits-mast__copy">
            Govern definitions, monitor immutable evidence, and work persistent
            incidents from one desk surface.
          </p>
        </div>
        <div className="limits-mast__stamp" aria-label="Authority model">
          INTERACTIVE / AUTO / YOLO
          {props.selectedRun ? ` · ${props.selectedRun.mode.toUpperCase()} RUN` : ''}
        </div>
      </div>

      <Tabs
        value={props.activeTab}
        onValueChange={(value) => props.onTabChange(value as LimitsTab)}
      >
        <TabsList aria-label="Limits sections">
          <TabsTrigger value="monitor">Monitor</TabsTrigger>
          <TabsTrigger value="definitions">Definitions</TabsTrigger>
          <TabsTrigger value="breaches" aria-label="Breaches">
            Breach ledger
          </TabsTrigger>
          <TabsTrigger value="schedules">Schedules</TabsTrigger>
          <TabsTrigger value="reports">Reports</TabsTrigger>
        </TabsList>

        <TabsContent value="monitor">
          <MonitorTab {...props} />
        </TabsContent>
        <TabsContent value="definitions">
          <DefinitionsTab {...props} />
        </TabsContent>
        <TabsContent value="breaches">
          <BreachesTab {...props} />
        </TabsContent>
        <TabsContent value="schedules">
          <DeferredTab
            symbol="⌁"
            title="Schedule control is staged"
            copy="Cron expressions, timezone-aware occurrences, execution history, and mode governance arrive after the durable scheduler API."
          />
        </TabsContent>
        <TabsContent value="reports">
          <DeferredTab
            symbol="§"
            title="Evidence reports are staged"
            copy="Immutable analysis artifacts and deep links activate after report persistence can pin a monitoring evidence snapshot."
          />
        </TabsContent>
      </Tabs>
    </PageScaffold>
  );
}

function MonitorTab(props: LimitsProps) {
  const [evidenceEvaluation, setEvidenceEvaluation] =
    useState<LimitEvaluation | null>(null);
  const deepLinkedEvaluationIdRef = useRef<number | null>(null);
  const summary = props.selectedRunExplicit
    ? summaryFromRun(props.selectedRun)
    : props.dashboard?.summary;
  const groups = useMemo(() => {
    if (props.selectedRunExplicit) {
      return props.evaluations.length
        ? [{ category: 'selected', evaluations: props.evaluations }]
        : [];
    }
    return props.dashboard?.evaluation_groups ?? [];
  }, [
    props.dashboard?.evaluation_groups,
    props.evaluations,
    props.selectedRunExplicit,
  ]);

  useEffect(() => {
    if (props.selectedEvaluationId == null) {
      if (deepLinkedEvaluationIdRef.current != null) {
        setEvidenceEvaluation(null);
      }
      deepLinkedEvaluationIdRef.current = null;
      return;
    }
    deepLinkedEvaluationIdRef.current = props.selectedEvaluationId;
    const evaluation = props.evaluations.find(
      (item) => item.id === props.selectedEvaluationId,
    );
    if (
      evaluation
      && props.selectedRun?.id === evaluation.monitoring_run_id
    ) {
      setEvidenceEvaluation(evaluation);
    }
  }, [
    props.evaluations,
    props.selectedEvaluationId,
    props.selectedRun?.id,
  ]);

  useEffect(() => {
    if (
      evidenceEvaluation
      && props.selectedRun?.id !== evidenceEvaluation.monitoring_run_id
    ) {
      setEvidenceEvaluation(null);
      props.onEvidenceFocus?.(null);
    }
  }, [
    evidenceEvaluation,
    props.onEvidenceFocus,
    props.selectedRun?.id,
  ]);

  const evaluationColumns = useMemo<Column<LimitEvaluation>[]>(() => [
    {
      key: 'status',
      header: 'State',
      width: '0.7fr',
      render: (row) => (
        <Badge variant={evaluationBadge(row.status)}>
          {row.status}
        </Badge>
      ),
    },
    {
      key: 'scope_label',
      header: 'Limit / scope',
      width: '1.35fr',
      render: (row) => (
        <div className="limits-table-stack">
          <strong>{row.scope_label}</strong>
          <span>
            Limit version #{row.limit_version_id} · {humanize(row.scope_type)}
          </span>
        </div>
      ),
    },
    {
      key: 'observed_value',
      header: 'Observed',
      numeric: true,
      width: '0.8fr',
      render: (row) => formatMetric(row.observed_value),
    },
    {
      key: 'hard_boundary',
      header: 'Hard boundary',
      numeric: true,
      width: '0.85fr',
      render: (row) => boundaryLabel(row),
    },
    {
      key: 'utilization',
      header: 'Utilization',
      numeric: true,
      width: '0.85fr',
      render: (row) => formatRatio(row.utilization),
    },
    {
      key: 'headroom',
      header: 'Headroom',
      numeric: true,
      width: '0.8fr',
      render: (row) => formatMetric(row.headroom),
    },
    {
      key: 'coverage',
      header: 'Coverage / reason',
      width: '1.35fr',
      render: (row) => (
        <div className="limits-table-stack">
          <strong>{formatRatio(row.coverage_ratio)}</strong>
          <span>{row.reason ?? `${row.coverage_count ?? '—'} covered`}</span>
        </div>
      ),
    },
    {
      key: 'evidence',
      header: 'Evidence',
      width: '0.75fr',
      render: (row) => (
        <Button
          variant="ghost"
          onClick={() => {
            deepLinkedEvaluationIdRef.current = null;
            setEvidenceEvaluation(row);
            props.onEvidenceFocus?.(row.id);
          }}
          aria-label={`Evidence for ${row.scope_label}`}
        >
          Evidence
        </Button>
      ),
    },
  ], []);

  return (
    <div className="limits-monitor">
      <Panel title="Monitoring envelope" meta="Server-resolved inputs">
        <div className="limits-controls">
          <Select
            label="Portfolio"
            value={String(props.selectedPortfolioId ?? '')}
            onChange={(value) => props.onSelectPortfolio(Number(value))}
            options={props.portfolios.map((item) => ({
              value: String(item.id),
              label: item.name,
            }))}
          />
          <Select
            label="Pricing profile"
            value={String(props.selectedPricingProfileId ?? '')}
            onChange={(value) => (
              props.onSelectPricingProfile(value ? Number(value) : null)
            )}
            options={[
              { value: '', label: 'No pinned pricing profile' },
              ...props.pricingProfiles.map((item) => ({
                value: String(item.id),
                label: `${item.name} · ${dateOnly(item.valuation_date)}`,
              })),
            ]}
          />
          <Select
            label="Engine config"
            value={String(props.selectedEngineConfigId ?? '')}
            onChange={(value) => (
              props.onSelectEngineConfig(value ? Number(value) : null)
            )}
            options={[
              { value: '', label: 'Position engines' },
              ...props.engineConfigs.map((item) => ({
                value: String(item.id),
                label: `${item.name}${item.is_default ? ' · default' : ''}`,
              })),
            ]}
          />
          <Select
            label="Market snapshot"
            value={String(props.selectedMarketSnapshotId ?? '')}
            onChange={(value) => (
              props.onSelectMarketSnapshot(value ? Number(value) : null)
            )}
            options={[
              { value: '', label: 'Use evidence id instead' },
              ...props.marketSnapshots.map((item) => ({
                value: String(item.id),
                label: `${item.name} · ${dateTime(item.valuation_date)}`,
              })),
            ]}
          />
          <Input
            label="Effective market evidence id"
            value={props.effectiveMarketEvidenceId}
            disabled={props.selectedMarketSnapshotId != null}
            placeholder="evidence:desk:timestamp"
            onChange={(event) => (
              props.onEffectiveMarketEvidenceChange(event.target.value)
            )}
          />
          <Input
            label="Valuation as of"
            type="datetime-local"
            value={props.valuationAsOf}
            onChange={(event) => props.onValuationAsOfChange(event.target.value)}
          />
          <Select
            label="Source policy"
            value={props.sourcePolicy}
            onChange={(value) => (
              props.onSourcePolicyChange(value as LimitSourcePolicy)
            )}
            options={[
              { value: 'reuse_only', label: 'Reuse only' },
              { value: 'refresh_if_stale', label: 'Refresh if stale' },
              { value: 'force_refresh', label: 'Force refresh' },
            ]}
          />
          <Input
            label="Max source age (seconds)"
            type="number"
            min={0}
            value={props.maxSourceAgeSeconds ?? ''}
            onChange={(event) => (
              props.onMaxSourceAgeSecondsChange(
                event.target.value === '' ? null : Number(event.target.value),
              )
            )}
          />
          <TextAreaField
            label="Source inputs"
            value={props.sourceInputsText}
            onChange={props.onSourceInputsTextChange}
          />
        </div>
        <div className="limits-control-note">
          <strong>Valuation evidence is independent of the shell accounting date.</strong>
          <span>
            The submitted timestamp, policy, source age, and selectors are
            frozen into the monitoring envelope. Scenario limits require
            <code> scenario_test.scenario_request</code>; VaR/CVaR backtests
            require <code>backtest.spec</code> plus an effective evidence id.
          </span>
        </div>
      </Panel>

      {props.loading && !props.dashboard ? (
        <LoadingState />
      ) : !props.dashboard?.latest_run && !props.evaluations.length ? (
        <Empty
          message="No monitoring data for this portfolio."
          hint="Choose explicit evidence inputs and run the first check."
          action={(
            <Button
              variant="primary"
              onClick={props.onRunNow}
              disabled={
                props.running
                || props.selectedPortfolioId == null
                || (
                  props.selectedMarketSnapshotId == null
                  && !props.effectiveMarketEvidenceId.trim()
                )
              }
            >
              Run now
            </Button>
          )}
        />
      ) : (
        <>
          <MetricRows
            metrics={[
              {
                label: 'Breaches',
                value: formatCount(summary?.breaches),
                variant: summary?.breaches ? 'neg' : 'default',
              },
              {
                label: 'Warnings',
                value: formatCount(summary?.warnings),
              },
              {
                label: 'Unknowns',
                value: formatCount(summary?.unknowns),
              },
              {
                label: 'OK',
                value: formatCount(summary?.ok),
                variant: summary?.ok ? 'pos' : 'default',
              },
              {
                label: 'Highest utilization',
                value: formatRatio(summary?.highest_utilization ?? null),
                variant:
                  summary?.highest_utilization != null
                  && summary.highest_utilization >= 1
                    ? 'neg'
                    : 'default',
              },
              {
                label: 'Active incidents',
                value: formatCount(summary?.active_incidents),
              },
            ]}
          />

          {props.selectedRun ? (
            <div className="limits-run-strip">
              <div className="limits-run-strip__identity">
                <span className="limits-run-strip__kicker">
                  {props.selectedRunExplicit ? 'SELECTED EVIDENCE' : 'CURRENT EVIDENCE'}
                </span>
                <strong>
                  {props.selectedRunExplicit ? 'Selected run' : 'Run'} #{props.selectedRun.id}
                </strong>
                <Badge variant={runBadge(props.selectedRun.status)}>
                  {runStatusLabel(props.selectedRun.status)}
                </Badge>
              </div>
              <dl className="limits-run-strip__facts">
                <Fact label="Valuation as of" value={dateTime(props.selectedRun.valuation_as_of)} />
                <Fact label="Authority mode" value={props.selectedRun.mode.toUpperCase()} />
                <Fact label="Source policy" value={humanize(props.selectedRun.source_policy)} />
                <Fact
                  label="Max source age"
                  value={
                    props.selectedRun.max_source_age_seconds == null
                      ? 'unbounded'
                      : `${props.selectedRun.max_source_age_seconds}s`
                  }
                />
                <Fact
                  label="Market evidence"
                  value={
                    props.selectedRun.effective_market_evidence_id
                    ?? (
                      props.selectedRun.market_snapshot_id == null
                        ? '—'
                        : `snapshot #${props.selectedRun.market_snapshot_id}`
                    )
                  }
                />
              </dl>
            </div>
          ) : null}

          {groups.map((group) => (
            <Panel
              key={group.category}
              title={
                group.category === 'selected'
                  ? 'Selected run limits'
                  : `${categoryLabel(group.category)} limits`
              }
              meta={`${group.evaluations.length} evaluations`}
            >
              <Table
                columns={evaluationColumns}
                rows={group.evaluations}
                rowKey={(row) => row.id}
                className="limits-evaluation-table"
              />
            </Panel>
          ))}
        </>
      )}

      <EvidenceModal
        evaluation={evidenceEvaluation}
        run={props.selectedRun}
        onClose={() => {
          deepLinkedEvaluationIdRef.current = null;
          setEvidenceEvaluation(null);
          props.onEvidenceFocus?.(null);
          props.onCloseEvaluation?.();
        }}
      />
    </div>
  );
}

function DefinitionsTab(props: LimitsProps) {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [scope, setScope] = useState('');
  const [state, setState] = useState('');
  const [newOpen, setNewOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [metadataOpen, setMetadataOpen] = useState(false);
  const selected =
    props.definitions.find((item) => item.id === props.selectedDefinitionId) ?? null;
  useEffect(() => {
    setDraftOpen(false);
    setMetadataOpen(false);
  }, [props.selectedDefinitionId]);
  useEffect(() => {
    setDraftOpen(false);
    setMetadataOpen(false);
  }, [selected?.row_version]);
  const visible = useMemo(() => props.definitions.filter((item) => {
    const query = search.trim().toLowerCase();
    const matchesQuery =
      !query
      || item.name.toLowerCase().includes(query)
      || item.key.toLowerCase().includes(query)
      || item.owner.toLowerCase().includes(query);
    const matchesCategory = !category || item.category === category;
    const versions = item.versions;
    const matchesScope = !scope || versions.some((version) => version.scope_type === scope);
    const matchesState = !state || versions.some((version) => version.state === state);
    return matchesQuery && matchesCategory && matchesScope && matchesState;
  }), [category, props.definitions, scope, search, state]);

  if (props.loading && !props.definitions.length) return <LoadingState />;

  return (
    <div className="limits-definitions">
      <TableToolbar
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search key, name, or owner…',
        }}
        filters={(
          <>
            <Select
              variant="inline"
              label="Category"
              value={category}
              onChange={setCategory}
              options={[
                { value: '', label: 'All categories' },
                { value: 'greek', label: 'Greek' },
                { value: 'var', label: 'VaR' },
                { value: 'cvar', label: 'CVaR' },
                { value: 'stress', label: 'Stress' },
              ]}
            />
            <Select
              variant="inline"
              label="Scope"
              value={scope}
              onChange={setScope}
              options={[
                { value: '', label: 'All scopes' },
                { value: 'portfolio', label: 'Portfolio' },
                { value: 'underlying', label: 'Underlying' },
                { value: 'product_family', label: 'Product family' },
                { value: 'position', label: 'Position' },
              ]}
            />
            <Select
              variant="inline"
              label="State"
              value={state}
              onChange={setState}
              options={[
                { value: '', label: 'All states' },
                { value: 'active', label: 'Active' },
                { value: 'draft', label: 'Draft' },
                { value: 'superseded', label: 'Superseded' },
                { value: 'retired', label: 'Retired' },
              ]}
            />
            <Button variant="primary" onClick={() => setNewOpen(true)}>
              New limit
            </Button>
          </>
        )}
      />

      {!visible.length ? (
        <Empty
          message="No limit definitions match these filters."
          action={<Button onClick={() => setNewOpen(true)}>New limit</Button>}
        />
      ) : (
        <SplitLayout
          railLabel="Limit definitions"
          rail={(
            <RailList scroll>
              <div className="limits-rail-head">
                <span>DEFINITION LEDGER</span>
                <strong>{visible.length}</strong>
              </div>
              {visible.map((item) => (
                <RailItem
                  key={item.id}
                  active={item.id === props.selectedDefinitionId}
                  accent={categoryAccent(item.category)}
                  onClick={() => props.onSelectDefinition(item.id)}
                >
                  <span className="wl-rail__title">
                    #{item.id} · {item.name}
                  </span>
                  <span className="wl-rail__meta">
                    {item.key} · {item.owner}
                  </span>
                  <span className="limits-rail-state">
                    {item.active_version
                      ? `Active v${item.active_version.version}`
                      : 'No active version'}
                  </span>
                </RailItem>
              ))}
            </RailList>
          )}
          railWidth="minmax(15rem, 23rem)"
          resizable
        >
          {selected ? (
            <DefinitionDetail
              definition={selected}
              pending={props.mutationPending}
              onDraft={() => setDraftOpen(true)}
              onEditMetadata={() => setMetadataOpen(true)}
              onActivate={(version) => props.onActivateDefinitionVersion(
                selected.id,
                version.id,
                { expected_row_version: selected.row_version },
              )}
              onDeactivate={() => props.onDeactivateDefinition(
                selected.id,
                { expected_row_version: selected.row_version },
              )}
              onRetire={() => props.onRetireDefinition(
                selected.id,
                { expected_row_version: selected.row_version },
              )}
            />
          ) : (
            <Empty message="Select a definition to inspect its version ledger." />
          )}
        </SplitLayout>
      )}

      <DefinitionFormModal
        open={newOpen}
        title="New limit"
        submitLabel="Create draft limit"
        defaultPortfolioId={props.selectedPortfolioId}
        pending={props.mutationPending}
        onOpenChange={setNewOpen}
        onSubmit={async (draft) => {
          if (draft.kind !== 'create') return false;
          const saved = await props.onCreateDefinition(draft.body);
          if (saved) setNewOpen(false);
          return saved;
        }}
      />
      <DefinitionFormModal
        open={draftOpen}
        title="Create next draft"
        submitLabel="Save draft"
        base={selected?.active_version ?? selected?.versions.at(-1) ?? null}
        category={selected?.category}
        defaultPortfolioId={props.selectedPortfolioId}
        pending={props.mutationPending}
        onOpenChange={setDraftOpen}
        onSubmit={async (draft) => {
          if (draft.kind !== 'version' || !selected) return false;
          const saved = await props.onCreateDefinitionVersion(selected.id, {
            expected_row_version: selected.row_version,
            version: draft.body,
          });
          if (saved) setDraftOpen(false);
          return saved;
        }}
      />
      <MetadataModal
        definition={metadataOpen ? selected : null}
        pending={props.mutationPending}
        onClose={() => setMetadataOpen(false)}
        onSave={async (body) => {
          if (!selected) return false;
          const saved = await props.onUpdateDefinition(selected.id, body);
          if (saved) setMetadataOpen(false);
          return saved;
        }}
      />
    </div>
  );
}

function DefinitionDetail({
  definition,
  pending,
  onDraft,
  onEditMetadata,
  onActivate,
  onDeactivate,
  onRetire,
}: {
  definition: RiskLimit;
  pending: boolean;
  onDraft: () => void;
  onEditMetadata: () => void;
  onActivate: (version: LimitVersion) => void;
  onDeactivate: () => void;
  onRetire: () => void;
}) {
  const active = definition.active_version;
  const versions = [...definition.versions].sort((a, b) => b.version - a.version);
  return (
    <div className="limits-definition">
      <header className="limits-definition__head">
        <div>
          <span className="limits-definition__key">{definition.key}</span>
          <h2>{definition.name}</h2>
          <p>{definition.description || 'No description recorded.'}</p>
        </div>
        <div className="limits-definition__badges">
          <Badge variant="info">{definition.category}</Badge>
          <span>OWNER {definition.owner}</span>
          <span>ROW VERSION {definition.row_version}</span>
        </div>
      </header>

      <div className="limits-definition__actions">
        <Button variant="primary" onClick={onDraft} disabled={pending}>
          Create next draft
        </Button>
        <Button onClick={onEditMetadata} disabled={pending}>
          Edit metadata
        </Button>
        {active ? (
          <Button variant="danger" onClick={onDeactivate} disabled={pending}>
            Deactivate
          </Button>
        ) : null}
        <Button variant="ghost" onClick={onRetire} disabled={pending}>
          Retire
        </Button>
      </div>

      <Panel
        title={active ? `Active version ${active.version}` : 'No active version'}
        meta={active ? dateTime(active.effective_from) : 'Draft-only identity'}
      >
        {active ? (
          <VersionFacts version={active} />
        ) : (
          <Empty message="Activate a reviewed draft to govern monitoring." />
        )}
      </Panel>

      <Panel title="Version timeline" meta={`${versions.length} immutable revisions`}>
        <div className="limits-version-list">
          {versions.map((version, index) => (
            <article className="limits-version" key={version.id}>
              <div className="limits-version__marker" aria-hidden="true" />
              <div className="limits-version__body">
                <header>
                  <div>
                    <strong>Version {version.version}</strong>
                    <Badge variant={versionBadge(version.state)}>
                      {version.state}
                    </Badge>
                  </div>
                  <span>{dateTime(version.created_at)}</span>
                </header>
                <p>
                  {metricCode(version.metric_kind)} · {humanize(version.scope_type)}
                  {' · '}{humanize(version.aggregation)} · {thresholdSummary(version)}
                </p>
                {version.rationale ? <p>{version.rationale}</p> : null}
                {version.state === 'draft' ? (
                  <Button
                    variant="primary"
                    onClick={() => onActivate(version)}
                    disabled={pending}
                  >
                    Activate version {version.version}
                  </Button>
                ) : null}
                {versions[index + 1] ? (
                  <details>
                    <summary>Diff from version {versions[index + 1].version}</summary>
                    <pre>{versionDiff(versions[index + 1], version)}</pre>
                  </details>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function BreachesTab(props: LimitsProps) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [action, setAction] = useState<'assign' | 'comment' | 'waive' | null>(null);
  useEffect(() => {
    setAction(null);
  }, [props.selectedIncident?.row_version, props.selectedIncidentId]);
  const visible = useMemo(() => props.incidents.filter((item) => {
    const query = search.trim().toLowerCase();
    return (
      (!query
        || item.scope_label.toLowerCase().includes(query)
        || item.risk_limit?.name.toLowerCase().includes(query)
        || String(item.id).includes(query))
      && (!status || item.status === status)
      && (!severity || item.severity === severity)
    );
  }), [props.incidents, search, severity, status]);

  const columns = useMemo<Column<LimitIncident>[]>(() => [
    {
      key: 'id',
      header: 'Incident',
      width: '0.75fr',
      render: (row) => `#${row.id}`,
    },
    {
      key: 'severity',
      header: 'Severity',
      width: '0.8fr',
      render: (row) => (
        <Badge variant={row.severity === 'breach' ? 'neg' : 'warn'}>
          {row.severity}
        </Badge>
      ),
    },
    {
      key: 'scope_label',
      header: 'Definition / scope',
      width: '1.6fr',
      render: (row) => (
        <div className="limits-table-stack">
          <strong>{row.risk_limit?.name ?? `Limit #${row.risk_limit_id}`}</strong>
          <span>{row.scope_label}</span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Lifecycle',
      width: '0.75fr',
      render: (row) => (
        <span
          className="limits-incident-code"
          title={humanize(row.status)}
          aria-label={humanize(row.status)}
        >
          {incidentStatusCode(row.status)}
        </span>
      ),
    },
    {
      key: 'owner',
      header: 'Owner / assignee',
      width: '1fr',
      render: (row) => row.assignee ?? row.owner ?? 'Unassigned',
    },
    {
      key: 'last_seen_at',
      header: 'Last seen',
      width: '1fr',
      render: (row) => dateTime(row.last_seen_at),
    },
  ], []);

  if (props.loading && !props.incidents.length) return <LoadingState />;

  return (
    <div className="limits-breaches">
      <TableToolbar
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search incident, limit, or scope…',
        }}
        filters={(
          <>
            <Select
              variant="inline"
              label="Status"
              value={status}
              onChange={setStatus}
              options={[
                { value: '', label: 'All lifecycle states' },
                { value: 'open', label: 'State OPN' },
                { value: 'acknowledged', label: 'State ACK' },
                { value: 'assigned', label: 'State ASG' },
                { value: 'waived', label: 'State WVD' },
                { value: 'recovered', label: 'State RCV' },
                { value: 'resolved', label: 'State RES' },
              ]}
            />
            <Select
              variant="inline"
              label="Severity"
              value={severity}
              onChange={setSeverity}
              options={[
                { value: '', label: 'All severities' },
                { value: 'breach', label: 'Breach' },
                { value: 'warning', label: 'Warning' },
              ]}
            />
          </>
        )}
      />

      {!visible.length ? (
        <Empty message="No incidents match this ledger view." />
      ) : (
        <Table
          columns={columns}
          rows={visible}
          rowKey={(row) => row.id}
          selectedKey={props.selectedIncidentId}
          onRowClick={(row) => props.onSelectIncident(row.id)}
          className="limits-incident-table"
        />
      )}

      {props.selectedIncident ? (
        <IncidentDetail
          incident={props.selectedIncident}
          pending={props.mutationPending}
          onAcknowledge={() => props.onAcknowledgeIncident(
            props.selectedIncident!.id,
            { expected_row_version: props.selectedIncident!.row_version },
          )}
          onResolve={() => props.onResolveIncident(
            props.selectedIncident!.id,
            { expected_row_version: props.selectedIncident!.row_version },
          )}
          onReopen={() => props.onReopenIncident(
            props.selectedIncident!.id,
            { expected_row_version: props.selectedIncident!.row_version },
          )}
          onAction={setAction}
          onOpenEvaluation={props.onOpenEvaluation}
          onOpenAudit={props.onOpenAudit}
        />
      ) : null}

      <IncidentActionModal
        action={action}
        incident={props.selectedIncident}
        pending={props.mutationPending}
        onClose={() => setAction(null)}
        onAssign={async (body) => {
          if (!props.selectedIncident) return false;
          const saved = await props.onAssignIncident(props.selectedIncident.id, body);
          if (saved) setAction(null);
          return saved;
        }}
        onComment={async (body) => {
          if (!props.selectedIncident) return false;
          const saved = await props.onCommentIncident(props.selectedIncident.id, body);
          if (saved) setAction(null);
          return saved;
        }}
        onWaive={async (body) => {
          if (!props.selectedIncident) return false;
          const saved = await props.onWaiveIncident(props.selectedIncident.id, body);
          if (saved) setAction(null);
          return saved;
        }}
      />
    </div>
  );
}

function IncidentDetail({
  incident,
  pending,
  onAcknowledge,
  onResolve,
  onReopen,
  onAction,
  onOpenEvaluation,
  onOpenAudit,
}: {
  incident: LimitIncident;
  pending: boolean;
  onAcknowledge: () => void;
  onResolve: () => void;
  onReopen: () => void;
  onAction: (value: 'assign' | 'comment' | 'waive') => void;
  onOpenEvaluation?: (id: number) => void;
  onOpenAudit?: (auditRef: string) => void;
}) {
  return (
    <Panel
      title={`Incident #${incident.id}`}
      meta={`${incident.events.length} ledger events`}
      className="limits-incident-detail"
    >
      <div className="limits-incident-detail__head">
        <div>
          <div className="limits-incident-detail__statuses">
            <Badge variant={incident.severity === 'breach' ? 'neg' : 'warn'}>
              {incident.severity}
            </Badge>
            <Badge variant="ink">{humanize(incident.status)}</Badge>
            <span
              className="limits-incident-code"
              title={humanize(incident.status)}
            >
              {incidentStatusCode(incident.status)}
            </span>
          </div>
          <h2>{incident.risk_limit?.name ?? `Limit #${incident.risk_limit_id}`}</h2>
          <p>{incident.scope_label} · {humanize(incident.scope_type)}</p>
        </div>
        <div className="limits-incident-detail__version">
          ROW VERSION {incident.row_version}
        </div>
      </div>
      <div className="limits-incident-actions">
        <Button onClick={() => onAction('comment')} disabled={pending}>Comment</Button>
        {isActiveIncident(incident.status) ? (
          <>
            {incident.status === 'open' ? (
              <Button onClick={onAcknowledge} disabled={pending}>Acknowledge</Button>
            ) : null}
            <Button onClick={() => onAction('assign')} disabled={pending}>Assign</Button>
            <Button
              variant="danger"
              onClick={() => onAction('waive')}
              disabled={pending}
            >
              Waive
            </Button>
            <Button variant="primary" onClick={onResolve} disabled={pending}>
              Resolve
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={onReopen} disabled={pending}>Reopen</Button>
        )}
      </div>
      <dl className="limits-incident-facts">
        <Fact label="Owner" value={incident.owner ?? 'Unassigned'} />
        <Fact label="Assignee" value={incident.assignee ?? 'Unassigned'} />
        <Fact label="First seen" value={dateTime(incident.first_seen_at)} />
        <Fact label="Last seen" value={dateTime(incident.last_seen_at)} />
        <Fact label="Waiver expires" value={dateTime(incident.waiver_expires_at)} />
        <Fact label="Waiver rationale" value={incident.waiver_rationale ?? '—'} />
      </dl>
      <div className="limits-ledger">
        {incident.events.map((event) => (
          <article className="limits-ledger__event" key={event.id}>
            <div className="limits-ledger__rail" aria-hidden="true" />
            <div>
              <header>
                <strong>Event · {humanize(event.event_type)}</strong>
                <span>{dateTime(event.created_at)}</span>
              </header>
              <p>{event.actor}{event.mode ? ` · ${event.mode}` : ''}</p>
              {typeof event.payload.comment === 'string' ? (
                <blockquote>{event.payload.comment}</blockquote>
              ) : null}
              <div className="limits-ledger__links">
                {event.evaluation_id != null ? (
                  <Button
                    variant="ghost"
                    onClick={() => onOpenEvaluation?.(event.evaluation_id!)}
                    disabled={!onOpenEvaluation}
                  >
                    Evaluation #{event.evaluation_id}
                  </Button>
                ) : null}
                {event.audit_ref ? (
                  <Button
                    variant="ghost"
                    onClick={() => onOpenAudit?.(event.audit_ref!)}
                    disabled={!onOpenAudit}
                  >
                    {event.audit_ref}
                  </Button>
                ) : null}
              </div>
            </div>
          </article>
        ))}
      </div>
    </Panel>
  );
}

function EvidenceModal({
  evaluation,
  run,
  onClose,
}: {
  evaluation: LimitEvaluation | null;
  run: LimitMonitoringRun | null;
  onClose: () => void;
}) {
  return (
    <Modal
      open={evaluation != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Limit evidence"
      description="Frozen source links and server-produced evaluation evidence."
      defaultWidth={760}
      defaultHeight={620}
    >
      {evaluation && run && evaluation.monitoring_run_id === run.id ? (
        <div className="limits-evidence">
          <div className="limits-evidence__verdict">
            <Badge variant={evaluationBadge(evaluation.status)}>
              {evaluation.status}
            </Badge>
            <strong>{evaluation.scope_label}</strong>
            <span>{evaluation.reason ?? 'Evaluation completed with sufficient evidence.'}</span>
          </div>
          <dl className="limits-evidence__facts">
            <Fact label="Definition snapshot" value={run.definition_snapshot_hash} />
            <Fact
              label="Market evidence"
              value={
                run.effective_market_evidence_id
                ?? (
                  run.market_snapshot_id == null
                    ? '—'
                    : `snapshot #${run.market_snapshot_id}`
                )
              }
            />
            <Fact label="Observed" value={formatMetric(evaluation.observed_value)} />
            <Fact label="Adverse" value={formatMetric(evaluation.adverse_value)} />
            <Fact label="Utilization" value={formatRatio(evaluation.utilization)} />
            <Fact label="Headroom" value={formatMetric(evaluation.headroom)} />
            <Fact label="Coverage" value={formatRatio(evaluation.coverage_ratio)} />
            <Fact label="Evaluated" value={dateTime(evaluation.evaluated_at)} />
          </dl>
          <section>
            <h3>Source references</h3>
            {run.source_references.length ? run.source_references.map((source) => (
              <article className="limits-source" key={source.id}>
                <header>
                  <strong>{humanize(source.source_kind)} #{source.id}</strong>
                  <Badge variant={source.is_fresh ? 'pos' : 'warn'}>
                    {source.is_fresh ? 'Fresh' : 'Stale'}
                  </Badge>
                </header>
                <p>{source.source_status}</p>
                <pre>{pretty(source.completeness_diagnostics)}</pre>
              </article>
            )) : <Empty message="No source references were attached." />}
          </section>
          <section>
            <h3>Evaluation payload</h3>
            <pre>{pretty(
              Object.fromEntries(
                Object.entries(evaluation.evidence)
                  .filter(([key]) => key !== 'is_fresh'),
              ),
            )}</pre>
          </section>
        </div>
      ) : evaluation ? (
        <Empty message="The immutable monitoring run for this evaluation is still loading." />
      ) : null}
    </Modal>
  );
}

type DefinitionDraft =
  | { kind: 'create'; body: LimitCreateInput }
  | { kind: 'version'; body: LimitVersionInput };

function DefinitionFormModal({
  open,
  title,
  submitLabel,
  base = null,
  category,
  defaultPortfolioId,
  pending,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  title: string;
  submitLabel: string;
  base?: LimitVersion | null;
  category?: LimitCategory;
  defaultPortfolioId: number | null;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (draft: DefinitionDraft) => boolean | Promise<boolean>;
}) {
  const isCreate = base == null && title === 'New limit';
  const [identity, setIdentity] = useState({
    key: '',
    name: '',
    description: '',
    category: 'greek' as LimitCategory,
    owner: '',
    tags: '',
  });
  const [version, setVersion] = useState<LimitVersionInput>(
    () => versionInput(base, defaultPortfolioId, category ?? 'greek'),
  );
  const [methodology, setMethodology] = useState(() => pretty(base?.methodology ?? {}));
  const [scopeConfig, setScopeConfig] = useState(() => pretty(
    base?.scope_config ?? portfolioScope(defaultPortfolioId),
  ));
  const [freshness, setFreshness] = useState(() => pretty(base?.freshness_policy ?? {}));
  const [formError, setFormError] = useState<string | null>(null);

  const resetFromBase = () => {
    setIdentity({
      key: '',
      name: '',
      description: '',
      category: 'greek',
      owner: '',
      tags: '',
    });
    const next = versionInput(base, defaultPortfolioId, category ?? 'greek');
    setVersion(next);
    setMethodology(pretty(next.methodology ?? {}));
    setScopeConfig(pretty(next.scope_config ?? {}));
    setFreshness(pretty(next.freshness_policy ?? {}));
    setFormError(null);
  };

  useEffect(() => {
    if (open) resetFromBase();
    // Reset only when a modal is opened for a different immutable base target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, base?.id, category, defaultPortfolioId]);

  const updateVersion = <K extends keyof LimitVersionInput>(
    key: K,
    value: LimitVersionInput[K],
  ) => setVersion((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    try {
      const parsedMethodology = parseObject(methodology, 'Methodology');
      const parsedScope = parseObject(scopeConfig, 'Scope config');
      const parsedFreshness = parseObject(freshness, 'Freshness policy');
      const body: LimitVersionInput = {
        ...version,
        methodology: parsedMethodology,
        scope_config: parsedScope,
        freshness_policy: parsedFreshness,
        effective_until: version.effective_until
          ? localDateTimeToIso(version.effective_until, 'Effective until')
          : null,
      };
      const definitionCategory = isCreate
        ? identity.category
        : category ?? categoryForMetric(body.metric_kind);
      validateLimitVersion(body, definitionCategory);
      let saved = false;
      if (isCreate) {
        if (!identity.key.trim() || !identity.name.trim() || !identity.owner.trim()) {
          throw new Error('Key, name, and owner are required.');
        }
        if (!/^[a-z][a-z0-9_-]{2,119}$/.test(identity.key.trim())) {
          throw new Error('Key must be a lowercase stable machine identifier.');
        }
        const tags = identity.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean);
        if (new Set(tags).size !== tags.length) {
          throw new Error('Tags must be unique.');
        }
        saved = await onSubmit({
          kind: 'create',
          body: {
            key: identity.key.trim(),
            name: identity.name.trim(),
            description: identity.description.trim(),
            category: identity.category,
            owner: identity.owner.trim(),
            tags,
            initial_version: body,
          },
        });
      } else {
        saved = await onSubmit({ kind: 'version', body });
      }
      if (!saved) {
        setFormError('The server rejected this change. Review the page feedback and retry.');
        return;
      }
      resetFromBase();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) resetFromBase();
      }}
      title={title}
      description="Drafts are reversible. Activation is always a separate governed action."
      defaultWidth={920}
      defaultHeight={760}
    >
      <div className="limits-form">
        {isCreate ? (
          <fieldset className="limits-form__section">
            <legend>Stable identity</legend>
            <div className="limits-form__grid">
              <Input
                label="Key"
                value={identity.key}
                onChange={(event) => setIdentity({ ...identity, key: event.target.value })}
              />
              <Input
                label="Name"
                value={identity.name}
                onChange={(event) => setIdentity({ ...identity, name: event.target.value })}
              />
              <Input
                label="Owner"
                value={identity.owner}
                onChange={(event) => setIdentity({ ...identity, owner: event.target.value })}
              />
              <Select
                label="Category"
                value={identity.category}
                onChange={(value) => {
                  const nextCategory = value as LimitCategory;
                  const nextVersion = versionInput(
                    null,
                    defaultPortfolioId,
                    nextCategory,
                  );
                  setIdentity({ ...identity, category: nextCategory });
                  setVersion(nextVersion);
                  setMethodology(pretty(nextVersion.methodology ?? {}));
                  setScopeConfig(pretty(nextVersion.scope_config ?? {}));
                  setFreshness(pretty(nextVersion.freshness_policy ?? {}));
                }}
                options={[
                  { value: 'greek', label: 'Greek' },
                  { value: 'var', label: 'VaR' },
                  { value: 'cvar', label: 'CVaR' },
                  { value: 'stress', label: 'Stress' },
                ]}
              />
              <Input
                label="Tags"
                value={identity.tags}
                placeholder="intraday, board"
                onChange={(event) => setIdentity({ ...identity, tags: event.target.value })}
              />
              <Input
                label="Description"
                value={identity.description}
                onChange={(event) => setIdentity({
                  ...identity,
                  description: event.target.value,
                })}
              />
            </div>
          </fieldset>
        ) : null}

        <fieldset className="limits-form__section">
          <legend>Metric and source</legend>
          <div className="limits-form__grid">
            <Select
              label="Metric"
              value={version.metric_kind}
              onChange={(value) => {
                const next = withMetricDefaults(
                  version,
                  value as LimitMetricKind,
                );
                setVersion(next);
                setMethodology(pretty(next.methodology ?? {}));
              }}
              options={metricOptionsForCategory(
                isCreate
                  ? identity.category
                  : category ?? categoryForMetric(version.metric_kind),
              )}
            />
            <Select
              label="Source"
              value={version.source_kind}
              onChange={(value) => {
                const source = value as LimitVersionInput['source_kind'];
                let nextMethodology = version.methodology ?? {};
                if (version.metric_kind === 'var' || version.metric_kind === 'cvar') {
                  nextMethodology = source === 'scenario_test'
                    ? {
                        method: 'scenario_distribution',
                        confidence: 0.95,
                        horizon: 'scenario_set',
                        scaling: 'none',
                      }
                    : methodologyForMetric(version.metric_kind);
                } else if (categoryForMetric(version.metric_kind) === 'greek') {
                  nextMethodology = {};
                }
                setVersion({
                  ...version,
                  source_kind: source,
                  methodology: nextMethodology,
                });
                setMethodology(pretty(nextMethodology));
              }}
              options={[
                { value: 'risk_run', label: 'Risk run' },
                { value: 'scenario_test', label: 'Scenario test' },
                { value: 'backtest', label: 'Backtest' },
              ]}
            />
            <Select
              label="Scope"
              value={version.scope_type}
              onChange={(value) => {
                const nextScope = value as LimitScopeType;
                updateVersion('scope_type', nextScope);
                setScopeConfig(pretty(defaultScopeConfig(
                  nextScope,
                  defaultPortfolioId,
                )));
              }}
              options={[
                { value: 'portfolio', label: 'Portfolio' },
                { value: 'underlying', label: 'Underlying' },
                { value: 'product_family', label: 'Product family' },
                { value: 'position', label: 'Position' },
              ]}
            />
            <Select
              label="Aggregation"
              value={version.aggregation}
              onChange={(value) => updateVersion(
                'aggregation',
                value as LimitVersionInput['aggregation'],
              )}
              options={[
                { value: 'net', label: 'Net' },
                { value: 'gross_abs', label: 'Gross absolute' },
                { value: 'max_abs', label: 'Maximum absolute' },
                { value: 'minimum', label: 'Minimum' },
                { value: 'maximum', label: 'Maximum' },
              ]}
            />
            <Select
              label="Transform"
              value={version.transform}
              onChange={(value) => updateVersion(
                'transform',
                value as LimitVersionInput['transform'],
              )}
              options={[
                { value: 'signed', label: 'Signed' },
                { value: 'absolute', label: 'Absolute' },
                { value: 'loss_magnitude', label: 'Loss magnitude' },
              ]}
            />
            <Select
              label="Comparator"
              value={version.comparator}
              onChange={(value) => updateVersion('comparator', value as LimitComparator)}
              options={[
                { value: 'upper', label: 'Upper' },
                { value: 'lower', label: 'Lower' },
                { value: 'range', label: 'Range' },
              ]}
            />
          </div>
          <div className="limits-form__grid limits-form__grid--wide">
            <TextAreaField
              label="Methodology"
              value={methodology}
              onChange={setMethodology}
            />
            <TextAreaField
              label="Scope config"
              value={scopeConfig}
              onChange={setScopeConfig}
            />
          </div>
        </fieldset>

        <fieldset className="limits-form__section">
          <legend>Thresholds and evidence policy</legend>
          <div className="limits-form__grid">
            <OptionalNumber
              label="Warning lower"
              value={version.warning_lower}
              onChange={(value) => updateVersion('warning_lower', value)}
            />
            <OptionalNumber
              label="Warning upper"
              value={version.warning_upper}
              onChange={(value) => updateVersion('warning_upper', value)}
            />
            <OptionalNumber
              label="Hard lower"
              value={version.hard_lower}
              onChange={(value) => updateVersion('hard_lower', value)}
            />
            <OptionalNumber
              label="Hard upper"
              value={version.hard_upper}
              onChange={(value) => updateVersion('hard_upper', value)}
            />
            <Input
              label="Unit"
              value={version.unit}
              onChange={(event) => updateVersion('unit', event.target.value)}
            />
            <Input
              label="Reporting currency"
              value={version.currency ?? ''}
              placeholder="USD"
              onChange={(event) => updateVersion(
                'currency',
                event.target.value || null,
              )}
            />
            <Input
              label="Greek bump convention"
              value={version.bump_convention ?? ''}
              placeholder="per +1 percentage point"
              onChange={(event) => updateVersion(
                'bump_convention',
                event.target.value || null,
              )}
            />
            <Input
              label="Effective until"
              type="datetime-local"
              value={version.effective_until ?? ''}
              onChange={(event) => updateVersion(
                'effective_until',
                event.target.value || null,
              )}
            />
            <Input
              label="Rationale"
              value={version.rationale ?? ''}
              onChange={(event) => updateVersion(
                'rationale',
                event.target.value || null,
              )}
            />
            <TextAreaField
              label="Freshness policy"
              value={freshness}
              onChange={setFreshness}
            />
          </div>
        </fieldset>

        {formError ? <div className="limits-form__error" role="alert">{formError}</div> : null}
        <div className="limits-form__actions">
          <Button variant="primary" disabled={pending} onClick={() => void submit()}>
            {submitLabel}
          </Button>
          <Button disabled={pending} onClick={() => onOpenChange(false)}>Cancel</Button>
        </div>
      </div>
    </Modal>
  );
}

function MetadataModal({
  definition,
  pending,
  onClose,
  onSave,
}: {
  definition: RiskLimit | null;
  pending: boolean;
  onClose: () => void;
  onSave: (body: LimitMetadataPatchInput) => boolean | Promise<boolean>;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [owner, setOwner] = useState('');
  const [tags, setTags] = useState('');
  useEffect(() => {
    if (!definition) return;
    setName(definition.name);
    setDescription(definition.description);
    setOwner(definition.owner);
    setTags(definition.tags.join(', '));
  }, [definition?.id]);
  return (
    <Modal
      open={definition != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Edit limit metadata"
      description="Active version fields remain immutable; this edits only stable identity metadata."
    >
      {definition ? (
        <div className="limits-form">
          <Input label="Name" value={name} onChange={(event) => setName(event.target.value)} />
          <Input label="Description" value={description} onChange={(event) => setDescription(event.target.value)} />
          <Input label="Owner" value={owner} onChange={(event) => setOwner(event.target.value)} />
          <Input label="Tags" value={tags} onChange={(event) => setTags(event.target.value)} />
          <div className="limits-form__actions">
            <Button
              variant="primary"
              disabled={pending || !name.trim() || !owner.trim()}
              onClick={() => void onSave({
                  expected_row_version: definition.row_version,
                  name: name.trim(),
                  description: description.trim(),
                  owner: owner.trim(),
                  tags: tags
                    .split(',')
                    .map((tag) => tag.trim())
                    .filter(Boolean),
                })}
            >
              Save metadata
            </Button>
            <Button disabled={pending} onClick={onClose}>Cancel</Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

function IncidentActionModal({
  action,
  incident,
  pending,
  onClose,
  onAssign,
  onComment,
  onWaive,
}: {
  action: 'assign' | 'comment' | 'waive' | null;
  incident: LimitIncident | null;
  pending: boolean;
  onClose: () => void;
  onAssign: (body: LimitIncidentAssignInput) => boolean | Promise<boolean>;
  onComment: (body: LimitIncidentCommentInput) => boolean | Promise<boolean>;
  onWaive: (body: LimitIncidentWaiveInput) => boolean | Promise<boolean>;
}) {
  const [assignee, setAssignee] = useState('');
  const [comment, setComment] = useState('');
  const [rationale, setRationale] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const rowVersion = incident?.row_version ?? 0;
  useEffect(() => {
    setAssignee(incident?.assignee ?? '');
    setComment('');
    setRationale('');
    setExpiresAt('');
  }, [action, incident?.id]);
  return (
    <Modal
      open={action != null && incident != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={
        action === 'assign'
          ? 'Assign incident'
          : action === 'comment'
            ? 'Comment on incident'
            : 'Waive incident'
      }
      description={
        incident
          ? `Incident #${incident.id} · row version ${incident.row_version}`
          : undefined
      }
    >
      <div className="limits-form">
        {action === 'assign' ? (
          <Input label="Assignee" value={assignee} onChange={(event) => setAssignee(event.target.value)} />
        ) : null}
        {action === 'comment' ? (
          <TextAreaField label="Comment" value={comment} onChange={setComment} />
        ) : null}
        {action === 'waive' ? (
          <>
            <TextAreaField
              label="Waiver rationale"
              value={rationale}
              onChange={setRationale}
            />
            <Input
              label="Waiver expires at"
              type="datetime-local"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
          </>
        ) : null}
        <div className="limits-form__actions">
          {action === 'assign' ? (
            <Button
              variant="primary"
              disabled={pending || !assignee.trim()}
              onClick={() => void onAssign({
                expected_row_version: rowVersion,
                assignee: assignee.trim(),
              })}
            >
              Save assignment
            </Button>
          ) : null}
          {action === 'comment' ? (
            <Button
              variant="primary"
              disabled={pending || !comment.trim()}
              onClick={() => void onComment({
                expected_row_version: rowVersion,
                comment: comment.trim(),
              })}
            >
              Add comment
            </Button>
          ) : null}
          {action === 'waive' ? (
            <Button
              variant="danger"
              disabled={pending || !rationale.trim() || !expiresAt}
              onClick={() => void onWaive({
                expected_row_version: rowVersion,
                rationale: rationale.trim(),
                expires_at: localDateTimeToIso(expiresAt, 'Waiver expiry'),
              })}
            >
              Confirm waiver
            </Button>
          ) : null}
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Modal>
  );
}

function VersionFacts({ version }: { version: LimitVersion }) {
  return (
    <dl className="limits-version-facts">
      <Fact label="Metric" value={metricLabel(version.metric_kind)} />
      <Fact label="Source" value={humanize(version.source_kind)} />
      <Fact label="Scope" value={humanize(version.scope_type)} />
      <Fact label="Aggregation" value={humanize(version.aggregation)} />
      <Fact label="Transform" value={humanize(version.transform)} />
      <Fact label="Comparator" value={humanize(version.comparator)} />
      <Fact label="Thresholds" value={thresholdSummary(version)} />
      <Fact label="Unit" value={version.unit} />
      <Fact label="Currency" value={version.currency ?? 'Not applicable'} />
      <Fact label="Bump convention" value={version.bump_convention ?? 'Not applicable'} />
      <Fact label="Effective from" value={dateTime(version.effective_from)} />
      <Fact label="Effective until" value={dateTime(version.effective_until)} />
    </dl>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function OptionalNumber({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: number | null;
  onChange: (value: number | null) => void;
}) {
  return (
    <Input
      label={label}
      type="number"
      value={value ?? ''}
      onChange={(event) => (
        onChange(event.target.value === '' ? null : Number(event.target.value))
      )}
    />
  );
}

function TextAreaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `limits-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  return (
    <div className="wl-field">
      <label className="wl-field__label" htmlFor={id}>{label}</label>
      <textarea
        id={id}
        className="limits-textarea"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function DeferredTab({
  symbol,
  title,
  copy,
}: {
  symbol: string;
  title: string;
  copy: string;
}) {
  return (
    <div className="limits-deferred">
      <span className="limits-deferred__symbol" aria-hidden="true">{symbol}</span>
      <span className="limits-deferred__eyebrow">NEXT CONTROL PLANE</span>
      <h2>{title}</h2>
      <p>{copy}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="limits-loading" aria-busy="true">
      <span>Loading Limits…</span>
      <Skeleton height={96} />
      <Skeleton height={240} />
    </div>
  );
}

const METRIC_OPTIONS = [
  { value: 'delta', label: 'Delta' },
  { value: 'gamma', label: 'Gamma' },
  { value: 'vega', label: 'Vega' },
  { value: 'theta', label: 'Theta' },
  { value: 'rho', label: 'Rho' },
  { value: 'rho_q', label: 'RhoQ' },
  { value: 'var', label: 'VaR' },
  { value: 'cvar', label: 'CVaR' },
  { value: 'stress_pnl', label: 'Stress P&L' },
];

function metricOptionsForCategory(category: LimitCategory) {
  return METRIC_OPTIONS.filter((option) => (
    categoryForMetric(option.value as LimitMetricKind) === category
  ));
}

function categoryForMetric(metric: LimitMetricKind): LimitCategory {
  if (metric === 'stress_pnl') return 'stress';
  if (metric === 'var' || metric === 'cvar') return metric;
  return 'greek';
}

function portfolioScope(portfolioId: number | null): Record<string, unknown> {
  return { portfolio_ids: portfolioId == null ? [] : [portfolioId] };
}

function defaultScopeConfig(
  scope: LimitScopeType,
  portfolioId: number | null,
): Record<string, unknown> {
  if (scope === 'portfolio') return portfolioScope(portfolioId);
  if (scope === 'underlying' || scope === 'product_family') {
    return { all_in_portfolio: true };
  }
  return { position_ids: [] };
}

function methodologyForMetric(metric: LimitMetricKind): Record<string, unknown> {
  if (metric === 'var' || metric === 'cvar') {
    return {
      method: 'historical',
      confidence: 0.95,
      horizon: '1_trading_day',
      scaling: 'none',
    };
  }
  if (metric === 'stress_pnl') {
    return {
      selection: 'named',
      scenario_set_hash: '',
      scenario_name: '',
    };
  }
  return {};
}

function versionInput(
  base: LimitVersion | null | undefined,
  defaultPortfolioId: number | null,
  category: LimitCategory,
): LimitVersionInput {
  if (base) {
    return {
      metric_kind: base.metric_kind,
      source_kind: base.source_kind,
      methodology: { ...base.methodology },
      scope_type: base.scope_type,
      scope_config: { ...base.scope_config },
      aggregation: base.aggregation,
      transform: base.transform,
      comparator: base.comparator,
      warning_lower: base.warning_lower,
      warning_upper: base.warning_upper,
      hard_lower: base.hard_lower,
      hard_upper: base.hard_upper,
      unit: base.unit,
      currency: base.currency,
      bump_convention: base.bump_convention,
      freshness_policy: { ...base.freshness_policy },
      effective_until: toDateTimeLocal(base.effective_until),
      rationale: base.rationale,
    };
  }
  const metric: LimitMetricKind =
    category === 'var'
      ? 'var'
      : category === 'cvar'
        ? 'cvar'
        : category === 'stress'
          ? 'stress_pnl'
          : 'delta';
  const isTail = category !== 'greek';
  return {
    metric_kind: metric,
    source_kind: category === 'stress' ? 'scenario_test' : isTail ? 'backtest' : 'risk_run',
    methodology: methodologyForMetric(metric),
    scope_type: 'portfolio',
    scope_config: portfolioScope(defaultPortfolioId),
    aggregation: 'net',
    transform: isTail ? 'loss_magnitude' : 'absolute',
    comparator: 'upper',
    warning_lower: null,
    warning_upper: 80,
    hard_lower: null,
    hard_upper: 100,
    unit: isTail ? 'USD' : 'underlying_units',
    currency: isTail ? 'USD' : null,
    bump_convention: null,
    freshness_policy: { max_age_seconds: 300 },
    effective_until: null,
    rationale: null,
  };
}

function withMetricDefaults(
  current: LimitVersionInput,
  metric: LimitMetricKind,
): LimitVersionInput {
  const category = categoryForMetric(metric);
  const greek = category === 'greek';
  const monetaryGreek = ['vega', 'theta', 'rho', 'rho_q'].includes(metric);
  return {
    ...current,
    metric_kind: metric,
    source_kind:
      category === 'stress'
        ? 'scenario_test'
        : greek
          ? 'risk_run'
          : current.source_kind === 'scenario_test'
            ? 'scenario_test'
            : 'backtest',
    methodology:
      greek
        ? {}
        : current.source_kind === 'scenario_test' && category !== 'stress'
          ? {
              method: 'scenario_distribution',
              confidence: 0.95,
              horizon: 'scenario_set',
              scaling: 'none',
            }
          : methodologyForMetric(metric),
    transform: greek ? (
      current.transform === 'loss_magnitude' ? 'absolute' : current.transform
    ) : 'loss_magnitude',
    comparator: greek ? current.comparator : 'upper',
    warning_lower: greek ? current.warning_lower : null,
    hard_lower: greek ? current.hard_lower : null,
    currency: monetaryGreek || !greek ? current.currency ?? 'USD' : null,
    bump_convention:
      metric === 'rho' || metric === 'rho_q'
        ? current.bump_convention ?? 'per +1 percentage point'
        : null,
    unit: greek
      ? current.unit === 'USD' ? 'underlying_units' : current.unit
      : current.unit === 'underlying_units' ? 'USD' : current.unit,
  };
}

function validateLimitVersion(
  version: LimitVersionInput,
  category: LimitCategory,
) {
  if (categoryForMetric(version.metric_kind) !== category) {
    throw new Error('The metric must match the definition category.');
  }
  validateScope(version);
  validateThresholds(version);
  validateMethodology(version);
  if (!version.unit.trim()) throw new Error('Unit is required.');
  if (
    ['vega', 'theta', 'rho', 'rho_q', 'var', 'cvar', 'stress_pnl']
      .includes(version.metric_kind)
    && !version.currency?.trim()
  ) {
    throw new Error('Reporting currency is required for this metric.');
  }
  if (version.currency != null && !/^[A-Za-z]{3}$/.test(version.currency.trim())) {
    throw new Error('Reporting currency must be a three-letter ISO code.');
  }
  if (
    ['rho', 'rho_q'].includes(version.metric_kind)
    && !version.bump_convention?.trim()
  ) {
    throw new Error('Rho and RhoQ require a Greek bump convention.');
  }
  const maxAge = version.freshness_policy?.max_age_seconds;
  const freshnessKeys = Object.keys(version.freshness_policy ?? {});
  if (
    freshnessKeys.some(
      (key) => !['max_age_seconds', 'allow_profile_dated'].includes(key),
    )
  ) {
    throw new Error('Freshness policy contains unsupported fields.');
  }
  if (!Number.isInteger(maxAge) || (maxAge as number) < 0) {
    throw new Error('Freshness policy requires a non-negative integer max_age_seconds.');
  }
  if (
    Object.hasOwn(version.freshness_policy ?? {}, 'allow_profile_dated')
    && typeof version.freshness_policy?.allow_profile_dated !== 'boolean'
  ) {
    throw new Error('allow_profile_dated must be boolean.');
  }
}

function validateScope(version: LimitVersionInput) {
  const config = version.scope_config ?? {};
  const keys = Object.keys(config);
  const positiveIds = (value: unknown) => (
    Array.isArray(value)
    && value.length > 0
    && value.every((item) => Number.isInteger(item) && (item as number) > 0)
    && new Set(value).size === value.length
  );
  const strings = (value: unknown) => {
    if (
      !Array.isArray(value)
      || !value.length
      || !value.every((item) => typeof item === 'string' && item.trim())
    ) return false;
    const normalized = value.map((item) => (item as string).trim());
    return new Set(normalized).size === normalized.length;
  };
  if (
    version.scope_type === 'portfolio'
    && !(keys.length === 1 && keys[0] === 'portfolio_ids' && positiveIds(config.portfolio_ids))
  ) {
    throw new Error('Portfolio scope requires a non-empty portfolio_ids list.');
  }
  if (
    version.scope_type === 'position'
    && !(keys.length === 1 && keys[0] === 'position_ids' && positiveIds(config.position_ids))
  ) {
    throw new Error('Position scope requires a non-empty position_ids list.');
  }
  if (version.scope_type === 'underlying') {
    const valid = (
      keys.length === 1
      && (
        (keys[0] === 'symbols' && strings(config.symbols))
        || (keys[0] === 'all_in_portfolio' && config.all_in_portfolio === true)
      )
    );
    if (!valid) {
      throw new Error('Underlying scope requires symbols or all_in_portfolio.');
    }
  }
  if (version.scope_type === 'product_family') {
    const valid = (
      keys.length === 1
      && (
        (keys[0] === 'families' && strings(config.families))
        || (keys[0] === 'all_in_portfolio' && config.all_in_portfolio === true)
      )
    );
    if (!valid) {
      throw new Error('Product-family scope requires families or all_in_portfolio.');
    }
  }
}

function validateThresholds(version: LimitVersionInput) {
  const thresholds = [
    version.warning_lower,
    version.warning_upper,
    version.hard_lower,
    version.hard_upper,
  ];
  if (thresholds.some((value) => value != null && !Number.isFinite(value))) {
    throw new Error('Thresholds must be finite numbers or blank.');
  }
  if (version.transform === 'loss_magnitude' && ![
    'var',
    'cvar',
    'stress_pnl',
  ].includes(version.metric_kind)) {
    throw new Error('Greek limits do not support loss magnitude.');
  }
  if (
    ['var', 'cvar', 'stress_pnl'].includes(version.metric_kind)
    && version.transform !== 'loss_magnitude'
  ) {
    throw new Error('VaR, CVaR, and stress limits require loss magnitude.');
  }
  if (version.transform === 'absolute' || version.transform === 'loss_magnitude') {
    if (
      version.comparator !== 'upper'
      || version.warning_lower != null
      || version.hard_lower != null
      || version.warning_upper == null
      || version.hard_upper == null
      || version.warning_upper < 0
      || version.hard_upper <= 0
      || version.warning_upper >= version.hard_upper
    ) {
      throw new Error(
        'Absolute and loss limits require 0 <= warning upper < hard upper.',
      );
    }
    return;
  }
  if (version.comparator === 'upper') {
    if (
      version.warning_lower != null
      || version.hard_lower != null
      || version.warning_upper == null
      || version.hard_upper == null
      || version.warning_upper < 0
      || version.hard_upper <= 0
      || version.warning_upper >= version.hard_upper
    ) {
      throw new Error(
        'Signed upper limits require 0 <= warning upper < hard upper.',
      );
    }
    return;
  }
  if (version.comparator === 'lower') {
    if (
      version.warning_upper != null
      || version.hard_upper != null
      || version.warning_lower == null
      || version.hard_lower == null
      || version.warning_lower > 0
      || version.hard_lower >= 0
      || version.hard_lower >= version.warning_lower
    ) {
      throw new Error(
        'Signed lower limits require hard lower < warning lower <= 0.',
      );
    }
    return;
  }
  if (
    version.warning_lower == null
    || version.warning_upper == null
    || version.hard_lower == null
    || version.hard_upper == null
    || !(
      version.hard_lower < version.warning_lower
      && version.warning_lower < 0
      && 0 < version.warning_upper
      && version.warning_upper < version.hard_upper
    )
  ) {
    throw new Error(
      'Range limits require hard lower < warning lower < 0 < warning upper < hard upper.',
    );
  }
}

function validateMethodology(version: LimitVersionInput) {
  const methodology = version.methodology ?? {};
  if (categoryForMetric(version.metric_kind) === 'greek') {
    if (version.source_kind !== 'risk_run' || Object.keys(methodology).length) {
      throw new Error('Greek limits require risk_run evidence and empty methodology.');
    }
    return;
  }
  if (version.metric_kind === 'var' || version.metric_kind === 'cvar') {
    const expected = version.source_kind === 'scenario_test'
      ? {
          method: 'scenario_distribution',
          confidence: 0.95,
          horizon: 'scenario_set',
          scaling: 'none',
        }
      : version.source_kind === 'backtest'
        ? {
            method: 'historical',
            confidence: 0.95,
            horizon: '1_trading_day',
            scaling: 'none',
          }
        : null;
    if (
      !expected
      || Object.keys(methodology).length !== Object.keys(expected).length
      || Object.entries(expected).some(([key, value]) => methodology[key] !== value)
    ) {
      throw new Error('VaR/CVaR methodology must match the selected v1 source contract.');
    }
    return;
  }
  if (
    version.source_kind !== 'scenario_test'
    || !['named', 'worst_of_set'].includes(String(methodology.selection))
    || !/^sha256:[0-9a-f]{64}$/.test(String(methodology.scenario_set_hash))
  ) {
    throw new Error(
      'Stress limits require scenario_test evidence and a canonical scenario selection.',
    );
  }
  if (
    methodology.selection === 'named'
    && (
      Object.keys(methodology).sort().join(',') !== 'scenario_name,scenario_set_hash,selection'
      || typeof methodology.scenario_name !== 'string'
      || !methodology.scenario_name.trim()
    )
  ) {
    throw new Error('Named stress selection requires exactly one scenario name.');
  }
  if (
    methodology.selection === 'worst_of_set'
    && (
      Object.keys(methodology).sort().join(',') !== 'scenario_names,scenario_set_hash,selection'
      || !Array.isArray(methodology.scenario_names)
      || !methodology.scenario_names.length
      || !methodology.scenario_names.every(
        (name) => typeof name === 'string' && name.trim(),
      )
      || new Set(
        methodology.scenario_names.map((name) => String(name).trim()),
      ).size !== methodology.scenario_names.length
    )
  ) {
    throw new Error('Worst-of stress selection requires scenario names.');
  }
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed == null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function evaluationBadge(status: LimitEvaluation['status']): BadgeVariant {
  if (status === 'breach') return 'neg';
  if (status === 'warning') return 'warn';
  if (status === 'unknown') return 'info';
  return 'pos';
}

function runBadge(status: LimitMonitoringRun['status']): BadgeVariant {
  if (status === 'failed') return 'neg';
  if (status === 'completed_with_unknowns') return 'warn';
  if (status === 'completed') return 'pos';
  return 'info';
}

function runStatusLabel(status: LimitMonitoringRun['status']): string {
  return status === 'completed_with_unknowns'
    ? 'Unknown evidence'
    : humanize(status);
}

function versionBadge(state: LimitVersion['state']): BadgeVariant {
  if (state === 'active') return 'pos';
  if (state === 'draft') return 'info';
  return 'ink';
}

function categoryAccent(category: LimitCategory): string {
  if (category === 'greek') return '--info';
  if (category === 'stress') return '--warn';
  return '--neg';
}

function categoryLabel(category: string): string {
  if (category === 'var') return 'VaR';
  if (category === 'cvar') return 'CVaR';
  return humanize(category);
}

function metricLabel(metric: LimitMetricKind): string {
  if (metric === 'rho_q') return 'RhoQ';
  if (metric === 'var') return 'VaR';
  if (metric === 'cvar') return 'CVaR';
  if (metric === 'stress_pnl') return 'Stress P&L';
  return metric.charAt(0).toUpperCase() + metric.slice(1);
}

function metricCode(metric: LimitMetricKind): string {
  if (metric === 'rho_q') return 'ρq';
  if (metric === 'stress_pnl') return 'STRESS P&L';
  return metric.toUpperCase();
}

function thresholdSummary(version: Pick<
  LimitVersion,
  'comparator' | 'hard_lower' | 'hard_upper' | 'warning_lower' | 'warning_upper'
>): string {
  const warning = version.comparator === 'lower'
    ? formatMetric(version.warning_lower)
    : version.comparator === 'upper'
      ? formatMetric(version.warning_upper)
      : `${formatMetric(version.warning_lower)} – ${formatMetric(version.warning_upper)}`;
  const hard = version.comparator === 'lower'
    ? formatMetric(version.hard_lower)
    : version.comparator === 'upper'
      ? formatMetric(version.hard_upper)
      : `${formatMetric(version.hard_lower)} – ${formatMetric(version.hard_upper)}`;
  return `warning ${warning} · hard ${hard}`;
}

function boundaryLabel(row: LimitEvaluation): string {
  if (row.governing_boundary === 'lower') return formatMetric(row.hard_lower);
  if (row.governing_boundary === 'upper') return formatMetric(row.hard_upper);
  if (row.hard_lower != null || row.hard_upper != null) {
    return `${formatMetric(row.hard_lower)} – ${formatMetric(row.hard_upper)}`;
  }
  return '—';
}

function incidentStatusCode(status: LimitIncident['status']): string {
  return {
    open: 'OPN',
    acknowledged: 'ACK',
    assigned: 'ASG',
    waived: 'WVD',
    recovered: 'RCV',
    resolved: 'RES',
  }[status];
}

function isActiveIncident(status: LimitIncident['status']): boolean {
  return ['open', 'acknowledged', 'assigned', 'waived'].includes(status);
}

function versionDiff(previous: LimitVersion, current: LimitVersion): string {
  const changed = Object.fromEntries(
    Object.entries(current)
      .filter(([key, value]) => (
        !['id', 'risk_limit_id', 'version', 'created_at', 'activated_at'].includes(key)
        && JSON.stringify(value) !== JSON.stringify(
          (previous as unknown as Record<string, unknown>)[key],
        )
      ))
      .map(([key, value]) => [key, {
        from: (previous as unknown as Record<string, unknown>)[key],
        to: value,
      }]),
  );
  return pretty(changed);
}

function formatCount(value: number | null | undefined): string {
  return value == null ? '—' : new Intl.NumberFormat('en-US').format(value);
}

function summaryFromRun(
  run: LimitMonitoringRun | null,
): Partial<LimitDashboard['summary']> | undefined {
  if (!run) return undefined;
  const value = (key: string, fallback: string): number | undefined => {
    const candidate = run.summary[key] ?? run.summary[fallback];
    return typeof candidate === 'number' && Number.isFinite(candidate)
      ? candidate
      : undefined;
  };
  return {
    breaches: value('breaches', 'breach'),
    warnings: value('warnings', 'warning'),
    unknowns: value('unknowns', 'unknown'),
    ok: value('ok', 'ok'),
  };
}

function formatMetric(value: number | null | undefined): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 4,
  }).format(value);
}

function formatRatio(value: number | null | undefined): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(value);
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return '—';
  return value.match(/^\d{4}-\d{2}-\d{2}/)?.[0] ?? value;
}

function dateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = parseServerDateTime(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed);
}

function toDateTimeLocal(value: string | null | undefined): string {
  if (!value) return '';
  const parsed = parseServerDateTime(value);
  if (!Number.isFinite(parsed.getTime())) return value.slice(0, 16);
  const offset = parsed.getTimezoneOffset() * 60_000;
  return new Date(parsed.getTime() - offset).toISOString().slice(0, 16);
}

function localDateTimeToIso(value: string, label: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    throw new Error(`${label} must be a valid local date and time.`);
  }
  return parsed.toISOString();
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ');
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
