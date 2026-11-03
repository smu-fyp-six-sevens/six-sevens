// Date ranges for the dashboard: inclusive, as local ISO days (YYYY-MM-DD). A null `from` is all time.
export interface Period {
  from: string | null;
  to: string;
}

const DAY_MS = 24 * 60 * 60 * 1000;

// A date as its local ISO day, so "today" is the user's today rather than UTC's.
export function isoDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

export function parseDay(day: string): Date {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year, month - 1, date);
}

export function addDays(day: string, days: number): string {
  const date = parseDay(day);
  date.setDate(date.getDate() + days);
  return isoDay(date);
}

export const today = () => isoDay(new Date());

export function daysBetween(from: string, to: string): number {
  return Math.round((parseDay(to).getTime() - parseDay(from).getTime()) / DAY_MS);
}

export const PRESETS = ['Last 7 days', 'Last 30 days', 'Last 90 days', 'All time'] as const;

export function presetPeriod(preset: string): Period {
  const end = today();
  if (preset === 'All time') return { from: null, to: end };
  // Inclusive of today: "Last 7 days" is today and the six before it.
  return { from: addDays(end, 1 - Number(preset.split(' ')[1])), to: end };
}

export function inPeriod(day: string, period: Period): boolean {
  return (!period.from || day >= period.from) && day <= period.to;
}

// "4 Oct – 3 Nov", with the year when either end isn't this year; "Until 3 Nov" for all time.
export function formatPeriod(period: Period): string {
  const year = new Date().getFullYear();
  const showYear = [period.from, period.to].some((day) => day && parseDay(day).getFullYear() !== year);
  const format = (day: string) => parseDay(day).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', ...(showYear ? { year: 'numeric' } : {}) });
  if (!period.from) return `Until ${format(period.to)}`;
  return period.from === period.to ? format(period.from) : `${format(period.from)} – ${format(period.to)}`;
}

// The days two periods share, if any.
export function overlap(a: Period, b: Period): Period | null {
  const from = [a.from, b.from].reduce<string | null>((latest, day) => (day && (!latest || day > latest) ? day : latest), null);
  const to = a.to < b.to ? a.to : b.to;
  return !from || from <= to ? { from, to } : null;
}
