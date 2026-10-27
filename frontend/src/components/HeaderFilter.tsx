import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

export interface FilterOption {
  value: string;
  label: ReactNode;
  // Plain text for the checkbox's accessible name.
  text: string;
  count?: number;
}

interface HeaderFilterProps {
  // What is filtered ("status"): names the button and the list.
  name: string;
  // Shown on the button when it carries the column's title; the icon alone sits next to a sort button.
  title?: string;
  options: FilterOption[];
  selected: Set<string>;
  disabled?: boolean;
  onChange: (next: Set<string>) => void;
}

// A column header's filter: a button that opens a list of checkboxes, so several values can be picked at once.
// Nothing selected means no filter, shown as every box ticked; unticking one then keeps all the others.
export default function HeaderFilter({ name, title, options, selected, disabled = false, onChange }: HeaderFilterProps) {
  const listId = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const everything = selected.size === 0;

  function toggle(value: string) {
    const next = new Set(everything ? options.map((option) => option.value) : selected);

    if (next.has(value)) next.delete(value);
    else next.add(value);

    // Every box ticked again is the same as no filter.
    onChange(next.size === options.length ? new Set() : next);
  }

  useEffect(() => {
    if (!isOpen) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsOpen(false);
    }

    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);

    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]);

  return (
    <span className="header-filter" ref={rootRef}>
      <button
        type="button"
        className="header-filter-trigger"
        data-active={selected.size > 0 || undefined}
        disabled={disabled}
        aria-label={`Filter by ${name}${selected.size ? ` (${selected.size} selected)` : ''}`}
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listId : undefined}
        onClick={() => setIsOpen((open) => !open)}
      >
        {title}
        <svg className="header-filter-icon" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M1 2h10L7 6.5V10L5 11V6.5z" />
        </svg>
      </button>

      {isOpen && (
        <div id={listId} className="header-filter-list" role="group" aria-label={`${name} filter`}>
          {options.map((option) => (
            <label key={option.value} className="header-filter-option">
              <input
                type="checkbox"
                checked={everything || selected.has(option.value)}
                onChange={() => toggle(option.value)}
                aria-label={option.text}
              />
              <span className="header-filter-label">{option.label}</span>
              {option.count !== undefined && <span className="header-filter-count">{option.count}</span>}
            </label>
          ))}

          {!everything && (
            <button type="button" className="header-filter-clear" onClick={() => onChange(new Set())}>
              Select all
            </button>
          )}
        </div>
      )}
    </span>
  );
}
