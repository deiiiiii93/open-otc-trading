# Multi-Channel Model Selection Design

**Date**: 2026-05-09
**Status**: Draft — pending implementation
**Author**: Brainstormed with the agent (this session)

## 1. Context

The Agent Desk currently runs through a single LLM gateway (Zenmux) with a hardcoded provider/model pair selected via env vars (`AGENT_PROVIDER`, `AGENT_MODEL_ANTHROPIC`, `AGENT_MODEL_OPENAI`). Users have no UI control over which model handles their request. The codebase has the *plumbing* in place (the schemas `AgentModelSelection`/`AgentModelOption`/`AgentModelConfigOut` exist; `AgentService.respond` and `stream_and_persist` accept a `model_selection`; the factory accepts an override) but:

- No HTTP endpoint exposes the catalog to the frontend.
- The `/api/chat/threads/{id}/messages/stream` endpoint silently drops the optional `payload.model` field.
- The catalog is hardcoded to one Anthropic model + one OpenAI model — no room for "different features from different models".
- Zenmux is hardcoded as the only channel (`_ANTHROPIC_BASE_URL` constant, `settings.zenmux_base_url`) — adding a direct DeepSeek/Groq endpoint requires code changes.
- The frontend has zero plumbing: no types, no fetch, no picker, no per-message model display.

This spec delivers a frontend picker and a backend redesign that makes channels and models first-class config rather than baked constants.

## 2. Goals

**In scope (v1):**
- Channels and their models are configured via a YAML file (`config/agent_channels.yaml`).
- Two channel types: `zenmux` (the existing dual-sub-path gateway) and `openai_compatible` (any OpenAI-API-compatible endpoint — DeepSeek, Groq, local Ollama, etc.).
- Frontend picker, inline in the composer, lets the user choose a `(channel, provider, model)` per send. Selection is **session-sticky** (in-memory state).
- Each AI message in the chat history shows a model chip with a provider-colored dot.
- HITL "confirm" / "dismiss" resumes use the model that produced the originating interrupt — not the current session selection.
- Hot-reload of the YAML + env keys via `POST /api/agent/channels/reload`, fronted by a small refresh button next to the picker.
- Removal of legacy single-channel `Settings` fields in this same change.

**Out of scope (v1):**
- Authentication on the reload endpoint (desk tool, no multi-tenant).
- Live remote model catalogs (e.g., polling Zenmux's `/v1/models`).
- Per-user or per-thread persistent preferences (localStorage, server preferences).
- Cost / token / latency metering per model.
- Provider-specific feature flags (Anthropic interleaved thinking, OpenAI reasoning effort, etc.). This spec only delivers the dispatch layer — those features are separate work.
- Auto-fallback to a different model when one channel is failing.
- Multi-file YAML hot-merge.

## 3. Decisions Locked In

| Decision | Choice |
|---|---|
| Use case framing | Power users / experimentation — rich catalog, lightweight per-message switching. |
| Channels v1 | Zenmux (existing) + Generic OpenAI-compatible. |
| Catalog source | YAML/JSON config file. |
| Persistence of selection | Session-sticky (in-memory; resets on reload). |
| Picker location | Inline in `ChatComposer`, on the Send-button row. |
| Wire format for selection | `{channel, provider, model}` (Approach B). |
| API key resolution | Cached at startup; reload endpoint re-reads `.env` and YAML to refresh. |
| Legacy `Settings.agent_provider` / `agent_model_*` / `zenmux_*` | Removed in this same change. |
| HITL resume model | Read from originating message's `meta.model_selection`; fall back to default with audit. |
| Bubble chip visibility | Always-on. |
| Provider color dot on chip | Yes. |
| Per-model `tags` (e.g., `fast`, `tool-use`) in YAML | Yes — rendered as picker badges/tooltips. |

## 4. Configuration

### 4.1 YAML schema

`config/agent_channels.yaml` — tracked in git, not secret. Path overridable via `AGENT_CHANNELS_FILE` env var (default `<repo>/config/agent_channels.yaml`).

```yaml
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: ZENMUX_API_KEY
    base_url: https://zenmux.ai/api/v1
    anthropic_base_url: https://zenmux.ai/api/anthropic
    models:
      - id: anthropic/claude-opus-4-7
        provider: anthropic
        label: Claude Opus 4.7
        description: Most capable, highest cost
        tags: [reasoning]
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Claude Sonnet 4.6
        tags: [tool-use, reasoning]
      - id: anthropic/claude-haiku-4-5
        provider: anthropic
        label: Claude Haiku 4.5
        tags: [fast]
      - id: openai/gpt-5.4
        provider: openai
        label: GPT-5.4
        tags: [tool-use]

  - name: deepseek
    label: DeepSeek
    type: openai_compatible
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    models:
      - id: deepseek-v4-flash
        provider: deepseek
        label: DeepSeek V4 Flash
        description: Fast, low-cost reasoning
        tags: [fast, reasoning]
```

### 4.2 Field semantics

- `default` *(optional)*: top-level default selection. If absent — or if present but points at a channel/model that is unhealthy or no longer exists — the loader falls back to the first model of the first healthy channel and logs a `default_fallback` audit event.
- `channel.name`: unique key referenced from `AgentModelSelection.channel`.
- `channel.type`:
  - `"zenmux"` — dispatches to `ChatAnthropic` (when `model.provider == "anthropic"`) or `ChatOpenAI` (when `model.provider == "openai"`). Requires both `base_url` and `anthropic_base_url`.
  - `"openai_compatible"` — always dispatches to `ChatOpenAI` against `base_url`. `model.provider` is free-form display metadata.
- `channel.api_key_env`: env var name. Resolved against `os.getenv` at registry load time. May be null/empty for local channels with no auth.
- `model.provider`:
  - On `zenmux` channels: must be `"anthropic"` or `"openai"` (load-bearing — picks the sub-path).
  - On `openai_compatible` channels: any non-empty string. Used for display only.
- `model.tags`: optional `list[str]`. Rendered as small badges in the picker; non-functional.
- `model.description`: optional, shown as tooltip / sub-label in the picker.

### 4.3 Loader behavior

New module: `backend/app/services/deep_agent/channel_registry.py`.

```python
@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    provider: str
    label: str
    description: str | None = None
    tags: tuple[str, ...] = ()

@dataclass(frozen=True)
class ChannelDescriptor:
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    api_key: str | None       # resolved value, NOT env name
    base_url: str
    anthropic_base_url: str | None
    models: tuple[ModelDescriptor, ...]
    healthy: bool

class ChannelRegistry:
    channels: tuple[ChannelDescriptor, ...]
    default: tuple[str, str, str]  # (channel, provider, model)

    def find_model(channel, provider, model) -> tuple[ChannelDescriptor, ModelDescriptor]: ...
    def default_selection() -> dict[str, str]: ...
    def to_catalog_payload() -> dict: ...

# Module-level singleton + atomic swap
_REGISTRY: ChannelRegistry | None = None
_LOCK = threading.RLock()

def load(*, force_reread_dotenv: bool = True) -> ChannelRegistry: ...
def get_registry() -> ChannelRegistry: ...
def configure_registry(registry: ChannelRegistry | None) -> None:  # test override
    ...
```

Validation (raise on failure during `load`):
1. Channel names unique.
2. `(channel.name, model.id)` pairs unique.
3. `zenmux` channels must declare `anthropic_base_url`.
4. On `zenmux` channels, every `model.provider` ∈ `{anthropic, openai}`.
5. On `openai_compatible` channels, `model.provider` is non-empty string.
6. At least one channel exists.

Note: zero healthy channels is **not** an error. The registry loads cleanly, `enabled: false` is returned by the catalog endpoint, and `build_agent_model` returns `None` (agent renders as disabled, matching today's behavior when `ZENMUX_API_KEY` is unset). The rest of the app (RFQ, portfolio, risk, reports) is unaffected.

`healthy = api_key is not None or api_key_env is None/empty` (local-channel case).

`load(force_reread_dotenv=True)` calls `dotenv.load_dotenv(override=True)` against the project `.env` so a rotated key picks up without restart. The registry stores the resolved API key; subsequent `os.getenv` calls are not needed inside `build_agent_model`.

If `load` fails on a reload (bad YAML, no healthy channels), the swap does NOT happen — the old registry remains live. The error is returned to the reload caller.

## 5. Backend Changes

### 5.1 `model_factory.py` — rewrite

Public surface stays similar, but takes a registry instead of `Settings`:

```python
def build_agent_model(
    registry: ChannelRegistry,
    selection: Mapping[str, str] | None = None,
) -> BaseChatModel | None: ...

def default_agent_model_selection(registry) -> dict[str, str]: ...
def resolve_agent_model_selection(registry, selection: Mapping | None) -> dict[str, str]: ...
def agent_model_config(registry) -> dict: ...
```

Inside `build_agent_model`:
- `(channel, model_desc) = registry.find_model(...)`. Raises if unknown.
- If channel/model is unhealthy → return `None`.
- If `channel.type == "zenmux"` and `model_desc.provider == "anthropic"`:
  ```python
  ChatAnthropic(
      model_name=model_desc.id,
      api_key=SecretStr(channel.api_key),
      base_url=channel.anthropic_base_url,
      default_headers={"anthropic-version": "2023-06-01"},
      timeout=None, stop=None,
  )
  ```
- Else (`zenmux`+openai or `openai_compatible`):
  ```python
  ChatOpenAI(
      model=model_desc.id,
      api_key=SecretStr(channel.api_key) if channel.api_key else SecretStr(""),
      base_url=channel.base_url,
  )
  ```

`resolve_agent_model_selection` validates the selection against the registry. If `channel` is missing in the input dict (legacy `{provider, model}` row), it is back-filled to `"zenmux"` before lookup.

Deleted: `_ANTHROPIC_BASE_URL`, `_PROVIDER_LABELS`, `_model_for_provider`, `_option`, `agent_model_options`.

### 5.2 `schemas.py` — selection & catalog

```python
class AgentModelSelection(BaseModel):
    channel: str
    provider: str
    model: str

    @model_validator(mode="before")
    @classmethod
    def _backcompat_default_channel(cls, data):
        if isinstance(data, dict) and "channel" not in data:
            data = {**data, "channel": "zenmux"}
        return data

class AgentModelOption(AgentModelSelection):
    label: str
    description: str | None = None
    is_default: bool = False
    tags: list[str] = Field(default_factory=list)

class AgentChannelOut(BaseModel):
    name: str
    label: str
    type: Literal["zenmux", "openai_compatible"]
    healthy: bool = True
    models: list[AgentModelOption] = Field(default_factory=list)

class AgentModelConfigOut(BaseModel):
    enabled: bool
    active: AgentModelSelection
    channels: list[AgentChannelOut] = Field(default_factory=list)
```

`AgentMessageCreate.model: AgentModelSelection | None` is unchanged in shape — only the inner schema's `provider` becomes free-form `str`.

### 5.3 `AgentService` (`agents.py`)

- Constructor reads the registry via `channel_registry.get_registry()` (replaces `settings.zenmux_api_key` checks).
- `self.model` and `self.deep_agent` are still pre-built for the default selection (perf for the common case).
- New `rebuild_default_model()` method invalidates and rebuilds those after a registry reload.
- `_sync_agent_for_selection` and `_streaming_agent` already accept `model_selection` (per the in-flight changes); they keep their shape but route through the new factory.
- `is_enabled(...)` consults the registry, not legacy `Settings`.

### 5.4 API endpoints (`main.py`)

| Method | Path | Status | Behavior |
|---|---|---|---|
| `GET` | `/api/agent/models` | NEW | Returns `AgentModelConfigOut` from `agent_model_config(registry)`. |
| `POST` | `/api/agent/channels/reload` | NEW | Calls `channel_registry.load(force_reread_dotenv=True)`; on success calls `agent_service.rebuild_default_model()`. Returns `{ok, active, healthy_channels: [name, ...], errors: []}`. No auth in v1. |
| `POST` | `/api/chat/threads/{id}/messages/stream` | MODIFIED | Accepts `payload.model: AgentModelSelection | None`. Computes `resolved = resolve_agent_model_selection(registry, payload.model.model_dump() if payload.model else None)` and persists it as `meta.model_selection` on the user-message row before streaming. Passes the same dict in as `model_selection=...` to `stream_and_persist`. |
| `POST` | `/api/chat/threads/{id}/messages/{mid}/actions/{aid}/{confirm|dismiss}` | MODIFIED | Reads `source_message.meta.get("model_selection")`. Resumes via `agent_service._sync_agent_for_selection(selection)` instead of `agent_service.deep_agent`. If selection unresolvable in current registry: log + audit `model_selection_fallback: true` + use default. |

### 5.5 `Settings` cleanup (`config.py`)

Removed fields (both `_EnvironmentSettings` and `Settings`):
- `zenmux_api_key`, `zenmux_base_url`
- `default_model`
- `agent_provider`, `agent_model_anthropic`, `agent_model_openai`

Removed helpers: `_validate_agent_provider`, `_validate_agent_provider_field`, `_apply_dependent_defaults`.

Added: `agent_channels_file: Path` (default `Path("./config/agent_channels.yaml")`), with validation alias `AGENT_CHANNELS_FILE`.

`configure_settings(...)` test override stays. New parallel `configure_registry(...)` test helper is added.

## 6. Frontend Changes

### 6.1 Types (`frontend/src/types.ts`)

```ts
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
```

`ChatMessage.meta` extended (read-only, optional): `model_selection?: AgentModelSelection`, `model_selection_fallback?: boolean`.

### 6.2 Provider colors

Shared map in `frontend/src/components/providerColors.ts`:

```ts
export const PROVIDER_COLOR: Record<string, string> = {
  anthropic: '#e8743b',
  openai: '#19c37d',
  deepseek: '#4d7cff',
  meta: '#0866ff',
  mistral: '#ff7000',
};
export const PROVIDER_COLOR_DEFAULT = '#888';

export function colorForProvider(provider: string | undefined): string {
  return PROVIDER_COLOR[provider ?? ''] ?? PROVIDER_COLOR_DEFAULT;
}
```

Used by `ModelPicker` (option dot) and `ChatBubble` (chip dot).

### 6.3 `AgentDeskLive` state (`routes/AgentDesk.live.tsx`)

```ts
const [modelConfig, setModelConfig] = useState<AgentModelConfig | null>(null);
const [selectedModel, setSelectedModel] = useState<AgentModelSelection | null>(null);

useEffect(() => {
  api<AgentModelConfig>('/api/agent/models')
    .then(cfg => { setModelConfig(cfg); setSelectedModel(cfg.active); })
    .catch(e => setError(e instanceof Error ? e.message : String(e)));
}, []);

// In handleSend's fetch body:
body: JSON.stringify({
  content: message,
  character: 'auto',
  model: selectedModel,   // null is acceptable; backend treats as "use default"
})
```

A `handleRefreshCatalog` callback POSTs `/api/agent/channels/reload`, then re-fetches `/api/agent/models`. Wired to the refresh button next to the picker.

### 6.4 `ModelPicker` component (new — `frontend/src/components/ModelPicker.tsx`)

```ts
type Props = {
  channels: AgentChannel[];
  selected: AgentModelSelection | null;
  onChange: (s: AgentModelSelection) => void;
  onRefresh?: () => void;     // optional; renders a small ↻ button when supplied
  disabled?: boolean;
};
```

Render shape:

```
[● Sonnet 4.6 ▾]  [↻]   ← inline in composer actions row, before Send

(opened panel:)
┌──────────────────────────────────────┐
│ ZENMUX                                │
│   ● Claude Opus 4.7   [reasoning]     │
│   ● Claude Sonnet 4.6 [tool-use,…]    │
│   ● Claude Haiku 4.5  [fast]          │
│   ● GPT-5.4          [tool-use]       │
│ DEEPSEEK                              │
│   ● DeepSeek V4 Flash [fast,reasoning]│
└──────────────────────────────────────┘
```

Behavior:
- Closes on outside click and Escape.
- Arrow-key navigation across the flat list of models.
- Each row shows a colored dot keyed to `model.provider` (left), the model `label` (middle), and `tags` rendered as small badges (right).
- `description` is shown as the row's `title` attribute (tooltip on hover) for v1.
- Unhealthy channels (`channel.healthy === false` from the catalog response) render their section header dimmed and their model rows non-interactive, with a `title` tooltip "API key for `<api_key_env>` not set". The user can still see what *would* be available.
- `disabled` state (`!modelConfig.enabled` or `channels.length === 0` or no healthy channels): button reads "Agent disabled", panel does not open.

### 6.5 `ChatComposer` + `AgentDesk` wiring

`AgentDesk` accepts `availableChannels`, `selectedModel`, `onChangeModel`, `onRefreshCatalog` and forwards to `ChatComposer`. `ChatComposer` renders `<ModelPicker .../>` immediately to the left of the Send button:

```tsx
<div className="wl-composer__actions">
  <ModelPicker
    channels={availableChannels}
    selected={selectedModel}
    onChange={onChangeModel}
    onRefresh={onRefreshCatalog}
    disabled={availableChannels.length === 0}
  />
  <Button variant="primary" onClick={handleSend} disabled={...}>
    {streaming ? 'Streaming…' : sending ? 'Sending…' : 'Send ▸'}
  </Button>
</div>
```

### 6.6 `ChatBubble` — model chip

When `message.role === 'assistant'` and `message.meta?.model_selection` is present, render a small chip below the bubble content:

```
┌─────────────────────────────────┐
│ [assistant content]              │
│                                  │
│  ● Sonnet 4.6 · Zenmux            │
└─────────────────────────────────┘
```

- The dot color is `colorForProvider(meta.model_selection.provider)`.
- The model label and channel label are looked up in `modelConfig.channels`. If the channel/model is no longer present in the catalog (e.g., admin removed it), display `meta.model_selection.model` raw with no channel suffix and a "?" badge.
- If `meta.model_selection_fallback === true`, append " (fell back to default)" to the chip.
- **Always-on**: the chip is visible in both `compact` and `detailed` view modes.
- Old messages with `meta.model_selection` absent show no chip (no retroactive labeling).

## 7. Migration & Back-Compat

### 7.1 `AgentMessage.meta` row eras

| Era | `meta.model_selection` | Read-side |
|---|---|---|
| Pre-feature | absent | No chip. HITL resume uses default. |
| In-flight (current branch state) | `{provider, model}` | `AgentModelSelection` validator coerces `channel: "zenmux"`. |
| Post-feature | `{channel, provider, model}` | Used as-is. |

No DB migration. `AgentMessage.meta` is JSON; new writes carry the full triple.

### 7.2 LangGraph checkpoints (`agent_checkpoints.sqlite`)

No changes. The checkpointer persists graph state, not the model class. Each invocation rebuilds the model from the registry. Forward-compatible.

**In-flight interrupt risk**: a paused interrupt produced under the legacy code references its model via `{provider, model}` only. Resume will coerce `channel="zenmux"`. If the post-deploy YAML still has the matching Zenmux model, resume succeeds; otherwise it falls back to default with audit. Migration recommendation: keep the previously-default Zenmux model in the YAML during the rollout window so in-flight interrupts resolve cleanly.

### 7.3 `.env`

Removed env vars:
```
AGENT_PROVIDER
AGENT_MODEL_ANTHROPIC
AGENT_MODEL_OPENAI
ZENMUX_BASE_URL
OPEN_OTC_DEFAULT_MODEL
```

Kept:
```
ZENMUX_API_KEY      # referenced via api_key_env in YAML
DEEPSEEK_API_KEY    # new, referenced from YAML
```

Optional new:
```
AGENT_CHANNELS_FILE=./config/agent_channels.yaml
```

`.env.example` gets a comment pointing to the YAML. The `config/` directory and YAML file are tracked in git.

### 7.4 Tests

- `tests/test_model_factory.py` — full rewrite against a fixture registry. New cases for `(zenmux, anthropic)`, `(zenmux, openai)`, `(openai_compatible, *)`, unhealthy → `None`, legacy back-fill, unknown selection → raise.
- New `tests/test_channel_registry.py` — YAML happy path, validation rules (duplicate name, missing `anthropic_base_url`, invalid provider on zenmux), `healthy` flag, atomic reload-on-bad-yaml, `load_dotenv(override=True)` rotation.
- `tests/test_api.py` — coverage for `GET /api/agent/models`, modified stream endpoint accepts and persists `model`, HITL resume reads originating message selection and falls back with audit when unresolvable, `POST /api/agent/channels/reload`.
- The currently-uncommitted modifications to `tests/test_api.py` and `tests/test_model_factory.py` are rebased onto this new shape, not landed as-is.

## 8. Risks & Watch-Outs

1. **Per-turn agent compilation (non-default selections)**. `AgentService._streaming_agent` rebuilds an orchestrator + checkpointer for each non-default selection. Fine for desk-tool throughput; revisit only if metrics show graph-compilation dominating latency.
2. **No remote model-id validation**. A YAML typo (e.g., misspelled `deepseek-v4-flash`) surfaces only on first send as an upstream 4xx. We log the upstream error verbatim into `meta.error`.
3. **Reload during in-flight stream**. The model object referenced by the active stream stays alive via Python refs; that stream completes against the old model. New requests after reload pick up the new registry. Document with a comment in the reload endpoint.
4. **HITL fallback signal**. When a referenced model has been removed since the message was sent, resume falls back to default — the resumed message carries `meta.model_selection_fallback: true` so the frontend can render "fell back to default" on the chip.
5. **YAML drift across desks**. Out of scope to centralize. Document as "treat the YAML as desk-local config".

## 9. Implementation Order

1. `channel_registry.py` + `config/agent_channels.yaml` seed + tests.
2. `schemas.py` updates (back-compat validator, `tags`, `AgentChannelOut`) + tests.
3. `model_factory.py` rewrite + tests.
4. `AgentService` consumes registry; `rebuild_default_model` method; existing tests adjusted.
5. `main.py` — `GET /api/agent/models`, `POST /api/agent/channels/reload`, modified stream endpoint, HITL resume change.
6. `Settings` cleanup + `.env.example` rewrite + `.env` migration notes.
7. Frontend types.
8. `AgentDeskLive` catalog fetch + state + send-body wiring + refresh callback.
9. `ModelPicker` component + tests + `providerColors.ts`.
10. `ChatComposer` + `AgentDesk` prop wire-through.
11. `ChatBubble` model chip + tests.
12. Manual end-to-end smoke run through Zenmux-Anthropic, Zenmux-OpenAI, and DeepSeek with real keys; rotate a key, hit the refresh button, verify catalog updates without restart; trigger an HITL action and confirm-resume runs against the originating model.

Backend through step 6 is independently shippable — each step compiles + tests pass. The frontend (steps 7–11) is dark until step 5 lands but doesn't block.
