import { ChevronDown, RefreshCw } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { AgentChannel, AgentModelSelection } from '../types';
import { colorForProvider } from './providerColors';
import './ModelPicker.css';

type Props = {
  channels: AgentChannel[];
  selected: AgentModelSelection | null;
  onChange: (s: AgentModelSelection) => void;
  onRefresh?: () => void | Promise<void>;
  disabled?: boolean;
  compact?: boolean;
};

export function ModelPicker({
  channels,
  selected,
  onChange,
  onRefresh,
  disabled,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const panelId = useId();
  const optionBaseId = useId();

  const options = useMemo(() => (
    channels.flatMap((ch) => ch.models.map((md) => ({ channel: ch, model: md })))
  ), [channels]);

  const noHealthy = !channels.some((ch) => ch.healthy);
  const isDisabled = !!disabled || noHealthy;

  useEffect(() => {
    if (!open) return;
    const handlePointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = options.findIndex(({ model }) => (
      selected != null
      && selected.channel === model.channel
      && selected.provider === model.provider
      && selected.model === model.model
    ));
    const firstHealthyIndex = options.findIndex(({ channel }) => channel.healthy);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : Math.max(firstHealthyIndex, 0));
  }, [open, options, selected]);

  const triggerLabel = isDisabled
    ? 'Agent disabled'
    : findSelectedLabel(channels, selected) ?? 'Pick a model';

  const selectOption = (index: number) => {
    const option = options[index];
    if (!option || !option.channel.healthy) return;
    onChange({
      channel: option.model.channel,
      provider: option.model.provider,
      model: option.model.model,
    });
    setOpen(false);
  };

  const handleKeyDown = (event: ReactKeyboardEvent) => {
    if (isDisabled) return;
    if (!open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      setOpen(true);
      return;
    }
    if (!open) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) => {
        if (options.length === 0) return 0;
        return (current + direction + options.length) % options.length;
      });
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      selectOption(activeIndex);
    }
  };

  const handleRefresh = async () => {
    if (!onRefresh || refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      await onRefresh();
      setRefreshedAt(new Date());
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div
      className={`wl-model-picker${compact ? ' wl-model-picker--compact' : ''}`}
      ref={containerRef}
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        className="wl-model-picker__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={panelId}
        aria-activedescendant={open ? `${optionBaseId}-${activeIndex}` : undefined}
        onClick={() => !isDisabled && setOpen((v) => !v)}
        disabled={isDisabled}
      >
        {selected && (
          <span
            className="wl-model-picker__dot"
            style={{ background: colorForProvider(selected.provider) }}
            aria-hidden="true"
          />
        )}
        <span className="wl-model-picker__trigger-label">{triggerLabel}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div id={panelId} role="listbox" className="wl-model-picker__panel">
          {onRefresh && (
            <div className="wl-model-picker__panel-head">
              <div>
                <span className="wl-model-picker__panel-title">Model catalog</span>
                <span className="wl-model-picker__panel-meta">
                  {refreshing
                    ? 'Refreshing...'
                    : refreshedAt
                      ? `Refreshed ${formatTime(refreshedAt)}`
                      : 'Not refreshed this session'}
                </span>
                {refreshError && (
                  <span className="wl-model-picker__panel-error">{refreshError}</span>
                )}
              </div>
              <button
                type="button"
                className="wl-model-picker__refresh"
                aria-label="refresh model catalog"
                onClick={handleRefresh}
                disabled={refreshing}
              >
                <RefreshCw size={14} aria-hidden="true" />
              </button>
            </div>
          )}
          {channels.map((ch) => (
            <div
              key={ch.name}
              className={ch.healthy ? '' : 'wl-model-picker__group--unhealthy'}
              role="group"
              aria-label={ch.label}
            >
              <span className="wl-model-picker__group-label">{ch.label.toUpperCase()}</span>
              {ch.models.map((md) => {
                const optionIndex = options.findIndex(({ channel, model }) => (
                  channel.name === ch.name
                  && model.provider === md.provider
                  && model.model === md.model
                ));
                const active =
                  selected != null
                  && selected.channel === md.channel
                  && selected.provider === md.provider
                  && selected.model === md.model;
                const focused = optionIndex === activeIndex;
                return (
                  <div
                    key={`${ch.name}:${md.model}`}
                    id={`${optionBaseId}-${optionIndex}`}
                    role="option"
                    aria-selected={active}
                    aria-disabled={!ch.healthy}
                    title={
                      ch.healthy
                        ? md.description ?? undefined
                        : `${ch.label} is disabled because its API key is not configured.`
                    }
                    className={`wl-model-picker__row${active ? ' is-active' : ''}${focused ? ' is-focused' : ''}`}
                    onMouseEnter={() => setActiveIndex(optionIndex)}
                    onClick={() => {
                      selectOption(optionIndex);
                    }}
                  >
                    <span
                      className="wl-model-picker__dot"
                      style={{ background: colorForProvider(md.provider) }}
                      aria-hidden="true"
                    />
                    <span className="wl-model-picker__row-label">{md.label}</span>
                    {(md.tags ?? []).map((t) => (
                      <span key={t} className="wl-model-picker__tag">{t}</span>
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function findSelectedLabel(
  channels: AgentChannel[],
  selected: AgentModelSelection | null,
): string | null {
  if (!selected) return null;
  for (const ch of channels) {
    if (ch.name !== selected.channel) continue;
    for (const md of ch.models) {
      if (md.provider === selected.provider && md.model === selected.model) {
        return md.label;
      }
    }
  }
  return selected.model;
}
