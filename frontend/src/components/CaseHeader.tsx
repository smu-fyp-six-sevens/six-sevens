import type { ReactNode } from 'react';
import type { CaseRecord } from '../App';
import { listCaseDocuments } from '../services/uploadService';
import { caseNumberLabel } from '../utils/caseSearch';
import Breadcrumbs, { type Breadcrumb } from './Breadcrumbs';

interface CaseHeaderProps {
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

export function caseLabel(caseRecord: CaseRecord): string {
  return caseNumberLabel(caseRecord);
}

export function casePath(caseRecord: CaseRecord): string {
  return `/cases/${caseRecord.caseNumber ?? caseRecord.id}`;
}

export const caseQueueCrumb: Breadcrumb = { label: 'Case Queue', to: '/cases' };

// The case number: click it to switch to any other case.
export function caseCrumb(
  cases: CaseRecord[],
  caseRecord: CaseRecord,
  { current = false }: { current?: boolean } = {},
): Breadcrumb {
  return {
    label: caseNumberLabel(caseRecord),
    to: `${casePath(caseRecord)}?section=summary`,
    current,
    menuLabel: 'switch case',
    loadMenu: () =>
      cases.map((item) => ({
        key: item.id,
        to: `${casePath(item)}?section=summary`,
        tag: caseNumberLabel(item),
        label: item.applicant,
        current: item.id === caseRecord.id,
      })),
  };
}

// The document's filename: click it to switch to another document in the same case.
export function documentCrumb(caseRecord: CaseRecord, documentId: string, filename: string): Breadcrumb {
  return {
    label: filename,
    to: `${casePath(caseRecord)}/documents/${documentId}`,
    current: true,
    menuLabel: 'switch document',
    loadMenu: async () =>
      (await listCaseDocuments(caseRecord.id)).map((document) => ({
        key: document.id,
        to: `${casePath(caseRecord)}/documents/${document.id}`,
        label: document.filename,
        current: document.id === documentId,
      })),
  };
}

export function CaseTitle({ caseRecord }: { caseRecord: CaseRecord }) {
  return (
    <div className="case-title">
      <h1>{caseRecord.applicant}</h1>
    </div>
  );
}
