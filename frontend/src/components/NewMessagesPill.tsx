import './NewMessagesPill.css';

type Props = {
  streaming: boolean;
  count: number;
  onClick: () => void;
};

export function NewMessagesPill({ streaming, count, onClick }: Props) {
  if (!streaming && count <= 0) return null;
  const label = streaming ? '↓ live' : `↓ ${count} new`;
  return (
    <button type="button" className="wl-new-messages-pill" onClick={onClick}>
      {label}
    </button>
  );
}
