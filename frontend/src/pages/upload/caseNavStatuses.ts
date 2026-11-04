import { TERMINAL_DOCUMENT_STATUSES } from '../../services/uploadService';
import { missingRequirements } from './documentChecklist';
import { caseSectionStatuses } from './caseSections';
import { useCaseSnapshot } from './useCaseSnapshot';

type Snapshot = ReturnType<typeof useCaseSnapshot>;

// Where each of the case's sections stands, for the section menu's dots and subtitles: one place for it, so the case
// page and a document page show the same menu.
export function sectionStatusesFrom(snapshot: Pick<
  Snapshot,
  | 'approvedDocuments'
  | 'extractedDocuments'
  | 'flaggedDocuments'
  | 'fulfilledRequirements'
  | 'idDocumentCoverage'
  | 'proofOfAddressCoverage'
  | 'analysisReady'
  | 'analysisOverview'
  | 'risk'
  | 'additionalDocuments'
>) {
  const { approvedDocuments, extractedDocuments, flaggedDocuments } = snapshot;
  return caseSectionStatuses({
    approvedCount: approvedDocuments.length,
    detectedCount: approvedDocuments.filter((document) => document.status === 'detected').length,
    missingCount: missingRequirements(snapshot.fulfilledRequirements, {
      idDocument: snapshot.idDocumentCoverage,
      proofOfAddress: snapshot.proofOfAddressCoverage,
    }).length,
    // Not yet approved or rejected, so still uploading or extracting (detection comes after approval).
    processingCount: extractedDocuments.filter((document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status)).length,
    flaggedCount: flaggedDocuments.length,
    toApproveCount: extractedDocuments.filter(
      (document) => document.status === 'analyzed' && !flaggedDocuments.includes(document),
    ).length,
    analysisReady: snapshot.analysisReady,
    analysisOverview: snapshot.analysisOverview,
    risk: snapshot.risk,
    additionalDocumentCount: snapshot.additionalDocuments?.items.length ?? 0,
  });
}

// For a page that has no case snapshot of its own (a document page): the case's live state, as menu statuses.
export function useCaseNavStatuses(caseId: string) {
  const snapshot = useCaseSnapshot(caseId);
  return { statuses: sectionStatusesFrom(snapshot), statusesLoading: !snapshot.snapshotLoaded };
}
