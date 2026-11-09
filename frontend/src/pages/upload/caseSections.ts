import type { CaseAnalysisOverview, CaseRisk, WaitingDocument } from '../../services/uploadService';
import { REQUIRED_DOCUMENT_TYPES } from '../../services/uploadService';
import type { IconName } from '../../components/Icon';

export type CaseSection =
  | 'summary'
  | 'documents'
  | 'risk'
  | 'analysis'
  | 'additional'
  | 'notes'
  | 'audit'
  | 'cost';

export const CASE_SECTIONS: { id: CaseSection; label: string; short: string; icon: IconName }[] = [
  { id: 'summary', label: 'Case Summary', short: 'Summary', icon: 'summary' },
  { id: 'documents', label: 'Documents', short: 'Documents', icon: 'documents' },
  { id: 'analysis', label: 'Case Analysis', short: 'Analysis', icon: 'analysis' },
  { id: 'risk', label: 'Risk Summary', short: 'Risk', icon: 'risk' },
  { id: 'additional', label: 'Additional Documents', short: 'Additional', icon: 'additional' },
  { id: 'notes', label: 'Notes', short: 'Notes', icon: 'notes' },
  { id: 'audit', label: 'Audit Trail', short: 'Audit', icon: 'audit' },
  { id: 'cost', label: 'Claude Cost', short: 'Cost', icon: 'cost' },
];

// The sections whose menu item has a status line under its label, which only the live snapshot can fill in.
export const SECTIONS_WITH_STATUS: CaseSection[] = ['documents', 'analysis', 'risk', 'additional'];

export function isCaseSection(value: string | null): value is CaseSection {
  return CASE_SECTIONS.some((section) => section.id === value);
}

// done: nothing left to do · action: the officer has something to do · progress: waiting on the pipeline.
export type SectionTone = 'done' | 'action' | 'progress' | 'error' | 'idle';

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

// What an out-of-date analysis is waiting for before it reruns on its own, naming the documents the risk score is
// waiting on.
export function outOfDateMessage(overview: CaseAnalysisOverview): OutOfDateMessage {
  const waiting = overview.waiting_documents ?? [];
  const waitingList = waiting.map((d) => `${d.filename}: ${WAITING_FOR[d.reason]}`).join('\n');
  if (waiting.length === 0) {
    return {
      short: 'Run Again',
      detail: 'The documents changed since the last run. A new run starts on its own; if none has, run it again.',
      waitingList,
    };
  }
  if (waiting.length === 1) {
    const [{ filename, reason }] = waiting;
    const byReason: Record<WaitingDocument['reason'], [string, string]> = {
      processing: ['Processing', `${filename} is still being processed. Approve it once it's ready; the analysis reruns once it's scanned.`],
      needs_approval: ['1 to Approve', `Approve ${filename}; the analysis reruns once it's scanned.`],
      scanning: ['Scanning', `${filename} is still being scanned; the analysis reruns when it finishes.`],
      failed: ['1 Failed', `${filename} failed processing: retry or reject it, and the analysis reruns.`],
    };
    const [short, detail] = byReason[reason];
    return { short, detail, waitingList };
  }
  const failed = waiting.filter((d) => d.reason === 'failed').length;
  return failed > 0
    ? {
        short: `${failed} Failed`,
        detail: `${waiting.length} documents are holding up the score, ${failed} of them failed processing: retry or reject ${failed === 1 ? 'it' : 'them'}. The analysis reruns once the rest are approved and scanned.`,
        waitingList,
      }
    : {
        short: `${waiting.length} Pending`,
        detail: `${waiting.length} documents still need approving or scanning; the analysis reruns once they're done.`,
        waitingList,
      };
}

export function caseSectionStatuses({
  approvedCount,
  detectedCount,
  missingCount,
  processingCount,
  flaggedCount,
  toApproveCount,
  analysisReady,
  analysisOverview,
  risk,
  additionalDocumentCount,
}: {
  approvedCount: number;
  detectedCount: number;
  // Required documents the case still lacks (the Documents tab's Missing group).
  missingCount: number;
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

  // The Documents tab: what is still to do, most urgent first, and once nothing is, how many are through detection.
  const waiting = [
    missingCount > 0 && `${missingCount} Missing`,
    flaggedCount > 0 && `${flaggedCount} To Review`,
    toApproveCount > 0 && `${toApproveCount} To Approve`,
    processingCount > 0 && `${processingCount} Processing`,
  ].filter(Boolean);
  const documents: SectionStatus =
    waiting.length > 0
      ? { tone: flaggedCount + toApproveCount > 0 ? 'action' : processingCount > 0 ? 'progress' : 'idle', text: waiting.slice(0, 3).join(' · ') }
      : approvedCount === 0
        ? { tone: 'done', text: 'All Decided' }
        : detectedCount === approvedCount
          ? { tone: 'done', text: `${detectedCount} Detected` }
          : { tone: 'progress', text: `${detectedCount}/${approvedCount} Detected` };

  const analysis: SectionStatus = !analysisOverview
    ? analysisReady
      ? { tone: 'progress', text: 'Starting' }
      : { tone: 'idle', text: 'Waiting' }
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
          // Scored is done, like any other section's finished step: the level itself is in the text, not the dot.
          tone: 'done',
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

  return { documents, analysis, risk: riskStatus, additional };
}

// How many required checklist documents aren't approved yet, out of all of them: "8 out of 11 not yet approved".
export function missingRequirementsText(missing: number): string {
  return `${missing} out of ${REQUIRED_DOCUMENT_TYPES.length} not yet approved`;
}
