import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import { subscribeToCaseStatus } from '../services/caseSocket';
import {
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  DOCUMENT_TYPE_OPTIONS,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  formatClassificationOutcome,
  getDocument,
  updateDocumentType,
  updateExtractedFields,
} from '../services/uploadService';
import type { DocumentRecord } from '../services/uploadService';

interface DocumentDetailsPageProps {
  selectedCase: CaseRecord;
  documentId: string;
  onBack: () => void;
}

const formatDocumentType = (documentType?: string | null) => {
  if (!documentType) return "—";

  const formatted = documentType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

  return formatted.replace(/\bId\b/g, "ID");
};
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

interface ExtractedField {
  value: unknown;
  confidence: number | null;
}

function displayableFieldEntries(
  fields: Record<string, unknown> | null,
): [string, ExtractedField][] {
  if (!fields) {
    return [];
  }

  return Object.entries(fields)
    .filter(([key]) => !NON_DISPLAYED_FIELD_KEYS.has(key))
    .map(([key, rawField]) => {
      if (
        rawField &&
        typeof rawField === 'object' &&
        'value' in rawField
      ) {
        const field = rawField as {
          value?: unknown;
          confidence?: unknown;
        };

        return [
          key,
          {
            value: field.value ?? null,
            confidence:
              typeof field.confidence === 'number'
                ? field.confidence
                : null,
          },
        ];
      }

      // Fallback for older/simple extracted-field data.
      return [
        key,
        {
          value: rawField,
          confidence: null,
        },
      ];
    });
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

function buildFieldDraft(
  fields: Record<string, unknown> | null,
): Record<string, string> {
  const draft: Record<string, string> = {};

  for (const [key, field] of displayableFieldEntries(fields)) {
    draft[key] = valueToString(field.value);
  }

  return draft;
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

  const [selectedType, setSelectedType] = useState('');
  const [isSavingType, setIsSavingType] = useState(false);
  const [typeError, setTypeError] = useState<string | null>(null);

  // Last extracted_fields the draft was built from — lets a live update reset
  // the draft only when the fields really changed (e.g. a rerun finishing),
  // not wipe an officer's in-progress edits on every unrelated case update.
  const extractedFieldsJsonRef = useRef('');

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

  const extractedFields = useMemo(
    () => displayableFieldEntries(document?.extracted_fields ?? null),
    [document?.extracted_fields],
  );

  const isUnrecognisedDocument =
    !document?.document_type ||
    document.document_type.toLowerCase() === 'unrecognised';

  const needsManualReview =
  (document?.extraction_confidence != null &&
    document.extraction_confidence < EXTRACTION_CONFIDENCE_THRESHOLD) ||
  extractedFields.some(
    ([, field]) =>
      field.confidence != null &&
      field.confidence < EXTRACTION_CONFIDENCE_THRESHOLD,
  );

  // The type can only be corrected once the pipeline has finished with the
  // document, and only while classification confidence is still below the
  // threshold — the backend enforces both. Saving sets confidence to 1.0, so
  // the dropdown goes away afterwards.
  const canCorrectType =
    (document?.status === 'analyzed' || document?.status === 'failed') &&
    !(
      document.confidence != null &&
      document.confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD
    );

  // Saving a corrected type re-runs extraction in the background; this page
  // only fetched once on load, so listen for the result while it's in flight.
  const caseId = document?.case_id ?? null;
  const isInFlight =
    document?.status === 'uploaded' ||
    document?.status === 'queued' ||
    document?.status === 'processing';

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

  async function handleSaveType() {
    if (!document || !selectedType) {
      return;
    }

    setIsSavingType(true);
    setTypeError(null);

    try {
      const updated = await updateDocumentType(document.id, selectedType);

      extractedFieldsJsonRef.current = JSON.stringify(
        updated.extracted_fields ?? null,
      );
      setDocument(updated);
      setFieldDraft(buildFieldDraft(updated.extracted_fields));
      setSelectedType('');
      setSaveMessage(null);
      setSaveError(null);
    } catch (error) {
      setTypeError(
        error instanceof Error
          ? error.message
          : 'Failed to update the document type.',
      );
    } finally {
      setIsSavingType(false);
    }
  }

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
      setSaveError('Please complete all fields before saving.');
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

      for (const [key, field] of displayableFieldEntries(
        updated.extracted_fields,
      )) {
        nextDraft[key] = valueToString(field.value);
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
        <AppHeader />

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
        <AppHeader />

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
      <AppHeader />

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
                : document.status === 'failed'
                  ? 'document-status--failed'
                  : ''
            }`}
          >
            <span
              className={`status-dot ${
                document.status === 'failed'
                  ? 'status-dot--failed'
                  : ''
              }`}
            />
            {document.status}
          </div>
        </div>

        <div className="details-info">
          <div>
            <span className="details-label">Document Type</span>
            {canCorrectType ? (
              <div className="details-type-editor">
                <select
                  className="details-type-select"
                  value={selectedType || document.document_type || ''}
                  disabled={isSavingType}
                  onChange={(event) => {
                    setSelectedType(event.target.value);
                    setTypeError(null);
                  }}
                >
                  {!document.document_type && (
                    <option value="" disabled>
                      Select a type…
                    </option>
                  )}
                  {DOCUMENT_TYPE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  className="details-save-button"
                  disabled={
                    isSavingType ||
                    !selectedType ||
                    selectedType === document.document_type
                  }
                  onClick={() => void handleSaveType()}
                >
                  {isSavingType ? 'Saving…' : 'Save & re-run extraction'}
                </button>

                {typeError && (
                  <span className="save-error">{typeError}</span>
                )}
              </div>
            ) : (
              <strong>
                {formatDocumentType(document.document_type)}
              </strong>
            )}
          </div>

          <div>
            <span className="details-label">
              Classification Confidence
            </span>
            <strong>
              {document.confidence != null
                ? document.confidence.toFixed(2)
                : '—'}
            </strong>
          </div>

          <div>
            <span className="details-label">Routing Outcome</span>
            <strong>
              {formatClassificationOutcome(document.classification_outcome)}
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
          <div className="review-message review-message--error">
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

            {extractedFields.map(([key, field]) => (
              <div
                key={key}
                className="extracted-table-row"
              >
                <span className="field-name">
                  {humanizeFieldKey(key)}
                </span>

                <span className="field-value">
                  {field.value === null || field.value === undefined
                    ? '—'
                    : valueToString(field.value)}
                </span>

                <span>
                  {field.confidence != null ? (
                    <span
                      className={
                        field.confidence < EXTRACTION_CONFIDENCE_THRESHOLD
                          ? 'confidence-badge confidence-badge--warning'
                          : 'confidence-badge'
                      }
                    >
                      {field.confidence.toFixed(2)}
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

      {(needsManualReview || isUnrecognisedDocument) && (
        <section className="details-card details-card--review">
          <div className="details-section-heading details-section-heading--warning">
            <span className="details-section-icon">⚠</span>
            <span>Flagged Items for Manual Review</span>
          </div>

          <p className="review-description">
            This document has low extraction confidence or empty fields. Review and
            correct the extracted fields below before approving the
            document.
          </p>

          <div className="manual-review-warning">
            <span className="manual-review-warning-icon">⚠</span>

            <div className="manual-review-warning-content">
              <strong className="manual-review-warning-title">
                Important: Review before saving
              </strong>

              <p>
                All fields below will be saved together. Fields you manually
                change will receive a confidence score of <strong>1.00</strong>.
                Unchanged fields will keep their original confidence score.
              </p>

              <p className="manual-review-warning-note">
                Please make sure all required fields are completed before saving.
              </p>
            </div>
          </div>

          <div className="manual-review-table">
            <div className="manual-review-header">
              <span>Field</span>
              <span>Extracted Value</span>
              <span>Confidence</span>
            </div>

            {extractedFields.map(([key, field]) => {
              const isEmptyField =
                field.value == null ||
                String(field.value).trim() === '' ||
                String(field.value).trim() === '-';

              const canEditField =
                isUnrecognisedDocument ||
                isEmptyField ||
                (field.confidence != null &&
                  field.confidence < EXTRACTION_CONFIDENCE_THRESHOLD);

              return (
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
                    disabled={isSaving || !canEditField}
                    onChange={(event) =>
                      handleFieldChange(
                        key,
                        event.target.value,
                      )
                    }
                  />

                  <span>
                    {field.confidence != null ? (
                      <span
                        className={
                          field.confidence <
                          EXTRACTION_CONFIDENCE_THRESHOLD
                            ? 'confidence-badge confidence-badge--warning'
                            : 'confidence-badge'
                        }
                      >
                        {field.confidence.toFixed(2)}
                      </span>
                    ) : (
                      '—'
                    )}
                  </span>
                </div>
              );
            })}
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