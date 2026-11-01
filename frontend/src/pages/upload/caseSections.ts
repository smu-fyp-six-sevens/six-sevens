import type { CaseAnalysisOverview, CaseRisk, CaseRiskLevel, WaitingDocument } from '../../services/uploadService';
import { REQUIRED_DOCUMENT_TYPES } from '../../services/uploadService';

export type CaseSection =
  | 'summary'
  | 'documents'
  | 'approved'
  | 'risk'
  | 'analysis'
  | 'additional'
  | 'notes'
  | 'audit'
  | 'cost';

export const CASE_SECTIONS: { id: CaseSection; label: string; icon: string }[] = [
  { id: 'summary', label: 'Case Summary', icon: '◫' },
  { id: 'documents', label: 'Documents', icon: '▤' },
  { id: 'approved', label: 'Approved Documents', icon: '✓' },
  { id: 'analysis', label: 'Case Analysis', icon: '◇' },
  { id: 'risk', label: 'Risk Summary', icon: '▥' },
  { id: 'additional', label: 'Additional Documents', icon: '⊕' },
  { id: 'notes', label: 'Notes', icon: '✎' },
  { id: 'audit', label: 'Audit Trail', icon: '◴' },
  { id: 'cost', label: 'Claude Cost', icon: '$' },
];

// The sections whose menu item has a status line under its label, which only the live snapshot can fill in.
export const SECTIONS_WITH_STATUS: CaseSection[] = ['documents', 'approved', 'analysis', 'risk', 'additional'];

export function isCaseSection(value: string | null): value is CaseSection {
  return CASE_SECTIONS.some((section) => section.id === value);
}

// done: nothing left to do · action: the officer has something to do · progress: waiting on the pipeline.
export type SectionTone = 'done' | 'action' | 'progress' | 'error' | 'idle' | `risk-${Lowercase<CaseRiskLevel>}`;

export interface SectionStatus {
  tone: SectionTone;
  text: string;
}

const WAITING_FOR: Record<WaitingDocument['reason'], string> = {
  processing: 'still being processed',
  needs_approval: 'waiting for approval',
  scanning: 'still being scanned',
  failed: 'failed processing',
};

export interface OutOfDateMessage {
  // For the side menu and Case Summary: "Out of Date · <short>".
  short: string;
  // The full sentence: which document, what it's waiting for, and what to do next.
  detail: string;
  // One line per waiting document, for a hover.
  waitingList: string;
}

// Why an out-of-date analysis can or can't be rerun yet, naming the documents the risk score is waiting on.
export function outOfDateMessage(overview: CaseAnalysisOverview): OutOfDateMessage {
  const waiting = overview.waiting_documents ?? [];
  const waitingList = waiting.map((d) => `${d.filename}: ${WAITING_FOR[d.reason]}`).join('\n');
  if (waiting.length === 0) {
    return {
      short: 'Run Again',
      detail: 'The documents changed since the last run: run again to update the score.',
      waitingList,
    };
  }
  if (waiting.length === 1) {
    const [{ filename, reason }] = waiting;
    const byReason: Record<WaitingDocument['reason'], [string, string]> = {
      processing: ['Processing', `${filename} is still being processed. Approve it once it's ready, then run again when it's scanned.`],
      needs_approval: ['1 to Approve', `Approve ${filename}, then run again once it's scanned.`],
      scanning: ['Scanning', `${filename} is still being scanned; run again when it finishes.`],
      failed: ['1 Failed', `${filename} failed processing: retry or reject it, then run again.`],
    };
    const [short, detail] = byReason[reason];
    return { short, detail, waitingList };
  }
  const failed = waiting.filter((d) => d.reason === 'failed').length;
  return failed > 0
    ? {
        short: `${failed} Failed`,
        detail: `${waiting.length} documents are holding up the score, ${failed} of them failed processing: retry or reject ${failed === 1 ? 'it' : 'them'}, and run again once the rest are approved and scanned.`,
        waitingList,
      }
    : {
        short: `${waiting.length} Pending`,
        detail: `${waiting.length} documents still need approving or scanning; run again once they're done.`,
        waitingList,
      };
}

export function caseSectionStatuses({
  documentCount,
  approvedCount,
  detectedCount,
  processingCount,
  flaggedCount,
  toApproveCount,
  analysisReady,
  analysisOverview,
  risk,
  additionalDocumentCount,
}: {
  documentCount: number;
  approvedCount: number;
  detectedCount: number;
  // Documents still waiting on the officer's decision: being uploaded or extracted, flagged, or ready to approve.
  processingCount: number;
  flaggedCount: number;
  toApproveCount: number;
  analysisReady: boolean;
  analysisOverview: CaseAnalysisOverview | null;
  risk: CaseRisk | null;
  additionalDocumentCount: number;
}): Partial<Record<CaseSection, SectionStatus>> {
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

  // The upload and review tab: only documents not yet approved or rejected count. What needs the officer comes
  // first, flagged ones before the rest; detection is the Approved tab's.
  const waiting = [
    flaggedCount > 0 && `${flaggedCount} To Review`,
    toApproveCount > 0 && `${toApproveCount} To Approve`,
    processingCount > 0 && `${processingCount} Processing`,
  ].filter(Boolean);
  const documents: SectionStatus =
    documentCount === 0
      ? { tone: 'idle', text: 'None Uploaded' }
      : waiting.length === 0
        ? { tone: 'done', text: 'All Decided' }
        : { tone: flaggedCount + toApproveCount > 0 ? 'action' : 'progress', text: waiting.slice(0, 2).join(' · ') };

  // The approved documents, and how many of them detection has finished.
  const approved: SectionStatus =
    approvedCount === 0
      ? { tone: 'idle', text: 'None Yet' }
      : { tone: detectedCount === approvedCount ? 'done' : 'progress', text: `${detectedCount}/${approvedCount} Detected` };

  const analysis: SectionStatus = !analysisOverview
    ? analysisReady
      ? { tone: 'action', text: 'Ready to Run' }
      : { tone: 'idle', text: 'Not Ready' }
    : analysisOverview.status === 'running'
      ? { tone: 'progress', text: 'Running' }
      : analysisOverview.status === 'failed'
        ? { tone: 'error', text: 'Did Not Finish' }
        : analysisOverview.stale || analysisOverview.status === 'superseded'
          ? { tone: 'action', text: `Out of Date · ${outOfDateMessage(analysisOverview).short}` }
          : { tone: 'done', text: plural(analysisOverview.finding_count, 'Finding') };

  const riskStatus: SectionStatus = !risk
    ? { tone: 'idle', text: 'Not Scored' }
    : risk.state === 'scored' && risk.level
      ? {
          tone: `risk-${risk.level.toLowerCase() as Lowercase<CaseRiskLevel>}`,
          text: risk.score != null ? `${risk.level} · ${risk.score}` : risk.level,
        }
      : risk.state === 'failed'
        ? { tone: 'error', text: 'Detection Failed' }
        : risk.state === 'pending'
          ? { tone: 'progress', text: `${risk.documents_scanned}/${risk.documents_total} Scanned` }
          : { tone: 'idle', text: 'Not Scored' };

  // Like the card, the requests only exist once a case analysis has scored the case.
  const additional: SectionStatus = !risk
    ? { tone: 'idle', text: 'Not Checked' }
    : additionalDocumentCount > 0
      ? { tone: 'action', text: `${additionalDocumentCount} To Request` }
      : { tone: 'done', text: 'None Needed' };

  return { documents, approved, analysis, risk: riskStatus, additional };
}

// How many required checklist documents aren't approved yet, out of all of them: "8 out of 11 not yet approved".
export function missingRequirementsText(missing: number): string {
  return `${missing} out of ${REQUIRED_DOCUMENT_TYPES.length} not yet approved`;
}
