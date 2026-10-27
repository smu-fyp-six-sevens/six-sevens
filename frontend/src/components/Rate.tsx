import { InfoTip } from './InfoTip';
import { formatRate } from '../utils/checkLabels';

type RateKind = 'precision' | 'recall';

// Why a rate can't be calculated yet, shown instead of a misleading 0% or 100%.
const NOTHING_TO_MEASURE: Record<RateKind, string> = {
  precision: 'Precision needs at least one of this check’s findings marked correct or wrong. None has been yet.',
  recall: 'Recall needs at least one real issue of this kind (marked correct or reported missed). None has been found for this check.',
};

// A precision or recall as a percentage. What the rate means is explained by its heading (InfoLabel); when there is
// nothing to measure yet it says "Not measured", and the reason opens on hover or focus.
export function Rate({ rate, kind, className }: { rate: number | null; kind: RateKind; className?: string }) {
  if (rate === null) {
    return (
      <InfoTip
        className={`rate rate--empty${className ? ` ${className}` : ''}`}
        tip={<><strong>Not measured yet.</strong> {NOTHING_TO_MEASURE[kind]}</>}
      >
        Not measured
      </InfoTip>
    );
  }
  return <span className={`rate${className ? ` ${className}` : ''}`}>{formatRate(rate)}</span>;
}
