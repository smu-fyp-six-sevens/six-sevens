import type { CaseRecord } from '../App';
import KebabMenu from './KebabMenu';

interface CaseCardProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
  onDelete: (caseRecord: CaseRecord) => void;
  isDeleting: boolean;
}

const CASE_ID_CLASS: Record<string, string> = {
  'Ready for review': 'case-id--green',
  'In progress': 'case-id--blue',
  'Documents failed': 'case-id--red',
  'Processing stuck': 'case-id--amber',
  'Documents pending': 'case-id--grey',
};

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
  isDeleting,
}: CaseCardProps) {
  const caseLabel = caseRecord.caseNumber || caseRecord.id;

  return (
    <div className="case-card">
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