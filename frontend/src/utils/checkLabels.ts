// Cross-document findings are filed under a category rather than a tool (backend FindingCategory), and a few checks
// read better by what they look for than by their tool's name. Labels only: the codes the backend stores stay as they are.
const CATEGORY_LABELS: Record<string, string> = {
  cross_document_variance: 'Documents Differ',
  identity_entity_conflict: 'Identity Conflict',
  registry_discrepancy: 'Registry Mismatch',
  behavioural_anomaly: 'Unusual Behaviour',
  unsupported_claim: 'Unsupported Claim',
  tampering_forensic: 'Tampering',
  ai_generation_tool: 'AI Generation',
  ai_generation: 'AI Generation',
  // Compares the resolution and compression of a file's images, and looks for enlarged ones.
  image_manipulation_tool: 'Image Resolution & Compression',
  // Looks for pasted or painted-over images: seams, stretching, and brightness or colour that doesn't match.
  image_tampering_tool: 'Image Boundary, Scaling & Colour Consistency',
};

// Small words stay lower case inside a name ("Source of Wealth").
const MINOR_WORDS = new Set(['a', 'an', 'and', 'of', 'or', 'the', 'to', 'in', 'on', 'for']);

// A check's name in title case: "font_consistency_tool" -> "Font Consistency"; a cross-document category, or a check
// named above -> its label.
export function formatCheck(check: string | null): string {
  if (!check) return 'Check';
  if (CATEGORY_LABELS[check]) return CATEGORY_LABELS[check];
  return check
    .replace(/_tool$/, '')
    .split('_')
    .map((word, index) =>
      word.toLowerCase() === 'ai'
        ? 'AI'
        : index > 0 && MINOR_WORDS.has(word)
          ? word
          : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

// A rate as a percentage, or a dash when there is nothing to measure yet (never 0% or 100% by default). Rate.tsx
// explains the dash on hover.
export function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

// The note under a table of rates: Rate.tsx shows a rate under its target in red.
export function belowTargetNote(targetPrecision: number, targetRecall: number): string {
  return targetPrecision === targetRecall
    ? `Red: below the ${formatRate(targetPrecision)} target`
    : `Red: below target (precision ${formatRate(targetPrecision)}, recall ${formatRate(targetRecall)})`;
}

// What each rate measures, shown on hover over a rate or its heading.
export const RATE_MEANING: Record<'precision' | 'recall', string> = {
  precision: 'How often a raised finding is a real issue. Correct ÷ (correct + wrong).',
  recall: 'How many real issues the check catches. Correct ÷ (correct + missed).',
};
