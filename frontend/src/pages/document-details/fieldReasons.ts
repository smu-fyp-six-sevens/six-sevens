import { EXTRACTION_CONFIDENCE_THRESHOLD } from '../../services/uploadService';
import type { LangGraphTrace } from '../../services/uploadService';

// Why extraction was unsure of each field, from the document's latest extraction (a retype runs it again and adds a
// newer step). The trace is best-effort: without it there are no reasons, and the low confidence still applies.
export function lowConfidenceReasons(trace: LangGraphTrace | null): Record<string, string> {
  const steps = (trace?.steps ?? []).filter((step) => step.step === 'extraction');
  const reasons = steps[steps.length - 1]?.low_confidence_reasons;
  if (typeof reasons !== 'object' || reasons === null) return {};
  return Object.fromEntries(
    Object.entries(reasons).filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1] !== ''),
  );
}

// A field the officer has saved has a confidence of 1.00, so its reason stops showing.
export function fieldReason(
  reasons: Record<string, string>,
  field: string,
  confidence: number | null | undefined,
): string | null {
  if (typeof confidence !== 'number' || confidence >= EXTRACTION_CONFIDENCE_THRESHOLD) return null;
  return reasons[field] ?? null;
}
