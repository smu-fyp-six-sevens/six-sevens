import type { ReactNode } from 'react';
import type { CaseRecord } from '../App';
import Breadcrumbs, { type Breadcrumb } from './Breadcrumbs';

interface CaseHeaderProps {
  // From caseCrumbs.ts.
  crumbs: Breadcrumb[];
  // The title row's contents: usually <CaseTitle /> plus any actions.
  children: ReactNode;
}

// Shared by the case and document pages so the case title stays put when moving between them.
export default function CaseHeader({ crumbs, children }: CaseHeaderProps) {
  return (
    <div className="case-header">
      <Breadcrumbs crumbs={crumbs} />

      <div className="case-title-row">{children}</div>
    </div>
  );
}

export function CaseTitle({ caseRecord }: { caseRecord: CaseRecord }) {
  return (
    <div className="case-title">
      <h1>{caseRecord.applicant}</h1>
    </div>
  );
}
