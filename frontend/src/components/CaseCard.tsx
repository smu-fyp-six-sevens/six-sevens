import type { CaseRecord } from '../App';

interface CaseCardProps {
  caseRecord: CaseRecord;
  onOpen: (caseRecord: CaseRecord) => void;
}

export default function CaseCard({ caseRecord, onOpen }: CaseCardProps) {
  return (
    <button
      type="button"
      className="case-card"
      onClick={() => onOpen(caseRecord)}
      aria-label={`Open case ${caseRecord.id}, ${caseRecord.applicant}`}
    >
      <span className="case-id">{caseRecord.id}</span>
      <span className="case-main">
        <span className="case-name">{caseRecord.applicant}</span>
        <span className="case-sub">
          {caseRecord.type} · {caseRecord.status}
        </span>
      </span>
      <span className="case-arrow" aria-hidden="true">→</span>
    </button>
  );
}
