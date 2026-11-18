import type { CaseAnalysis } from '../../services/uploadService';
import { formatCheck } from '../../utils/checkLabels';

// A cross-document comparison that found the documents agree, as a chip: "UEN is the same on 3 documents" -> "UEN".
export function checkChipLabel(line: string): string {
  const same = /^(?:The )?(.+?) (?:is|are) the same\b/i.exec(line);
  if (same) return same[1].charAt(0).toUpperCase() + same[1].slice(1);
  if (/^No address differs/i.test(line)) return 'Addresses';
  // "<name>'s address "..." on <document> is a previous address: <newer document> ... shows "..."" (backend
  // deterministic._addresses): the full sentence is the chip's hover text.
  if (/ is a previous address: /.test(line)) return 'Previous address';
  return line;
}

// "Checked and fine": detection checks that raised nothing, then cross-document comparisons that agreed, each once,
// as [chip label, what it means on hover].
export function checkedAndFine(analysis: CaseAnalysis): [string, string][] {
  const chips = new Map<string, string>();
  for (const check of analysis.clean_checks ?? []) {
    chips.set(chipCase(formatCheck(check)), `${formatCheck(check)} ran on the documents and raised nothing`);
  }
  for (const line of analysis.consistent_checks ?? []) {
    // A line no chip name was found for is shown whole, as the sentence it is.
    const label = checkChipLabel(line) === line ? line : chipCase(checkChipLabel(line));
    if (!chips.has(label)) chips.set(label, line);
  }
  return [...chips];
}

// The checks that couldn't run on some document, once each by name, with where and why: "Hidden Text" ->
// ["C011_Payslip.pdf (no text layer)"].
export function unverifiedChecks(analysis: CaseAnalysis): { label: string; where: string[] }[] {
  const checks = new Map<string, string[]>();
  for (const check of analysis.risk?.unassessed_checks ?? []) {
    const label = chipCase(formatCheck(check.tool));
    const where = `${check.filename}${check.reason ? ` (${check.reason.charAt(0).toLowerCase()}${check.reason.slice(1)})` : ''}`;
    checks.set(label, [...(checks.get(label) ?? []), where]);
  }
  return [...checks].map(([label, where]) => ({ label, where }));
}

// A chip is a name, so each word starts with a capital: "Image manipulation" -> "Image Manipulation". Capitals
// already there (UEN, ID) are kept.
function chipCase(label: string): string {
  return label.replace(/(^|\s)(\p{Ll})/gu, (_match, space: string, letter: string) => space + letter.toUpperCase());
}

// The Findings tab's group for cross-document findings: they belong to the case, not to one document.
export const CASE_GROUP_ID = '__case__';

// Where a finding sits on the Findings tab (CaseRiskCard), for a summary reason to link to.
export function riskFindingAnchor(finding: { source: string; document_id: string; finding_id: string }): string {
  return `risk-finding-${finding.source === 'cross_document' ? CASE_GROUP_ID : finding.document_id}-${finding.finding_id}`;
}

// The id of a cross-document finding's row on the Case Analysis card, which a summary reason scrolls to.
export function analysisFindingAnchor(findingId: string): string {
  return `analysis-finding-${findingId}`;
}
