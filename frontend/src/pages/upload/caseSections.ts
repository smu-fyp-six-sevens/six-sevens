import type { CaseAnalysisOverview, CaseRisk, CaseRiskLevel } from '../../services/uploadService';

export type CaseSection =
  | 'summary'
  | 'documents'
  | 'flagged'
  | 'risk'
  | 'analysis'
  | 'additional'
  | 'audit';

export const CASE_SECTIONS: { id: CaseSection; label: string; icon: string }[] = [
  // Placeholder: the section is empty for now.
  { id: 'summary', label: 'Case summary', icon: '◫' },
  { id: 'documents', label: 'Documents', icon: '▤' },
  { id: 'flagged', label: 'Flagged items', icon: '△' },
  { id: 'analysis', label: 'Case analysis', icon: '◇' },
  { id: 'risk', label: 'Risk summary', icon: '▥' },
  { id: 'additional', label: 'Additional documents', icon: '⊕' },
  { id: 'audit', label: 'Audit trail', icon: '◴' },
];

export function isCaseSection(value: string | null): value is CaseSection {
  return CASE_SECTIONS.some((section) => section.id === value);
}

// done: nothing left to do · action: the officer has something to do · progress: waiting on the pipeline.
export type SectionTone = 'done' | 'action' | 'progress' | 'error' | 'idle' | `risk-${Lowercase<CaseRiskLevel>}`;

export interface SectionStatus {
  tone: SectionTone;
  text: string;
}

export function caseSectionStatuses({
  documentCount,
  approvedCount,
  hasInFlightDocument,
  flaggedCount,
  analysisReady,
  analysisOverview,
  risk,
  additionalDocumentCount,
}: {
  documentCount: number;
  approvedCount: number;
  hasInFlightDocument: boolean;
  flaggedCount: number;
  analysisReady: boolean;
  analysisOverview: CaseAnalysisOverview | null;
  risk: CaseRisk | null;
  additionalDocumentCount: number;
}): Partial<Record<CaseSection, SectionStatus>> {
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

  const documents: SectionStatus =
    documentCount === 0
      ? { tone: 'idle', text: 'None uploaded' }
      : hasInFlightDocument
        ? { tone: 'progress', text: `Processing · ${approvedCount}/${documentCount} approved` }
        : approvedCount < documentCount
          ? { tone: 'action', text: `${approvedCount}/${documentCount} approved` }
          : { tone: 'done', text: `All ${documentCount} approved` };

  const flagged: SectionStatus =
    flaggedCount > 0
      ? { tone: 'action', text: `${flaggedCount} to review` }
      : documentCount === 0
        ? { tone: 'idle', text: 'Nothing yet' }
        : { tone: 'done', text: 'None flagged' };

  const analysis: SectionStatus = !analysisOverview
    ? analysisReady
      ? { tone: 'action', text: 'Ready to run' }
      : { tone: 'idle', text: 'Not ready' }
    : analysisOverview.status === 'running'
      ? { tone: 'progress', text: 'Running' }
      : analysisOverview.status === 'failed'
        ? { tone: 'error', text: 'Did not finish' }
        : analysisOverview.stale || analysisOverview.status === 'superseded'
          ? { tone: 'action', text: 'Out of date' }
          : { tone: 'done', text: plural(analysisOverview.finding_count, 'finding') };

  const riskStatus: SectionStatus = !risk
    ? { tone: 'idle', text: 'Not scored' }
    : risk.state === 'scored' && risk.level
      ? {
          tone: `risk-${risk.level.toLowerCase() as Lowercase<CaseRiskLevel>}`,
          text: risk.score != null ? `${risk.level} · ${risk.score}` : risk.level,
        }
      : risk.state === 'failed'
        ? { tone: 'error', text: 'Detection failed' }
        : risk.state === 'pending'
          ? { tone: 'progress', text: `${risk.documents_scanned}/${risk.documents_total} scanned` }
          : { tone: 'idle', text: 'Not scored' };

  // Like the card, the requests only exist once a case analysis has scored the case.
  const additional: SectionStatus = !risk
    ? { tone: 'idle', text: 'Not checked' }
    : additionalDocumentCount > 0
      ? { tone: 'action', text: `${additionalDocumentCount} to request` }
      : { tone: 'done', text: 'None needed' };

  return { documents, flagged, analysis, risk: riskStatus, additional };
}
