import { beforeEach, describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  window.dispatchEvent(new Event('resize'));
}

describe('Modal', () => {
  beforeEach(() => {
    setViewportWidth(1024);
  });

  it('renders title and body when open', () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-render">
        <p>Are you sure?</p>
      </Modal>
    );
    expect(screen.getByText('Confirm')).toBeInTheDocument();
    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    render(
      <Modal open={false} onOpenChange={() => {}} title="Confirm" layoutKey="test-closed">
        <p>Are you sure?</p>
      </Modal>
    );
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument();
  });

  it('calls onOpenChange(false) when close button clicked', async () => {
    const onOpenChange = vi.fn();
    render(
      <Modal open onOpenChange={onOpenChange} title="Confirm" layoutKey="test-close">
        body
      </Modal>
    );
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('moves when the title bar is dragged', async () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-drag">
        body
      </Modal>
    );
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(dialog).toHaveClass('wl-window-frame--active'));
    const startLeft = parseFloat(dialog.style.left);
    const startTop = parseFloat(dialog.style.top);

    fireEvent.pointerDown(screen.getByText('Confirm').closest('header')!, {
      button: 0,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 125 });
    fireEvent.pointerUp(window);

    await waitFor(() => {
      expect(parseFloat(dialog.style.left)).toBe(startLeft + 40);
      expect(parseFloat(dialog.style.top)).toBe(startTop + 25);
    });
  });

  it('resizes and persists the layout', async () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-resize">
        body
      </Modal>
    );
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(dialog).toHaveClass('wl-window-frame--active'));
    const startWidth = parseFloat(dialog.style.width);
    const startHeight = parseFloat(dialog.style.height);
    const handle = dialog.querySelector('[data-window-frame-resize-handle="se"]') as HTMLElement;

    fireEvent.pointerDown(handle, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 180, clientY: 160 });
    fireEvent.pointerUp(window);

    await waitFor(() => {
      expect(parseFloat(dialog.style.width)).toBe(startWidth + 80);
      expect(parseFloat(dialog.style.height)).toBe(startHeight + 60);
      expect(localStorage.getItem('open-otc:window-layout:modal:test-resize')).toContain('"width"');
    });
  });

  it('restores a saved layout', async () => {
    localStorage.setItem(
      'open-otc:window-layout:modal:test-restore',
      JSON.stringify({ x: 70, y: 80, width: 500, height: 360 }),
    );
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-restore">
        body
      </Modal>
    );
    const dialog = screen.getByRole('dialog');
    await waitFor(() => {
      expect(dialog.style.left).toBe('70px');
      expect(dialog.style.top).toBe('80px');
      expect(dialog.style.width).toBe('500px');
      expect(dialog.style.height).toBe('360px');
    });
  });

  it('keeps drag and resize disabled on mobile width', () => {
    setViewportWidth(540);
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-mobile">
        body
      </Modal>
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).not.toHaveClass('wl-window-frame--active');
    expect(dialog.querySelector('[data-window-frame-resize-handle]')).toBeNull();
    setViewportWidth(1024);
  });

  it('has no a11y violations', async () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm" layoutKey="test-a11y">
        <p>Are you sure?</p>
      </Modal>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(document.body);
  });
});
