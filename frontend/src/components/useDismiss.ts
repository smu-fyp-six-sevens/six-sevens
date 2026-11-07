import { useEffect, type RefObject } from 'react';

// Closes an open menu on a click outside it or on Escape. Pass onEscape when Escape also returns focus to the trigger.
export function useDismiss(
  rootRef: RefObject<HTMLElement | null>,
  isOpen: boolean,
  onOutsideClick: () => void,
  onEscape: () => void = onOutsideClick,
) {
  useEffect(() => {
    if (!isOpen) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) onOutsideClick();
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') onEscape();
    }

    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);

    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps
}
