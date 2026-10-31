import { useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import KebabMenu from './KebabMenu';
import { updateCase } from '../services/uploadService';
import { MAX_COMPANY_NAME_LENGTH, validateCompanyName } from '../utils/caseValidation';
import { CharacterCount } from './CharacterCount';
import { ErrorMessage } from './ErrorMessage';

interface CaseRowProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
  onDelete: (caseRecord: CaseRecord) => void;
  onCaseUpdated: (caseId: string, companyName: string) => void;
  isDeleting: boolean;
}

function formatCreationDate(caseRecord: CaseRecord): string {
  const createdAt = caseRecord.created_at;

  if (!createdAt) {
    return '—';
  }

  const date = new Date(createdAt);

  if (Number.isNaN(date.getTime())) {
    return '—';
  }

  return date.toLocaleDateString('en-SG', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

export default function CaseRow({
  caseRecord,
  onOpen,
  onDelete,
  onCaseUpdated,
  isDeleting,
}: CaseRowProps) {
  const caseLabel = caseRecord.caseNumber || caseRecord.id;
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(caseRecord.applicant);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(caseRecord.applicant);
  }, [caseRecord.applicant]);

  function startEditing() {
    setDraft(caseRecord.applicant);
    setSaveError(null);
    setIsEditing(true);
  }

  function cancelEditing() {
    setDraft(caseRecord.applicant);
    setSaveError(null);
    setIsEditing(false);
  }

  async function handleSave() {
    const validationError = validateCompanyName(draft);
    if (validationError) {
      setSaveError(validationError);
      return;
    }

    const trimmed = draft.trim();
    setIsSaving(true);
    setSaveError(null);

    try {
      const updated = await updateCase(caseRecord.id, trimmed);
      onCaseUpdated(caseRecord.id, updated.company_name ?? trimmed);
      setIsEditing(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save case.');
    } finally {
      setIsSaving(false);
    }
  }

  if (isEditing) {
    return (
      <li className="case-row case-row--editing">
        <span className="case-row-id">{caseLabel}</span>

        <span className="case-row-edit">
          <input
            type="text"
            className="case-name-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={MAX_COMPANY_NAME_LENGTH}
            disabled={isSaving}
            aria-label="Company name"
            aria-invalid={saveError ? true : undefined}
            autoFocus
          />
          <CharacterCount length={draft.length} />
          {saveError && <ErrorMessage inline message={saveError} />}
        </span>

        <span className="case-edit-actions add-field-form-actions">
          <button type="button" onClick={cancelEditing} disabled={isSaving}>
            Cancel
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={isSaving}>
            {isSaving ? 'Saving…' : 'Save'}
          </button>
        </span>
      </li>
    );
  }

  return (
    <li className="case-row">
      <button
        type="button"
        className="case-row-open"
        onClick={() => onOpen(caseRecord)}
        aria-label={`Open case ${caseLabel}, ${caseRecord.applicant}`}
      >
        <span className="case-row-id">{caseLabel}</span>

        <span className="case-row-name">{caseRecord.applicant}</span>

        <span className="case-row-status">
          <span className={`case-status-dot tone-${caseRecord.statusTone}`} aria-hidden="true" />
          {caseRecord.status}
          {caseRecord.stuck && (
            <span className="case-row-stuck" title="A document has been processing longer than expected">
              Stuck
            </span>
          )}
        </span>

        <span className="case-row-risk">
          {caseRecord.riskLevel ? (
            <span className={`risk-badge risk-badge--${caseRecord.riskLevel.toLowerCase()}`}>
              {caseRecord.riskLevel}
            </span>
          ) : (
            <span className="risk-badge risk-badge--none" title="Not scored yet">Not assessed</span>
          )}
        </span>

        <span className="case-row-created">{formatCreationDate(caseRecord)}</span>
      </button>

      <KebabMenu
        label={isDeleting ? `Deleting case ${caseLabel}` : `Case actions for ${caseLabel}`}
        disabled={isDeleting}
        items={[
          {
            id: 'edit',
            label: 'Edit case',
            onSelect: startEditing,
          },
          {
            id: 'delete',
            label: isDeleting ? 'Deleting…' : 'Delete case',
            danger: true,
            disabled: isDeleting,
            onSelect: () => onDelete(caseRecord),
          },
        ]}
      />
    </li>
  );
}
