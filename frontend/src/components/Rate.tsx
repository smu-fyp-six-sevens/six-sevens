import { RATE_MEANING, formatRate } from '../utils/checkLabels';

type RateKind = 'precision' | 'recall';

// Why a rate can't be calculated yet, shown on hover instead of a misleading 0% or 100%.
const NOTHING_TO_MEASURE: Record<RateKind, string> = {
  precision:
    'No reviews: none of this check’s findings have been marked correct or wrong yet, so there is nothing to judge it on.',
  recall:
    'No examples: no real issue of this kind has been found (nothing marked correct, nothing reported missed), so there was nothing for this check to catch.',
};

// A precision or recall as a percentage, with what it means on hover. When there is nothing to measure it shows
// "no reviews" / "no examples", underlined, with the reason on hover.
export function Rate({ rate, kind, className }: { rate: number | null; kind: RateKind; className?: string }) {
  const empty = rate === null;
  return (
    <span
      className={`rate${empty ? ' rate--empty' : ''}${className ? ` ${className}` : ''}`}
      title={empty ? NOTHING_TO_MEASURE[kind] : RATE_MEANING[kind]}
    >
      {formatRate(rate, kind)}
    </span>
  );
}
