import type { CaseRecord } from '../App';

export function caseNumberLabel(caseRecord: CaseRecord): string {
  return caseRecord.caseNumber || caseRecord.id;
}

export function findExactCase(cases: CaseRecord[], query: string): CaseRecord | undefined {
  return cases.find((item) =>
    [caseNumberLabel(item), item.id].some((value) => value.toLowerCase() === query),
  );
}
