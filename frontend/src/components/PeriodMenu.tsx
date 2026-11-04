import { useId, useRef, useState } from 'react';
import { useDismiss } from './useDismiss';
import type { ReactNode } from 'react';
import RangeCalendar from './RangeCalendar';
import type { Period } from '../utils/dateRange';

export interface PeriodOption {
  label: string;
  // The dates it covers, shown beside it.
  detail?: string;
  disabled?: boolean;
  onSelect: () => void;
}

interface PeriodMenuProps {
  // What is chosen ("date range"): names the button and the list.
  name: string;
  trigger: ReactNode;
  // Styles the button as in use (a comparison is on).
  active?: boolean;
  options: PeriodOption[];
  // The option ticked; "Custom range" when a custom period is in use.
  selected?: string | null;
  // The custom period in use, for the calendar to open on.
  custom?: Period | null;
  onCustom: (period: Period & { from: string }) => void;
  // A last action under the options.
  clear?: { label: string; onClear: () => void };
}

export const CUSTOM_RANGE = 'Custom range';

// SelectMenu's look for a period: a list of ready-made periods and "Custom range", which swaps the list for a
// two-month calendar.
export default function PeriodMenu({ name, trigger, active = false, options, selected, custom, onCustom, clear }: PeriodMenuProps) {
  const listId = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [view, setView] = useState<'closed' | 'list' | 'calendar'>('closed');
  const isOpen = view !== 'closed';

  function close() {
    setView('closed');
    triggerRef.current?.focus();
  }

  useDismiss(rootRef, isOpen, () => setView('closed'), close);

  const tick = (on: boolean) => (
    <svg className="select-menu-check" viewBox="0 0 12 12" aria-hidden="true">{on && <path d="M2 6.5 4.8 9 10 3" />}</svg>
  );

  return (
    <span className="header-filter select-menu period-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="header-filter-trigger"
        data-active={active || undefined}
        aria-label={name}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listId : undefined}
        onClick={() => setView(isOpen ? 'closed' : 'list')}
      >
        {trigger}
        <svg className="select-menu-chevron" viewBox="0 0 12 8" aria-hidden="true">
          <path d="m1 1.5 5 5 5-5" />
        </svg>
      </button>

      {view === 'list' && (
        <div id={listId} className="header-filter-list period-menu-list" role="dialog" aria-label={name}>
          {options.map((option) => (
            <button
              key={option.label}
              type="button"
              className="header-filter-option select-menu-option"
              aria-pressed={selected === option.label}
              disabled={option.disabled}
              onClick={() => {
                option.onSelect();
                close();
              }}
            >
              {tick(selected === option.label)}
              <span className="header-filter-label">{option.label}</span>
              {option.detail && <span className="period-menu-detail">{option.detail}</span>}
            </button>
          ))}
          <button type="button" className="header-filter-option select-menu-option" aria-pressed={selected === CUSTOM_RANGE} onClick={() => setView('calendar')}>
            {tick(selected === CUSTOM_RANGE)}
            <span className="header-filter-label">{CUSTOM_RANGE}…</span>
            <svg className="period-menu-calendar-icon" viewBox="0 0 16 16" aria-hidden="true">
              <rect x="2" y="3" width="12" height="11" rx="2" />
              <path d="M2 7h12M5.5 1.5v3M10.5 1.5v3" />
            </svg>
          </button>
          {clear && (
            <button type="button" className="header-filter-clear" onClick={() => { clear.onClear(); close(); }}>
              {clear.label}
            </button>
          )}
        </div>
      )}

      {view === 'calendar' && (
        <div id={listId} className="header-filter-list period-menu-calendar" role="dialog" aria-label={`${name}: custom range`}>
          <RangeCalendar
            initial={custom}
            onApply={(period) => {
              onCustom(period);
              close();
            }}
            onCancel={() => setView('list')}
          />
        </div>
      )}
    </span>
  );
}
