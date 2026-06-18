import { Modal } from './Modal';
import type { ScenarioStress } from '../types';
import './ScenarioDetailDialog.css';

type Props = {
  open: boolean;
  name: string;
  description?: string;
  stresses: ScenarioStress[];
  onClose: () => void;
};

const PARAM_LABEL: Record<ScenarioStress['param'], string> = {
  spot: 'Spot', vol: 'Vol', rate: 'Rate', dividend: 'Dividend',
};
const ALL_PARAMS: ScenarioStress['param'][] = ['spot', 'vol', 'rate', 'dividend'];

function fmtValue(s: ScenarioStress): string {
  if (s.stress_type === 'PERCENTAGE') {
    const pct = Number((s.value * 100).toFixed(6));
    const sign = pct > 0 ? '+' : '';
    return `${sign}${pct}%`;
  }
  return `${s.value}`;
}

function fmtScope(s: ScenarioStress): string {
  if (s.level === 'underlying') return `underlying (${s.target ?? '?'})`;
  return s.level;
}

export function ScenarioDetailDialog({ open, name, description, stresses, onClose }: Props) {
  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={name}
      layoutKey="scenario-detail"
      defaultWidth={520}
      defaultHeight={320}
    >
      <div className="wl-scenario-detail">
        {description && <p className="wl-scenario-detail__desc">{description}</p>}
        <table className="wl-scenario-detail__table">
          <thead>
            <tr><th>Param</th><th>Stress</th><th>Value</th><th>Scope</th></tr>
          </thead>
          <tbody>
            {stresses.map((s, i) => (
              <tr key={`leg-${i}`}>
                <td>{PARAM_LABEL[s.param] ?? s.param}</td>
                <td>{s.stress_type.toLowerCase()}</td>
                <td>{fmtValue(s)}</td>
                <td>{fmtScope(s)}</td>
              </tr>
            ))}
            {ALL_PARAMS.filter((p) => !stresses.some((s) => s.param === p)).map((p) => (
              <tr key={`none-${p}`}>
                <td>{PARAM_LABEL[p]}</td><td>—</td><td>unchanged</td><td>—</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
