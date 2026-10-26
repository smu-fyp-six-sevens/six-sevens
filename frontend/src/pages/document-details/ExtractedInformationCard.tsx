import { useState, type Dispatch, type SetStateAction } from 'react';
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
} from '../../services/uploadService';
import { buildFieldDraft, formatFieldDisplayValue, formatNumberWithCommas, isArrayOfLabelValueStrings, isArrayOfObjects, isArrayOfPrimitives, isPlainKeyValueObject, keyValueObjectRows, labelValueStringRows } from './fieldDisplay';
import { humanizeFieldKey, normaliseFieldName } from '../../utils/fieldLabels';

export function ExtractedInformationCard({
  canAddOrDeleteFields,
  document,
  extractedFields,
  isInFlight,
  lowestConfidence,
  setDocument,
  setFieldDraft,
  editing,
}: {
  // Until the document is approved or rejected, even a verified one: the officer can always add what is missing.
  canAddOrDeleteFields: boolean;
  document: DocumentRecord;
  extractedFields: [string, ExtractedField][];
  isInFlight: boolean;
  lowestConfidence: number | null;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  setFieldDraft: Dispatch<SetStateAction<Record<string, string>>>;
  // The add and delete field state (useFieldEditing), passed as one object.
  editing: FieldEditing;
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
  const [isCollapsed, setIsCollapsed] = useState(false);
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

  return (
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
                <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
            </div>
          </div>

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

          {!isCollapsed && deleteFieldError && (
            <div className="add-field-error">
              {deleteFieldError}
            </div>
          )}

          {isCollapsed ? null : extractedFields.length === 0 ? (
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
                </div>
              ))}
            </div>
          )}

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
