import type { CaseRecord } from '../App';

interface CaseCardProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
  onDelete: (caseRecord: CaseRecord) => void;
  isDeleting: boolean;
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
        <span className="case-id">
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

        <span className="case-arrow" aria-hidden="true">
          →
        </span>
      </button>

      <span className="case-card-delete-slot">
        <button
          type="button"
          className={`case-card-delete${isDeleting ? ' is-visible' : ''}`}
          aria-label={isDeleting ? `Deleting case ${caseLabel}` : `Delete case ${caseLabel}`}
          disabled={isDeleting}
          onClick={() => onDelete(caseRecord)}
        >
          <svg
            viewBox="0 0 24 24"
            width="24"
            height="24"
            aria-hidden="true"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4 7h16" />
            <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            <path d="M7 7l1 12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-12" />
            <path d="M10 11v6" />
            <path d="M14 11v6" />
          </svg>
        </button>
      </span>
    </div>
  );
}