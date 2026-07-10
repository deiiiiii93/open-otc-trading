import { useMemo, useState } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import type {
  AgentRegistry,
  AgentRegistryChannel,
  AgentRegistryModel,
  ChannelWrite,
  ModelWrite,
  PageContext,
  PageContextReporter,
} from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { SplitLayout } from '../components/SplitLayout';
import { PageToolbar, PageToolbarSpacer, PageToolbarSearch } from '../components/PageToolbar';
import { RailItem } from '../components/RailItem';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { Empty } from '../components/Empty';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import './ModelMaintenance.css';

// The editor pane is contextual to what the rail has selected: an existing
// channel, an existing model, a blank channel draft, or a blank model draft
// nested under a channel.
export type Selection =
  | { kind: 'channel'; channel: string }
  | { kind: 'model'; channel: string; modelId: string }
  | { kind: 'new-channel' }
  | { kind: 'add-model'; channel: string }
  | null;

type ChannelForm = {
  name: string;
  label: string;
  type: 'zenmux' | 'openai_compatible';
  base_url: string;
  anthropic_base_url: string;
  api_key_env: string;
};

type ModelForm = {
  id: string;
  provider: string;
  label: string;
  description: string;
  tagsText: string;
  protocol: string;
};

export type Props = {
  registry: AgentRegistry | null;
  loading: boolean;
  saving: boolean;
  saveStatus: string | null;
  validationErrors: string[] | null;
  onValidateDraft: (kind: string, payload: unknown) => void;
  onClearValidation: () => void;
  onReload: () => void;
  onSaveChannel: (name: string, write: ChannelWrite) => Promise<boolean>;
  onCreateChannel: (write: ChannelWrite) => Promise<boolean>;
  onDeleteChannel: (name: string) => Promise<boolean>;
  onSaveModel: (channel: string, id: string, write: ModelWrite) => Promise<boolean>;
  onCreateModel: (channel: string, write: ModelWrite) => Promise<boolean>;
  onDeleteModel: (channel: string, id: string) => Promise<boolean>;
  onSetDefault: (channel: string, id: string) => Promise<boolean>;
  onPageContextChange?: PageContextReporter;
};

const blankChannel = (): ChannelForm => ({
  name: '',
  label: '',
  type: 'zenmux',
  base_url: '',
  anthropic_base_url: '',
  api_key_env: '',
});

const blankModel = (): ModelForm => ({
  id: '',
  provider: '',
  label: '',
  description: '',
  tagsText: '',
  protocol: '',
});

function channelFormFromChannel(ch: AgentRegistryChannel): ChannelForm {
  return {
    name: ch.name,
    label: ch.label,
    type: ch.type,
    base_url: ch.base_url,
    anthropic_base_url: ch.anthropic_base_url ?? '',
    api_key_env: ch.api_key_env ?? '',
  };
}

function modelFormFromModel(m: AgentRegistryModel): ModelForm {
  return {
    id: m.id,
    provider: m.provider,
    label: m.label,
    description: m.description ?? '',
    tagsText: m.tags.join(', '),
    protocol: m.protocol ?? '',
  };
}

function toChannelWrite(f: ChannelForm, models?: ModelWrite[]): ChannelWrite {
  const write: ChannelWrite = {
    name: f.name.trim(),
    label: f.label.trim(),
    type: f.type,
    base_url: f.base_url.trim(),
    // The Anthropic base URL is a zenmux-only routing detail; never persist a
    // stale value once the channel is switched to an OpenAI-compatible type.
    anthropic_base_url: f.type === 'zenmux' ? f.anthropic_base_url.trim() || null : null,
    api_key_env: f.api_key_env.trim() || null,
  };
  if (models) write.models = models;
  return write;
}

function toModelWrite(f: ModelForm): ModelWrite {
  return {
    id: f.id.trim(),
    provider: f.provider.trim(),
    label: f.label.trim(),
    description: f.description.trim() || null,
    tags: f.tagsText.split(',').map((t) => t.trim()).filter(Boolean),
    protocol: f.protocol.trim() || null,
  };
}

function matchesNeedle(value: string, needle: string): boolean {
  return value.toLowerCase().includes(needle);
}

export function ModelMaintenance({
  registry,
  loading,
  saving,
  saveStatus,
  validationErrors,
  onValidateDraft,
  onClearValidation,
  onReload,
  onSaveChannel,
  onCreateChannel,
  onDeleteChannel,
  onSaveModel,
  onCreateModel,
  onDeleteModel,
  onSetDefault,
  onPageContextChange,
}: Props) {
  const [filter, setFilter] = useState('');
  const [selection, setSelection] = useState<Selection>(null);
  const [channelForm, setChannelForm] = useState<ChannelForm>(blankChannel);
  const [modelForm, setModelForm] = useState<ModelForm>(blankModel);

  const channels = registry?.channels ?? [];
  const channelCount = channels.length;
  const healthyCount = channels.filter((c) => c.healthy).length;

  const isDefault = (channel: string, id: string) =>
    registry?.default.channel === channel && registry?.default.model === id;

  const pageContext = useMemo<PageContext>(() => ({
    route: 'model-maintenance',
    title: 'Model Maintenance',
    path: '/',
    entity_ids: {
      channel: selection && 'channel' in selection ? selection.channel : null,
    },
    snapshot: {
      channel_count: channelCount,
      healthy_count: healthyCount,
      selected: selection?.kind ?? null,
    },
    chips: ['Models', `${channelCount} channels`, `${healthyCount}/${channelCount} healthy`],
  }), [channelCount, healthyCount, selection]);
  usePageContextReporter(pageContext, onPageContextChange);

  // --- selection handlers (also reset the relevant draft + clear validation) ---
  const selectChannel = (name: string) => {
    const ch = channels.find((c) => c.name === name);
    setSelection({ kind: 'channel', channel: name });
    setChannelForm(ch ? channelFormFromChannel(ch) : blankChannel());
    onClearValidation();
  };

  const selectModel = (channel: string, id: string) => {
    const m = channels.find((c) => c.name === channel)?.models.find((x) => x.id === id);
    setSelection({ kind: 'model', channel, modelId: id });
    setModelForm(m ? modelFormFromModel(m) : blankModel());
    onClearValidation();
  };

  const startNewChannel = () => {
    setSelection({ kind: 'new-channel' });
    setChannelForm(blankChannel());
    setModelForm(blankModel());
    onClearValidation();
  };

  const startAddModel = (channel: string) => {
    setSelection({ kind: 'add-model', channel });
    setModelForm(blankModel());
    onClearValidation();
  };

  // --- form change handlers (drive debounced validation for NEW drafts) ---
  const updateChannelForm = (patch: Partial<ChannelForm>) => {
    const next = { ...channelForm, ...patch };
    setChannelForm(next);
    if (selection?.kind === 'new-channel') {
      onValidateDraft('add_channel', toChannelWrite(next, [toModelWrite(modelForm)]));
    }
  };

  const updateModelForm = (patch: Partial<ModelForm>) => {
    const next = { ...modelForm, ...patch };
    setModelForm(next);
    if (selection?.kind === 'new-channel') {
      onValidateDraft('add_channel', toChannelWrite(channelForm, [toModelWrite(next)]));
    } else if (selection?.kind === 'add-model') {
      // Backend _apply_draft('add_model', …) expects {channel, model}, not a
      // bare ModelWrite — sending the bare model KeyErrors into a swallowed 500.
      onValidateDraft('add_model', { channel: selection.channel, model: toModelWrite(next) });
    }
  };

  // --- submit handlers ---
  const submitNewChannel = async () => {
    const write = toChannelWrite(channelForm, [toModelWrite(modelForm)]);
    const ok = await onCreateChannel(write);
    if (ok) selectChannel(write.name);
  };

  const submitAddModel = async () => {
    if (selection?.kind !== 'add-model') return;
    const write = toModelWrite(modelForm);
    const channel = selection.channel;
    const ok = await onCreateModel(channel, write);
    if (ok) selectModel(channel, write.id);
  };

  const submitSaveChannel = async () => {
    if (selection?.kind !== 'channel') return;
    await onSaveChannel(selection.channel, toChannelWrite(channelForm));
  };

  const submitSaveModel = async () => {
    if (selection?.kind !== 'model') return;
    await onSaveModel(selection.channel, selection.modelId, toModelWrite(modelForm));
  };

  const submitDeleteChannel = async () => {
    if (selection?.kind !== 'channel') return;
    const ok = await onDeleteChannel(selection.channel);
    if (ok) setSelection(null);
  };

  const submitDeleteModel = async () => {
    if (selection?.kind !== 'model') return;
    const channel = selection.channel;
    const ok = await onDeleteModel(channel, selection.modelId);
    if (ok) selectChannel(channel);
  };

  // --- rail filtering ---
  const visibleChannels = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const result: { channel: AgentRegistryChannel; models: AgentRegistryModel[] }[] = [];
    for (const channel of channels) {
      const channelHit = !needle || matchesNeedle(channel.label, needle) || matchesNeedle(channel.name, needle);
      const models = channelHit
        ? channel.models
        : channel.models.filter((m) => matchesNeedle(m.label, needle) || matchesNeedle(m.id, needle));
      if (channelHit || models.length > 0) result.push({ channel, models });
    }
    return result;
  }, [channels, filter]);

  // --- field renderers ---
  const renderChannelFields = (nameEditable: boolean) => (
    <div className="wl-mm__grid">
      <label className="wl-mm__field">
        <span className="wl-mm__label">Name (key)</span>
        <input
          value={channelForm.name}
          disabled={!nameEditable || saving}
          onChange={(e) => updateChannelForm({ name: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Label</span>
        <input
          value={channelForm.label}
          disabled={saving}
          onChange={(e) => updateChannelForm({ label: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Type</span>
        <select
          value={channelForm.type}
          disabled={saving}
          onChange={(e) => updateChannelForm({ type: e.currentTarget.value as ChannelForm['type'] })}
        >
          <option value="zenmux">zenmux</option>
          <option value="openai_compatible">openai_compatible</option>
        </select>
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Base URL</span>
        <input
          value={channelForm.base_url}
          disabled={saving}
          onChange={(e) => updateChannelForm({ base_url: e.currentTarget.value })}
        />
      </label>
      {channelForm.type === 'zenmux' && (
        <label className="wl-mm__field">
          <span className="wl-mm__label">Anthropic base URL</span>
          <input
            value={channelForm.anthropic_base_url}
            disabled={saving}
            onChange={(e) => updateChannelForm({ anthropic_base_url: e.currentTarget.value })}
          />
        </label>
      )}
      <label className="wl-mm__field">
        <span className="wl-mm__label">API key env</span>
        <input
          value={channelForm.api_key_env}
          disabled={saving}
          onChange={(e) => updateChannelForm({ api_key_env: e.currentTarget.value })}
        />
      </label>
    </div>
  );

  const renderModelFields = () => (
    <div className="wl-mm__grid">
      <label className="wl-mm__field">
        <span className="wl-mm__label">Model ID</span>
        <input
          value={modelForm.id}
          disabled={saving}
          onChange={(e) => updateModelForm({ id: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Provider</span>
        <input
          value={modelForm.provider}
          disabled={saving}
          onChange={(e) => updateModelForm({ provider: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Label</span>
        <input
          value={modelForm.label}
          disabled={saving}
          onChange={(e) => updateModelForm({ label: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field">
        <span className="wl-mm__label">Protocol</span>
        <input
          value={modelForm.protocol}
          disabled={saving}
          placeholder="(optional)"
          onChange={(e) => updateModelForm({ protocol: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field wl-mm__field--wide">
        <span className="wl-mm__label">Tags (comma-separated)</span>
        <input
          value={modelForm.tagsText}
          disabled={saving}
          placeholder="fast, extractor"
          onChange={(e) => updateModelForm({ tagsText: e.currentTarget.value })}
        />
      </label>
      <label className="wl-mm__field wl-mm__field--wide">
        <span className="wl-mm__label">Description</span>
        <input
          value={modelForm.description}
          disabled={saving}
          onChange={(e) => updateModelForm({ description: e.currentTarget.value })}
        />
      </label>
    </div>
  );

  const renderValidationErrors = () =>
    validationErrors && validationErrors.length > 0 ? (
      <ul className="wl-mm__issues">
        {validationErrors.map((err, index) => (
          <li key={index} className="wl-mm__issue--error">✕ {err}</li>
        ))}
      </ul>
    ) : null;

  const renderEditor = () => {
    if (loading) return <Empty message="Loading registry…" symbol="◎" variant="loading" />;
    if (!selection) {
      return <Empty message="Select a channel or model to edit it, or add a new channel." symbol="◈" />;
    }
    if (selection.kind === 'new-channel') {
      return (
        <div className="wl-mm__form">
          <div className="wl-mm__section-head"><span>New channel</span></div>
          {renderChannelFields(true)}
          <div className="wl-mm__section-head"><span>First model</span></div>
          {renderModelFields()}
          {renderValidationErrors()}
          <div className="wl-mm__actions">
            <Button type="button" variant="primary" disabled={saving} onClick={() => void submitNewChannel()}>
              Create channel
            </Button>
          </div>
        </div>
      );
    }
    if (selection.kind === 'add-model') {
      return (
        <div className="wl-mm__form">
          <div className="wl-mm__section-head">
            <span>Add model</span>
            <small>to channel {selection.channel}</small>
          </div>
          {renderModelFields()}
          {renderValidationErrors()}
          <div className="wl-mm__actions">
            <Button type="button" variant="primary" disabled={saving} onClick={() => void submitAddModel()}>
              Add model
            </Button>
          </div>
        </div>
      );
    }
    if (selection.kind === 'channel') {
      return (
        <div className="wl-mm__form">
          <div className="wl-mm__section-head"><span>Edit channel</span></div>
          {renderChannelFields(false)}
          {renderValidationErrors()}
          <div className="wl-mm__actions">
            <Button type="button" variant="primary" disabled={saving} onClick={() => void submitSaveChannel()}>
              Save
            </Button>
            <Button type="button" disabled={saving} onClick={() => startAddModel(selection.channel)}>
              Add Model
            </Button>
            <Button type="button" variant="danger" disabled={saving} onClick={() => void submitDeleteChannel()}>
              Delete Channel
            </Button>
          </div>
        </div>
      );
    }
    // selection.kind === 'model'
    return (
      <div className="wl-mm__form">
        <div className="wl-mm__section-head">
          <span>Edit model</span>
          <small>in channel {selection.channel}</small>
        </div>
        {renderModelFields()}
        {renderValidationErrors()}
        <div className="wl-mm__actions">
          <Button type="button" variant="primary" disabled={saving} onClick={() => void submitSaveModel()}>
            Save
          </Button>
          <Button
            type="button"
            disabled={saving || isDefault(selection.channel, selection.modelId)}
            onClick={() => void onSetDefault(selection.channel, selection.modelId)}
          >
            Set as default
          </Button>
          <Button type="button" variant="danger" disabled={saving} onClick={() => void submitDeleteModel()}>
            Delete Model
          </Button>
        </div>
      </div>
    );
  };

  const rail = (
    <div className="wl-mm__list">
      {visibleChannels.length === 0 ? (
        <Empty message="No channels match this filter." symbol="∅" />
      ) : (
        visibleChannels.map(({ channel, models }) => (
          <div key={channel.name} className="wl-mm__group">
            <RailItem
              layout="row"
              className="wl-mm__channel"
              active={selection?.kind === 'channel' && selection.channel === channel.name}
              onClick={() => selectChannel(channel.name)}
            >
              <span className="wl-mm__channel-name wl-rail__title">{channel.label}</span>
              <Badge variant={channel.healthy ? 'pos' : 'neg'}>{channel.healthy ? 'healthy' : 'down'}</Badge>
            </RailItem>
            {models.map((m) => (
              <RailItem
                key={m.id}
                layout="row"
                className="wl-mm__model"
                active={
                  selection?.kind === 'model' &&
                  selection.channel === channel.name &&
                  selection.modelId === m.id
                }
                onClick={() => selectModel(channel.name, m.id)}
              >
                <span className="wl-mm__model-name wl-rail__title">{m.label}</span>
                {isDefault(channel.name, m.id) && <Badge variant="info">default</Badge>}
              </RailItem>
            ))}
          </div>
        ))
      )}
    </div>
  );

  const feedbackNode = saveStatus ? <p className="wl-mm__save-status">{saveStatus}</p> : undefined;

  return (
    <PageScaffold title="Model Maintenance" chips={pageContext.chips} feedback={feedbackNode}>
      <PageToolbar>
        <Button type="button" variant="ghost" onClick={startNewChannel}>
          <Plus size={14} aria-hidden="true" />
          <span>New Channel</span>
        </Button>
        <Button type="button" onClick={onReload}>
          <RefreshCw size={16} aria-hidden="true" />
          <span>Reload</span>
        </Button>
        <span className="wl-mm__health-chip" aria-live="polite">
          {healthyCount} of {channelCount} channels healthy
        </span>
        <PageToolbarSpacer />
        <PageToolbarSearch
          value={filter}
          onChange={setFilter}
          placeholder="Filter channels & models…"
          aria-label="Filter channels and models"
        />
      </PageToolbar>
      <SplitLayout rail={rail} railLabel="Channels">
        <section className="wl-mm__editor">{renderEditor()}</section>
      </SplitLayout>
    </PageScaffold>
  );
}
