import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import { Portfolios } from './Portfolios';
import type { PortfolioDetail, PortfolioSummary } from '../types';

const portfolios: PortfolioSummary[] = [
  {
    id: 1, name: 'Snow', kind: 'view', base_currency: 'USD',
    description: null, tags: [], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 5, created_at: '2026-05-10', updated_at: '2026-05-10',
  },
  {
    id: 2, name: 'Book', kind: 'container', base_currency: 'USD',
    description: null, tags: ['desk'], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 12, created_at: '2026-05-10', updated_at: '2026-05-10',
  },
];

const viewDetail: PortfolioDetail = {
  ...portfolios[0],
  positions: [],
};

const noop = () => {};
const asyncNoop = async () => {};

const baseProps = {
  portfolios,
  allPortfolios: portfolios,
  allPositions: [] as { id: number; source_trade_id?: string | null; underlying: string; product_type: string }[],
  selected: viewDetail,
  selectedPortfolioId: 1,
  pendingMembershipPreview: [
    { id: 10, source_trade_id: 'TRADE-10', underlying: 'AAPL', product_type: 'Snowball', quantity: -2, entry_price: 100, status: 'open' },
    { id: 11, source_trade_id: 'TRADE-11', underlying: 'TSLA', product_type: 'Snowball', quantity: 1, entry_price: 200, status: 'open' },
    { id: 12, source_trade_id: 'TRADE-12', underlying: 'AAPL', product_type: 'Snowball', quantity: -1, entry_price: 100, status: 'open' },
  ],
  saveState: { kind: 'idle' } as const,
  onSelectPortfolio: noop,
  onOpenCreate: noop,
  onOpenDelete: noop,
  onSaveRule: asyncNoop,
  onAddInclude: asyncNoop,
  onRemoveInclude: asyncNoop,
  onAddExclude: asyncNoop,
  onRemoveExclude: asyncNoop,
  onAddSource: asyncNoop,
  onRemoveSource: asyncNoop,
  onSetTags: asyncNoop,
  onRunPricing: noop,
  onRunRisk: noop,
};

test('renders PageHeader title with selected portfolio name', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('PORTFOLIOS · Snow');
});

test('renders four Tiles with computed values for resolved rows', () => {
  render(<Portfolios {...baseProps} />);

  // Find the tiles container and scope all tile-value lookups inside it
  const tilesContainer = document.querySelector('.wl-metric-row');
  expect(tilesContainer).toBeTruthy();
  const tiles = within(tilesContainer as HTMLElement);

  expect(tiles.getByText('POSITIONS')).toBeInTheDocument();
  expect(tiles.getByText('UNDERLYINGS')).toBeInTheDocument();
  expect(tiles.getByText('NET QTY')).toBeInTheDocument();
  expect(tiles.getByText('STATUS')).toBeInTheDocument();
  expect(tiles.getByText('3')).toBeInTheDocument();        // positions count (3 rows)
  expect(tiles.getByText('2')).toBeInTheDocument();        // unique underlyings (AAPL, TSLA)
  expect(tiles.getByText('-2')).toBeInTheDocument();       // signed net qty (-2 + 1 + -1)
  expect(tiles.getByText('ALL OPEN')).toBeInTheDocument();
});

test('renders five fieldsets for a View portfolio', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByRole('group', { name: /rule/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /sources/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /manual includes/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /manual excludes/i })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: /^tags$/i })).toBeInTheDocument();
});

test('renders only TAGS fieldset for a Container portfolio', () => {
  const containerDetail: PortfolioDetail = { ...portfolios[1], positions: [] };
  render(
    <Portfolios
      {...baseProps}
      selected={containerDetail}
      selectedPortfolioId={2}
      pendingMembershipPreview={null}
    />,
  );
  expect(screen.queryByRole('group', { name: /rule/i })).not.toBeInTheDocument();
  expect(screen.getByRole('group', { name: /^tags$/i })).toBeInTheDocument();
  expect(screen.getByText(/owned positions imported via XLSX/i)).toBeInTheDocument();
});

test('Run Pricing primary button calls onRunPricing', async () => {
  const onRunPricing = vi.fn();
  render(<Portfolios {...baseProps} onRunPricing={onRunPricing} />);
  await userEvent.click(screen.getByRole('button', { name: /run pricing/i }));
  expect(onRunPricing).toHaveBeenCalledTimes(1);
});

test('Portfolio select fires onSelectPortfolio with chosen id', async () => {
  const onSelectPortfolio = vi.fn();
  render(<Portfolios {...baseProps} onSelectPortfolio={onSelectPortfolio} />);
  await userEvent.selectOptions(screen.getByLabelText(/select portfolio/i), '2');
  expect(onSelectPortfolio).toHaveBeenCalledWith(2);
});

test('New menu opens and fires onOpenCreate with the chosen kind', async () => {
  const onOpenCreate = vi.fn();
  render(<Portfolios {...baseProps} onOpenCreate={onOpenCreate} />);
  await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /new view portfolio/i }));
  expect(onOpenCreate).toHaveBeenCalledWith('view');
});

test('Overflow menu Delete fires onOpenDelete', async () => {
  const onOpenDelete = vi.fn();
  render(<Portfolios {...baseProps} onOpenDelete={onOpenDelete} />);
  await userEvent.click(screen.getByRole('button', { name: /more actions/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /delete portfolio/i }));
  expect(onOpenDelete).toHaveBeenCalledTimes(1);
});

test('saving state shows label on Definition panel', () => {
  render(<Portfolios {...baseProps} saveState={{ kind: 'saving' }} />);
  expect(screen.getByText(/saving…/i)).toBeInTheDocument();
});

test('error save state shows the error message', () => {
  render(<Portfolios {...baseProps} saveState={{ kind: 'error', message: 'boom' }} />);
  expect(screen.getByText(/save failed — boom/i)).toBeInTheDocument();
});
