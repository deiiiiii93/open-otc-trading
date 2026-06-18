import React, { useRef, useState } from 'react';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import './SplitLayout.css';

type Props = {
  rail: React.ReactNode;
  children: React.ReactNode;
  railWidth?: string;
  collapsible?: boolean;
  resizable?: boolean;
  minRailWidth?: number;
  maxRailWidth?: number;
  railLabel?: string;
  // 'nav' (default) wraps the rail in a labelled navigation landmark — right for
  // list/tree rails. Use 'div' when the rail content already owns its semantics
  // (e.g. a `role="tablist"` strip, or content that is itself a landmark) so we
  // don't nest a landmark inside a landmark.
  railAs?: 'nav' | 'div';
  className?: string;
};

export function SplitLayout({
  rail,
  children,
  railWidth,
  collapsible = false,
  resizable = false,
  minRailWidth = 180,
  maxRailWidth = 420,
  railLabel = 'Items',
  railAs = 'nav',
  className = '',
}: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [resizedRailWidth, setResizedRailWidth] = useState<number | null>(null);
  const effectiveRailWidth = resizedRailWidth != null ? `${resizedRailWidth}px` : railWidth;
  const style = effectiveRailWidth ? ({ '--wl-rail-width': effectiveRailWidth } as React.CSSProperties) : undefined;
  const railContent = railAs === 'nav'
    ? <nav className="wl-split__rail" aria-label={railLabel} hidden={collapsed}>{rail}</nav>
    : <div className="wl-split__rail" hidden={collapsed}>{rail}</div>;
  const handleResizePointerDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!rootRef.current) return;
    event.preventDefault();
    const left = rootRef.current.getBoundingClientRect().left;
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const next = Math.round(moveEvent.clientX - left);
      setResizedRailWidth(Math.min(maxRailWidth, Math.max(minRailWidth, next)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };
  return (
    <div
      ref={rootRef}
      className={`wl-split${collapsed ? ' wl-split--collapsed' : ''}${resizable ? ' wl-split--resizable' : ''} ${className}`.trim()}
      style={style}
    >
      {railContent}
      {resizable && !collapsed ? (
        <button
          type="button"
          className="wl-split__resizer"
          aria-label={`Resize ${railLabel}`}
          aria-valuemin={minRailWidth}
          aria-valuemax={maxRailWidth}
          aria-valuenow={resizedRailWidth ?? undefined}
          onPointerDown={handleResizePointerDown}
        />
      ) : null}
      <section className="wl-split__workspace">
        {collapsible && (
          <button
            type="button"
            className="wl-split__toggle"
            aria-label={collapsed ? 'Expand panel' : 'Collapse panel'}
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : <PanelLeftClose size={16} aria-hidden="true" />}
          </button>
        )}
        {children}
      </section>
    </div>
  );
}
