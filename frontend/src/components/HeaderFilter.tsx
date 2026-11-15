import { useEffect, useId, useRef, useState } from 'react';
import { useDismiss } from './useDismiss';
import type { ReactNode } from 'react';

export interface FilterOption {
  value: string;
  label: ReactNode;
  // Plain text for the checkbox's accessible name.
  text: string;
  count?: number;
  // Not part of "everything": unticked by default, and picking it clears the others (and the reverse).
  separate?: boolean;
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
// "Unselect all" clears the boxes so a few can be picked from scratch; until one is ticked nothing is filtered.
export default function HeaderFilter({ name, title, options, selected, disabled = false, onChange }: HeaderFilterProps) {
  const listId = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  // Every box shown unticked after "Unselect all", while there is still no filter.
  const [cleared, setCleared] = useState(false);
  const everything = selected.size === 0;
  const regular = options.filter((option) => !option.separate).map((option) => option.value);
  const isSeparate = (value: string) => options.some((option) => option.value === value && option.separate);

  function toggle(value: string) {
    if (isSeparate(value)) {
      setCleared(false);
      onChange(selected.has(value) ? new Set() : new Set([value]));
      return;
    }

    const kept = [...selected].filter((item) => !isSeparate(item));
    const next = new Set(cleared ? [] : everything || kept.length === 0 ? regular : kept);

    if (next.has(value)) next.delete(value);
    else next.add(value);

    // Every box ticked again is the same as no filter; so is none, which then shows unticked.
    setCleared(next.size === 0);
    onChange(next.size === regular.length ? new Set() : next);
  }

  function selectAll() {
    setCleared(false);
    onChange(new Set());
  }

  function unselectAll() {
    setCleared(true);
    onChange(new Set());
  }

  // Reopening shows the filter as it is: with nothing picked, every box ticked.
  useEffect(() => {
    if (!isOpen) setCleared(false);
  }, [isOpen]);

  useDismiss(rootRef, isOpen, () => setIsOpen(false));

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
            <label
              key={option.value}
              className={`header-filter-option${option.separate ? ' header-filter-option--separate' : ''}`}
            >
              <input
                type="checkbox"
                checked={!cleared && (selected.has(option.value) || (everything && !option.separate))}
                onChange={() => toggle(option.value)}
                aria-label={option.text}
              />
              <span className="header-filter-label">{option.label}</span>
              {option.count !== undefined && <span className="header-filter-count">{option.count}</span>}
            </label>
          ))}

          {everything && !cleared ? (
            <button type="button" className="header-filter-clear" onClick={unselectAll}>
              Unselect all
            </button>
          ) : (
            <button type="button" className="header-filter-clear" onClick={selectAll}>
              Select all
            </button>
          )}
        </div>
      )}
    </span>
  );
}
