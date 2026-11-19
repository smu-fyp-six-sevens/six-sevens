import { useEffect, useId, useRef, useState } from 'react';
import { useDismiss } from './useDismiss';
import type { KeyboardEvent } from 'react';

interface SelectMenuProps {
  // What is chosen ("date range"): names the button and the list.
  name: string;
  options: string[];
  value: string;
  disabled?: boolean;
  onChange: (next: string) => void;
  // What an option reads as, when its value isn't its label (an id).
  optionLabel?: (option: string) => string;
  // An option that starts a second group: a hairline above it.
  separateFrom?: string;
  className?: string;
}

// A single-choice dropdown in the look of HeaderFilter: a button that opens a list, the current choice ticked.
export default function SelectMenu({
  name, options, value, disabled = false, onChange, optionLabel = (option) => option, separateFrom, className = '',
}: SelectMenuProps) {
  const listId = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [isOpen, setIsOpen] = useState(false);

  function close(refocus: boolean) {
    setIsOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  function choose(option: string) {
    onChange(option);
    close(true);
  }

  // Up and down arrows move between the options, wrapping at the ends.
  function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const current = optionRefs.current.findIndex((item) => item === document.activeElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    optionRefs.current[(current + step + options.length) % options.length]?.focus();
  }

  // Opening focuses the current choice.
  useEffect(() => {
    if (isOpen) optionRefs.current[Math.max(options.indexOf(value), 0)]?.focus();
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  useDismiss(rootRef, isOpen, () => setIsOpen(false), () => close(true));

  return (
    <span className={`header-filter select-menu ${className}`.trim()} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="header-filter-trigger"
        disabled={disabled}
        aria-label={`${name}: ${optionLabel(value)}`}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listId : undefined}
        onClick={() => setIsOpen((open) => !open)}
      >
        <span className="select-menu-value">{optionLabel(value)}</span>
        <svg className="select-menu-chevron" viewBox="0 0 12 8" aria-hidden="true">
          <path d="m1 1.5 5 5 5-5" />
        </svg>
      </button>

      {isOpen && (
        <div id={listId} className="header-filter-list" role="listbox" aria-label={name} onKeyDown={moveFocus}>
          {options.map((option, index) => (
            <button
              key={option}
              ref={(element) => { optionRefs.current[index] = element; }}
              type="button"
              role="option"
              aria-selected={option === value}
              className={`header-filter-option select-menu-option${option === separateFrom ? ' header-filter-option--separate' : ''}`}
              onClick={() => choose(option)}
            >
              <svg className="select-menu-check" viewBox="0 0 12 12" aria-hidden="true">
                {option === value && <path d="M2 6.5 4.8 9 10 3" />}
              </svg>
              <span className="header-filter-label">{optionLabel(option)}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
