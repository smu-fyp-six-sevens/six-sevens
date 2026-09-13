import { useEffect, useMemo, useState } from 'react';
import type { CaseRecord } from '../App';
import {
  EXTRACTION_CONFIDENCE_THRESHOLD,
  getDocument,
  updateExtractedFields,
} from '../services/uploadService';
import type { DocumentRecord } from '../services/uploadService';

interface DocumentDetailsPageProps {
  selectedCase: CaseRecord;
  documentId: string;
  onBack: () => void;
}

const NON_DISPLAYED_FIELD_KEYS = new Set(['transactions']);

const FIELD_KEY_ACRONYMS = new Set([
  'id',
  'uen',
  'tin',
  'nric',
]);

function humanizeFieldKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) =>
      FIELD_KEY_ACRONYMS.has(word.toLowerCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

function displayableFieldEntries(
  fields: Record<string, unknown> | null,
): [string, unknown][] {
  if (!fields) {
    return [];
  }

  return Object.entries(fields).filter(
    ([key]) => !NON_DISPLAYED_FIELD_KEYS.has(key),
  );
}

function valueToString(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }

  if (typeof value === 'object') {
    return JSON.stringify(value);
  }

  return String(value);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString('en-SG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export default function DocumentDetailsPage({
  selectedCase,
  documentId,
  onBack,
}: DocumentDetailsPageProps) {
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [fieldDraft, setFieldDraft] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

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

        const nextDraft: Record<string, string> = {};

        for (const [key, value] of displayableFieldEntries(
          result.extracted_fields,
        )) {
          nextDraft[key] = valueToString(value);
        }

        setFieldDraft(nextDraft);
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

  const extractedFields = useMemo(
    () => displayableFieldEntries(document?.extracted_fields ?? null),
    [document?.extracted_fields],
  );

  const needsManualReview =
    document?.extraction_confidence != null &&
    document.extraction_confidence < EXTRACTION_CONFIDENCE_THRESHOLD;

  function handleFieldChange(key: string, value: string) {
    setFieldDraft((current) => ({
      ...current,
      [key]: value,
    }));

    setSaveMessage(null);
    setSaveError(null);
  }

  async function handleSaveFields() {
    if (!document) {
        return;
    }

    // Make sure every extracted field has a value before saving.
    const hasEmptyField = extractedFields.some(([key]) => {
        const value = fieldDraft[key]?.trim();

        return !value;
    });

    if (hasEmptyField) {
        setSaveMessage(null);
        setSaveError(
        'Please complete all fields before saving.',
        );
        return;
    }

    setIsSaving(true);
    setSaveMessage(null);
    setSaveError(null);

    try {
        const updated = await updateExtractedFields(
        document.id,
        fieldDraft,
        );

        setDocument(updated);

        const nextDraft: Record<string, string> = {};

        for (const [key, value] of displayableFieldEntries(
        updated.extracted_fields,
        )) {
        nextDraft[key] = valueToString(value);
        }

        setFieldDraft(nextDraft);

        setSaveMessage(
        'Changes saved. The extracted fields are now verified and can no longer be edited.',
        );
    } catch (error) {
        setSaveError(
        error instanceof Error
            ? error.message
            : 'Failed to save extracted fields.',
        );
    } finally {
        setIsSaving(false);
    }
    }

  if (isLoading) {
    return (
      <div className="shell shell--upload">
        <header className="app-header">
          <div className="brand">GLDB Fraud Review</div>
          <div className="officer-tag">Loan Officer · J. Tan</div>
        </header>

        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case
        </button>

        <div className="document-details-loading">
          Loading document details…
        </div>
      </div>
    );
  }

  if (loadError || !document) {
    return (
      <div className="shell shell--upload">
        <header className="app-header">
          <div className="brand">GLDB Fraud Review</div>
          <div className="officer-tag">Loan Officer · J. Tan</div>
        </header>

        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case
        </button>

        <div className="document-details-error">
          {loadError ?? 'Document could not be found.'}
        </div>
      </div>
    );
  }

  return (
    <div className="shell shell--upload document-details-page">
      <header className="app-header">
        <div className="brand">GLDB Fraud Review</div>
        <div className="officer-tag">Loan Officer · J. Tan</div>
      </header>

      <button type="button" className="back-link" onClick={onBack}>
        ← Back to case
      </button>

      <div className="eyebrow">
        Case #{selectedCase.caseNumber} · Document · {document.filename}
      </div>

      <div className="document-details-intro">
        <h1>Document Details</h1>
        <p className="sub">
          View extracted data and review any fields that require
          manual correction.
        </p>
      </div>

      {/* =====================================================
          Document summary
          ===================================================== */}

      <section className="details-card">
        <div className="document-header">
          <div className="document-header-main">
            <h2>{document.filename}</h2>

            <div className="document-meta">
              <span>
                Uploaded on {formatDate(document.uploaded_at)}
              </span>

              <span>
                Document ID: {document.id}
              </span>
            </div>
          </div>

          <div
            className={`document-status ${
              document.status === 'analyzed'
                ? 'document-status--success'
                : ''
            }`}
          >
            <span className="status-dot" />
            {document.status}
          </div>
        </div>

        <div className="details-info">
          <div>
            <span className="details-label">Document Type</span>
            <strong>
              {document.document_type ?? '—'}
            </strong>
          </div>

          <div>
            <span className="details-label">Classification Confidence</span>
            <strong>
              {document.confidence != null
                ? document.confidence.toFixed(2)
                : '—'}
            </strong>
          </div>

          <div>
            <span className="details-label">Routing Outcome</span>
            <strong>
              {document.classification_outcome ?? '—'}
            </strong>
          </div>
        </div>
      </section>

      {/* =====================================================
          Extracted information
          ===================================================== */}

      <section className="details-card">
        <div className="details-section-heading">
          <span className="details-section-icon">▣</span>
          <span>Extracted Information</span>

          {document.extraction_confidence != null && (
            <span className="extraction-confidence">
              Extraction confidence:{' '}
              <strong>
                {document.extraction_confidence.toFixed(2)}
              </strong>
            </span>
          )}
        </div>

        {extractedFields.length === 0 ? (
          <div className="review-message">
            <div>
              <strong>No extracted fields available.</strong>
              <div>
                There is currently no extracted data available for
                this document.
              </div>
            </div>
          </div>
        ) : (
          <div className="extracted-table">
            <div className="extracted-table-header">
              <span>Field</span>
              <span>Extracted Value</span>
              <span>Confidence</span>
            </div>

            {extractedFields.map(([key, value]) => (
              <div
                key={key}
                className="extracted-table-row"
              >
                <span className="field-name">
                  {humanizeFieldKey(key)}
                </span>

                <span className="field-value">
                  {value === null || value === undefined
                    ? '—'
                    : valueToString(value)}
                </span>

                <span>
                  {document.extraction_confidence != null ? (
                    <span className="confidence-badge">
                      {document.extraction_confidence.toFixed(2)}
                    </span>
                  ) : (
                    '—'
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* =====================================================
          Manual review
          ===================================================== */}

      {needsManualReview && (
        <section className="details-card details-card--review">
          <div className="details-section-heading details-section-heading--warning">
            <span className="details-section-icon">⚠</span>
            <span>Flagged Items for Manual Review</span>
          </div>

          <p className="review-description">
            This document has low extraction confidence. Review and
            correct the extracted fields below before approving the
            document.
          </p>

           <div className="manual-review-warning">
            <span className="manual-review-warning-icon">⚠</span>

            <div>
                <strong>Important: Review before saving</strong>

                <p>
                All fields below will be saved together. Once saved,
                the extracted data will be marked as verified with a
                confidence score of <strong>1.00</strong> and will no
                longer be editable.
                </p>

                <p>
                Please make sure all required fields are completed
                before saving.
                </p>
            </div>
            </div>

          <div className="manual-review-table">
            <div className="manual-review-header">
              <span>Field</span>
              <span>Extracted Value</span>
              <span>Confidence</span>
            </div>

            {extractedFields.map(([key]) => (
              <div
                key={key}
                className="manual-review-row"
              >
                <span className="manual-review-field">
                  {humanizeFieldKey(key)}
                </span>

                <input
                    type="text"
                    className={`manual-review-input ${
                        !fieldDraft[key]?.trim()
                        ? 'manual-review-input--empty'
                        : ''
                    }`}
                    value={fieldDraft[key] ?? ''}
                    disabled={isSaving}
                    onChange={(event) =>
                        handleFieldChange(
                        key,
                        event.target.value,
                        )
                    }
                    />

                <span>
                  {document.extraction_confidence != null ? (
                    <span className="confidence-badge confidence-badge--warning">
                      {document.extraction_confidence.toFixed(2)}
                    </span>
                  ) : (
                    '—'
                  )}
                </span>
              </div>
            ))}
          </div>

          <div className="manual-review-actions">
            <div className="manual-review-message">
              {saveMessage && (
                <span className="save-success">
                  ✓ {saveMessage}
                </span>
              )}

              {saveError && (
                <span className="save-error">
                  {saveError}
                </span>
              )}
            </div>

            <button
              type="button"
              className="details-save-button"
              disabled={isSaving}
              onClick={() => void handleSaveFields()}
            >
              {isSaving ? 'Saving…' : 'Save fields'}
            </button>
          </div>
        </section>
      )}

      {/* =====================================================
          Document actions
          ===================================================== */}

      <div className="document-actions">
        <button
          type="button"
          className="document-action--reject"
        >
          ✕ Reject Document
        </button>

        <button
          type="button"
          className="document-action--approve"
        >
          ✓ Approve Document
        </button>
      </div>
    </div>
  );
}