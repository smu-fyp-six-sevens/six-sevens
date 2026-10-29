import type { ExtractedField } from './fieldDisplay';
import {
  ArrayFieldEditButton,
  LabelValueArrayEditButton,
  ObjectFieldEditButton,
} from './ArrayFieldEditors';
import {
  EXTRACTION_CONFIDENCE_THRESHOLD,
} from '../../services/uploadService';
import { isArrayOfLabelValueStrings, isArrayOfObjects, isPlainKeyValueObject } from './fieldDisplay';
import { humanizeFieldKey } from '../../utils/fieldLabels';
import { ErrorMessage } from '../../components/ErrorMessage';

export function ManualReviewCard({
  canEditFields,
  fieldDraft,
  handleFieldChange,
  handleSaveFields,
  isInFlight,
  isSaving,
  needsAttention,
  reviewFields,
  saveError,
  saveMessage,
}: {
  canEditFields: boolean;
  fieldDraft: Record<string, string>;
  handleFieldChange: (key: string, value: string) => void;
  handleSaveFields: () => void | Promise<void>;
  isInFlight: boolean;
  isSaving: boolean;
  needsAttention: (key: string, field: ExtractedField) => boolean;
  reviewFields: [string, ExtractedField][];
  saveError: string | null;
  saveMessage: string | null;
}) {
  return (
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

              {reviewFields.map(([key, field]) => {
                const canEditField = !isInFlight && needsAttention(key, field);

                return (
                  <div
                    key={key}
                    className={`manual-review-row${
                      canEditField ? ' manual-review-row--needs-attention' : ''
                    }`}
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

                {saveError && <ErrorMessage inline message={saveError} />}
              </div>

            {canEditFields && (
              <button
                type="button"
                className="details-save-button"
                disabled={isSaving || isInFlight}
                onClick={() => void handleSaveFields()}
              >
                {isSaving ? 'Saving…' : 'Save fields'}
              </button>
            )}
            </div>
          </section>
  );
}
