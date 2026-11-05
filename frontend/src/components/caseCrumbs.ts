import { listCaseDocuments, listCasesPage } from '../services/uploadService';
import { toCaseRecord, type CaseRecord } from '../utils/caseRecord';
import { caseNumberLabel } from '../utils/caseSearch';
import type { Breadcrumb } from './Breadcrumbs';

// The breadcrumbs of the case and document pages (CaseHeader shows them). Kept out of CaseHeader.tsx so that
// file exports only components, which React's fast refresh needs.

function casePath(caseRecord: CaseRecord): string {
  return `/cases/${caseRecord.caseNumber ?? caseRecord.id}`;
}

export const caseQueueCrumb: Breadcrumb = { label: 'Case Queue', to: '/cases' };

// How many cases the switch-case menu lists at a time; "Load more cases" adds the next lot.
const MENU_CASES = 50;

// The case number: click it to switch to another case. The menu lists the newest cases, fetched when it opens. `section`
// is the case page's section the officer is on: switching case lands on the same section of the other case.
export function caseCrumb(
  caseRecord: CaseRecord,
  { current = false, section = 'summary' }: { current?: boolean; section?: string } = {},
): Breadcrumb {
  return {
    label: caseNumberLabel(caseRecord),
    to: `${casePath(caseRecord)}?section=${section}`,
    current,
    menuLabel: 'switch case',
    loadMoreLabel: 'Load more cases',
    loadMenu: async (offset) => {
      const page = await listCasesPage({ limit: MENU_CASES, offset, sort: 'newest', statuses: [], risks: [], q: '' });

      const items = page.items.map(toCaseRecord).map((item) => ({
        key: item.id,
        to: `${casePath(item)}?section=${section}`,
        tag: caseNumberLabel(item),
        label: item.applicant,
        current: item.id === caseRecord.id,
      }));
      return { items, hasMore: offset + page.items.length < page.total };
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
