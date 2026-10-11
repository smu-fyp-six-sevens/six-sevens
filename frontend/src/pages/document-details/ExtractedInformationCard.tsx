import { useState, type Dispatch, type SetStateAction } from 'react';
import type { DocumentRecord } from '../../services/uploadService';
import type { ExtractedField } from './fieldDisplay';
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
  addFieldError,
  canEditFields,
  deleteFieldError,
  deletingFieldId,
  document,
  extractedFields,
  isAddFieldOpen,
  isAddingField,
  isInFlight,
  lowestConfidence,
  newFieldName,
  newFieldValue,
  setAddFieldError,
  setDeleteFieldError,
  setDeletingFieldId,
  setDocument,
  setFieldDraft,
  setIsAddFieldOpen,
  setIsAddingField,
  setNewFieldName,
  setNewFieldValue,
}: {
  addFieldError: string | null;
  canEditFields: boolean;
  deleteFieldError: string | null;
  deletingFieldId: string | null;
  document: DocumentRecord;
  extractedFields: [string, ExtractedField][];
  isAddFieldOpen: boolean;
  isAddingField: boolean;
  isInFlight: boolean;
  lowestConfidence: number | null;
  newFieldName: string;
  newFieldValue: string;
  setAddFieldError: Dispatch<SetStateAction<string | null>>;
  setDeleteFieldError: Dispatch<SetStateAction<string | null>>;
  setDeletingFieldId: Dispatch<SetStateAction<string | null>>;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  setFieldDraft: Dispatch<SetStateAction<Record<string, string>>>;
  setIsAddFieldOpen: Dispatch<SetStateAction<boolean>>;
  setIsAddingField: Dispatch<SetStateAction<boolean>>;
  setNewFieldName: Dispatch<SetStateAction<string>>;
  setNewFieldValue: Dispatch<SetStateAction<string>>;
}) {
  const [isCollapsed, setIsCollapsed] = useState(false);

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
              {canEditFields && !isInFlight && (
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

          {!isCollapsed && canEditFields && isAddFieldOpen && (
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

                    {canEditFields && field.id && (
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
                            // Save sends the whole draft and replaces every field, so a
                            // deleted key left in it would be recreated, empty, on the
                            // next Save. Other unsaved edits are kept.
                            setFieldDraft((current) => {
                              const rest = { ...current };
                              delete rest[key];
                              return rest;
                            });
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
        </section>
  );
}
