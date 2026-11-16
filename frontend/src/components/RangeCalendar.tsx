import { useState } from 'react';
import { isoDay, parseDay, today, type Period } from '../utils/dateRange';
import { Icon } from './Icon';

const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

// The days to show for a month, Monday first, with nulls before the 1st.
function monthDays(year: number, month: number): (string | null)[] {
  const first = new Date(year, month, 1);
  const count = new Date(year, month + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  return [...Array<null>(lead).fill(null), ...Array.from({ length: count }, (_, index) => isoDay(new Date(year, month, index + 1)))];
}

interface RangeCalendarProps {
  // The range to start from, if any.
  initial?: Period | null;
  onApply: (period: Period & { from: string }) => void;
  onCancel: () => void;
}

// Two months side by side: the first click picks the start, the second the end (either order); hovering previews
// the range. Days after today can't be picked.
export default function RangeCalendar({ initial, onApply, onCancel }: RangeCalendarProps) {
  const latest = today();
  const [start, setStart] = useState<string | null>(initial?.from ?? null);
  const [end, setEnd] = useState<string | null>(initial?.from ? initial.to : null);
  const [hover, setHover] = useState<string | null>(null);
  // The right-hand month: the range's end month, or this one.
  const anchor = parseDay(end ?? latest);
  const [view, setView] = useState({ year: anchor.getFullYear(), month: anchor.getMonth() });

  const months = [-1, 0].map((offset) => {
    const date = new Date(view.year, view.month + offset, 1);
    return { year: date.getFullYear(), month: date.getMonth() };
  });
  const canGoForward = isoDay(new Date(view.year, view.month + 1, 1)) <= latest;
  const shift = (by: number) => setView(({ year, month }) => {
    const date = new Date(year, month + by, 1);
    return { year: date.getFullYear(), month: date.getMonth() };
  });

  function pick(day: string) {
    if (!start || end) {
      setStart(day);
      setEnd(null);
    } else if (day < start) {
      setEnd(start);
      setStart(day);
    } else {
      setEnd(day);
    }
  }

  // The range drawn: the picked one, or the start to the hovered day while choosing the end.
  const [low, high] = start && !end && hover ? [start, hover].sort() : [start, end ?? start];
  const summary = start && end
    ? `${parseDay(start).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' })} – ${parseDay(end).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' })}`
    : start ? 'Pick the end date' : 'Pick the start date';

  return (
    <div className="range-calendar">
      {/* Over the month names, so both stay reachable when narrow screens show one month. */}
      <div className="range-calendar-navs">
        <button type="button" className="range-calendar-nav" onClick={() => shift(-1)} aria-label="Previous month"><Icon name="chevronLeft" size={14} /></button>
        <button type="button" className="range-calendar-nav" onClick={() => shift(1)} disabled={!canGoForward} aria-label="Next month"><Icon name="chevronRight" size={14} /></button>
      </div>
      <div className="range-calendar-months">
        {months.map(({ year, month }) => (
          <div key={`${year}-${month}`} className="range-calendar-month">
            <div className="range-calendar-head">
              <strong>{new Date(year, month, 1).toLocaleDateString('en-SG', { month: 'long', year: 'numeric' })}</strong>
            </div>
            <div className="range-calendar-grid" role="grid" onMouseLeave={() => setHover(null)}>
              {WEEKDAYS.map((day) => <span key={day} className="range-calendar-weekday">{day}</span>)}
              {monthDays(year, month).map((day, cell) => {
                if (!day) return <span key={`blank-${cell}`} />;
                const inRange = low && high && day >= low && day <= high;
                const edge = day === low || day === high;
                const classes = ['range-calendar-day', inRange && 'in-range', edge && 'edge', day === low && 'range-start', day === high && 'range-end', day === latest && 'today']
                  .filter(Boolean).join(' ');
                return (
                  <button
                    key={day}
                    type="button"
                    className={classes}
                    disabled={day > latest}
                    aria-pressed={Boolean(edge)}
                    aria-label={parseDay(day).toLocaleDateString('en-SG', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                    onMouseEnter={() => setHover(day)}
                    onClick={() => pick(day)}
                  >
                    {parseDay(day).getDate()}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <div className="range-calendar-foot">
        <span>{summary}</span>
        <button type="button" className="range-calendar-cancel" onClick={onCancel}>Cancel</button>
        <button type="button" className="range-calendar-apply" disabled={!start || !end} onClick={() => start && end && onApply({ from: start, to: end })}>Apply</button>
      </div>
    </div>
  );
}
