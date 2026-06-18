import { type RefObject, useCallback, useEffect, useState } from 'react';

export const STICKY_THRESHOLD_PX = 120;

export function useStickyScroll(ref: RefObject<HTMLElement | null>): {
  isPinned: boolean;
  scrollToBottom: () => void;
} {
  const [isPinned, setIsPinned] = useState(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      setIsPinned(distance < STICKY_THRESHOLD_PX);
    };
    onScroll();
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      node.removeEventListener('scroll', onScroll);
    };
  }, [ref]);

  const scrollToBottom = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [ref]);

  return { isPinned, scrollToBottom };
}
