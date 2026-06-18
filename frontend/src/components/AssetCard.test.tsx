import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AssetCard } from './AssetCard';
import type { AgentAsset } from '../types';

describe('AssetCard', () => {
  const asset: AgentAsset = {
    id: 'a1',
    kind: 'json',
    title: 'pricing_request.json',
    metadata: { size: '3.2KB' },
  };

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders kind icon, title, and subtitle', () => {
    render(<AssetCard asset={asset} subtitle="3.2KB · LangGraph trace" />);
    expect(screen.getByText('JSON')).toBeInTheDocument();
    expect(screen.getByText('pricing_request.json')).toBeInTheDocument();
    expect(screen.getByText('3.2KB · LangGraph trace')).toBeInTheDocument();
  });

  it('has no a11y violations', async () => {
    const { container } = render(<AssetCard asset={asset} subtitle="3.2KB" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('renders ChartAsset when kind is chart and expanded', async () => {
    const chartAsset: AgentAsset = {
      id: 'chart-1',
      kind: 'chart',
      title: 'Risk',
      data: {
        chart_type: 'bar',
        x_key: 'name',
        y_key: 'value',
        series: [{ name: 'a', value: 1 }],
      },
    };
    render(<AssetCard asset={chartAsset} />);
    await userEvent.click(screen.getByLabelText(/expand chart/i));
    expect(document.querySelector('.wl-chart-asset')).not.toBeNull();
  });

  it('renders html assets with open, download, and preview controls', async () => {
    const htmlAsset: AgentAsset = {
      id: 'html-1',
      kind: 'html',
      title: 'candle_000852_SH.html',
      url: '/api/artifacts/agent/thread-1/trading_desk/charts/candle_000852_SH.html',
      path: '/trading_desk/charts/candle_000852_SH.html',
      mime_type: 'text/html',
    };

    render(<AssetCard asset={htmlAsset} />);

    expect(screen.getByText('HTML')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText(/expand html/i));
    expect(screen.getByTitle('candle_000852_SH.html')).toHaveAttribute('src', htmlAsset.url);
  });

  it('opens URL assets through an explicit click handler', async () => {
    const htmlAsset: AgentAsset = {
      id: 'html-1',
      kind: 'html',
      title: 'report.html',
      url: '/api/artifacts/report.html',
      mime_type: 'text/html',
    };
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);

    render(<AssetCard asset={htmlAsset} />);
    await userEvent.click(screen.getByRole('button', { name: /open/i }));

    expect(open).toHaveBeenCalledWith(
      'http://localhost:3000/api/artifacts/report.html',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('downloads URL assets by fetching a blob', async () => {
    const htmlAsset: AgentAsset = {
      id: 'html-1',
      kind: 'html',
      title: 'report.html',
      url: '/api/artifacts/report.html',
      mime_type: 'text/html',
    };
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const fetch = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response('report body', { status: 200, headers: { 'content-type': 'text/html' } }),
    );

    render(<AssetCard asset={htmlAsset} />);
    await userEvent.click(screen.getByRole('button', { name: /download/i }));

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:3000/api/artifacts/report.html',
      { credentials: 'same-origin' },
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report');
  });

  it('opens a preview modal for JSON assets instead of a new tab', async () => {
    const jsonAsset: AgentAsset = {
      id: 'json-1',
      kind: 'json',
      title: 'pricing_request.json',
      data: { trade_id: 'T1', price: 99.5 },
    };
    const open = vi.spyOn(window, 'open');

    render(<AssetCard asset={jsonAsset} />);
    await userEvent.click(screen.getByRole('button', { name: /open/i }));

    expect(open).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/"trade_id"/)).toBeInTheDocument();
      expect(screen.getByText(/"T1"/)).toBeInTheDocument();
    });
  });

  it('opens a preview modal for markdown assets fetched from URL', async () => {
    const markdownAsset: AgentAsset = {
      id: 'md-1',
      kind: 'markdown',
      title: 'default_portfolio.md',
      url: '/api/artifacts/default_portfolio.md',
    };
    const open = vi.spyOn(window, 'open');
    vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response('# Summary\n\n- item one', { status: 200 }),
    );

    render(<AssetCard asset={markdownAsset} />);
    await userEvent.click(screen.getByRole('button', { name: /open/i }));

    expect(open).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /summary/i })).toBeInTheDocument();
      expect(screen.getByText('item one')).toBeInTheDocument();
    });
  });

  it('shows an Open button for JSON assets with inline data and no URL', () => {
    const jsonAsset: AgentAsset = {
      id: 'json-2',
      kind: 'json',
      title: 'Current page context',
      data: { page: 'dashboard' },
    };

    render(<AssetCard asset={jsonAsset} />);
    expect(screen.getByRole('button', { name: /open/i })).toBeInTheDocument();
  });
});
