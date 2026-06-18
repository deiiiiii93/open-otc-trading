import './KindChip.css';
import type { PortfolioKind } from '../types';

export function KindChip({ kind }: { kind: PortfolioKind }) {
  return <span className={`wl-kindchip wl-kindchip--${kind}`}>{kind}</span>;
}
