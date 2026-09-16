import type { CaseRecord } from '../App';

interface CaseCardProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
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

export default function CaseCard({ caseRecord, onOpen }: CaseCardProps) {
  return (
    <button
      type="button"
      className="case-card"
      onClick={() => onOpen(caseRecord)}
      aria-label={`Open case ${caseRecord.caseNumber}, ${caseRecord.applicant}`}
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
  );
}