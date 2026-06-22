import { useEffect, useMemo, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import { NumberInput } from './NumberInput';
import { Select } from './Select';
import type { GridAxisSpec, ScenarioGridRequest, ScenarioSetSummary } from '../types';
import './ScenarioGridDialog.css';

const MAX_CELLS = 200; // mirror backend scenario_grid_max_cells
const PARAMS: GridAxisSpec['param'][] = ['spot', 'vol', 'rate', 'dividend'];
const STRESS_TYPES: GridAxisSpec['stress_type'][] = ['PERCENTAGE', 'ABSOLUTE', 'VALUE'];
const LEVELS: Array<'portfolio' | 'underlying'> = ['portfolio', 'underlying'];

// UI-facing axis row: numbers are kept as raw strings so the fields can be empty
// mid-edit; PERCENTAGE rows are entered in % and divided by 100 on submit.
type AxisRow = {
  param: GridAxisSpec['param'];
  start: string; stop: string; step: string;
  stress_type: GridAxisSpec['stress_type'];
  level: 'portfolio' | 'underlying';
  target: string;
};

function emptyAxis(): AxisRow {
  return { param: 'spot', start: '', stop: '', step: '', stress_type: 'PERCENTAGE', level: 'portfolio', target: '' };
}

function fromSpec(ax: GridAxisSpec): AxisRow {
  const scale = ax.stress_type === 'PERCENTAGE' ? 100 : 1;
  const s = (n: number) => String(Number((n * scale).toFixed(8)));
  return {
    param: ax.param, start: s(ax.start), stop: s(ax.stop), step: s(ax.step),
    stress_type: ax.stress_type, level: ax.level,
    target: ax.target == null ? '' : String(ax.target),
  };
}

// Cell count for one axis — same math as backend expand_axis.
function axisCount(ax: AxisRow): number {
  const start = Number(ax.start), stop = Number(ax.stop), step = Number(ax.step);
  if (ax.start === '' || ax.stop === '' || ax.step === '') return 0;
  if (![start, stop, step].every(Number.isFinite)) return 0;
  if (start === stop) return 1;
  if (step === 0) return 0;
  if ((stop - start > 0) !== (step > 0)) return 0;
  return Math.floor((stop - start) / step + 1e-9) + 1;
}

type Props = {
  open: boolean;
  initial?: ScenarioSetSummary | null;  // present => edit (re-generate) mode, name locked
  existingNames: string[];
  onGenerate: (body: ScenarioGridRequest) => Promise<void> | void;
  onClose: () => void;
};

function canonicalName(name: string): string {
  return name.trim().replace(/[^A-Za-z0-9_.-]/g, '_');
}

export function ScenarioGridDialog({ open, initial, existingNames, onGenerate, onClose }: Props) {
  const isEdit = initial != null;
  const [name, setName] = useState('');
  const [axes, setAxes] = useState<AxisRow[]>([emptyAxis()]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setBusy(false);
    setName(initial?.name ?? '');
    setAxes(initial && initial.axes.length > 0 ? initial.axes.map(fromSpec) : [emptyAxis()]);
  }, [open, initial]);

  const updateAxis = (i: number, patch: Partial<AxisRow>) =>
    setAxes((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  const addAxis = () => setAxes((prev) => [...prev, emptyAxis()]);
  const removeAxis = (i: number) => setAxes((prev) => prev.filter((_, idx) => idx !== i));

  const total = useMemo(() => axes.reduce((acc, a) => acc * axisCount(a), 1), [axes]);
  const anyInvalidAxis = axes.some((a) => axisCount(a) === 0);
  const dupParam = new Set(axes.map((a) => a.param)).size !== axes.length;
  const nameTaken = !isEdit && existingNames.includes(canonicalName(name));
  const canGenerate =
    name.trim() !== '' && !nameTaken && axes.length > 0 && !anyInvalidAxis &&
    !dupParam && total >= 1 && total <= MAX_CELLS;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canGenerate) {
      setError(
        nameTaken ? `A set named "${canonicalName(name)}" already exists.`
          : dupParam ? 'Each parameter may appear on at most one axis.'
          : anyInvalidAxis ? 'Every axis needs a valid start/stop/step (step sign must move start toward stop).'
          : total > MAX_CELLS ? `Grid would generate ${total} scenarios, over the cap of ${MAX_CELLS}.`
          : 'Fill in a name and at least one valid axis.',
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body: ScenarioGridRequest = {
        name: name.trim(),
        combine_mode: 'cross_product',
        axes: axes.map((a) => {
          const div = a.stress_type === 'PERCENTAGE' ? 100 : 1;
          return {
            param: a.param,
            start: Number((Number(a.start) / div).toFixed(10)),
            stop: Number((Number(a.stop) / div).toFixed(10)),
            step: Number((Number(a.step) / div).toFixed(10)),
            stress_type: a.stress_type,
            level: a.level,
            target: a.level === 'underlying' ? a.target.trim() : null,
          };
        }),
      };
      await onGenerate(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={isEdit ? `Edit grid ${initial?.name}` : 'Generate scenario set'}
      layoutKey="scenario-grid"
      defaultWidth={720}
      defaultHeight={560}
    >
      <form className="wl-scenario-grid" onSubmit={submit}>
        <label className="wl-scenario-grid__field">
          <span>Set name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={isEdit}
            autoFocus aria-label="Set name" />
        </label>

        <div className="wl-scenario-grid__axes">
          <div className="wl-scenario-grid__axes-head">
            <span>Axes (cross product)</span>
            <Button type="button" variant="ghost" onClick={addAxis}>+ add axis</Button>
          </div>
          {axes.map((ax, i) => (
            <div className="wl-scenario-grid__axis" key={i}>
              <Select label={`param ${i}`} value={ax.param}
                onChange={(v) => updateAxis(i, { param: v as GridAxisSpec['param'] })}
                options={PARAMS.map((p) => ({ value: p, label: p }))} />
              <NumberInput aria-label={`start ${i}`} type="number" step="any" placeholder="start"
                value={ax.start} onChange={(e) => updateAxis(i, { start: e.target.value })} />
              <NumberInput aria-label={`stop ${i}`} type="number" step="any" placeholder="stop"
                value={ax.stop} onChange={(e) => updateAxis(i, { stop: e.target.value })} />
              <NumberInput aria-label={`step ${i}`} type="number" step="any" placeholder="step"
                value={ax.step} onChange={(e) => updateAxis(i, { step: e.target.value })} />
              <Select label={`type ${i}`} value={ax.stress_type}
                onChange={(v) => updateAxis(i, { stress_type: v as GridAxisSpec['stress_type'] })}
                options={STRESS_TYPES.map((t) => ({ value: t, label: t.toLowerCase() }))} />
              <Select label={`level ${i}`} value={ax.level}
                onChange={(v) => updateAxis(i, { level: v as 'portfolio' | 'underlying' })}
                options={LEVELS.map((lv) => ({ value: lv, label: lv }))} />
              {ax.level === 'underlying' && (
                <input aria-label={`target ${i}`} placeholder="symbol" value={ax.target}
                  onChange={(e) => updateAxis(i, { target: e.target.value })} />
              )}
              <span className="wl-scenario-grid__axis-count" aria-hidden="true">×{axisCount(ax)}</span>
              <button type="button" className="wl-scenario-grid__remove" aria-label={`remove axis ${i}`}
                onClick={() => removeAxis(i)} disabled={axes.length === 1}>×</button>
            </div>
          ))}
        </div>

        <p className={`wl-scenario-grid__count${total > MAX_CELLS ? ' wl-scenario-grid__count--over' : ''}`}>
          → {total} scenario{total === 1 ? '' : 's'}{total > MAX_CELLS ? ` (cap ${MAX_CELLS})` : ''}
        </p>

        {error && <p className="wl-scenario-grid__error" role="alert">{error}</p>}
        <div className="wl-scenario-grid__actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy || !canGenerate}>
            {busy ? 'Generating…' : 'Generate'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
