import * as Dialog from '@radix-ui/react-dialog';
import React from 'react';
import { useWindowFrame, type ResizeDirection } from '../hooks/useWindowFrame';
import './WindowFrame.css';
import './Modal.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  contentClassName?: string;
  layoutKey?: string;
  draggable?: boolean;
  resizable?: boolean;
  defaultWidth?: number;
  defaultHeight?: number;
  minWidth?: number;
  minHeight?: number;
  children: React.ReactNode;
};

const RESIZE_DIRECTIONS: ResizeDirection[] = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];

function titleLayoutKey(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'dialog';
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  contentClassName = '',
  layoutKey,
  draggable = true,
  resizable = true,
  defaultWidth = 560,
  defaultHeight = 420,
  minWidth = 360,
  minHeight = 240,
  children,
}: Props) {
  const windowFrame = useWindowFrame({
    layoutKey: `modal:${layoutKey ?? titleLayoutKey(title)}`,
    open,
    enabled: draggable || resizable,
    defaultWidth,
    defaultHeight,
    minWidth,
    minHeight,
  });
  const className = [
    'wl-modal__content',
    windowFrame.isEnabled ? 'wl-window-frame--active' : '',
    contentClassName,
  ].filter(Boolean).join(' ');

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <Dialog.Portal>
        <Dialog.Overlay className="wl-modal__overlay" />
        <Dialog.Content
          ref={windowFrame.frameRef}
          className={className}
          style={windowFrame.frameStyle}
          {...(description ? {} : { 'aria-describedby': undefined })}
          onInteractOutside={(event) => {
            const target = event.target;
            if (
              target instanceof Element
              && target.closest('.wl-agent-pip, .wl-agent-panel')
            ) {
              event.preventDefault();
            }
          }}
        >
          <header
            className={`wl-modal__head ${draggable && windowFrame.isEnabled ? 'wl-modal__head--draggable' : ''}`}
            {...(draggable ? windowFrame.dragHandleProps : {})}
          >
            <Dialog.Title className="wl-modal__title">{title}</Dialog.Title>
            <Dialog.Close className="wl-modal__close" aria-label="Close">×</Dialog.Close>
          </header>
          {description && (
            <Dialog.Description className="wl-modal__description">{description}</Dialog.Description>
          )}
          <div className="wl-modal__body">{children}</div>
          {resizable && windowFrame.isEnabled && RESIZE_DIRECTIONS.map((direction) => (
            <span
              key={direction}
              className={`wl-window-resize wl-window-resize--${direction}`}
              {...windowFrame.getResizeHandleProps(direction)}
            />
          ))}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
