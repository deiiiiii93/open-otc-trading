import { beforeEach, describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FloatingAgent } from './FloatingAgent';

function setViewport(width: number, height = 900) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
  window.dispatchEvent(new Event('resize'));
}

describe('FloatingAgent', () => {
  beforeEach(() => {
    setViewport(1024);
    localStorage.clear();
  });

  it('renders collapsed pip with label', () => {
    render(<FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread={false} />);
    expect(screen.getByRole('button', { name: /agent/i })).toBeInTheDocument();
  });

  it('shows pulsing dot when hasUnread is true', () => {
    const { container } = render(
      <FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread />
    );
    expect(container.querySelector('.wl-agent-pip__dot--active')).not.toBeNull();
  });

  it('calls onOpenChange(true) when pip clicked', async () => {
    const onOpenChange = vi.fn();
    render(<FloatingAgent open={false} onOpenChange={onOpenChange} chips={[]} hasUnread={false} />);
    await userEvent.click(screen.getByRole('button', { name: /agent/i }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it('renders chips strip when open', () => {
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={['Run #87', 'SNB-CSI500']} hasUnread={false} />
    );
    expect(screen.getByText('Run #87')).toBeInTheDocument();
    expect(screen.getByText('SNB-CSI500')).toBeInTheDocument();
  });

  it('moves when the panel header is dragged', async () => {
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={[]} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const panel = screen.getByRole('dialog', { name: /agent panel/i });
    await waitFor(() => expect(panel).toHaveClass('wl-window-frame--active'));
    const startLeft = parseFloat(panel.style.left);
    const startTop = parseFloat(panel.style.top);

    fireEvent.pointerDown(screen.getByText(/agent/i).closest('header')!, {
      button: 0,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(window, { clientX: 70, clientY: 60 });
    fireEvent.pointerUp(window);

    await waitFor(() => {
      expect(parseFloat(panel.style.left)).toBe(startLeft - 30);
      expect(parseFloat(panel.style.top)).toBe(startTop - 40);
    });
  });

  it('resizes and persists the expanded panel layout', async () => {
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={[]} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const panel = screen.getByRole('dialog', { name: /agent panel/i });
    await waitFor(() => expect(panel).toHaveClass('wl-window-frame--active'));
    const startWidth = parseFloat(panel.style.width);
    const startHeight = parseFloat(panel.style.height);
    const handle = panel.querySelector('[data-window-frame-resize-handle="se"]') as HTMLElement;

    fireEvent.pointerDown(handle, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 160, clientY: 140 });
    fireEvent.pointerUp(window);

    await waitFor(() => {
      expect(parseFloat(panel.style.width)).toBe(startWidth + 60);
      expect(parseFloat(panel.style.height)).toBe(startHeight + 40);
      expect(localStorage.getItem('open-otc:window-layout:floating-agent')).toContain('"height"');
    });
  });

  it('collapsed pip remains fixed and not resizable', () => {
    const { container } = render(
      <FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread={false} />
    );
    expect(container.querySelector('.wl-agent-pip')).not.toBeNull();
    expect(container.querySelector('[data-window-frame-resize-handle]')).toBeNull();
    expect(screen.queryByRole('dialog', { name: /agent panel/i })).not.toBeInTheDocument();
  });

  it('restores a saved expanded panel layout', async () => {
    localStorage.setItem(
      'open-otc:window-layout:floating-agent',
      JSON.stringify({ x: 96, y: 48, width: 500, height: 620 }),
    );
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={[]} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const panel = screen.getByRole('dialog', { name: /agent panel/i });
    await waitFor(() => {
      expect(panel.style.left).toBe('96px');
      expect(panel.style.top).toBe('48px');
      expect(panel.style.width).toBe('500px');
      expect(panel.style.height).toBe('620px');
    });
  });

  it('keeps drag and resize disabled on mobile width', () => {
    setViewport(540);
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={[]} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const panel = screen.getByRole('dialog', { name: /agent panel/i });
    expect(panel).not.toHaveClass('wl-window-frame--active');
    expect(panel.querySelector('[data-window-frame-resize-handle]')).toBeNull();
  });

  it('allows dragging the collapsed pip', async () => {
    const onOpenChange = vi.fn();
    render(<FloatingAgent open={false} onOpenChange={onOpenChange} chips={[]} hasUnread={false} />);
    const pip = screen.getByRole('button', { name: /agent/i });
    const startLeft = pip.style.left;
    const startTop = pip.style.top;

    fireEvent.pointerDown(pip, {
      button: 0,
      clientX: 868,
      clientY: 848,
    });
    fireEvent.pointerMove(window, {
      clientX: 820,
      clientY: 800,
    });
    fireEvent.pointerUp(window);

    await waitFor(() => {
      expect(pip.style.left).not.toBe(startLeft);
      expect(pip.style.top).not.toBe(startTop);
    });
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('collapsed has no a11y violations', async () => {
    const { container } = render(
      <FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread={false} />
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('expanded has no a11y violations', async () => {
    const { container } = render(
      <FloatingAgent open onOpenChange={() => {}} chips={['Run #87']} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
