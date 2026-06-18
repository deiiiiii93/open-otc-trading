import React, { useState } from 'react';
import type { AgentAsset } from '../types';
import { ChartAsset } from './ChartAsset';
import './AssetCard.css';

const kindLabel: Record<AgentAsset['kind'], string> = {
  file: 'FILE',
  html: 'HTML',
  image: 'IMG',
  table: 'TBL',
  chart: 'CHRT',
  json: 'JSON',
  markdown: 'MD',
};

type Props = {
  asset: AgentAsset;
  subtitle?: string;
  actions?: React.ReactNode;
};

export function AssetCard({ asset, subtitle, actions }: Props) {
  const [expanded, setExpanded] = useState(false);
  const isChart = asset.kind === 'chart';
  const isHtml = asset.kind === 'html';
  const tableData = asset.kind === 'table' ? normalizeTableAsset(asset.data) : null;
  const canPreview = isChart || isHtml;

  return (
    <div className="wl-asset">
      <div className="wl-asset__icon">{kindLabel[asset.kind]}</div>
      <div className="wl-asset__meta">
        <div className="wl-asset__title">{asset.title}</div>
        {subtitle && <div className="wl-asset__sub">{subtitle}</div>}
      </div>
      <div className="wl-asset__actions">
        {asset.url && (
          <button className="wl-asset__action" type="button" onClick={() => openAsset(asset)}>
            Open
          </button>
        )}
        <button className="wl-asset__action" type="button" onClick={() => { void downloadAsset(asset); }}>
          Download
        </button>
        {actions}
      </div>
      {canPreview && (
        <button
          className="wl-asset__expand"
          onClick={() => setExpanded((prev) => !prev)}
          aria-label={expanded ? `Collapse ${asset.kind}` : `Expand ${asset.kind}`}
        >
          {expanded ? '▾' : '▸'}
        </button>
      )}
      {isChart && expanded && (
        <div className="wl-asset__chart">
          <ChartAsset data={asset.data} title={asset.title} />
        </div>
      )}
      {isHtml && expanded && asset.url && (
        <div className="wl-asset__html">
          <iframe
            className="wl-asset__html-frame"
            title={asset.title}
            src={asset.url}
            sandbox="allow-scripts allow-same-origin"
          />
        </div>
      )}
      {tableData && (
        <div className="wl-asset__table-wrap">
          <table className="wl-asset__table">
            <thead>
              <tr>
                {tableData.columns.map((column) => <th key={column} scope="col">{column}</th>)}
              </tr>
            </thead>
            <tbody>
              {tableData.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {tableData.columns.map((column) => (
                    <td key={column}>{formatAssetCell(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function openAsset(asset: AgentAsset): void {
  if (!asset.url) return;
  const url = absoluteUrl(asset.url);
  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (!opened) {
    window.location.assign(url);
  }
}

async function downloadAsset(asset: AgentAsset): Promise<void> {
  if (asset.url) {
    await downloadRemoteAsset(asset);
    return;
  }
  downloadLocalAsset(asset);
}

async function downloadRemoteAsset(asset: AgentAsset): Promise<void> {
  const filename = asset.title || `${asset.id}.${asset.kind}`;
  const url = absoluteUrl(asset.url ?? '');
  try {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) {
      throw new Error(`Artifact download failed: ${response.status}`);
    }
    const blob = await response.blob();
    triggerBlobDownload(blob, filename);
  } catch {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
}

function downloadLocalAsset(asset: AgentAsset): void {
  const filename = asset.title || `${asset.id}.${asset.kind}`;
  const payload = asset.data === undefined
    ? JSON.stringify(asset.metadata ?? {}, null, 2)
    : typeof asset.data === 'string'
      ? asset.data
      : JSON.stringify(asset.data, null, 2);
  const blob = new Blob([payload], { type: asset.mime_type ?? 'application/octet-stream' });
  triggerBlobDownload(blob, filename);
}

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function absoluteUrl(url: string): string {
  return new URL(url, window.location.href).toString();
}

function normalizeTableAsset(data: unknown): { columns: string[]; rows: Record<string, unknown>[] } | null {
  if (!data || typeof data !== 'object') return null;
  const candidate = data as { columns?: unknown; rows?: unknown };
  if (!Array.isArray(candidate.columns) || !Array.isArray(candidate.rows)) return null;
  const columns = candidate.columns.filter((column): column is string => typeof column === 'string');
  const rows = candidate.rows.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object' && !Array.isArray(row));
  if (!columns.length || !rows.length) return null;
  return { columns, rows };
}

function formatAssetCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toPrecision(6);
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}
