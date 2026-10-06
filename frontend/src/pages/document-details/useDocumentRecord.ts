import { useEffect, useRef, useState } from 'react';
import { subscribeToCaseStatus } from '../../services/caseSocket';
import { getDocument } from '../../services/uploadService';
import type { DocumentRecord } from '../../services/uploadService';
import { buildFieldDraft } from './fieldDisplay';

// Loads the document and keeps it live while it's still in flight.
export function useDocumentRecord(documentId: string) {
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fieldDraft, setFieldDraft] = useState<Record<string, string>>({});
  // Last extracted_fields the draft was built from.
  const extractedFieldsJsonRef = useRef('');
  // Forces a re-render every so often while PROCESSING so the "Stuck" badge/Retry button can appear.
  const [, forceStuckRecheck] = useState(0);

  const caseId = document?.case_id ?? null;
  const isInFlight =
    document?.status === 'uploaded' ||
    document?.status === 'queued' ||
    document?.status === 'processing' ||
    document?.status === 'extracting';

  useEffect(() => {
    let cancelled = false;

    async function loadDocument() {
      setIsLoading(true);
      setLoadError(null);

      try {
        const result = await getDocument(documentId);

        if (cancelled) {
          return;
        }

        setDocument(result);
        extractedFieldsJsonRef.current = JSON.stringify(
          result.extracted_fields ?? null,
        );
        setFieldDraft(buildFieldDraft(result.extracted_fields));
      } catch (error) {
        if (cancelled) {
          return;
        }

        setLoadError(
          error instanceof Error
            ? error.message
            : 'Failed to load document.',
        );
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadDocument();

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    if (document?.status !== 'processing') {
      return;
    }
    const interval = window.setInterval(() => forceStuckRecheck((n) => n + 1), 15000);
    return () => window.clearInterval(interval);
  }, [document?.status]);

  useEffect(() => {
    if (!caseId || !isInFlight) {
      return;
    }

    const subscription = subscribeToCaseStatus(caseId, (snapshot) => {
      const updated = snapshot.documents.find(
        (candidate) => candidate.id === documentId,
      );

      if (!updated) {
        return;
      }

      const nextJson = JSON.stringify(updated.extracted_fields ?? null);

      if (nextJson !== extractedFieldsJsonRef.current) {
        extractedFieldsJsonRef.current = nextJson;
        setFieldDraft(buildFieldDraft(updated.extracted_fields));
      }

      setDocument(updated);
    });

    return () => subscription.unsubscribe();
  }, [caseId, isInFlight, documentId]);

  return {
    document,
    setDocument,
    isLoading,
    loadError,
    fieldDraft,
    setFieldDraft,
    extractedFieldsJsonRef,
    isInFlight,
  };
}
