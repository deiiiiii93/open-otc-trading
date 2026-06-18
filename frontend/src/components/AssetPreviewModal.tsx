import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Modal } from './Modal';
import type { AgentAsset } from '../types';
import './AssetPreviewModal.css';

type Props = {
  asset: AgentAsset | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function AssetPreviewModal({ asset, open, onOpenChange }: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !asset) {
      setContent(null);
      setError(null);
      return;
    }
    setContent(null);
    setError(null);
    loadAssetContent(asset)
      .then((text) => setContent(text))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load asset'));
  }, [open, asset]);

  if (!asset) return null;

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={asset.title}
      defaultWidth={720}
      defaultHeight={520}
    >
      <div className="wl-asset-preview">
        {error && <div className="wl-asset-preview__error">{error}</div>}
        {content === null && !error && (
          <div className="wl-asset-preview__loading">Loading…</div>
        )}
        {content !== null && asset.kind === 'markdown' && (
          <div className="wl-asset-preview__markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {content}
            </ReactMarkdown>
          </div>
        )}
        {content !== null && asset.kind === 'json' && (
          <JsonTree data={parseJson(content)} />
        )}
      </div>
    </Modal>
  );
}

async function loadAssetContent(asset: AgentAsset): Promise<string> {
  if (asset.kind === 'json') {
    if (asset.data !== undefined) {
      return typeof asset.data === 'string' ? asset.data : JSON.stringify(asset.data, null, 2);
    }
  }
  if (asset.kind === 'markdown' && typeof asset.data === 'string') {
    return asset.data;
  }
  if (asset.url) {
    const response = await fetch(absoluteUrl(asset.url), { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Failed to load asset: ${response.status}`);
    return response.text();
  }
  if (asset.data !== undefined) {
    return typeof asset.data === 'string' ? asset.data : JSON.stringify(asset.data, null, 2);
  }
  throw new Error('Asset has no content');
}

function absoluteUrl(url: string): string {
  return new URL(url, window.location.href).toString();
}

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function JsonTree({ data }: { data: unknown }) {
  return <div className="wl-asset-preview__json">{renderJsonValue(data)}</div>;
}

function renderJsonValue(value: unknown): React.ReactNode {
  if (value === null) {
    return <span className="wl-asset-preview__json-null">null</span>;
  }
  if (typeof value === 'boolean') {
    return <span className="wl-asset-preview__json-bool">{String(value)}</span>;
  }
  if (typeof value === 'number') {
    return <span className="wl-asset-preview__json-number">{String(value)}</span>;
  }
  if (typeof value === 'string') {
    return <span className="wl-asset-preview__json-string">{JSON.stringify(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="wl-asset-preview__json-punct">[]</span>;
    return (
      <span className="wl-asset-preview__json-block">
        <span className="wl-asset-preview__json-punct">[</span>
        {value.map((item, index) => (
          <div key={index} className="wl-asset-preview__json-row">
            {renderJsonValue(item)}
            {index < value.length - 1 && (
              <span className="wl-asset-preview__json-punct">,</span>
            )}
          </div>
        ))}
        <span className="wl-asset-preview__json-punct">]</span>
      </span>
    );
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="wl-asset-preview__json-punct">{'{}'}</span>;
    return (
      <span className="wl-asset-preview__json-block">
        <span className="wl-asset-preview__json-punct">{'{'}</span>
        {entries.map(([key, item], index) => (
          <div key={key} className="wl-asset-preview__json-row">
            <span className="wl-asset-preview__json-key">{JSON.stringify(key)}:</span>
            {renderJsonValue(item)}
            {index < entries.length - 1 && (
              <span className="wl-asset-preview__json-punct">,</span>
            )}
          </div>
        ))}
        <span className="wl-asset-preview__json-punct">{'}'}</span>
      </span>
    );
  }
  return null;
}
