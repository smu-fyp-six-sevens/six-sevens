import { useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import KebabMenu from './KebabMenu';
import { updateCase } from '../services/uploadService';
import { MAX_COMPANY_NAME_LENGTH, validateCompanyName } from '../utils/caseValidation';

interface CaseCardProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
  onDelete: (caseRecord: CaseRecord) => void;
  onCaseUpdated: (caseId: string, companyName: string) => void;
  isDeleting: boolean;
}

const CASE_ID_CLASS: Record<string, string> = {
  'Ready for review': 'case-id--green',
  'In progress': 'case-id--blue',
  'Documents failed': 'case-id--red',
  'Processing stuck': 'case-id--amber',
  'Documents pending': 'case-id--grey',
};

function caseCardColorClass(status: string): string {
  const idClass = CASE_ID_CLASS[status] ?? 'case-id--grey';
  return idClass.replace('case-id--', 'case-card--');
}

function formatCreationDate(caseRecord: CaseRecord): string {
  const createdAt = (caseRecord as CaseRecord & {
    created_at?: string | null;
  }).created_at;

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

export default function CaseCard({
  caseRecord,
  onOpen,
  onDelete,
  onCaseUpdated,
  isDeleting,
}: CaseCardProps) {
  const caseLabel = caseRecord.caseNumber || caseRecord.id;
  const cardColorClass = caseCardColorClass(caseRecord.status);
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
      <div className={`case-card ${cardColorClass}`}>
        <span
          className={`case-id ${
            CASE_ID_CLASS[caseRecord.status] ?? 'case-id--grey'
          }`}
        >
          {caseRecord.caseNumber}
        </span>

        <span className="case-main case-main--editing">
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
          {saveError && (
            <span className="case-edit-error" role="alert">
              {saveError}
            </span>
          )}
        </span>

        <span className="case-edit-actions add-field-form-actions">
          <button type="button" onClick={cancelEditing} disabled={isSaving}>
            Cancel
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={isSaving}>
            {isSaving ? 'Saving…' : 'Save'}
          </button>
        </span>
      </div>
    );
  }

  return (
    <div className={`case-card ${cardColorClass}`}>
      <button
        type="button"
        className="case-card-open"
        onClick={() => onOpen(caseRecord)}
        aria-label={`Open case ${caseLabel}, ${caseRecord.applicant}`}
      >
        <span
          className={`case-id ${
            CASE_ID_CLASS[caseRecord.status] ?? 'case-id--grey'
          }`}
        >
          {caseRecord.caseNumber}
        </span>

        <span className="case-main">
          <span className="case-name">
            {caseRecord.applicant}
          </span>

          <span className="case-sub">
            {caseRecord.type} · {caseRecord.status}
          </span>

          <span className="case-created">
            Created: {formatCreationDate(caseRecord)}
          </span>
        </span>
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
    </div>
  );
}