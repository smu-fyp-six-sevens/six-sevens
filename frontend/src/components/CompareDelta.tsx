// A figure against the comparison period. Neutral ink: whether up is good depends on the figure, so the sign
// carries direction only.
//
// Card (default): "vs 1" and a pill with the change and its percentage ("+1 · +100%"); "Same as before" when equal.
// Compact: the pill alone ("+1"), for a column in a list or chart; nothing when equal.
export default function CompareDelta({
  value,
  previous,
  format = (amount) => String(amount),
  compact = false,
  better,
  points = false,
  previousLabel,
}: {
  value: number | null;
  previous: number | null;
  format?: (amount: number) => string;
  compact?: boolean;
  // Which way is an improvement, for figures where that's clear: the pill turns green that way, red the other.
  better?: 'up' | 'down';
  // For a rate (0 to 1): the change in percentage points ("−5 pts"), without a percentage of a percentage.
  points?: boolean;
  // The comparison figure in place of "vs {previous}", e.g. "vs 2 / 7".
  previousLabel?: string;
}) {
  if (previous === null || value === null) {
    if (compact) return <span className="compare-pill compare-pill--none" aria-hidden="true" />;
    return (
      <span className="compare-line compare-line--none">
        {previous === null ? (value === null ? 'No data in either period' : 'No data in the comparison') : `vs ${format(previous)} · none now`}
      </span>
    );
  }
  const change = value - previous;
  // Rounding noise ($0.0001) reads as no change.
  const same = Math.abs(change) < 1e-9 || format(Math.abs(change)) === format(0);
  if (same) {
    if (compact) return <span className="compare-pill compare-pill--none"><span className="sr-only">no change</span></span>;
    return <span className="compare-line compare-line--same">Same as before</span>;
  }
  const sign = change > 0 ? '+' : '−';
  const amount = points ? `${Math.round(Math.abs(change) * 100)} pts` : format(Math.abs(change));
  const percent = previous !== 0 ? `${sign}${Math.round(Math.abs(change / previous) * 100)}%` : 'new';
  const pill = (
    <span className={`compare-pill compare-pill--${change > 0 ? 'up' : 'down'}${better ? ` compare-pill--${(change > 0) === (better === 'up') ? 'good' : 'bad'}` : ''}`} title={`Comparison period: ${format(previous)}`}>
      {sign}{amount}
      {!compact && !points && <span className="compare-pill-percent"> · {percent}</span>}
      {compact && <span className="sr-only">, was {format(previous)}</span>}
    </span>
  );
  if (compact) return pill;
  return (
    <span className="compare-line">
      <span className="compare-was">{previousLabel ?? `vs ${format(previous)}`}</span>
      {pill}
    </span>
  );
}
