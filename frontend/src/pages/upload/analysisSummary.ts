import type { CaseAnalysis } from '../../services/uploadService';
import { formatCheck } from '../../utils/checkLabels';

// A cross-document comparison that found the documents agree, as a chip: "UEN is the same on 3 documents" -> "UEN".
export function checkChipLabel(line: string): string {
  const same = /^(?:The )?(.+?) (?:is|are) the same\b/i.exec(line);
  if (same) return same[1].charAt(0).toUpperCase() + same[1].slice(1);
  if (/^No address differs/i.test(line)) return 'Addresses';
  return line;
}

// "Checked and fine": detection checks that raised nothing, then cross-document comparisons that agreed, each once,
// as [chip label, what it means on hover].
export function checkedAndFine(analysis: CaseAnalysis): [string, string][] {
  const chips = new Map<string, string>();
  for (const check of analysis.clean_checks ?? []) {
    chips.set(formatCheck(check), `${formatCheck(check)} ran on the documents and raised nothing`);
  }
  for (const line of analysis.consistent_checks ?? []) {
    if (!chips.has(checkChipLabel(line))) chips.set(checkChipLabel(line), line);
  }
  return [...chips];
}

// The id of a cross-document finding's row on the Case Analysis card, which a summary reason scrolls to.
export function analysisFindingAnchor(findingId: string): string {
  return `analysis-finding-${findingId}`;
}
