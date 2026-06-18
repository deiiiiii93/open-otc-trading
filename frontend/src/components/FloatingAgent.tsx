import React, { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { Chip } from './Chip';
import { useWindowFrame, type ResizeDirection } from '../hooks/useWindowFrame';
import './WindowFrame.css';
import './FloatingAgent.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  chips: string[];
  hasUnread: boolean;
  children?: React.ReactNode;
};

const RESIZE_DIRECTIONS: ResizeDirection[] = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];
const PIP_STORAGE_KEY = 'open-otc:window-layout:floating-agent-pip';
const PIP_DRAG_MARGIN = 16;
const PIP_WIDTH = 140;
const PIP_HEIGHT = 36;
const DRAG_THRESHOLD = 3;

type PipRect = {
  x: number;
  y: number;
};

function viewport() {
  if (typeof window === 'undefined') return { width: 1024, height: 768 };
  return {
    width: window.innerWidth || 1024,
    height: window.innerHeight || 768,
  };
}

function clampPipPosition(x: number, y: number): PipRect {
  const { width: viewportWidth, height: viewportHeight } = viewport();
  const maxX = Math.max(PIP_DRAG_MARGIN, viewportWidth - PIP_WIDTH - PIP_DRAG_MARGIN);
  const maxY = Math.max(PIP_DRAG_MARGIN, viewportHeight - PIP_HEIGHT - PIP_DRAG_MARGIN);
  return {
    x: Math.min(Math.max(x, PIP_DRAG_MARGIN), maxX),
    y: Math.min(Math.max(y, PIP_DRAG_MARGIN), maxY),
  };
}

function parseStoredPipRect(): PipRect {
  if (typeof window === 'undefined') {
    return { x: PIP_DRAG_MARGIN, y: PIP_DRAG_MARGIN };
  }
  try {
    const raw = window.localStorage.getItem(PIP_STORAGE_KEY);
    if (!raw) {
      const { width: viewportWidth, height: viewportHeight } = viewport();
      return clampPipPosition(viewportWidth - PIP_WIDTH - PIP_DRAG_MARGIN, viewportHeight - PIP_HEIGHT - PIP_DRAG_MARGIN);
    }
    const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown; };
    if (typeof parsed.x === 'number' && typeof parsed.y === 'number') {
      return clampPipPosition(parsed.x, parsed.y);
    }
  } catch {
    // Ignore parse and storage errors.
  }
  const { width: viewportWidth, height: viewportHeight } = viewport();
  return clampPipPosition(viewportWidth - PIP_WIDTH - PIP_DRAG_MARGIN, viewportHeight - PIP_HEIGHT - PIP_DRAG_MARGIN);
}

function writeStoredPipRect(rect: PipRect): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PIP_STORAGE_KEY, JSON.stringify(rect));
  } catch {
    // Ignore quota and private-mode errors.
  }
}

export function FloatingAgent({ open, onOpenChange, chips, hasUnread, children }: Props) {
  const windowFrame = useWindowFrame({
    layoutKey: 'floating-agent',
    open,
    defaultWidth: 520,
    defaultHeight: 720,
    minWidth: 360,
    minHeight: 420,
    placement: 'bottom-right',
  });
  const [pipRect, setPipRect] = useState<PipRect>(() => parseStoredPipRect());
  const suppressNextOpen = useRef(false);
  const dragState = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startRectX: number;
    startRectY: number;
    moved: boolean;
  } | null>(null);

  useEffect(() => {
    if (open) return;
    const onResize = () => setPipRect((current) => clampPipPosition(current.x, current.y));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open]);

  useEffect(() => {
    writeStoredPipRect(pipRect);
  }, [pipRect]);

  const handlePipOpen = () => {
    if (suppressNextOpen.current) {
      suppressNextOpen.current = false;
      return;
    }
    onOpenChange(true);
  };

  const beginPipDrag = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    dragState.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startRectX: pipRect.x,
      startRectY: pipRect.y,
      moved: false,
    };
    suppressNextOpen.current = false;
    const target = event.currentTarget;
    try {
      target.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture may fail on old browsers; fallback still works via window listeners.
    }
    const onMove = (moveEvent: PointerEvent) => {
      const state = dragState.current;
      if (!state || state.pointerId !== moveEvent.pointerId) return;
      const nextX = state.startRectX + (moveEvent.clientX - state.startX);
      const nextY = state.startRectY + (moveEvent.clientY - state.startY);
      const clamped = clampPipPosition(nextX, nextY);
      state.moved = state.moved || Math.abs(nextX - state.startRectX) > DRAG_THRESHOLD || Math.abs(nextY - state.startRectY) > DRAG_THRESHOLD;
      setPipRect(clamped);
    };
    const endDrag = (endEvent: PointerEvent) => {
      if (dragState.current?.pointerId !== endEvent.pointerId) return;
      const moved = dragState.current?.moved ?? false;
      if (moved) suppressNextOpen.current = true;
      try {
        if (target.hasPointerCapture(endEvent.pointerId)) {
          target.releasePointerCapture(endEvent.pointerId);
        }
      } catch {
        // Ignore release failures.
      }
      dragState.current = null;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', endDrag);
      window.removeEventListener('pointercancel', endDrag);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
  }, [pipRect.x, pipRect.y]);

  const pipStyle: React.CSSProperties = open ? {} : {
    position: 'fixed',
    left: `${pipRect.x}px`,
    top: `${pipRect.y}px`,
    right: 'auto',
    bottom: 'auto',
    width: `${PIP_WIDTH}px`,
    height: `${PIP_HEIGHT}px`,
    boxSizing: 'border-box',
  };

  if (!open) {
    return (
      <button
        type="button"
        className="wl-agent-pip"
        aria-label="Open agent"
        onPointerDown={beginPipDrag}
        onClick={handlePipOpen}
        style={pipStyle}
      >
        <span className={`wl-agent-pip__dot ${hasUnread ? 'wl-agent-pip__dot--active' : ''}`} />
        <span className="wl-agent-pip__label">⌘⇧K · AGENT</span>
      </button>
    );
  }

  return (
    <div
      ref={windowFrame.frameRef}
      className={`wl-agent-panel ${windowFrame.isEnabled ? 'wl-window-frame--active' : ''}`}
      style={windowFrame.frameStyle}
      role="dialog"
      aria-label="Agent panel"
    >
      <header
        className="wl-agent-panel__head wl-agent-panel__head--draggable"
        {...windowFrame.dragHandleProps}
      >
        <span>⌘⇧K · AGENT</span>
        <button
          type="button"
          className="wl-agent-panel__close"
          aria-label="Close agent"
          onClick={() => onOpenChange(false)}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </header>
      {chips.length > 0 && (
        <div className="wl-agent-panel__ctx">
          <span className="wl-agent-panel__ctx-label">Context</span>
          {chips.map((c) => <Chip key={c}>{c}</Chip>)}
        </div>
      )}
      <div className="wl-agent-panel__body">{children}</div>
      {windowFrame.isEnabled && RESIZE_DIRECTIONS.map((direction) => (
        <span
          key={direction}
          className={`wl-window-resize wl-window-resize--${direction}`}
          {...windowFrame.getResizeHandleProps(direction)}
        />
      ))}
    </div>
  );
}
