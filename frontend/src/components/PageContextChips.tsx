import { Chip } from './Chip';
import './PageContextChips.css';

type Props = {
  chips: string[];
};

export function PageContextChips({ chips }: Props) {
  if (chips.length === 0) return null;
  return (
    <div className="wl-pchips">
      {chips.map((c) => <Chip key={c}>{c}</Chip>)}
    </div>
  );
}
