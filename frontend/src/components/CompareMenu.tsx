import { useRef, useState } from 'react';
import { useDismiss } from './useDismiss';
import RangeCalendar from './RangeCalendar';
import { formatPeriod, overlap, type Period } from '../utils/dateRange';

type Picked = Period & { from: string };

interface CompareMenuProps {
  // The period in view, which step 1 opens on.
  period: Period;
  // The comparison in use; null when not comparing.
  compare: Period | null;
  onCompare: (first: Picked, second: Picked) => void;
  onStop: () => void;
}

// Compare in two steps on one calendar: pick the first period (it opens on the one in view), then the period to
// compare it with, the first period's days disabled so the two can't overlap. Applying sets both.
export default function CompareMenu({ period, compare, onCompare, onStop }: CompareMenuProps) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [step, setStep] = useState<'closed' | 'first' | 'second'>('closed');
  const [first, setFirst] = useState<Picked | null>(null);
  const isOpen = step !== 'closed';

  function close() {
    setStep('closed');
    triggerRef.current?.focus();
  }

  useDismiss(rootRef, isOpen, () => setStep('closed'), close);

  return (
    <span className="header-filter select-menu period-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="header-filter-trigger"
        data-active={compare ? true : undefined}
        aria-label="Compare two periods"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        onClick={() => {
          setFirst(null);
          setStep(isOpen ? 'closed' : 'first');
        }}
      >
        Compare
        <svg className="select-menu-chevron" viewBox="0 0 12 8" aria-hidden="true">
          <path d="m1 1.5 5 5 5-5" />
        </svg>
      </button>
      {compare && (
        <button type="button" className="period-menu-clear" aria-label="Stop comparing" title="Stop comparing" onClick={() => { onStop(); setStep('closed'); }}>
          <svg viewBox="0 0 12 12" aria-hidden="true"><path d="m3 3 6 6M9 3l-6 6" /></svg>
        </button>
      )}

      {isOpen && (
        <div className="header-filter-list period-menu-calendar compare-menu" role="dialog" aria-label="Compare two periods">
          <ol className="compare-steps">
            <li className={step === 'first' ? 'current' : 'done'}>
              <span>1</span>
              {step === 'first' ? 'Pick the first period' : (
                <>
                  First period: <strong>{first && formatPeriod(first)}</strong>
                </>
              )}
            </li>
            <li className={step === 'second' ? 'current' : undefined}>
              <span>2</span>
              Pick the period to compare it with
            </li>
          </ol>
          {step === 'first' ? (
            <RangeCalendar
              key="first"
              initial={first ?? (period.from ? period : null)}
              applyLabel="Next"
              onApply={(picked) => {
                setFirst(picked);
                setStep('second');
              }}
              onCancel={close}
            />
          ) : (
            <RangeCalendar
              key="second"
              initial={compare && first && !overlap(compare, first) ? compare : null}
              blocked={first ? { period: first, label: 'First period' } : null}
              applyLabel="Compare"
              cancelLabel="Back"
              onApply={(picked) => {
                if (first) onCompare(first, picked);
                close();
              }}
              onCancel={() => setStep('first')}
            />
          )}
        </div>
      )}
    </span>
  );
}
