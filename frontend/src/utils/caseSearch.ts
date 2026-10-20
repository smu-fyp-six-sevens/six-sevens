import type { CaseRecord } from '../App';

export function caseNumberLabel(caseRecord: CaseRecord): string {
  return caseRecord.caseNumber || caseRecord.id;
}

// Case number or company name contains the (already trimmed, lower-cased) query.
export function matchesCaseQuery(caseRecord: CaseRecord, query: string): boolean {
  return (
    !query ||
    [caseNumberLabel(caseRecord), caseRecord.applicant].some((value) =>
      value.toLowerCase().includes(query),
    )
  );
}

export function findExactCase(cases: CaseRecord[], query: string): CaseRecord | undefined {
  return cases.find((item) =>
    [caseNumberLabel(item), item.id].some((value) => value.toLowerCase() === query),
  );
}
