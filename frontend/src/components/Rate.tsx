import { InfoTip } from './InfoTip';
import { formatRate } from '../utils/checkLabels';

type RateKind = 'precision' | 'recall';

// Why a rate can't be calculated yet, shown instead of a misleading 0% or 100%.
const NOTHING_TO_MEASURE: Record<RateKind, string> = {
  precision: 'Precision needs at least one of this check’s findings marked correct or wrong. None has been yet.',
  recall: 'Recall needs at least one real issue of this kind (marked correct or reported missed). None has been found for this check.',
};

// A precision or recall as a percentage. What the rate means is explained by its heading (InfoLabel); when there is
// nothing to measure yet it says "Not measured", and the reason opens on hover or focus. Given a target, a rate under
// it is shown in red, and the target opens on hover or focus.
export function Rate({
  rate, kind, className, target,
}: { rate: number | null; kind: RateKind; className?: string; target?: number }) {
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
  if (target !== undefined && rate < target) {
    return (
      <InfoTip
        className={`rate rate--below${className ? ` ${className}` : ''}`}
        tip={<><strong>Below target.</strong> The {kind} target is {formatRate(target)}.</>}
      >
        {formatRate(rate)}
      </InfoTip>
    );
  }
  return <span className={`rate${className ? ` ${className}` : ''}`}>{formatRate(rate)}</span>;
}
