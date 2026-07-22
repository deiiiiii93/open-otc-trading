/* eslint-disable @typescript-eslint/no-explicit-any */
export type Route =
  | 'chat'
  | 'rfq'
  | 'try-solve'
  | 'positions'
  | 'booking'
  | 'pricing-parameters'
  | 'engine-configs'
  | 'portfolios'
  | 'risk'
  | 'greeks-landscape'
  | 'scenario-test'
  | 'backtest'
  | 'tasks'
  | 'reports'
  | 'client'
  | 'client-rfq'
  | 'hedging'
  | 'instruments'
  | 'skills'
  | 'tracing'
  | 'arena'
  | 'workflows'
  | 'memory'
  | 'model-maintenance'
  | 'audit'
  | 'limits';

export interface AgentRegistryModel {
  id: string;
  provider: string;
  label: string;
  description: string | null;
  tags: string[];
  protocol: string | null;
}

export interface AgentRegistryChannel {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  base_url: string;
  anthropic_base_url: string | null;
  api_key_env: string | null;
  healthy: boolean;
  models: AgentRegistryModel[];
}

export interface AgentRegistry {
  default: { channel: string; model: string };
  channels: AgentRegistryChannel[];
}

export type ModelWrite = {
  id: string;
  provider: string;
  label: string;
  description?: string | null;
  tags: string[];
  protocol?: string | null;
};

export type ChannelWrite = {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  base_url: string;
  anthropic_base_url?: string | null;
  api_key_env?: string | null;
  models?: ModelWrite[];
};

export interface MemoryFact {
  id: number;
  scope_type: 'user' | 'book' | 'domain' | 'correction';
  scope_id: string;
  content: string;
  confidence: number;
  status: 'proposed' | 'approved' | 'active' | 'archived';
  category: string | null;
  source_error: boolean;
  pinned: boolean;
  created_by: string;
  extractor_model: string | null;
  source_session_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryStatus {
  enabled: boolean;
  config: {
    confidence_floor: number;
    max_facts_per_scope: number;
    max_correction_facts: number;
    injection_token_budget: number;
    correction_token_budget: number;
  };
  counts: Record<string, Record<string, number>>;
}

export type WorkflowParam = {
  name: string;
  label: string;
  type: 'string' | 'date' | 'portfolio';
};

export type DeskWorkflowSummary = {
  slug: string;
  title: string;
  persona: 'trader' | 'risk_manager' | 'sales' | 'quant';
  description: string;
  scope: 'local' | 'shared';
  default_mode: 'auto' | 'yolo';
  source: 'seed' | 'user';
  params?: WorkflowParam[];
};

export type DeskWorkflow = DeskWorkflowSummary & { script: string };

export type Thread = {
  id: number;
  title: string;
  character: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
  messages: ChatMessage[];
};

export type TracingConfig = {
  mode: 'local' | 'langsmith' | 'both' | 'off';
  langsmith_url: string | null;
};

export type TraceSummary = {
  id: string;
  trace_id: string;
  name: string;
  run_type: string;
  status: 'running' | 'success' | 'error';
  start_time: string;
  end_time: string | null;
  total_tokens: number | null;
  thread_id: number | null;
  task_id: number | null;
  workflow_id: number | null;
};

export type TraceRunNode = TraceSummary & {
  parent_run_id: string | null;
  dotted_order: string;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  inputs_preview: string | null;
  inputs_truncated: boolean;
  outputs_preview: string | null;
  outputs_truncated: boolean;
};

export type TraceRunDetail = TraceSummary & {
  parent_run_id: string | null;
  dotted_order: string;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  inputs: string | null;
  outputs: string | null;
  extra: string | null;
};

export type AsyncAgentTask = {
  task_id: number;
  description: string;
  status: string;
  awaiting_approval: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  last_message_preview?: string | null;
};

export type ReplyOptionMeta = {
  label: string;
  description?: string;
  value?: string;
};

export type ChoiceMeta = {
  label: string;
  value: string | number;
};

export type TermFormField = {
  key: string;
  label: string;
  help?: string;
  type: 'percent' | 'number' | 'date' | 'enum' | 'text';
  choices?: ChoiceMeta[];
  default?: ChoiceMeta;
  required?: boolean;
};

export type TermFormMeta = {
  title: string;
  subtitle?: string;
  fields: TermFormField[];
  submit_label?: string;
};

export type AgentTodoItem = {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
};

/**
 * Canonical agent execution mode sent on the chat request.
 * - `interactive` — HITL confirmation prompts surface to the user.
 * - `auto` (default) — auto-clears HITL prompts; the agent may still ask via reply-option cards.
 * - `yolo` — fully headless: no HITL prompts and no reply cards; money-adjacent actions auto-execute.
 */
export type AgentExecutionMode = 'interactive' | 'auto' | 'yolo';

export type ChatMessage = {
  id: number;
  role: string;
  character?: string | null;
  content: string;
  meta?: {
    assets?: AgentAsset[];
    pending_actions?: AgentActionProposal[];
    confirmed_action?: AgentActionProposal & { result?: Record<string, any> };
    context_used?: PageContext | null;
    context_usage?: AgentContextUsage | null;
    routed_character?: string;
    process_events?: ToolEvent[] | string[];
    todos?: AgentTodoItem[];
    agent_phase?: 'completed' | 'completed_with_tool_errors' | 'drained' | 'error' | 'awaiting_confirmation';
    model_selection?: AgentModelSelection;
    model_selection_fallback?: boolean;
    /** @deprecated Use `mode` instead. */
    yolo_mode?: boolean;
    mode?: AgentExecutionMode;
    reply_options?: ReplyOptionMeta[];
    term_form?: TermFormMeta;
    envelope_initial?: Envelope;
    envelope_final?: Envelope;
    envelope_transitioned?: boolean;
    cost_preview?: {
      tool_name: string;
      estimated_seconds: number;
    } | null;
    [key: string]: any;
  };
};

export type AgentContextUsage = {
  bytes: number;
  estimated_tokens: number;
  chip_count: number;
  snapshot_key_count: number;
  entity_id_count: number;
  warning_level: 'none' | 'large' | 'huge';
  computed_at: string;
};

export type AgentModelSelection = {
  channel: string;
  provider: string;
  model: string;
};

export type AgentModelOption = AgentModelSelection & {
  label: string;
  description?: string | null;
  is_default?: boolean;
  tags?: string[];
};

export type AgentChannel = {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  healthy: boolean;
  models: AgentModelOption[];
};

export type AgentModelConfig = {
  enabled: boolean;
  active: AgentModelSelection;
  channels: AgentChannel[];
};

export type AgentAsset = {
  id: string;
  kind: 'file' | 'image' | 'table' | 'chart' | 'json' | 'markdown' | 'html';
  title: string;
  mime_type?: string | null;
  url?: string | null;
  path?: string | null;
  data?: any;
  metadata?: Record<string, any>;
};

export type AgentActionProposal = {
  id: string;
  tool_name: string;
  /** Legacy field still present on rows from before the refactor. */
  type?: string;
  label: string;
  summary: string;
  payload?: Record<string, any>;
  requires_confirmation?: boolean;
  status?: 'pending' | 'confirmed' | 'dismissed' | 'failed';
  persona?: 'trader' | 'risk_manager' | 'high_board';
  risk_level?: 'read' | 'write' | 'irreversible';
  /** Set when the proposal came from an async-agent bubble-up. */
  async_task_id?: number | null;
  /** Set after a confirmed action queues a persisted background task. */
  task_id?: number | null;
  task_kind?: string | null;
  task_status?: string | null;
  task_progress_current?: number | null;
  task_progress_total?: number | null;
  task_message?: string | null;
};

export type AsyncAgentStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled';

export type AsyncAgentTaskOut = {
  task_id: number;
  description: string;
  status: AsyncAgentStatus;
  awaiting_approval: boolean;
  started_at: string | null;
  finished_at: string | null;
  last_message_preview: string | null;
};

export type ToolEvent = {
  id: string;
  name: string;
  status: 'running' | 'done' | 'error';
  args?: Record<string, unknown> | { _truncated: true; preview: string; size: number };
  output?: unknown;
  duration_ms?: number;
  error?: string;
};

const PERSONA_DISPLAY_NAMES: Record<string, string> = {
  trader: 'Trader',
  risk_manager: 'Risk Manager',
  high_board: 'High Board',
  auto: 'Auto',
  async_agent: 'Background',
};

export const proposalToolName = (proposal: AgentActionProposal): string =>
  proposal.tool_name ?? proposal.type ?? 'unknown_tool';

export const personaDisplayLabel = (persona?: string | null): string =>
  persona ? PERSONA_DISPLAY_NAMES[persona] ?? toTitleCase(persona) : '';

function toTitleCase(value: string): string {
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export type LoadedCompleteness = "complete" | "paginated" | "partial" | "empty";
export type ConfirmationMode = "implicit" | "explicit" | "destructive";
export type Envelope =
  | "pet_page"
  | "pet_diagnostic"
  | "desk_workflow"
  | "desk_async";

export type LoadedContext = {
  completeness: LoadedCompleteness;
  visible_count?: number;
  total_count?: number;
  query_ref?: string;
};

export type PageAction = {
  name: string;
  required_ids: string[];
  confirmation: ConfirmationMode;
  backend_endpoint: string;
};

export type PageContext = {
  route: Route;
  title: string;
  entity_ids: Record<string, number | string | null | undefined>;
  snapshot: Record<string, any>;
  // Phase 2 additions:
  loaded_context?: LoadedContext;
  actions?: PageAction[];
  // Legacy (still emitted by all pages; backend defaults both to empty/null in Phase 3):
  path?: string;
  /** @deprecated Replaced by `loaded_context` + `actions`; remove in Phase 3. */
  chips: string[];
};

export type PageContextReporter = (context: PageContext) => void;

export type RFQQuoteVersion = {
  id: number;
  rfq_id: number;
  version: number;
  quote_mode: string;
  status: string;
  request_payload: Record<string, any>;
  quote_payload: Record<string, any>;
  error?: string | null;
  created_by: string;
  approved_by?: string | null;
  approved_at?: string | null;
  released_at?: string | null;
  valid_until?: string | null;
  created_at: string;
};

export type RFQ = {
  id: number;
  client_name: string;
  channel: string;
  status: string;
  request_payload: Record<string, unknown>;
  quote_payload: Record<string, any>;
  approved_response?: string | null;
  quote_versions?: RFQQuoteVersion[];
  created_at?: string;
  updated_at?: string;
};

export type AuditEvent = {
  id: number;
  event_type: string;
  actor: string;
  subject_type: string;
  subject_id: string;
  payload: Record<string, any>;
  created_at: string;
};

export type RfqUnknownFieldSpec = {
  field_path: string;
  label: string;
  lower_bound: number;
  upper_bound: number;
  initial_guess: number;
};

export type RfqTemplate = {
  key: string;
  label: string;
  product_type: string;
  engine_spec: Record<string, any>;
  unknown_fields: string[];
  unknown_field_specs?: RfqUnknownFieldSpec[];
  product_kwargs: Record<string, any>;
};

export type RfqCatalog = {
  product_types: Array<{ name: string; template_key?: string | null; quote_modes: string[] }>;
  engine_options: string[];
  unknown_fields: Record<string, string[]>;
  templates: RfqTemplate[];
  advanced: Record<string, any>;
};

export type TrySolveFieldType = 'text' | 'number' | 'date' | 'boolean' | 'select';

export type TrySolveSolverState = 'solver_ready' | 'schema_captured';

export type TrySolveStatus =
  | 'draft'
  | 'missing_terms'
  | 'missing_market'
  | 'mapping_pending'
  | 'invalid_target'
  | 'unsupported_market'
  | 'unsupported_quote_field'
  | 'quantark_build_failed'
  | 'solve_failed'
  | 'solver_ready'
  | 'schema_captured'
  | 'solved'
  | string;

export type TrySolveField = {
  key: string;
  label: string;
  field_type: TrySolveFieldType;
  excel_aliases?: string[];
  required?: boolean;
  default?: any;
  options?: string[];
  canonical_path?: string | null;
};

export type TrySolveQuoteField = {
  key: string;
  label: string;
  excel_header: string;
  canonical_path: string;
  lower_bound: number;
  upper_bound: number;
  initial_guess?: number | null;
  solver_ready: boolean;
};

export type TrySolveProduct = {
  product_key: string;
  label: string;
  excel_sheet: string;
  initial_solver_state: TrySolveSolverState;
  fields: TrySolveField[];
  quote_fields: TrySolveQuoteField[];
  quantark_product_type?: string | null;
  default_engine_name?: string | null;
  notes?: string;
};

export type TrySolveCatalog = {
  products: TrySolveProduct[];
  status_options: TrySolveStatus[];
};

export type EngineConfigVariant = {
  id: number;
  name: string;
  description?: string | null;
  status: string;
  is_default: boolean;
  rules: Record<string, any>;
  business_days_in_year?: number | null;
  created_at: string;
  updated_at: string;
};

export type EngineConfigVariantInput = {
  name: string;
  description?: string | null;
  status?: string;
  is_default?: boolean;
  rules: Record<string, any>;
  business_days_in_year?: number | null;
};

export type TrySolveMarket = {
  pricing_parameter_profile_id?: number | null;
  market_data_profile_id?: number | null;
  valuation_date?: string | null;
  spot?: number | null;
  volatility?: number | null;
  rate?: number | null;
  dividend_yield?: number | null;
  day_count_convention?: string | null;
  bus_days_in_year?: number | null;
  calendar?: string | null;
};

export type TrySolveQuoteRequest = {
  quote_field_key: string;
  target_label: 'price' | 'premium' | 'premium %' | 'reoffer';
  target_value: number;
  quote_value_mode?: 'absolute' | 'percentage';
  lower_bound?: number | null;
  upper_bound?: number | null;
  initial_guess?: number | null;
};

export type TrySolveRowIn = {
  row_id: string;
  source: 'manual' | 'excel';
  product_key: string;
  source_sheet?: string | null;
  source_row?: number | null;
  fields: Record<string, any>;
  raw_values: Record<string, any>;
  market: TrySolveMarket;
  quote_request: TrySolveQuoteRequest;
};

export type TrySolveRowOut = TrySolveRowIn & {
  product_label: string;
  status: TrySolveStatus;
  diagnostics: string[];
  quantark_product_type?: string | null;
  engine_name?: string | null;
  solved_value?: number | null;
  model_price?: number | null;
  residual?: number | null;
  executable_terms?: Record<string, any> | null;
};

export type TrySolveBatchOut = {
  batch_id: string;
  rows: TrySolveRowOut[];
  summary: Record<string, any>;
};

export type TrySolveValidateRequest = {
  row: TrySolveRowIn;
};

export type TrySolveSolveRequest = {
  row: TrySolveRowIn;
};

export type TrySolveBatchSolveRequest = {
  rows: TrySolveRowIn[];
};

export type TrySolveExportRequest = {
  rows: TrySolveRowOut[];
  scope: 'all' | 'selected' | 'solved' | 'errors';
  selected_row_ids: string[];
};

export type TrySolveExportOut = {
  filename: string;
  url: string;
  row_count: number;
  scope: string;
};

export type ProductRoot = {
  id: number;
  asset_class: string;
  product_family: string;
  quantark_class?: string | null;
  underlying: string;
  currency: string;
  terms?: Record<string, unknown>;
  raw_terms?: Record<string, unknown>;
  components?: Record<string, unknown>[];
};

export type Position = {
  id: number;
  portfolio_id: number;
  product_id?: number | null;
  product?: ProductRoot | null;
  underlying: string;
  product_type: string;
  product_kwargs: Record<string, any>;
  engine_name: string;
  engine_kwargs: Record<string, any>;
  quantity: number;
  entry_price: number;
  currency: string;
  status: string;
  position_kind: 'otc' | 'listed';
  source_trade_id?: string | null;
  source_row?: number | null;
  mapping_status: string;
  mapping_error?: string | null;
  source_payload?: Record<string, any> | null;
  rfq_id?: number | null;
  rfq_quote_version_id?: number | null;
  trade_effective_date?: string | null;
};

export type PositionLifecycleEvent = {
  id: number;
  position_id: number;
  event_type: string;
  event_data: Record<string, unknown>;
  old_status: string | null;
  new_status: string | null;
  actor: string;
  created_at: string;
  cancelled_at?: string | null;
  cancelled_by?: string | null;
  cancellation_reason?: string | null;
};

export type Portfolio = {
  id: number;
  name: string;
  kind: PortfolioKind;
  base_currency: string;
  created_at?: string;
  updated_at?: string;
  positions: Position[];
};

export type PositionImportBatch = {
  id: number;
  portfolio_id: number;
  imported_count: number;
  supported_count: number;
  unsupported_count: number;
  error_count: number;
  status: string;
};

export type MarketInput = {
  id: number;
  portfolio_id: number;
  position_id?: number | null;
  source_trade_id: string;
  symbol: string;
  valuation_date: string;
  spot?: number | null;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
  source_row?: number | null;
  source_payload?: Record<string, any> | null;
};

export type PricingParameterRow = {
  id: number;
  profile_id: number;
  source_trade_id: string;
  symbol: string;
  instrument_id?: number | null;
  /** Set on curve-generated rows: the position this row is bound to. */
  position_id?: number | null;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
  source_row?: number | null;
  source_payload?: Record<string, any> | null;
  created_at: string;
  updated_at: string;
};

export type PricingParameterProfile = {
  id: number;
  name: string;
  valuation_date: string;
  source_type: 'xlsx' | 'market_data_spot' | 'default_underlying' | string;
  source_path?: string | null;
  status: string;
  summary: Record<string, any>;
  created_at: string;
  updated_at: string;
  rows: PricingParameterRow[];
};

/** One resolved field from GET .../positions/{id}/pricing-params. The extra
 * provenance keys vary by `source`; all are optional. */
export type ResolvedParam = {
  value: number | null;
  source: 'market_quote' | 'pricing_parameter_profile' | 'assumption_set' | 'missing';
  as_of?: string;
  age_days?: number;
  quote_source?: string;
  profile_id?: number;
  source_trade_id?: string;
  assumption_set_id?: number;
  assumption_row_id?: number;
};

export type ResolvedPricingParams = {
  spot: ResolvedParam;
  rate: ResolvedParam;
  dividend_yield: ResolvedParam;
  volatility: ResolvedParam;
};

export type MarketDataProfile = {
  id: number;
  underlying_id?: number | null;
  name: string;
  source: string;
  symbol: string;
  asset_class: string;
  start_date: string;
  end_date: string;
  adjust?: string | null;
  valuation_date: string;
  data: Record<string, any>;
  source_metadata?: Record<string, any> | null;
  created_at: string;
  updated_at: string;
};

export type PositionValuationRun = {
  id: number;
  status: string;
  pricing_parameter_profile_id?: number | null;
  valuation_date: string;
  overrides: Record<string, any>;
  summary: Record<string, any>;
  resolved_position_ids?: number[] | null;
  results: PositionValuationResult[];
};

export type PositionValuationResult = {
  id: number;
  position_id: number;
  source_trade_id?: string | null;
  ok: boolean;
  price?: number | null;
  market_value?: number | null;
  pnl?: number | null;
  market_inputs?: Record<string, any>;
  result_payload?: Record<string, any>;
  error?: string | null;
};

export type ReportJob = {
  id: number;
  report_type: string;          // 'portfolio' | 'risk' | 'rfq'
  status: string;
  request_payload: Record<string, any>;
  result_payload: Record<string, any>;
  artifact_paths: Record<string, any>;
  task_id?: number | null;
  limit_monitoring_run_id?: number | null;
  created_at: string;           // ISO timestamp
};

export type TaskErrorPosition = {
  position_id: number | null;
  underlying?: string | null;
  product_type?: string | null;
  pricing_ok: boolean;
  pricing_error?: string | null;
  greeks_ok: boolean;
  greeks_error?: string | null;
};

export type TaskResultPayload = {
  errors?: {
    kind?: string;
    failed_count?: number;
    positions?: TaskErrorPosition[];
  };
  [key: string]: unknown;
};

export type TaskRun = {
  id: number;
  kind: 'risk_run' | 'report_job' | string;
  status: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | string;
  portfolio_id?: number | null;
  risk_run_id?: number | null;
  greeks_landscape_run_id?: number | null;
  report_job_id?: number | null;
  limit_monitoring_run_id?: number | null;
  progress_current: number;
  progress_total: number;
  message?: string | null;
  error?: string | null;
  result_payload?: TaskResultPayload | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

// --- Risk Limits ------------------------------------------------------------

export type LimitCategory = 'greek' | 'var' | 'cvar' | 'stress';
export type LimitMetricKind =
  | 'delta'
  | 'gamma'
  | 'vega'
  | 'theta'
  | 'rho'
  | 'rho_q'
  | 'var'
  | 'cvar'
  | 'stress_pnl';
export type LimitSourceKind = 'risk_run' | 'scenario_test' | 'backtest';
export type LimitScopeType = 'portfolio' | 'underlying' | 'product_family' | 'position';
export type LimitAggregation = 'net' | 'gross_abs' | 'max_abs' | 'minimum' | 'maximum';
export type LimitTransform = 'signed' | 'absolute' | 'loss_magnitude';
export type LimitComparator = 'upper' | 'lower' | 'range';
export type LimitSourcePolicy = 'reuse_only' | 'refresh_if_stale' | 'force_refresh';
export type LimitVersionState = 'draft' | 'active' | 'superseded' | 'retired';
export type LimitMonitoringStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_unknowns'
  | 'failed';
export type LimitEvaluationStatus = 'ok' | 'warning' | 'breach' | 'unknown';
export type LimitIncidentStatus =
  | 'open'
  | 'acknowledged'
  | 'assigned'
  | 'waived'
  | 'recovered'
  | 'resolved';

export type LimitVersionInput = {
  metric_kind: LimitMetricKind;
  source_kind: LimitSourceKind;
  methodology?: Record<string, unknown>;
  scope_type: LimitScopeType;
  scope_config?: Record<string, unknown>;
  aggregation: LimitAggregation;
  transform: LimitTransform;
  comparator: LimitComparator;
  warning_lower?: number | null;
  warning_upper?: number | null;
  hard_lower?: number | null;
  hard_upper?: number | null;
  unit: string;
  currency?: string | null;
  bump_convention?: string | null;
  freshness_policy?: Record<string, unknown>;
  effective_until?: string | null;
  rationale?: string | null;
};

export type LimitCreateInput = {
  key: string;
  name: string;
  description?: string;
  category: LimitCategory;
  owner: string;
  tags?: string[];
  initial_version: LimitVersionInput;
};

export type LimitMetadataPatchInput = {
  expected_row_version: number;
  name?: string;
  description?: string;
  owner?: string;
  tags?: string[];
};

export type LimitVersionCreateInput = {
  expected_row_version: number;
  version: LimitVersionInput;
};

export type LimitActionInput = {
  expected_row_version: number;
};

export type LimitSourceInputs = {
  risk_run?: {
    method?: string;
    [key: string]: unknown;
  };
  scenario_test?: {
    scenario_request: Record<string, unknown>;
    config?: Record<string, unknown>;
  };
  backtest?: {
    spec: Record<string, unknown>;
    config?: Record<string, unknown>;
  };
};

export type LimitMonitoringRunCreateInput = {
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  engine_config_id?: number | null;
  market_snapshot_id?: number | null;
  effective_market_evidence_id?: string | null;
  valuation_as_of: string;
  source_policy: LimitSourcePolicy;
  max_source_age_seconds?: number | null;
  source_inputs?: LimitSourceInputs;
};

export type LimitIncidentAssignInput = LimitActionInput & {
  assignee: string;
};

export type LimitIncidentCommentInput = LimitActionInput & {
  comment: string;
};

export type LimitIncidentWaiveInput = LimitActionInput & {
  rationale: string;
  expires_at: string;
};

export type LimitVersion = {
  id: number;
  risk_limit_id: number;
  version: number;
  state: LimitVersionState;
  metric_kind: LimitMetricKind;
  source_kind: LimitSourceKind;
  methodology: Record<string, unknown>;
  scope_type: LimitScopeType;
  scope_config: Record<string, unknown>;
  aggregation: LimitAggregation;
  transform: LimitTransform;
  comparator: LimitComparator;
  warning_lower: number | null;
  warning_upper: number | null;
  hard_lower: number | null;
  hard_upper: number | null;
  unit: string;
  currency: string | null;
  bump_convention: string | null;
  freshness_policy: Record<string, unknown>;
  effective_from: string | null;
  effective_until: string | null;
  rationale: string | null;
  created_at: string;
  activated_at: string | null;
};

export type RiskLimit = {
  id: number;
  key: string;
  name: string;
  description: string;
  category: LimitCategory;
  owner: string;
  tags: string[];
  active_version_id: number | null;
  row_version: number;
  created_at: string;
  updated_at: string;
  versions: LimitVersion[];
  active_version: LimitVersion | null;
};

export type LimitSourceReference = {
  id: number;
  source_kind: LimitSourceKind;
  risk_run_id: number | null;
  scenario_test_run_id: number | null;
  backtest_run_id: number | null;
  requested_parameters: Record<string, unknown>;
  source_status: string;
  is_fresh: boolean;
  completeness_diagnostics: Record<string, unknown>;
  source_valuation_at: string | null;
  source_created_at: string | null;
  created_at: string;
};

export type LimitMonitoringRun = {
  id: number;
  trigger: string;
  mode: 'interactive' | 'auto' | 'yolo';
  portfolio_id: number;
  pricing_parameter_profile_id: number | null;
  engine_config_id: number | null;
  market_snapshot_id: number | null;
  effective_market_evidence_id: string | null;
  valuation_as_of: string;
  source_policy: LimitSourcePolicy;
  max_source_age_seconds: number | null;
  status: LimitMonitoringStatus;
  summary: Record<string, unknown>;
  definition_snapshot_hash: string;
  limit_version_ids: number[];
  task_id: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  source_references: LimitSourceReference[];
};

export type LimitEvaluation = {
  id: number;
  monitoring_run_id: number;
  limit_version_id: number;
  scope_type: LimitScopeType;
  scope_key: string;
  scope_label: string;
  observed_value: number | null;
  adverse_value: number | null;
  warning_lower: number | null;
  warning_upper: number | null;
  hard_lower: number | null;
  hard_upper: number | null;
  utilization: number | null;
  headroom: number | null;
  governing_boundary: string | null;
  status: LimitEvaluationStatus;
  reason_code: string | null;
  reason: string | null;
  coverage_count: number | null;
  coverage_ratio: number | null;
  evidence: Record<string, unknown>;
  evaluated_at: string;
};

export type LimitIncidentEvent = {
  id: number;
  incident_id: number;
  event_type: string;
  evaluation_id: number | null;
  actor: string;
  persona: string | null;
  mode: string | null;
  thread_id: number | null;
  audit_ref: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type LimitIncident = {
  id: number;
  risk_limit_id: number;
  portfolio_id: number;
  scope_type: LimitScopeType;
  scope_key: string;
  scope_label: string;
  severity: 'warning' | 'breach';
  status: LimitIncidentStatus;
  first_evaluation_id: number | null;
  last_evaluation_id: number | null;
  first_seen_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  waived_at: string | null;
  resolved_at: string | null;
  owner: string | null;
  assignee: string | null;
  waiver_expires_at: string | null;
  waiver_rationale: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
  risk_limit: RiskLimit | null;
  events: LimitIncidentEvent[];
};

export type LimitDashboardSummary = {
  breaches: number;
  warnings: number;
  unknowns: number;
  ok: number;
  highest_utilization: number | null;
  active_incidents: number;
};

export type LimitEvaluationGroup = {
  category: LimitCategory;
  evaluations: LimitEvaluation[];
};

export type LimitTrendPoint = {
  run_id: number;
  created_at: string;
  status: LimitMonitoringStatus;
  summary: Record<string, unknown>;
};

export type LimitMonitoringDashboard = {
  summary: LimitDashboardSummary;
  current_evaluations: LimitEvaluation[];
  evaluation_groups: LimitEvaluationGroup[];
  current_evidence_run: LimitMonitoringRun | null;
  latest_run: LimitMonitoringRun | null;
  active_incidents: LimitIncident[];
  trends: LimitTrendPoint[];
};

export type LimitDashboard = LimitMonitoringDashboard;

export type LimitMonitoringSummary = LimitDashboardSummary & {
  latest_run: LimitMonitoringRun | null;
  latest_incident_event_id: number;
};

export type MarketSnapshot = {
  id: number;
  name: string;
  source: string;
  symbol: string;
  asset_class: string;
  valuation_date: string;
  data: Record<string, unknown>;
  source_metadata: Record<string, unknown>;
  created_at: string;
};

export type PortfolioKind = 'container' | 'view';

export type FilterRule =
  | { op: 'and'; children: FilterRule[] }
  | { op: 'or';  children: FilterRule[] }
  | { op: 'not'; child: FilterRule }
  | { op: 'eq' | 'ne' | 'lt' | 'lte' | 'gt' | 'gte';
      field: string; value: string | number }
  | { op: 'in' | 'not_in'; field: string; value: (string | number)[] }
  | { op: 'between'; field: string; value: [number | string, number | string] };

export type PortfolioSummary = {
  id: number;
  name: string;
  kind: PortfolioKind;
  base_currency: string;
  description: string | null;
  tags: string[];
  filter_rule: FilterRule | null;
  manual_include_ids: number[];
  manual_exclude_ids: number[];
  source_portfolio_ids: number[];
  resolved_position_count: number;
  created_at: string;
  updated_at: string;
};

export type PortfolioDetail = PortfolioSummary & {
  positions: Position[];
};

export type PortfolioPreviewBody = {
  kind: PortfolioKind;
  filter_rule?: FilterRule | null;
  manual_include_ids?: number[];
  manual_exclude_ids?: number[];
  source_portfolio_ids?: number[];
};

export type PortfolioMembership = {
  portfolio_id: number;
  position_ids: number[];
};

export type LatestAkshareClose = {
  spot: number | null;
  fetched_at: string | null;
  fallback: boolean;
  market_data_profile_id: number | null;
};

export type CurvePoint = { tenor: string; value: number };

export type UnderlyingPricingDefault = {
  underlying: string;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  /** Term-structure curves (per underlying); null/absent means "no curve — use the flat scalar". */
  rate_curve?: CurvePoint[] | null;
  dividend_yield_curve?: CurvePoint[] | null;
  volatility_curve?: CurvePoint[] | null;
  notes: string | null;
  is_complete: boolean;
  /** True when the underlying is in the open-position scope the build gate validates. */
  has_open_position: boolean;
  latest_akshare_close: LatestAkshareClose | null;
  created_at: string;
  updated_at: string;
};

/** Instrument row returned by GET /api/instruments (unified registry, replaces /api/underlyings). */
export type Instrument = {
  id: number;
  symbol: string;
  display_name: string | null;
  kind: string;
  exchange: string | null;
  currency: string;
  status: string;
  source: string;
  akshare_symbol: string | null;
  akshare_asset_class: string | null;
  contract_code: string | null;
  series_root: string | null;
  expiry: string | null;
  multiplier: number | null;
  strike: number | null;
  option_type: string | null;
  parent_id: number | null;
  loaded_at: string | null;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type Underlying = {
  id: number;
  symbol: string;
  display_name: string | null;
  asset_class: string;
  market: string | null;
  exchange: string | null;
  currency: string;
  akshare_symbol: string | null;
  akshare_asset_class: string | null;
  status: 'draft' | 'active' | 'inactive' | string;
  source: string;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  is_complete: boolean;
  latest_akshare_close: LatestAkshareClose | null;
  created_at: string;
  updated_at: string;
};

export type UnderlyingUpdate = Partial<Pick<
  Underlying,
  | 'display_name'
  | 'asset_class'
  | 'market'
  | 'exchange'
  | 'currency'
  | 'akshare_symbol'
  | 'akshare_asset_class'
  | 'status'
  | 'source'
  | 'rate'
  | 'dividend_yield'
  | 'volatility'
  | 'notes'
>>;

export interface FxRate {
  id: number;
  base_currency: string;
  quote_currency: string;
  rate: number;
  as_of_date: string;
  source: string;
  pricing_parameter_profile_id?: number | null;
}

export type HedgeFamilyCount = { family: string; total: number; allowed: number };

export type HedgeUnderlying = {
  underlying_id: number;
  symbol: string;
  display_name: string | null;
  asset_class: string;
  unresolvable: boolean;
  last_loaded_at: string | null;
  stale_count: number;
  families: HedgeFamilyCount[];
};

export type HedgeInstrument = {
  id: number;
  underlying_id: number;
  family: string;
  series_root: string;
  exchange: string;
  contract_code: string;
  instrument_type: string;
  option_type: string | null;
  strike: number | null;
  expiry: string | null;
  multiplier: number | null;
  last_price: number | null;
  status: string;
  allowed: boolean;
};

export type HedgeStrategyName =
  | 'delta_neutral' | 'delta_neutral_enhanced' | 'delta_gamma_neutral' | 'full_neutral'

export interface HedgeLeg {
  key: string
  instrument_id: number
  contract_code: string
  exchange: string
  instrument_type: string
  option_type: string | null
  strike: number | null
  expiry?: string | null
  multiplier: number
  family: string
  role: string
  delta: number
  gamma: number
  vega: number
  quantity: number
  priced_ok: boolean
  price_error: string | null
}

export type HedgeGreeks = { delta: number; gamma: number; vega: number }

/** Response of POST /api/hedging/book — position_ids align positionally with
 *  the request's non-zero-quantity legs (backend skips qty 0 in order). */
export interface HedgeBookResponse {
  status: 'booked'
  portfolio_id: number
  underlying: string
  risk_run_id: number
  position_ids: number[]
}

/** Frontend-shaped outcome of the last Book attempt, for the banner. */
export type HedgeBookingResult =
  | {
      kind: 'success'
      portfolioName: string
      riskRunDate: string | null
      legs: { contractCode: string; quantity: number; role: string; positionId: number }[]
    }
  | { kind: 'error'; message: string }

export interface HedgeDiagnosticTerm {
  contract_code: string
  quantity: number
  per_lot: number
  contribution: number
}

export interface HedgeDiagnostic {
  kind: 'hard_band_residual'
  greek: keyof HedgeGreeks
  target: number
  band: number
  residual: number
  shortfall: number
  suggested_band: number
  terms: HedgeDiagnosticTerm[]
}

export interface HedgeableUnderlying {
  underlying: string
  targets: HedgeGreeks
  spot: number | null
}

export interface HedgeableSummary {
  status: 'ok' | 'no_risk_run'
  portfolio_id: number
  risk_run_id?: number
  created_at?: string
  stale?: boolean
  message?: string
  underlyings?: HedgeableUnderlying[]
}

/** Allowed instrument usable as a hedge leg (id === instrument_id the solver needs). */
export interface HedgeCandidate {
  instrument_id: number
  contract_code: string
  instrument_type: string
  family: string
}

export interface HedgeProposal {
  status: 'feasible' | 'infeasible' | 'no_risk_run' | 'no_exposure' | 'no_spot'
  portfolio_id: number
  underlying: string
  strategy: string
  risk_run_id?: number
  /** Immutable server-issued evidence required to book feasible/infeasible proposals. */
  source_artifact_id?: number
  artifact_generated_at?: string
  valuation_as_of?: string
  risk_generated_at?: string
  expires_at?: string
  spot?: number
  targets?: { delta: number; gamma: number; vega: number }
  bands?: { delta: number; gamma: number; vega: number }
  legs?: HedgeLeg[]
  residual?: { delta: number; gamma: number; vega: number }
  in_band?: Record<string, boolean>
  binding?: { greek: string; shortfall: number }[]
  diagnostics?: HedgeDiagnostic[]
  warnings?: { contract_code: string; error: string }[]
  message?: string
}

// --- Skills management -------------------------------------------------

export type SkillTier = 'workflows' | 'references' | 'meta';

export type SkillLintIssue = {
  code: string;
  message: string;
  detail: string;
  severity: 'warning' | 'error';
};

export type SkillPersona = 'trader' | 'risk_manager' | 'high_board';

export type SkillRoutingEntry = { request: string; persona: SkillPersona };

export type SkillFrontmatter = {
  name: string;
  description: string;
  domain: string;
  workflow_type: 'diagnostic' | 'action' | 'read' | 'compound';
  allowed_envelopes: string[];
  may_escalate_to: string[];
  required_context: string[];
  optional_context: string[];
  write_actions: boolean;
  confirmation_required: boolean;
  success_criteria: string[];
  routing?: SkillRoutingEntry[];
};

export type SkillFileSummary = {
  tier: SkillTier;
  path: string;
  name: string;
  domain: string | null;
  frontmatter: Record<string, unknown> | null;
  frontmatter_error: string | null;
  lint: SkillLintIssue[];
  body_tokens: number | null;
};

export type SkillCatalog = {
  domains: string[];
  workflows: SkillFileSummary[];
  references: SkillFileSummary[];
  meta: SkillFileSummary[];
};

export type SkillFile = SkillFileSummary & {
  content: string;
  body: string | null;
};

export type SkillValidateResult = {
  issues: SkillLintIssue[];
  body_tokens: number | null;
  blocking: boolean;
};

export type SkillSaveResult = {
  saved: boolean;
  reloaded: boolean;
  reload_error: string | null;
  lint: SkillLintIssue[];
};

export type SkillDeleteResult = {
  deleted: boolean;
  reloaded: boolean;
  reload_error: string | null;
  warnings: string[];
};

export type SkillReloadResult = { reloaded: boolean; error: string | null };

// --- Scenario Test -------------------------------------------------

export type ScenarioStress = {
  param: 'spot' | 'vol' | 'rate' | 'dividend';
  stress_type: 'ABSOLUTE' | 'PERCENTAGE' | 'VALUE';
  value: number;
  level: 'portfolio' | 'underlying' | 'position';
  target?: string | number | null;
};

export type ScenarioSpec = {
  name: string;
  description?: string;
  stresses: ScenarioStress[];
};

export type ScenarioTestRunRequest = {
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  engine_config_id?: number | null;
  position_ids?: number[] | null;
  predefined?: string[];
  custom?: ScenarioSpec[] | null;
  scenario_set?: string | null;
  scenario_sets?: string[];
  config?: Record<string, unknown> | null;
};

export type ScenarioTestRun = {
  id: number;
  portfolio_id: number;
  pricing_parameter_profile_id: number | null;
  engine_config_id?: number | null;
  status: string;
  results: Record<string, unknown> | null;
  excluded_positions: Array<{ position_id: number; reason: string }> | null;
  artifacts: {
    report_html_path?: string | null;
    export_paths?: string[];
    notes?: string[];
  } | null;
  created_at: string;
  config?: Record<string, unknown> | null;
  scenario_spec?: Record<string, unknown> | null;
  resolved_position_ids?: number[] | null;
};

export type PredefinedScenario = {
  key: string;
  name: string;
  description: string;
  num_stresses: number;
  stresses: ScenarioStress[];
  metadata?: Record<string, unknown>;
};

export type ScenarioLibrary = {
  predefined: PredefinedScenario[];
  saved_sets: string[];
};

export type ScenarioSetDetail = {
  name: string;
  description: string;
  stresses: ScenarioStress[];
  // Number of scenarios in the underlying saved set. Flat-model items have 1;
  // sets with >1 (agent/API-created) are run in full but not editable in the UI.
  num_scenarios?: number;
};

export type GridAxisSpec = {
  param: 'spot' | 'vol' | 'rate' | 'dividend';
  start: number;
  stop: number;
  step: number;
  stress_type: 'ABSOLUTE' | 'PERCENTAGE' | 'VALUE';
  level: 'portfolio' | 'underlying';
  target?: string | number | null;
};

export type ScenarioGridRequest = {
  name: string;
  combine_mode: 'cross_product';
  axes: GridAxisSpec[];
};

export type ScenarioSetSummary = {
  name: string;
  num_scenarios: number;
  combine_mode: string | null;
  axes_summary: string;
  has_grid: boolean;
  axes: GridAxisSpec[];
};

// --- Backtest -------------------------------------------------

export type BacktestSpec = {
  start: string;
  end: string;
  engine_family?: 'autocallable' | 'other' | string;
  engine: 'quad' | 'pde' | 'mc' | string;
  autocallable_engine?: 'quad' | 'pde' | 'mc' | 'analytical' | string;
  other_engine?: 'quad' | 'pde' | 'mc' | 'analytical' | string;
  fallback_engine?: 'quad' | 'pde' | 'mc' | string;
  vol_source: 'realized' | 'flat' | string;
  vol_window?: number | null;
  rate?: number | null;
  flat_vol?: number | null;
};

export type BacktestRunRequest = {
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  engine_config_id?: number | null;
  position_ids?: number[] | null;
  spec: BacktestSpec;
  config?: Record<string, unknown> | null;
};

export type BacktestPnlPoint = {
  date: string;
  total_pnl: number;
  hedge_pnl: number;
  product_pnl: number;
};

export type BacktestLifecycleEvent = {
  type: string;
  date: string;
  cashflow: number;
};

export type BacktestPortfolioSummary = {
  total_pnl: number;
  hedge_pnl: number;
  product_pnl: number;
  num_trades: number;
  sharpe?: number | null;
  max_drawdown?: number | null;
  var_95?: number | null;
  cvar_95?: number | null;
  pnl_series: BacktestPnlPoint[];
};

export type BacktestUnderlying = {
  underlying: string;
  hedge_instrument?: string | { kind?: string; multiplier?: number; [key: string]: unknown } | null;
  num_products: number;
  total_pnl: number;
  hedge_pnl: number;
  num_trades: number;
  lifecycle_events?: BacktestLifecycleEvent[];
  event_summary?: Record<string, unknown>;
  pnl_series?: BacktestPnlPoint[];
  greeks_series?: Record<string, unknown>[];
};

export type BacktestResults = {
  window?: { start: string; end: string };
  engine?: string;
  portfolio?: BacktestPortfolioSummary;
  by_underlying?: BacktestUnderlying[];
  excluded_positions?: Array<{ position_id: number; reason: string }>;
  notes?: string[];
  error?: string;
};

export type BacktestRun = {
  id: number;
  portfolio_id: number;
  engine_config_id?: number | null;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'empty' | string;
  spec: BacktestSpec;
  config?: Record<string, unknown> | null;
  results?: BacktestResults | null;
  excluded_positions?: Array<{ position_id: number; reason: string }> | null;
  artifacts?: {
    dashboards?: Record<string, string>;
    [key: string]: unknown;
  } | null;
  created_at: string;
};

// --- Pricing preview API -------------------------------------------------

export type PricingGreeks = { delta: number; gamma: number; vega: number; theta: number; rho: number; rho_q: number };
export type PricingPreviewRequest = {
  product_type: string;
  product_kwargs: Record<string, unknown>;
  engine_name: string;
  engine_kwargs?: Record<string, unknown>;
  market: { spot: number; rate: number; volatility: number; dividend_yield: number; valuation_date?: string; currency?: string };
  compute_greeks: boolean;
};
export type PricingPreviewOut = {
  ok: boolean; price: number; engine: string; product_type: string;
  greeks?: PricingGreeks | null; greeks_error?: string | null; error?: string | null;
};

export interface AuditAction {
  id: number;
  kind: 'execution' | 'hitl_proposal' | 'hitl_decision';
  status:
    | 'attempted' | 'ok' | 'error' | 'denied' | 'interrupted' | 'refused'
    | 'proposed' | 'approved' | 'rejected';
  deny_reason: string | null;
  tool_name: string;
  tool_class: 'domain_write' | 'async_dispatch' | 'fs_write' | 'artifact_write';
  tool_call_id: string | null;
  audit_ref: string | null;
  mode: string | null;
  envelope: string | null;
  actor: string;
  model: string | null;
  persona: string | null;
  thread_id: number | null;
  workflow_id: number | null;
  session_id: number | null;
  task_id: number | null;
  message_id: number | null;
  desk_workflow_slug: string | null;
  args_json: Record<string, unknown>;
  redacted: boolean;
  result_preview: string | null;
  error: string | null;
  occurred_at: string;
  completed_at: string | null;
}

export interface AuditActionDetail extends AuditAction {
  related: AuditAction[];
}

export interface AuditSummary {
  by_status: Record<string, number>;
  by_class: Record<string, number>;
  by_mode: Record<string, number>;
  fail_closed_refusals: { persisted: number; unpersisted: number };
}
