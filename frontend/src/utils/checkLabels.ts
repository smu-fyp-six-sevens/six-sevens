// Cross-document findings are filed under a category rather than a tool (backend FindingCategory).
const CATEGORY_LABELS: Record<string, string> = {
  cross_document_variance: 'Documents differ',
  identity_entity_conflict: 'Identity conflict',
  registry_discrepancy: 'Registry mismatch',
  behavioural_anomaly: 'Unusual behaviour',
  unsupported_claim: 'Unsupported claim',
  tampering_forensic: 'Tampering',
};

// "font_consistency_tool" -> "Font consistency"; a cross-document category -> its label.
export function formatCheck(check: string | null): string {
  if (!check) return 'Check';
  if (CATEGORY_LABELS[check]) return CATEGORY_LABELS[check];
  const words = check.replace(/_tool$/, '').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// A rate as a percentage, or a dash when there is nothing to measure yet (never 0% or 100% by default). Rate.tsx
// explains the dash on hover.
export function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

// What each rate measures, shown on hover over a rate or its heading.
export const RATE_MEANING: Record<'precision' | 'recall', string> = {
  precision: 'Precision = correct ÷ (correct + wrong): of the findings this check raised, how many were real issues.',
  recall: 'Recall = correct ÷ (correct + missed): of the real issues of this kind, how many this check caught.',
};
