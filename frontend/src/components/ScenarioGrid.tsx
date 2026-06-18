import './ScenarioGrid.css';

export type ScenarioCell = {
  spot_shift_pct: number;
  vol_shift_abs: number;
  pnl: number;
};

type Props = {
  cells: ScenarioCell[][] | null;
  onPromoteToReport: () => void;
};

function fmtPnl(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

function fmtSpotShift(pct: number): string {
  return pct === 0 ? '0%' : (pct > 0 ? '+' : '') + pct + '%';
}

function fmtVolShift(abs: number): string {
  return abs === 0 ? '0v' : (abs > 0 ? '+' : '') + (abs * 100).toFixed(0) + 'v';
}

export function ScenarioGrid({ cells, onPromoteToReport }: Props) {
  const live = cells != null && cells.length > 0;
  const spotCols = live ? cells[0].map((c) => c.spot_shift_pct) : [-3, -2, -1, 0, 1, 2];
  const volRows = live ? cells.map((row) => row[0].vol_shift_abs) : [-0.02, 0, 0.02];

  return (
    <section className="wl-scenario">
      <header className="wl-scenario__head">
        <span className="wl-scenario__title">SCENARIO GRID · SHIFT × VOL</span>
        <button
          type="button"
          className="wl-scenario__promote"
          aria-label="Promote to Report"
          onClick={onPromoteToReport}
          disabled={!live}
        >
          ↗
        </button>
      </header>
      <div className="wl-scenario__body">
        <div className="wl-scenario__matrix" aria-hidden={!live}>
          <div className="wl-scenario__cell wl-scenario__cell--corner" />
          {spotCols.map((s) => (
            <div key={s} className="wl-scenario__cell wl-scenario__cell--header">{fmtSpotShift(s)}</div>
          ))}
          {volRows.map((v, rowIdx) => (
            <div key={v} className="wl-scenario__row" style={{ display: 'contents' }}>
              <div className="wl-scenario__cell wl-scenario__cell--header">{fmtVolShift(v)}</div>
              {spotCols.map((s, colIdx) => {
                const cell = live ? cells[rowIdx][colIdx] : null;
                if (cell == null) {
                  return <div key={s} className="wl-scenario__cell wl-scenario__cell--empty">—</div>;
                }
                const variantClass = cell.pnl > 0 ? 'wl-scenario__cell--pos' : cell.pnl < 0 ? 'wl-scenario__cell--neg' : '';
                return (
                  <div key={s} className={`wl-scenario__cell ${variantClass}`.trim()}>
                    {fmtPnl(cell.pnl)}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {!live && (
          <div className="wl-scenario__overlay">
            <div className="wl-scenario__overlay-text">Scenario engine deferred</div>
            <div className="wl-scenario__overlay-detail">Run risk to populate the grid.</div>
          </div>
        )}
      </div>
    </section>
  );
}
