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

// A rate as a percentage, or what's missing: "no reviews" for precision (nothing it raised was reviewed),
// "no examples" for recall (nothing it should have caught), never 0% or 100% by default.
export function formatRate(rate: number | null, kind: 'precision' | 'recall'): string {
  if (rate === null) return kind === 'precision' ? 'no reviews' : 'no examples';
  return `${Math.round(rate * 100)}%`;
}
