import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react';

type Rect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ResizeDirection = 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw';

type Placement = 'center' | 'bottom-right';

type UseWindowFrameOptions = {
  layoutKey: string;
  open: boolean;
  enabled?: boolean;
  defaultWidth: number;
  defaultHeight: number;
  minWidth: number;
  minHeight: number;
  placement?: Placement;
  margin?: number;
  mobileBreakpoint?: number;
};

const STORAGE_PREFIX = 'open-otc:window-layout:';

function viewport() {
  if (typeof window === 'undefined') return { width: 1024, height: 768 };
  return {
    width: window.innerWidth || 1024,
    height: window.innerHeight || 768,
  };
}

function isDesktop(breakpoint: number): boolean {
  return viewport().width > breakpoint;
}

function storageKey(layoutKey: string): string {
  return `${STORAGE_PREFIX}${layoutKey}`;
}

function readStoredRect(layoutKey: string): Rect | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(storageKey(layoutKey));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Rect>;
    if (
      typeof parsed.x !== 'number'
      || typeof parsed.y !== 'number'
      || typeof parsed.width !== 'number'
      || typeof parsed.height !== 'number'
    ) {
      return null;
    }
    return parsed as Rect;
  } catch {
    return null;
  }
}

function writeStoredRect(layoutKey: string, rect: Rect): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(storageKey(layoutKey), JSON.stringify(rect));
  } catch {
    // Ignore private-mode and quota errors; drag/resize still works for this session.
  }
}

function clampRect(rect: Rect, minWidth: number, minHeight: number, margin: number): Rect {
  const { width: viewportWidth, height: viewportHeight } = viewport();
  const maxWidth = Math.max(minWidth, viewportWidth - margin * 2);
  const maxHeight = Math.max(minHeight, viewportHeight - margin * 2);
  const width = Math.min(Math.max(rect.width, minWidth), maxWidth);
  const height = Math.min(Math.max(rect.height, minHeight), maxHeight);
  const maxX = Math.max(margin, viewportWidth - width - margin);
  const maxY = Math.max(margin, viewportHeight - height - margin);
  return {
    x: Math.min(Math.max(rect.x, margin), maxX),
    y: Math.min(Math.max(rect.y, margin), maxY),
    width,
    height,
  };
}

function defaultRect(
  width: number,
  height: number,
  placement: Placement,
  margin: number,
  minWidth: number,
  minHeight: number,
): Rect {
  const { width: viewportWidth, height: viewportHeight } = viewport();
  const safeWidth = Math.min(width, Math.max(minWidth, viewportWidth - margin * 2));
  const safeHeight = Math.min(height, Math.max(minHeight, viewportHeight - margin * 2));
  const x = placement === 'bottom-right'
    ? viewportWidth - safeWidth - margin
    : (viewportWidth - safeWidth) / 2;
  const y = placement === 'bottom-right'
    ? viewportHeight - safeHeight - margin
    : (viewportHeight - safeHeight) / 2;
  return clampRect({ x, y, width: safeWidth, height: safeHeight }, minWidth, minHeight, margin);
}

function shouldIgnoreDrag(target: EventTarget | null): boolean {
  return target instanceof Element
    && Boolean(target.closest('button, a, input, textarea, select, [data-window-frame-ignore]'));
}

export function useWindowFrame({
  layoutKey,
  open,
  enabled = true,
  defaultWidth,
  defaultHeight,
  minWidth,
  minHeight,
  placement = 'center',
  margin = 16,
  mobileBreakpoint = 640,
}: UseWindowFrameOptions) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const rectRef = useRef<Rect | null>(null);
  const [desktop, setDesktop] = useState(() => isDesktop(mobileBreakpoint));
  const [rect, setRect] = useState<Rect | null>(null);
  const isEnabled = enabled && desktop;

  useEffect(() => {
    const onResize = () => setDesktop(isDesktop(mobileBreakpoint));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [mobileBreakpoint]);

  useLayoutEffect(() => {
    if (!open || !isEnabled) {
      setRect(null);
      return;
    }

    const measured = frameRef.current?.getBoundingClientRect();
    const measuredWidth = measured && measured.width > 0 ? measured.width : defaultWidth;
    const measuredHeight = measured && measured.height > 0 ? measured.height : defaultHeight;
    const next = readStoredRect(layoutKey)
      ?? defaultRect(measuredWidth, measuredHeight, placement, margin, minWidth, minHeight);
    setRect(clampRect(next, minWidth, minHeight, margin));
  }, [defaultHeight, defaultWidth, isEnabled, layoutKey, margin, minHeight, minWidth, open, placement]);

  useEffect(() => {
    if (!open || !isEnabled || !rect) return;
    writeStoredRect(layoutKey, rect);
  }, [isEnabled, layoutKey, open, rect]);

  useEffect(() => {
    rectRef.current = rect;
  }, [rect]);

  useEffect(() => {
    if (!open || !isEnabled || !rect) return;
    const onResize = () => {
      setRect((current) => current ? clampRect(current, minWidth, minHeight, margin) : current);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [isEnabled, margin, minHeight, minWidth, open, rect]);

  const beginDrag = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (!isEnabled || event.button !== 0 || shouldIgnoreDrag(event.target)) return;
    event.preventDefault();
    const startRect = rectRef.current;
    if (!startRect) return;
    const startX = event.clientX;
    const startY = event.clientY;

    const onMove = (moveEvent: PointerEvent) => {
      const next = clampRect(
        {
          ...startRect,
          x: startRect.x + moveEvent.clientX - startX,
          y: startRect.y + moveEvent.clientY - startY,
        },
        minWidth,
        minHeight,
        margin,
      );
      setRect(next);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [isEnabled, margin, minHeight, minWidth]);

  const beginResize = useCallback((
    direction: ResizeDirection,
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    if (!isEnabled || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startRect = rectRef.current;
    if (!startRect) return;
    const startX = event.clientX;
    const startY = event.clientY;

    const onMove = (moveEvent: PointerEvent) => {
      const dx = moveEvent.clientX - startX;
      const dy = moveEvent.clientY - startY;
      const next = { ...startRect };

      if (direction.includes('e')) next.width = startRect.width + dx;
      if (direction.includes('s')) next.height = startRect.height + dy;
      if (direction.includes('w')) {
        next.x = startRect.x + dx;
        next.width = startRect.width - dx;
      }
      if (direction.includes('n')) {
        next.y = startRect.y + dy;
        next.height = startRect.height - dy;
      }

      setRect(clampRect(next, minWidth, minHeight, margin));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [isEnabled, margin, minHeight, minWidth]);

  const frameStyle: CSSProperties | undefined = isEnabled && rect
    ? {
        left: `${rect.x}px`,
        top: `${rect.y}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
        maxWidth: 'none',
        maxHeight: 'none',
        transform: 'none',
        right: 'auto',
        bottom: 'auto',
      }
    : undefined;

  return {
    frameRef,
    frameStyle,
    isEnabled,
    dragHandleProps: {
      onPointerDown: beginDrag,
      'data-window-frame-drag-handle': 'true',
    },
    getResizeHandleProps: (direction: ResizeDirection) => ({
      onPointerDown: (event: ReactPointerEvent<HTMLElement>) => beginResize(direction, event),
      'data-window-frame-resize-handle': direction,
      'aria-hidden': true,
    }),
  };
}
