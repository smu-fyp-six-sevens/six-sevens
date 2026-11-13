import { useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import ConfirmDialog from '../../components/ConfirmDialog';
import type { DocumentRecord } from '../../services/uploadService';
import type { ExtractedField } from './fieldDisplay';
import type { FieldEditing } from './useFieldEditing';
import {
  ArrayValueCell,
} from './ArrayFieldModal';
import {
  EXTRACTION_CONFIDENCE_THRESHOLD,
  addExtractedField,
  deleteExtractedField,
  hasBeenApproved,
} from '../../services/uploadService';
import { buildFieldDraft, formatFieldDisplayValue, formatNumberWithCommas, isArrayOfLabelValueStrings, isArrayOfObjects, isArrayOfPrimitives, isPlainKeyValueObject, keyValueObjectRows, labelValueStringRows } from './fieldDisplay';
import { humanizeFieldKey, normaliseFieldName } from '../../utils/fieldLabels';
import { ErrorMessage } from '../../components/ErrorMessage';
import { ArrayFieldEditButton, LabelValueArrayEditButton, ObjectFieldEditButton } from './ArrayFieldEditors';
import { Icon } from '../../components/Icon';
import { fieldReason } from './fieldReasons';

export function ExtractedInformationCard({
  canAddOrDeleteFields,
  document,
  extractedFields,
  fieldReasons,
  isInFlight,
  lowestConfidence,
  setDocument,
  setFieldDraft,
  editing,
  review,
  footer,
}: {
  // Until the document is approved or rejected, even a verified one: the officer can always add what is missing.
  canAddOrDeleteFields: boolean;
  document: DocumentRecord;
  extractedFields: [string, ExtractedField][];
  // Why extraction was unsure of a field, by field name: model output from the document, so only ever plain text.
  fieldReasons: Record<string, string>;
  isInFlight: boolean;
  lowestConfidence: number | null;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  setFieldDraft: Dispatch<SetStateAction<Record<string, string>>>;
  // The add and delete field state (useFieldEditing), passed as one object.
  editing: FieldEditing;
  // Set while the document needs the officer's review: the fields that need attention become inputs in this same
  // table, and Save fields sits under it. `extractedFields` then also holds the required fields extraction missed.
  review?: {
    canEditFields: boolean;
    fieldDraft: Record<string, string>;
    handleFieldChange: (key: string, value: string) => void;
    handleSaveFields: () => void | Promise<void>;
    isSaving: boolean;
    needsAttention: (key: string, field: ExtractedField) => boolean;
    saveError: string | null;
    saveMessage: string | null;
  };
  // Under the table, attached to this card: the approve and reject bar.
  footer?: ReactNode;
}) {
  const {
    addFieldError,
    deleteFieldError,
    deletingFieldId,
    isAddFieldOpen,
    isAddingField,
    newFieldName,
    newFieldValue,
    setAddFieldError,
    setDeleteFieldError,
    setDeletingFieldId,
    setIsAddFieldOpen,
    setIsAddingField,
    setNewFieldName,
    setNewFieldValue,
  } = editing;
  // Approved, the fields are settled and the detection report is what matters: folded up, and again when the
  // approval happens while the page is open. The officer can still open them.
  const isApproved = hasBeenApproved(document.status);
  const [isCollapsed, setIsCollapsed] = useState(isApproved);
  useEffect(() => {
    if (isApproved) setIsCollapsed(true);
  }, [isApproved]);
  // The field the officer asked to delete, until they confirm or cancel in the dialog.
  const [fieldToDelete, setFieldToDelete] = useState<{ id: string; key: string } | null>(null);

  async function confirmDeleteField() {
    if (!fieldToDelete) return;

    const { id: fieldId, key } = fieldToDelete;

    setDeletingFieldId(fieldId);
    setDeleteFieldError(null);

    try {
      const updated = await deleteExtractedField(document.id, fieldId);

      setDocument(updated);
      // Save sends the whole draft and replaces every field, so a
      // deleted key left in it would be recreated, empty, on the
      // next Save. Other unsaved edits are kept.
      setFieldDraft((current) => {
        const rest = { ...current };
        delete rest[key];
        return rest;
      });
    } catch (error) {
      setDeleteFieldError(error instanceof Error ? error.message : 'Failed to delete field.');
    } finally {
      setDeletingFieldId(null);
      setFieldToDelete(null);
    }
  }

  const attentionCount = review
    ? extractedFields.filter(([key, field]) => review.needsAttention(key, field)).length
    : 0;

  return (
        <section className={`details-card${review ? ' details-card--review' : ''}`}>
          <div className="details-section-heading">
            <span className="details-section-icon"><Icon name="file" size={20} /></span>
            <span>Extracted Information</span>

            {review && attentionCount > 0 && (
              <span className="documents-card-review-count">
                {attentionCount} {attentionCount === 1 ? 'field needs' : 'fields need'} review
              </span>
            )}

            {lowestConfidence != null && !review && (
              <span className="extraction-confidence">
                Lowest field confidence:{' '}
                <strong>
                  {lowestConfidence.toFixed(2)}
                </strong>
              </span>
            )}

            <div className="details-section-actions">
              {canAddOrDeleteFields && !isInFlight && (
                <button
                  type="button"
                  className="add-field-button"
                  onClick={() => {
                    setIsCollapsed(false);
                    setIsAddFieldOpen(true);
                    setAddFieldError(null);
                    setNewFieldName('');
                    setNewFieldValue('');
                  }}
                >
                  + Add field
                </button>
              )}

              <button
                type="button"
                className={`document-row-action-icon collapse-toggle${isCollapsed ? ' is-collapsed' : ''}`}
                aria-expanded={!isCollapsed}
                aria-label={isCollapsed ? 'Expand extracted information' : 'Collapse extracted information'}
                title={isCollapsed ? 'Expand' : 'Collapse'}
                onClick={() => setIsCollapsed((collapsed) => !collapsed)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
            </div>
          </div>

          {!isCollapsed && review && (
            <p className="review-description">
              Correct the highlighted fields and save before approving. A field you change is saved with a confidence of{' '}
              <strong>1.00</strong>; the others keep theirs.
            </p>
          )}

          {!isCollapsed && canAddOrDeleteFields && isAddFieldOpen && (
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
                {normaliseFieldName(newFieldName) && normaliseFieldName(newFieldName) !== newFieldName.trim() && (
                  <small>Saved as {normaliseFieldName(newFieldName)}</small>
                )}
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

              {addFieldError && <ErrorMessage className="add-field-error" message={addFieldError} />}

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
                    const fieldName = normaliseFieldName(newFieldName);

                    if (!fieldName) {
                      setAddFieldError('Field name cannot be empty.');
                      return;
                    }

                    if (document?.extracted_fields && fieldName in document.extracted_fields) {
                      setAddFieldError('A field with this name already exists.');
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
                      setFieldDraft(buildFieldDraft(updated.extracted_fields));
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

          {!isCollapsed && deleteFieldError && <ErrorMessage className="add-field-error" message={deleteFieldError} />}

          {isCollapsed ? null : extractedFields.length === 0 ? (
            <p className="extracted-empty">No fields have been extracted from this document.</p>
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
                  className={`extracted-table-row${review && !isInFlight && review.needsAttention(key, field) ? ' extracted-table-row--editing' : ''}`}
                >
                  <span className="field-name">
                    {humanizeFieldKey(key)}
                  </span>

                  <span className="field-value">
                    {review && !isInFlight && review.needsAttention(key, field) ? (
                      <FieldEditor fieldKey={key} field={field} review={review} />
                    ) : field.value === null || field.value === undefined ? (
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

                    {canAddOrDeleteFields && field.id && (
                      <button
                        type="button"
                        className="delete-field-button"
                        onClick={() => setFieldToDelete({ id: field.id as string, key })}
                        disabled={deletingFieldId === field.id || isInFlight}
                        title="Delete field"
                        aria-label={`Delete ${humanizeFieldKey(key)}`}
                      >
                        {deletingFieldId === field.id ? '…' : '×'}
                      </button>
                    )}
                  </span>

                  <FieldReason reason={fieldReason(fieldReasons, key, field.confidence)} />
                </div>
              ))}
            </div>
          )}

          {!isCollapsed && review && (
            <div className="manual-review-actions">
              <div className="manual-review-message">
                {review.saveMessage && <span className="save-success">✓ {review.saveMessage}</span>}

                {review.saveError && <ErrorMessage inline message={review.saveError} />}
              </div>

              {review.canEditFields && (
                <button
                  type="button"
                  className="details-save-button"
                  disabled={review.isSaving || isInFlight}
                  onClick={() => void review.handleSaveFields()}
                >
                  {review.isSaving ? 'Saving…' : 'Save fields'}
                </button>
              )}
            </div>
          )}

          {footer}

          {fieldToDelete && (
            <ConfirmDialog
              title="Delete this field?"
              message={`${humanizeFieldKey(fieldToDelete.key)} will be removed from this document and cannot be recovered.`}
              isConfirming={deletingFieldId === fieldToDelete.id}
              onCancel={() => {
                if (deletingFieldId === null) setFieldToDelete(null);
              }}
              onConfirm={() => void confirmDeleteField()}
            />
          )}
        </section>
  );
}

function FieldReason({ reason }: { reason: string | null }) {
  return reason ? <p className="extracted-field-reason">{reason}</p> : null;
}

// A field's value as an input (or the editor button for a list or object), while the document needs review.
function FieldEditor({
  fieldKey,
  field,
  review,
}: {
  fieldKey: string;
  field: ExtractedField;
  review: NonNullable<Parameters<typeof ExtractedInformationCard>[0]['review']>;
}) {
  const { fieldDraft, handleFieldChange, isSaving } = review;
  const label = humanizeFieldKey(fieldKey);

  if (isArrayOfObjects(field.value)) {
    return (
      <ArrayFieldEditButton
        label={label}
        draftJson={fieldDraft[fieldKey] ?? '[]'}
        disabled={isSaving}
        onChange={(json) => handleFieldChange(fieldKey, json)}
      />
    );
  }
  if (isArrayOfLabelValueStrings(field.value)) {
    return (
      <LabelValueArrayEditButton
        label={label}
        draftJson={fieldDraft[fieldKey] ?? '[]'}
        disabled={isSaving}
        onChange={(json) => handleFieldChange(fieldKey, json)}
      />
    );
  }
  if (isPlainKeyValueObject(field.value)) {
    return (
      <ObjectFieldEditButton
        label={label}
        draftJson={fieldDraft[fieldKey] ?? '{}'}
        disabled={isSaving}
        onChange={(json) => handleFieldChange(fieldKey, json)}
      />
    );
  }
  return (
    <input
      type="text"
      className={`manual-review-input${!fieldDraft[fieldKey]?.trim() ? ' manual-review-input--empty' : ''}`}
      aria-label={label}
      value={fieldDraft[fieldKey] ?? ''}
      disabled={isSaving}
      onChange={(event) => handleFieldChange(fieldKey, event.target.value)}
    />
  );
}
