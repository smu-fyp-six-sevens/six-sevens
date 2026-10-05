import { useEffect, useState } from 'react';
import { getDocumentCaseMatch } from '../../services/uploadService';
import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';

// Whether this document belongs to the case's company and individuals (see backend/app/case_match.py).
export function useDocumentCaseMatch(documentId: string, document: DocumentRecord | null) {
  const [caseMatch, setCaseMatch] = useState<DocumentCaseMatch | null>(null);
  const hasCase = Boolean(document?.case_id);
  const documentStatus = document?.status;
  const extractedFieldsJson = JSON.stringify(document?.extracted_fields ?? null);

  useEffect(() => {
    if (!hasCase || documentStatus !== 'analyzed') {
      setCaseMatch(null);
      return;
    }

    let cancelled = false;

    getDocumentCaseMatch(documentId)
      .then((result) => {
        if (!cancelled) setCaseMatch(result);
      })
      .catch(() => {
        // Informational only — the rest of the page works without it.
        if (!cancelled) setCaseMatch(null);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, hasCase, documentStatus, extractedFieldsJson]);

  return caseMatch;
}
