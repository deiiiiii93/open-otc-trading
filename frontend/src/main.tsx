import { StrictMode, useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LaptopMinimal, Moon, Rows2, Sun } from 'lucide-react';
import './tokens/index.css';
import { AppShell } from './components/AppShell';
import { Button } from './components/Button';
import { ThousandSeparatorProvider, useThousandSeparator } from './components/ThousandSeparatorContext';
import { DatePicker } from './components/DatePicker';
import { CommandPalette, type CommandItem } from './components/CommandPalette';
import { FloatingAgent } from './components/FloatingAgent';
import { FloatingAgentMiniChat } from './components/FloatingAgentMiniChat';
import { useTheme } from './hooks/useTheme';
import { useDensity } from './hooks/useDensity';
import { useCommandPalette } from './hooks/useCommandPalette';
import { useAgentChatController } from './hooks/useAgentChatController';
import type { PageContext, PageContextReporter, Route } from './types';
import { useRoute } from './hooks/useRoute';
import { pathToRoute, routeUrl, portfolioFromLocation } from './lib/routing';
import { PositionsLive } from './routes/Positions.live';
import { PricingParametersLive } from './routes/PricingParameters.live';
import { EngineConfigsLive } from './routes/EngineConfigs';
import { PortfoliosLive } from './routes/Portfolios.live';
import { RfqApprovalLive } from './routes/RfqApproval.live';
import { ClientRfqLive } from './routes/ClientRfq.live';
import { TrySolveLive } from './routes/TrySolve.live';
import { RiskLive } from './routes/Risk.live';
import { GreeksLandscapeLive } from './routes/GreeksLandscape';
import { TasksLive } from './routes/Tasks.live';
import { ReportsLive } from './routes/Reports.live';
import { AgentDeskLive } from './routes/AgentDesk.live';
import { BookingLive } from './routes/Booking.live';
import { HedgingLive } from './routes/Hedging.live';
import { InstrumentsLive } from './routes/Instruments.live';
import { SkillsLive } from './routes/Skills.live';
import { ScenarioTestLive } from './routes/ScenarioTest';
import { BacktestLive } from './routes/Backtest';
import { TracingLive } from './routes/Tracing.live';
import { ArenaLive } from './routes/Arena.live';
import { WorkflowsLive } from './routes/Workflows.live';
import { MemoryLive } from './routes/Memory.live';
import { fetchTracingConfig } from './api/client';
import { openTraceTarget } from './lib/tracing';
import type { TracingConfig } from './types';

const navItems = [
  { route: 'chat' as const,      label: 'Agent Desk' },
  { route: 'rfq' as const,       label: 'RFQ Approval' },
  { route: 'try-solve' as const, label: 'Try to Solve' },
  { route: 'positions' as const,  label: 'Positions' },
  { route: 'booking' as const,    label: 'Booking' },
  { route: 'pricing-parameters' as const, label: 'Pricing Parameters' },
  { route: 'engine-configs' as const, label: 'Engine Configs' },
  { route: 'portfolios' as const, label: 'Portfolios' },
  { route: 'instruments' as const, label: 'Instruments' },
  { route: 'hedging' as const,   label: 'Hedging' },
  { route: 'risk' as const,      label: 'Risk' },
  { route: 'greeks-landscape' as const, label: 'Greeks Landscape' },
  { route: 'scenario-test' as const, label: 'Scenario Test' },
  { route: 'backtest' as const,  label: 'Backtest' },
  { route: 'tasks' as const,     label: 'Tasks' },
  { route: 'reports' as const,   label: 'Reports' },
  { route: 'skills' as const,    label: 'Skills' },
  { route: 'tracing' as const,   label: 'Tracing' },
  { route: 'arena' as const,     label: 'Arena' },
  { route: 'workflows' as const, label: 'Workflows' },
  { route: 'memory' as const,    label: 'Memory' },
  { route: 'client' as const,    label: 'Client RFQ' },
];

function routeContext(route: Route): PageContext {
  const title = navItems.find((item) => item.route === route)?.label ?? route;
  return {
    route,
    title,
    path: location.pathname,
    entity_ids: {},
    snapshot: { route },
    chips: [title],
  };
}

function App() {
  const { route, navigate } = useRoute();
  const { theme, setTheme } = useTheme();
  const { density, toggle: toggleDensity } = useDensity();
  const { thousandSeparator, toggleThousandSeparator } = useThousandSeparator();
  const palette = useCommandPalette();
  const agentChat = useAgentChatController();
  const [agentOpen, setAgentOpen] = useState(false);
  const [accountingDate, setAccountingDate] = useState(() => todayIsoDate());
  // Last portfolio the user explicitly picked on Risk or Hedging — session
  // only. Pages treat it as a preference and fall back without writing back.
  const [sharedPortfolioId, setSharedPortfolioId] = useState<number | null>(
    () => portfolioFromLocation(window.location.pathname, window.location.search),
  );
  const [pageContext, setPageContext] = useState<PageContext>(
    () => routeContext(pathToRoute(window.location.pathname)),
  );
  const pageContextSignatureRef = useRef('');
  // Per-thread trace navigation: which thread the Tracing page is filtered to.
  const [traceThreadId, setTraceThreadId] = useState<number | null>(null);
  const [tracingConfig, setTracingConfig] = useState<TracingConfig | null>(null);

  useEffect(() => {
    fetchTracingConfig().then(setTracingConfig).catch(() => setTracingConfig(null));
  }, []);

  const handleOpenTrace = useCallback((threadId: number) => {
    const target = openTraceTarget(tracingConfig, threadId);
    if (target.kind === 'external') {
      window.open(target.url, '_blank', 'noopener');
    } else if (target.kind === 'internal') {
      setTraceThreadId(target.threadId);
      navigate('tracing');
    }
  }, [tracingConfig, navigate]);

  const handlePageContextChange = useCallback<PageContextReporter>((context) => {
    const next = { ...context, path: location.pathname };
    const signature = JSON.stringify(next);
    if (signature === pageContextSignatureRef.current) return;
    pageContextSignatureRef.current = signature;
    setPageContext(next);
  }, []);

  // Keep the URL's ?portfolio= in sync with the shared selection: attached only
  // on Risk/Hedging, stripped everywhere else (no cross-route leak). replaceState,
  // never push — a filter change must not create Back-history entries.
  useEffect(() => {
    const target = routeUrl(route, sharedPortfolioId);
    if (window.location.pathname + window.location.search !== target) {
      window.history.replaceState(null, '', target);
    }
  }, [route, sharedPortfolioId]);

  // Back/Forward must restore the portfolio selection the popped URL carried
  // (route-scoped, so popping to a non-portfolio page clears it). Reconcile the
  // URL imperatively here too: a popped entry may carry a stray ?portfolio= whose
  // derived state is unchanged (so the [route, sharedPortfolioId] sync effect
  // would not rerun to strip it).
  useEffect(() => {
    const onPop = () => {
      const nextPortfolio = portfolioFromLocation(
        window.location.pathname,
        window.location.search,
      );
      setSharedPortfolioId(nextPortfolio);
      const target = routeUrl(pathToRoute(window.location.pathname), nextPortfolio);
      if (window.location.pathname + window.location.search !== target) {
        window.history.replaceState(null, '', target);
      }
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    handlePageContextChange(routeContext(route));
  }, [handlePageContextChange, route]);

  const onSelectCommand = useCallback((item: CommandItem) => {
    palette.close();
    if (item.id === 'portfolios-create-container' || item.id === 'portfolios-create-view') {
      navigate('portfolios');
      return;
    }
    if (item.id.startsWith('jump-')) {
      const target = item.id.replace('jump-', '') as Route;
      navigate(target);
    }
  }, [palette, navigate]);

  const cycleTheme = () => {
    setTheme(theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system');
  };

  const themeIcon = theme === 'dark' ? (
    <Moon size={16} aria-hidden="true" />
  ) : theme === 'light' ? (
    <Sun size={16} aria-hidden="true" />
  ) : (
    <LaptopMinimal size={16} aria-hidden="true" />
  );

  const commandItems: CommandItem[] = [
    { id: 'jump-positions',  group: 'Jump To', label: 'Positions',     shortcut: '↵' },
    { id: 'jump-booking',    group: 'Jump To', label: 'Booking',       shortcut: '↵' },
    { id: 'jump-pricing-parameters', group: 'Jump To', label: 'Pricing Parameters', shortcut: '↵' },
    { id: 'jump-engine-configs', group: 'Jump To', label: 'Engine Configs', shortcut: '↵' },
    { id: 'jump-portfolios', group: 'Jump To', label: 'Portfolios',    shortcut: '↵' },
    { id: 'jump-rfq',       group: 'Jump To', label: 'RFQ Approval',  shortcut: '↵' },
    { id: 'jump-try-solve', group: 'Jump To', label: 'Try to Solve',  shortcut: '↵' },
    { id: 'jump-instruments', group: 'Jump To', label: 'Instruments',  shortcut: '↵' },
    { id: 'jump-hedging',   group: 'Jump To', label: 'Hedging',       shortcut: '↵' },
    { id: 'jump-memory',    group: 'Jump To', label: 'Memory',        shortcut: '↵' },
    { id: 'jump-risk',      group: 'Jump To', label: 'Risk',          shortcut: '↵' },
    { id: 'jump-greeks-landscape', group: 'Jump To', label: 'Greeks Landscape', shortcut: '↵' },
    { id: 'jump-scenario-test', group: 'Jump To', label: 'Scenario Test', shortcut: '↵' },
    { id: 'jump-backtest',  group: 'Jump To', label: 'Backtest',      shortcut: '↵' },
    { id: 'jump-tasks',     group: 'Jump To', label: 'Tasks',         shortcut: '↵' },
    { id: 'jump-reports',   group: 'Jump To', label: 'Reports',       shortcut: '↵' },
    { id: 'jump-skills',    group: 'Jump To', label: 'Skills',        shortcut: '↵' },
    { id: 'jump-workflows', group: 'Jump To', label: 'Workflows',     shortcut: '↵' },
    { id: 'jump-tracing',   group: 'Jump To', label: 'Tracing',       shortcut: '↵' },
    { id: 'jump-chat',      group: 'Jump To', label: 'Agent Desk',    shortcut: '↵' },
    { id: 'jump-client',    group: 'Jump To', label: 'Client RFQ',    shortcut: '↵' },
    { id: 'portfolios-create-container', group: 'Create', label: 'New container portfolio', shortcut: '↵' },
    { id: 'portfolios-create-view',      group: 'Create', label: 'New view portfolio',      shortcut: '↵' },
  ];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && e.shiftKey) {
        e.preventDefault();
        setAgentOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const showAgent = route !== 'client';

  return (
    <>
      <AppShell
        active={route}
        onNavigate={navigate}
        items={navItems}
        toolbar={
          <>
            <div className="wl-shell__toolbar-group wl-shell__toolbar-group--left">
              <DatePicker
                label="Accounting"
                id="accounting-date"
                value={accountingDate}
                onChange={(v) => setAccountingDate(v)}
              />
            </div>
            <div className="wl-shell__toolbar-group wl-shell__toolbar-group--right">
              <Button
                variant="ghost"
                className="wl-shell__toolbar-badge"
                onClick={cycleTheme}
                title={`Theme: ${theme}`}
                aria-label={`Theme: ${theme}`}
              >
                {themeIcon}
              </Button>
              <Button
                variant="ghost"
                className="wl-shell__toolbar-badge"
                onClick={toggleDensity}
                title={`Density: ${density}`}
                aria-label={`Density: ${density}`}
              >
                <Rows2 size={16} aria-hidden="true" />
              </Button>
              <Button
                variant="ghost"
                className="wl-shell__toolbar-badge"
                onClick={toggleThousandSeparator}
                aria-pressed={thousandSeparator}
                title={`Thousand separator: ${thousandSeparator ? 'on' : 'off'}`}
                aria-label={`Thousand separator: ${thousandSeparator ? 'on' : 'off'}`}
              >
                1,000
              </Button>
              <Button variant="ghost" onClick={palette.open}>⌘K</Button>
            </div>
          </>
        }
      >
        {route === 'positions'  && (
          <PositionsLive
            onPageContextChange={handlePageContextChange}
          />
        )}
        {route === 'booking' && <BookingLive onPageContextChange={handlePageContextChange} />}
        {route === 'pricing-parameters' && (
          <PricingParametersLive
            onPageContextChange={handlePageContextChange}
          />
        )}
        {route === 'engine-configs' && <EngineConfigsLive />}
        {route === 'portfolios' && <PortfoliosLive onPageContextChange={handlePageContextChange} />}
        {route === 'chat'      && (
          <AgentDeskLive
            controller={agentChat}
            pageContext={pageContext}
            accountingDate={accountingDate}
            onOpenTrace={tracingConfig && tracingConfig.mode !== 'off' ? handleOpenTrace : undefined}
          />
        )}
        {route === 'rfq'       && <RfqApprovalLive onPageContextChange={handlePageContextChange} />}
        {route === 'try-solve' && <TrySolveLive onPageContextChange={handlePageContextChange} />}
        {route === 'instruments' && (
          <InstrumentsLive onPageContextChange={handlePageContextChange} />
        )}
        {route === 'hedging'   && (
          <HedgingLive
            onPageContextChange={handlePageContextChange}
            onNavigate={navigate}
            portfolioId={sharedPortfolioId}
            onPortfolioIdChange={setSharedPortfolioId}
          />
        )}
        {route === 'risk'      && (
          <RiskLive
            onPageContextChange={handlePageContextChange}
            portfolioId={sharedPortfolioId}
            onPortfolioIdChange={setSharedPortfolioId}
          />
        )}
        {route === 'greeks-landscape' && <GreeksLandscapeLive onPageContextChange={handlePageContextChange} />}
        {route === 'scenario-test' && <ScenarioTestLive />}
        {route === 'backtest'  && <BacktestLive />}
        {route === 'tasks'     && <TasksLive onPageContextChange={handlePageContextChange} onNavigate={navigate} />}
        {route === 'reports'   && <ReportsLive onPageContextChange={handlePageContextChange} />}
        {route === 'skills'    && <SkillsLive onPageContextChange={handlePageContextChange} />}
        {route === 'tracing'   && <TracingLive threadId={traceThreadId} />}
        {route === 'arena'     && <ArenaLive />}
        {route === 'workflows' && <WorkflowsLive />}
        {route === 'memory'    && <MemoryLive onPageContextChange={handlePageContextChange} />}
        {route === 'client'    && <ClientRfqLive onPageContextChange={handlePageContextChange} />}
      </AppShell>
      <CommandPalette
        open={palette.isOpen}
        onOpenChange={(o) => o ? palette.open() : palette.close()}
        items={commandItems}
        onSelect={onSelectCommand}
      />
      {showAgent && (
        <FloatingAgent
          open={agentOpen}
          onOpenChange={setAgentOpen}
          chips={pageContext.chips}
          hasUnread={agentChat.streaming || hasPendingActions(agentChat.activeThread)}
        >
          <FloatingAgentMiniChat
            controller={agentChat}
            pageContext={pageContext}
            accountingDate={accountingDate}
            onOpenDesk={() => { setAgentOpen(false); navigate('chat'); }}
          />
        </FloatingAgent>
      )}
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><ThousandSeparatorProvider><App /></ThousandSeparatorProvider></StrictMode>
);

function hasPendingActions(thread: { messages: Array<{ meta?: Record<string, unknown> }> } | null): boolean {
  return !!thread?.messages.some((message) => {
    const actions = message.meta?.pending_actions;
    return Array.isArray(actions) && actions.some((action) => (
      typeof action === 'object'
      && action != null
      && ((action as { status?: string }).status ?? 'pending') !== 'dismissed'
      && ((action as { status?: string }).status ?? 'pending') !== 'confirmed'
    ));
  });
}

function todayIsoDate(): string {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 10);
}
