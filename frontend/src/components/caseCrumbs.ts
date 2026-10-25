import type { CaseRecord } from '../App';
import { listCaseDocuments } from '../services/uploadService';
import { caseNumberLabel } from '../utils/caseSearch';
import type { Breadcrumb } from './Breadcrumbs';

// The breadcrumbs of the case and document pages (CaseHeader shows them). Kept out of CaseHeader.tsx so that
// file exports only components, which React's fast refresh needs.

function casePath(caseRecord: CaseRecord): string {
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
    // Newest case first, by number compared as a number (GLDB-134, GLDB-133, … GLDB-77), not the list's own order.
    loadMenu: () =>
      [...cases]
        .sort((a, b) => caseNumberLabel(b).localeCompare(caseNumberLabel(a), undefined, { numeric: true }))
        .map((item) => ({
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
