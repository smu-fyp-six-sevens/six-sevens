import type { CaseRecord } from '../App';
import { listCaseDocuments, searchCases } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';
import { caseNumberLabel } from '../utils/caseSearch';
import type { Breadcrumb } from './Breadcrumbs';

// The breadcrumbs of the case and document pages (CaseHeader shows them). Kept out of CaseHeader.tsx so that
// file exports only components, which React's fast refresh needs.

function casePath(caseRecord: CaseRecord): string {
  return `/cases/${caseRecord.caseNumber ?? caseRecord.id}`;
}

export const caseQueueCrumb: Breadcrumb = { label: 'Case Queue', to: '/cases' };

// How many cases the switch-case menu lists (the server's own limit is 50).
const MENU_CASES = 20;

// The case number: click it to switch to another case. The menu lists the newest cases, fetched when it opens.
export function caseCrumb(
  caseRecord: CaseRecord,
  { current = false }: { current?: boolean } = {},
): Breadcrumb {
  return {
    label: caseNumberLabel(caseRecord),
    to: `${casePath(caseRecord)}?section=summary`,
    current,
    menuLabel: 'switch case',
    loadMenu: async () => {
      const { items } = await searchCases('', MENU_CASES);

      return items.map(toCaseRecord).map((item) => ({
        key: item.id,
        to: `${casePath(item)}?section=summary`,
        tag: caseNumberLabel(item),
        label: item.applicant,
        current: item.id === caseRecord.id,
      }));
    },
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
