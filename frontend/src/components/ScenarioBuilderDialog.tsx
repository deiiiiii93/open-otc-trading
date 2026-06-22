import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import { NumberInput } from './NumberInput';
import { Select } from './Select';
import type { ScenarioStress, ScenarioSetDetail } from '../types';
import './ScenarioBuilderDialog.css';

type Props = {
  open: boolean;
  initial?: ScenarioSetDetail | null;   // present => edit mode (name locked)
  existingNames: string[];
  onSave: (name: string, description: string, stresses: ScenarioStress[]) => Promise<void> | void;
  onClose: () => void;
};

function canonicalName(name: string): string {
  return name.trim().replace(/[^A-Za-z0-9_.-]/g, '_');
}

const PARAMS: ScenarioStress['param'][] = ['spot', 'vol', 'rate', 'dividend'];
const STRESS_TYPES: ScenarioStress['stress_type'][] = ['PERCENTAGE', 'ABSOLUTE', 'VALUE'];
const LEVELS: Array<'portfolio' | 'underlying'> = ['portfolio', 'underlying'];

function emptyLeg(): ScenarioStress {
  return { param: 'spot', stress_type: 'PERCENTAGE', value: NaN, level: 'portfolio', target: null };
}

export function ScenarioBuilderDialog({ open, initial, existingNames, onSave, onClose }: Props) {
  const isEdit = initial != null;
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [legs, setLegs] = useState<ScenarioStress[]>([emptyLeg()]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving(false);
    setName(initial?.name ?? '');
    setDescription(initial?.description ?? '');
    setLegs(initial && initial.stresses.length > 0
      ? initial.stresses.map((s) => ({
          ...s,
          value: s.stress_type === 'PERCENTAGE' ? Number((s.value * 100).toFixed(6)) : s.value,
        }))
      : [emptyLeg()]);
  }, [open, initial]);

  const updateLeg = (i: number, patch: Partial<ScenarioStress>) =>
    setLegs((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLeg = () => setLegs((prev) => [...prev, emptyLeg()]);
  const removeLeg = (i: number) => setLegs((prev) => prev.filter((_, idx) => idx !== i));

  function validate(): string | null {
    const trimmed = name.trim();
    if (!trimmed) return 'Name is required.';
    if (!isEdit && existingNames.includes(canonicalName(trimmed))) return `A scenario named "${canonicalName(trimmed)}" already exists (names are sanitized to letters, digits, _ . -).`;
    if (legs.length === 0) return 'Add at least one stress leg.';
    for (const l of legs) {
      if (!Number.isFinite(l.value)) return 'Each stress needs a numeric value.';
      if (l.level === 'underlying' && !String(l.target ?? '').trim()) {
        return 'Underlying-level stress needs a target symbol.';
      }
    }
    return null;
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const v = validate();
    if (v) { setError(v); return; }
    setSaving(true);
    setError(null);
    try {
      const cleaned = legs.map((l) => ({
        ...l,
        value: l.stress_type === 'PERCENTAGE' ? Number((l.value / 100).toFixed(8)) : l.value,
        target: l.level === 'underlying' ? String(l.target ?? '').trim() : null,
      }));
      await onSave(name.trim(), description.trim(), cleaned);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={isEdit ? `Edit ${initial?.name}` : 'New custom scenario'}
      layoutKey="scenario-builder"
      defaultWidth={640}
      defaultHeight={520}
    >
      <form className="wl-scenario-builder" onSubmit={submit}>
        <label className="wl-scenario-builder__field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={isEdit} autoFocus aria-label="Scenario name" />
        </label>
        <label className="wl-scenario-builder__field">
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} aria-label="Scenario description" />
        </label>

        <div className="wl-scenario-builder__legs">
          <div className="wl-scenario-builder__legs-head">
            <span>Stress legs</span>
            <Button type="button" variant="ghost" onClick={addLeg}>+ add leg</Button>
          </div>
          {legs.map((leg, i) => (
            <div className="wl-scenario-builder__leg" key={i}>
              <Select label={`param ${i}`} value={leg.param}
                onChange={(v) => updateLeg(i, { param: v as ScenarioStress['param'] })}
                options={PARAMS.map((p) => ({ value: p, label: p }))} />
              <Select label={`type ${i}`} value={leg.stress_type}
                onChange={(v) => updateLeg(i, { stress_type: v as ScenarioStress['stress_type'] })}
                options={STRESS_TYPES.map((t) => ({ value: t, label: t.toLowerCase() }))} />
              <NumberInput aria-label={`value ${i}`} type="number" step="any" value={Number.isNaN(leg.value) ? '' : leg.value}
                onChange={(e) => updateLeg(i, { value: e.target.value === '' ? NaN : Number(e.target.value) })} />
              {leg.stress_type === 'PERCENTAGE' && (
                <span className="wl-scenario-builder__unit" aria-hidden="true">%</span>
              )}
              <Select label={`level ${i}`} value={leg.level}
                onChange={(v) => updateLeg(i, { level: v as 'portfolio' | 'underlying' })}
                options={LEVELS.map((lv) => ({ value: lv, label: lv }))} />
              {leg.level === 'underlying' && (
                <input aria-label={`target ${i}`} placeholder="symbol" value={String(leg.target ?? '')}
                  onChange={(e) => updateLeg(i, { target: e.target.value })} />
              )}
              <button type="button" className="wl-scenario-builder__remove" aria-label={`remove leg ${i}`}
                onClick={() => removeLeg(i)} disabled={legs.length === 1}>×</button>
            </div>
          ))}
        </div>

        {error && <p className="wl-scenario-builder__error" role="alert">{error}</p>}
        <div className="wl-scenario-builder__actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </form>
    </Modal>
  );
}
