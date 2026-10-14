import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { subscribeToCaseStatus } from '../../services/caseSocket';
import type { CaseStatusSubscription } from '../../services/caseSocket';
import {
  EXTRACTION_CONFIDENCE_THRESHOLD,
  TERMINAL_DOCUMENT_STATUSES,
  hasBeenApproved,
  hasExtractedInformation,
  isClassificationConfirmed,
  lowestFieldConfidence,
} from '../../services/uploadService';
import type {
  CaseRisk,
  DocumentCaseMatch,
  DocumentRecord,
  SupportingDocumentRecommendation,
} from '../../services/uploadService';
import { acceptedIdentityNames } from './uploadHelpers';

// Live case state from the case WebSocket, plus the lists derived from it.
export function useCaseSnapshot(caseId: string) {
  const [documentDetails, setDocumentDetails] = useState<Map<string, DocumentRecord>>(new Map());
  const [fulfilledRequirements, setFulfilledRequirements] = useState<Set<string>>(new Set());
  const [proofOfAddressCoverage, setProofOfAddressCoverage] = useState<{
    covered: number;
    total: number;
    unmatched_names: string[];
  }>({ covered: 0, total: 0, unmatched_names: [] });
  const [idDocumentCoverage, setIdDocumentCoverage] = useState<{
    covered: number;
    total: number;
    missing_side: string | null;
  }>({ covered: 0, total: 0, missing_side: null });
  const [risk, setRisk] = useState<CaseRisk | null>(null);
  const [supportingDocumentRecommendations, setSupportingDocumentRecommendations] = useState<
    SupportingDocumentRecommendation[]
  >([]);
  const [wsConnected, setWsConnected] = useState(false);
  // Case-match result per document (see backend/app/case_match.py), pushed with every case snapshot.
  const [caseMatches, setCaseMatches] = useState<Map<string, DocumentCaseMatch>>(new Map());
  // Forces a re-render every so often while any document is PROCESSING, so a "Stuck"/Retry-eligible.
  const [, setStuckTick] = useState(0);

  const isMountedRef = useRef(true);
  // One WebSocket per case (see caseSocket.ts / backend routers/ws.py) replaces the old per-document.
  const caseSocketRef = useRef<CaseStatusSubscription | null>(null);

  // A case-match flag needs manual review until an officer approves the document.
  const caseMatchNeedsReview = useCallback(
    (document: DocumentRecord) =>
      !hasBeenApproved(document.status) &&
      caseMatches.get(document.id)?.status === 'manual_review',
    [caseMatches],
  );

  const flaggedDocuments = useMemo(() => Array.from(documentDetails.values()).filter((document) => {
    // Rejecting a document is the officer's final decision, so it's no longer waiting on review.
    if (document.status === 'rejected') return false;
    const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
    return (
      document.routing_status === 'manual_review' ||
      (document.status === 'analyzed' && !isClassificationConfirmed(document)) ||
      (document.status === 'analyzed' && !hasExtractedInformation(document.extracted_fields)) ||
      caseMatchNeedsReview(document) ||
      (lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD) ||
      (document.missing_required_fields?.length ?? 0) > 0
    );
  }), [documentDetails, caseMatchNeedsReview]);

  const identityNames = useMemo(
    () => acceptedIdentityNames(Array.from(documentDetails.values())),
    [documentDetails],
  );

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setWsConnected(false);

    const subscription = subscribeToCaseStatus(
      caseId,
      ({ documents, caseSummary, caseMatch }) => {
        if (!isMountedRef.current) return;

        setDocumentDetails(new Map(documents.map((document) => [document.id, document])));
        setCaseMatches(
          new Map((caseMatch?.documents ?? []).map((match) => [match.document_id, match])),
        );
        setFulfilledRequirements(new Set(caseSummary.fulfilled_requirements));
        setProofOfAddressCoverage(caseSummary.proof_of_address_coverage);
        setIdDocumentCoverage(caseSummary.id_document_coverage);
        setRisk(caseSummary.risk ?? null);
        setSupportingDocumentRecommendations(caseSummary.supporting_document_recommendations ?? []);
      },
      (connected) => {
        if (isMountedRef.current) setWsConnected(connected);
      },
    );
    caseSocketRef.current = subscription;

    return () => {
      caseSocketRef.current = null;
      subscription.unsubscribe();
    };
  }, [caseId]);

  const documents = useMemo(
    () =>
      Array.from(documentDetails.values()).sort((a, b) =>
        a.uploaded_at.localeCompare(b.uploaded_at),
      ),
    [documentDetails],
  );

  const anyDocumentProcessing = useMemo(
    () => documents.some((document) => document.status === 'processing'),
    [documents],
  );
  // Mirrors routers/cases.py's delete_case guard: blocked while any document is still in flight.
  const caseHasInFlightDocument = useMemo(
    () => documents.some((document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status)),
    [documents],
  );

  useEffect(() => {
    if (!anyDocumentProcessing) {
      return;
    }
    const interval = window.setInterval(() => setStuckTick((n) => n + 1), 15000);
    return () => window.clearInterval(interval);
  }, [anyDocumentProcessing]);

  // "Approved" here means the whole post-approval family (approved/detecting/detected) — a
  // document doesn't move back to the pre-approval table once the Detection Agent picks it up.
  const extractedDocuments = useMemo(
    () => documents.filter((document) => !hasBeenApproved(document.status)),
    [documents],
  );

  const approvedDocuments = useMemo(
    () => documents.filter((document) => hasBeenApproved(document.status)),
    [documents],
  );

  return {
    documentDetails,
    setDocumentDetails,
    fulfilledRequirements,
    proofOfAddressCoverage,
    idDocumentCoverage,
    risk,
    supportingDocumentRecommendations,
    wsConnected,
    caseMatches,
    setCaseMatches,
    isMountedRef,
    caseSocketRef,
    documents,
    anyDocumentProcessing,
    caseHasInFlightDocument,
    extractedDocuments,
    approvedDocuments,
    flaggedDocuments,
    identityNames,
    caseMatchNeedsReview,
  };
}
