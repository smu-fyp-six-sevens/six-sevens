import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import { subscribeToCaseStatus } from '../services/caseSocket';
import {
  deleteExtractedField,
  addExtractedField,
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  DOCUMENT_TYPE_OPTIONS,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  TERMINAL_DOCUMENT_STATUSES,
  formatDocumentStatus,
  formatRoutingOutcome,
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
  id?: string;
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
    .map(([key, rawField]) => {
      if (
        rawField &&
        typeof rawField === 'object' &&
        'value' in rawField
      ) {
        const field = rawField as {
          id?: unknown;
          value?: unknown;
          confidence?: unknown;
        };

        return [
          key,
          {
            id: typeof field.id === 'string' ? field.id : undefined,
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

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// A field like "line_items" — an array of {qty, price, ...} objects — reads
// as unreadable JSON via valueToString. Detected separately so it can
// render as an actual table in a modal instead (see ArrayFieldModal).
function isArrayOfObjects(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.length > 0 && value.every(isPlainObject);
}

// A flat array like statement_periods (["01-Jun-2026 to 30-Jun-2026", ...])
// fails isArrayOfObjects (its entries aren't objects) and would otherwise
// fall through to valueToString's JSON.stringify — shown as a readable
// comma-separated list instead.
function isArrayOfPrimitives(value: unknown): value is (string | number | boolean)[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean')
  );
}

// An array of primitive strings that are actually "Label: Value" pairs
// packed into a single string each — e.g. funding_sources:
// ["Owner contribution: $112,000", "Term loan - OCBC Bank: $750,000", ...].
// Conceptually the same shape as budget_line_items below, just serialized
// as strings instead of a JSON object — which also lets a label repeat
// (e.g. two separate "Term loan - OCBC Bank" draws), something a plain
// object couldn't represent at all. Checked ahead of the generic
// isArrayOfPrimitives so this more specific shape gets the table treatment
// instead of one long comma-separated line.
function isArrayOfLabelValueStrings(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' && item.includes(': '))
  );
}

function labelValueStringRows(items: string[]): Record<string, unknown>[] {
  return items.map((item) => {
    const separatorIndex = item.indexOf(': ');
    return {
      label: item.slice(0, separatorIndex),
      value: item.slice(separatorIndex + 2),
    };
  });
}

// A field like "budget_line_items" — a flat {"category": "$amount", ...}
// map rather than an array at all — fails both array checks above (it isn't
// an array, so Array.isArray is false) and would otherwise fall through to
// valueToString's JSON.stringify. Non-empty check excludes {} (nothing
// meaningful to show as rows).
function isPlainKeyValueObject(value: unknown): value is Record<string, unknown> {
  return isPlainObject(value) && Object.keys(value).length > 0;
}

// Reuses the same two-column table/modal treatment as an array of objects
// (see ArrayValueCell/ArrayFieldModal) by turning each key into its own row
// — "label"/"value" as the two columns — rather than a bespoke renderer for
// what's otherwise the same "click to view details" shape.
function keyValueObjectRows(value: Record<string, unknown>): Record<string, unknown>[] {
  return Object.entries(value).map(([label, entryValue]) => ({ label, value: entryValue }));
}

// Union of every key seen across the array's objects, first-seen order —
// not just Object.keys(rows[0]), since a row here or there can legitimately
// be missing a key (e.g. a line item with no discount applied).
function arrayTableColumns(rows: Record<string, unknown>[]): string[] {
  const seen = new Set<string>();
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}

// Thousands-grouped for readability (10000000 -> "10,000,000"). Only ever
// applied to genuine JSON numbers, never numeric-looking strings — an
// account/UEN/NRIC number extracted as a string is meant to be read
// verbatim, not grouped like a monetary amount. maximumFractionDigits is
// raised well past the locale default (3) so a precise decimal value isn't
// silently rounded for display.
function formatNumberWithCommas(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 20 });
}

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return formatNumberWithCommas(value);
  if (typeof value === 'object') return valueToString(value);
  return String(value);
}

// Read-only display for a scalar field value — same as valueToString except
// it comma-groups a genuine number. Kept separate from valueToString itself
// since that one also seeds the editable Manual Review draft, where the
// comma-free raw value must round-trip back to the backend unchanged.
function formatFieldDisplayValue(value: unknown): string {
  if (typeof value === 'number') return formatNumberWithCommas(value);
  return valueToString(value);
}

// Shared by both the read-only "Extracted Information" preview and the
// editable "Manual Review" cell (see ArrayValueCell / ArrayFieldEditButton
// below) — a modal rather than a hover popover so it stays open while
// you're actually reading/editing it, and rendered via a portal so it
// isn't clipped by any ancestor's overflow:hidden.
function ArrayFieldModal({
  title,
  columns,
  rows: initialRows,
  editable,
  onClose,
  onSave,
}: {
  title: string;
  columns: string[];
  rows: Record<string, unknown>[];
  editable: boolean;
  onClose: () => void;
  onSave?: (rows: Record<string, unknown>[]) => void;
}) {
  const [rows, setRows] = useState(initialRows);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  function updateCell(rowIndex: number, column: string, value: string) {
    setRows((current) =>
      current.map((row, i) => (i === rowIndex ? { ...row, [column]: value } : row)),
    );
  }

  function removeRow(rowIndex: number) {
    setRows((current) => current.filter((_, i) => i !== rowIndex));
  }

  function addRow() {
    const blank: Record<string, unknown> = {};
    for (const column of columns) blank[column] = '';
    setRows((current) => [...current, blank]);
  }

  return createPortal(
    <div className="array-modal-backdrop" onClick={onClose}>
      <div
        className="array-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="array-modal-header">
          <h3>{title}</h3>
          <button type="button" className="array-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="array-modal-body">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{humanizeFieldKey(column)}</th>
                ))}
                {editable && <th aria-label="Row actions" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((column) => (
                    <td key={column}>
                      {editable ? (
                        <input
                          className="array-modal-input"
                          value={row[column] === null || row[column] === undefined ? '' : String(row[column])}
                          onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                        />
                      ) : (
                        formatCellValue(row[column])
                      )}
                    </td>
                  ))}
                  {editable && (
                    <td>
                      <button
                        type="button"
                        className="array-modal-remove-row"
                        aria-label="Remove row"
                        onClick={() => removeRow(rowIndex)}
                      >
                        ✕
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={columns.length + (editable ? 1 : 0)} className="array-modal-empty">
                    No rows.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {editable && (
            <button type="button" className="array-modal-add-row" onClick={addRow}>
              + Add row
            </button>
          )}
        </div>

        {editable && (
          <div className="array-modal-footer">
            <button type="button" className="array-modal-cancel" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="array-modal-save"
              onClick={() => {
                onSave?.(rows);
                onClose();
              }}
            >
              Save
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

// Read-only trigger for the "Extracted Information" overview table.
function ArrayValueCell({ label, rows }: { label: string; rows: Record<string, unknown>[] }) {
  const [open, setOpen] = useState(false);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button type="button" className="array-value-trigger" onClick={() => setOpen(true)}>
        {rows.length} item{rows.length === 1 ? '' : 's'}
        <span className="array-value-hint"> (view)</span>
      </button>
      {open && (
        <ArrayFieldModal title={label} columns={columns} rows={rows} editable={false} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

// Editable trigger for the "Manual Review" table — replaces what used to be
// a plain text input showing raw JSON.stringify(value), which was both
// unreadable and, if hand-edited as text, extremely easy to corrupt into
// invalid JSON. draftJson/onChange plug straight into the existing
// fieldDraft state (see handleFieldChange) so saving goes through the same
// path as every other field.
function ArrayFieldEditButton({
  label,
  draftJson,
  disabled,
  onChange,
}: {
  label: string;
  draftJson: string;
  disabled: boolean;
  onChange: (json: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rows = useMemo<Record<string, unknown>[]>(() => {
    try {
      const parsed: unknown = JSON.parse(draftJson);
      return isArrayOfObjects(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }, [draftJson]);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button
        type="button"
        className="array-modal-trigger-button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Edit {rows.length} item{rows.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ArrayFieldModal
          title={label}
          columns={columns}
          rows={rows}
          editable
          onClose={() => setOpen(false)}
          onSave={(newRows) => onChange(JSON.stringify(newRows))}
        />
      )}
    </>
  );
}

// Same editable modal as ArrayFieldEditButton, but for a plain key-value
// object field (see isPlainKeyValueObject) rather than an array of objects.
// Round-trips through keyValueObjectRows' {label, value} row shape for the
// shared table UI, then rebuilds a plain object on save — an array of
// {label, value} rows would silently change the field's actual shape.
// Blank labels are dropped rather than saved as a "" key, and a later
// duplicate label overwrites an earlier one, same as any object literal.
function ObjectFieldEditButton({
  label,
  draftJson,
  disabled,
  onChange,
}: {
  label: string;
  draftJson: string;
  disabled: boolean;
  onChange: (json: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rows = useMemo<Record<string, unknown>[]>(() => {
    try {
      const parsed: unknown = JSON.parse(draftJson);
      return isPlainKeyValueObject(parsed) ? keyValueObjectRows(parsed) : [];
    } catch {
      return [];
    }
  }, [draftJson]);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button
        type="button"
        className="array-modal-trigger-button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Edit {rows.length} item{rows.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ArrayFieldModal
          title={label}
          columns={columns}
          rows={rows}
          editable
          onClose={() => setOpen(false)}
          onSave={(newRows) => {
            const rebuilt: Record<string, unknown> = {};
            for (const row of newRows) {
              const rowLabel = typeof row.label === 'string' ? row.label.trim() : '';
              if (!rowLabel) continue;
              rebuilt[rowLabel] = row.value ?? '';
            }
            onChange(JSON.stringify(rebuilt));
          }}
        />
      )}
    </>
  );
}

// Same editable modal again, for an array of "Label: Value" strings (see
// isArrayOfLabelValueStrings) — rebuilds an array of strings on save, NOT
// an object, since a repeated label (e.g. two separate "Term loan - OCBC
// Bank" draws) is valid here and an object would silently collapse it to
// one entry.
function LabelValueArrayEditButton({
  label,
  draftJson,
  disabled,
  onChange,
}: {
  label: string;
  draftJson: string;
  disabled: boolean;
  onChange: (json: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rows = useMemo<Record<string, unknown>[]>(() => {
    try {
      const parsed: unknown = JSON.parse(draftJson);
      return isArrayOfLabelValueStrings(parsed) ? labelValueStringRows(parsed) : [];
    } catch {
      return [];
    }
  }, [draftJson]);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button
        type="button"
        className="array-modal-trigger-button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Edit {rows.length} item{rows.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ArrayFieldModal
          title={label}
          columns={columns}
          rows={rows}
          editable
          onClose={() => setOpen(false)}
          onSave={(newRows) => {
            const rebuilt = newRows
              .map((row) => {
                const rowLabel = typeof row.label === 'string' ? row.label.trim() : '';
                const rowValue =
                  typeof row.value === 'string' ? row.value.trim() : valueToString(row.value);
                if (!rowLabel && !rowValue) return null;
                return `${rowLabel}: ${rowValue}`;
              })
              .filter((entry): entry is string => entry !== null);
            onChange(JSON.stringify(rebuilt));
          }}
        />
      )}
    </>
  );
}

// The raw failure_reason straight from the backend is developer-facing —
// e.g. "Extraction invocation failed: Workflow 142b7edf-... invocation
// failed after 3 attempts: ... (request timed out)" — meaningful to
// whoever's debugging the pipeline, not to a loan officer trying to decide
// what to do next. Maps the handful of known message shapes (see
// orchestrator.py's _fail call sites) to a short, human title; the raw
// text is still shown underneath for anyone who needs the specifics (e.g.
// escalating to support), just de-emphasised rather than being the only
// thing on screen.
function describeFailureReason(reason: string): { title: string; detail: string } {
  const patterns: [RegExp, string][] = [
    [/^Intake Coordinator invocation failed:/, 'Classification service error'],
    [/^Re-classification invocation failed:/, 'Reclassification service error'],
    [/^Extraction invocation failed:/, 'Data extraction service error'],
    [/^Unresolved after \d+ reclassification attempt\(s\):/, 'Document type could not be confirmed'],
    [/^Extraction produced no usable text$/, 'No readable text found in this document'],
    [/^Extraction could not process this as/, "Document didn't match the expected type"],
    [/^Unhandled orchestration error:/, 'Unexpected system error'],
  ];

  for (const [pattern, title] of patterns) {
    if (pattern.test(reason)) {
      return { title, detail: reason };
    }
  }

  return { title: 'Processing failed', detail: reason };
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

  const [isAddFieldOpen, setIsAddFieldOpen] = useState(false);
  const [newFieldName, setNewFieldName] = useState('');
  const [newFieldValue, setNewFieldValue] = useState('');
  const [isAddingField, setIsAddingField] = useState(false);
  const [addFieldError, setAddFieldError] = useState<string | null>(null);

  const [deletingFieldId, setDeletingFieldId] = useState<string | null>(null);
  const [deleteFieldError, setDeleteFieldError] = useState<string | null>(null);
  
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

  const failureReason =
    document?.status === 'failed' && document.failure_reason
      ? describeFailureReason(document.failure_reason)
      : null;

  // Manual review is driven by the lowest confidence among this document's
  // own extracted fields — not a single document-wide extraction_confidence
  // number, which could still read as confident even when one specific
  // field (e.g. a garbled account number) was actually a low-confidence
  // guess. See uploadService.lowestFieldConfidence, which UploadPage.tsx's
  // flagged-documents list uses the same way for consistency.
  const fieldConfidenceValues = extractedFields
    .map(([, field]) => field.confidence)
    .filter((confidence): confidence is number => confidence != null);
  const lowestConfidence =
    fieldConfidenceValues.length > 0 ? Math.min(...fieldConfidenceValues) : null;

  const needsManualReview =
    lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD;

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

  // What the dropdown currently shows: the officer's pick, else the existing
  // type. Saving is allowed even when it's unchanged — that's how an officer
  // confirms a low-confidence classification was actually right.
  const chosenType = selectedType || document?.document_type || '';

  // Saving a corrected type re-runs extraction in the background; this page
  // only fetched once on load, so listen for the result while it's in flight.
  const caseId = document?.case_id ?? null;
  const isInFlight =
    document?.status === 'uploaded' ||
    document?.status === 'queued' ||
    document?.status === 'processing' ||
    document?.status === 'extracting';

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
    if (!document || !chosenType) {
      return;
    }

    setIsSavingType(true);
    setTypeError(null);

    try {
      const updated = await updateDocumentType(document.id, chosenType);

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
      // fieldDraft is Record<string, string> — every field, including
      // arrays/objects, was flattened via valueToString (JSON.stringify)
      // so it can live in a text input's value. Sending that string
      // straight through would store a doubly-encoded JSON *string* rather
      // than a real array, which then fails isArrayOfObjects on the next
      // load and silently loses its table/modal treatment. Restore the
      // original shape for any field that was originally an array/object
      // before sending, so a re-fetch still recognises it correctly.
      const originalValueByKey = new Map(
        extractedFields.map(([key, field]) => [key, field.value]),
      );
      const payload: Record<string, unknown> = {};
      for (const [key, draftValue] of Object.entries(fieldDraft)) {
        const original = originalValueByKey.get(key);
        if (Array.isArray(original) || isPlainObject(original)) {
          try {
            payload[key] = JSON.parse(draftValue);
            continue;
          } catch {
            // Malformed edit — fall back to the raw string rather than
            // crashing the save; the backend will just store it as text.
          }
        }
        payload[key] = draftValue;
      }

      const updated = await updateExtractedFields(
        document.id,
        payload,
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
                document.status === 'analyzed'
                  ? 'status-dot--success'
                  : document.status === 'failed'
                    ? 'status-dot--failed'
                    : ''
              }`}
            />
            {formatDocumentStatus(document.status)}
            {!TERMINAL_DOCUMENT_STATUSES.has(document.status) && '…'}
          </div>
        </div>

        <div className="details-info">
          <div>
            <span className="details-label">Document Type</span>
            {canCorrectType ? (
              <div className="details-type-editor">
                <select
                  className="details-type-select"
                  value={chosenType}
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
                  disabled={isSavingType || !chosenType}
                  onClick={() => void handleSaveType()}
                >
                  {isSavingType
                    ? 'Saving…'
                    : chosenType === document.document_type
                      ? 'Confirm & re-run extraction'
                      : 'Save & re-run extraction'}
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
              {formatRoutingOutcome(document)}
            </strong>
          </div>
        </div>

        {failureReason && (
          <div className="review-message review-message--error details-failure-reason">
            <div>
              <strong>{failureReason.title}</strong>
              <div>{failureReason.detail}</div>
              {canCorrectType && (
                <div className="details-failure-reason-hint">
                  You can correct the document type above and re-run extraction.
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {/* =====================================================
          Extracted information
          ===================================================== */}

      <section className="details-card">
        <div className="details-section-heading">
          <span className="details-section-icon">▣</span>
          <span>Extracted Information</span>

          {lowestConfidence != null && (
            <span className="extraction-confidence">
              Lowest field confidence:{' '}
              <strong>
                {lowestConfidence.toFixed(2)}
              </strong>
            </span>
          )}

          <button
            type="button"
            className="add-field-button"
            onClick={() => {
              setIsAddFieldOpen(true);
              setAddFieldError(null);
              setNewFieldName('');
              setNewFieldValue('');
            }}
          >
            + Add field
          </button>
        </div>

        {isAddFieldOpen && (
          <div className="add-field-form">
            <div className="add-field-form-row">
              <label htmlFor="new-field-name">Field name</label>
              <input
                id="new-field-name"
                type="text"
                value={newFieldName}
                onChange={(event) => setNewFieldName(event.target.value)}
                placeholder="e.g. Company Address"
                disabled={isAddingField}
              />
            </div>

            <div className="add-field-form-row">
              <label htmlFor="new-field-value">Field value</label>
              <input
                id="new-field-value"
                type="text"
                value={newFieldValue}
                onChange={(event) => setNewFieldValue(event.target.value)}
                placeholder="Enter the value"
                disabled={isAddingField}
              />
            </div>

            {addFieldError && (
              <div className="add-field-error">
                {addFieldError}
              </div>
            )}

            <div className="add-field-form-actions">
              <button
                type="button"
                onClick={() => setIsAddFieldOpen(false)}
                disabled={isAddingField}
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={async () => {
                  const fieldName = newFieldName.trim();

                  if (!fieldName) {
                    setAddFieldError('Field name cannot be empty.');
                    return;
                  }

                  if (!document) {
                    setAddFieldError('Document is not loaded.');
                    return;
                  }

                  setIsAddingField(true);
                  setAddFieldError(null);

                  try {
                    const updated = await addExtractedField(
                      document.id,
                      fieldName,
                      newFieldValue,
                    );

                    setDocument(updated);
                    setIsAddFieldOpen(false);
                    setNewFieldName('');
                    setNewFieldValue('');
                  } catch (error) {
                    setAddFieldError(
                      error instanceof Error
                        ? error.message
                        : 'Failed to add field.',
                    );
                  } finally {
                    setIsAddingField(false);
                  }
                }}
                disabled={isAddingField}
              >
                Add field
              </button>
            </div>
          </div>
        )}

        {deleteFieldError && (
          <div className="add-field-error">
            {deleteFieldError}
          </div>
        )}

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
                  {field.value === null || field.value === undefined ? (
                    '—'
                  ) : isArrayOfObjects(field.value) ? (
                    <ArrayValueCell label={humanizeFieldKey(key)} rows={field.value} />
                  ) : isArrayOfLabelValueStrings(field.value) ? (
                    <ArrayValueCell label={humanizeFieldKey(key)} rows={labelValueStringRows(field.value)} />
                  ) : isArrayOfPrimitives(field.value) ? (
                    field.value
                      .map((item) => (typeof item === 'number' ? formatNumberWithCommas(item) : String(item)))
                      .join(', ')
                  ) : isPlainKeyValueObject(field.value) ? (
                    <ArrayValueCell label={humanizeFieldKey(key)} rows={keyValueObjectRows(field.value)} />
                  ) : (
                    formatFieldDisplayValue(field.value)
                  )}
                </span>

                <span className="extracted-field-actions">
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

                  {field.id && (
                    <button
                      type="button"
                      className="delete-field-button"
                      onClick={async () => {
                        const confirmed = window.confirm(
                          'Are you sure you want to delete this field? This field will not be recoverable.',
                        );

                        if (!confirmed) {
                          return;
                        }

                        const fieldId = field.id;

                        if (!fieldId) {
                          return;
                        }

                        if (!document) {
                          setDeleteFieldError('Document is not loaded.');
                          return;
                        }

                        setDeletingFieldId(fieldId);
                        setDeleteFieldError(null);

                        try {
                          const updated = await deleteExtractedField(
                            document.id,
                            fieldId,
                          );

                          setDocument(updated);
                        } catch (error) {
                          setDeleteFieldError(
                            error instanceof Error
                              ? error.message
                              : 'Failed to delete field.',
                          );
                        } finally {
                          setDeletingFieldId(null);
                        }
                      }}
                      disabled={deletingFieldId === field.id}
                      title="Delete field"
                      aria-label={`Delete ${humanizeFieldKey(key)}`}
                    >
                      {deletingFieldId === field.id ? '…' : '×'}
                    </button>
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
            One or more extracted fields have low confidence, or are empty. Review and
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

                  {isArrayOfObjects(field.value) ? (
                    <ArrayFieldEditButton
                      label={humanizeFieldKey(key)}
                      draftJson={fieldDraft[key] ?? '[]'}
                      disabled={isSaving || !canEditField}
                      onChange={(json) => handleFieldChange(key, json)}
                    />
                  ) : isArrayOfLabelValueStrings(field.value) ? (
                    <LabelValueArrayEditButton
                      label={humanizeFieldKey(key)}
                      draftJson={fieldDraft[key] ?? '[]'}
                      disabled={isSaving || !canEditField}
                      onChange={(json) => handleFieldChange(key, json)}
                    />
                  ) : isPlainKeyValueObject(field.value) ? (
                    <ObjectFieldEditButton
                      label={humanizeFieldKey(key)}
                      draftJson={fieldDraft[key] ?? '{}'}
                      disabled={isSaving || !canEditField}
                      onChange={(json) => handleFieldChange(key, json)}
                    />
                  ) : (
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
                  )}

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