import './Skeleton.css';

type Props = {
  height: number;
  width?: number | string;
  className?: string;
};

export function Skeleton({ height, width, className = '' }: Props) {
  return (
    <div
      className={`wl-skeleton ${className}`.trim()}
      style={{ height, width: typeof width === 'number' ? `${width}px` : width }}
    />
  );
}
