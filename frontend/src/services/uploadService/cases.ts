import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  CaseAnalysis,
  CaseAnalysisRunItem,
  CaseCost,
  CaseCounts,
  CaseDocumentsStatus,
  CaseListItem,
  CaseListPage,
  CaseOption,
  CaseRiskFilter,
  CaseSearchResult,
  CaseSort,
  UpdatedCase,
} from './types';

export async function deleteCase(caseId: string): Promise<void> {
  const response = await authFetch(`/cases/${caseId}`, { method: 'DELETE' });

  await assertOk(response, 'Failed to delete case');
}

export async function updateCase(caseId: string, companyName: string): Promise<UpdatedCase> {
  const response = await authFetch(`/cases/${caseId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ company_name: companyName }),
  });

  await assertOk(response, 'Failed to update case');

  return (await response.json()) as UpdatedCase;
}

// One page of the case queue. The server sorts, filters and slices; offset is the number of cases to skip.
export interface CasePageQuery {
  limit: number;
  offset: number;
  sort: CaseSort;
  statuses: CaseDocumentsStatus[];
  risks: CaseRiskFilter[];
  q: string;
}

export async function listCasesPage(query: CasePageQuery, signal?: AbortSignal): Promise<CaseListPage> {
  const params = new URLSearchParams({
    limit: String(query.limit),
    offset: String(query.offset),
    sort: query.sort,
  });
  for (const status of query.statuses) params.append('status', status);
  for (const risk of query.risks) params.append('risk', risk);
  if (query.q) params.set('q', query.q);

  const response = await authFetch(`/cases?${params}`, { signal });

  await assertOk(response, 'Failed to load cases');

  return (await response.json()) as CaseListPage;
}

export async function getCaseCounts(signal?: AbortSignal): Promise<CaseCounts> {
  const response = await authFetch('/cases/counts', { signal });

  await assertOk(response, 'Failed to count cases');

  return (await response.json()) as CaseCounts;
}

// Cases whose number or name contains q, best match first; without q, the newest.
export async function searchCases(q: string, limit: number, signal?: AbortSignal): Promise<CaseSearchResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (q) params.set('q', q);

  const response = await authFetch(`/cases/search?${params}`, { signal });

  await assertOk(response, 'Failed to search cases');

  return (await response.json()) as CaseSearchResult;
}

// The case behind /cases/GLDB-137, or null when there is none.
export async function getCaseByNumber(number: string, signal?: AbortSignal): Promise<CaseListItem | null> {
  const response = await authFetch(`/cases/by-number/${encodeURIComponent(number)}`, { signal });

  if (response.status === 404) {
    return null;
  }
  await assertOk(response, 'Failed to load the case');

  return (await response.json()) as CaseListItem;
}

// The picker for moving documents lists the newest cases (the server returns at most 50).
export async function listCaseOptions(): Promise<CaseOption[]> {
  const { items } = await searchCases('', 50);

  return items.map(({ id, greenlink_case_id, company_name }) => ({ id, greenlink_case_id, company_name }));
}

// Starts the per-case analysis in the background; the result arrives with the case snapshot.
export async function runCaseAnalysis(caseId: string): Promise<CaseAnalysis> {
  const response = await authFetch(`/cases/${caseId}/analysis/run`, { method: 'POST' });

  await assertOk(response, 'Failed to start the case analysis');

  return (await response.json()) as CaseAnalysis;
}

// Claude spend recorded in the audit trail for this case's runs (served by audit-metrics).
export async function getCaseCost(caseId: string): Promise<CaseCost> {
  const response = await authFetch(`/audit/cases/${caseId}/cost`);

  await assertOk(response, 'Failed to fetch the case cost');

  return (await response.json()) as CaseCost;
}

// Every analysis of the case, newest first: who started it, its score and change, and whether it's out of date.
export async function listCaseAnalysisRuns(caseId: string): Promise<CaseAnalysisRunItem[]> {
  const response = await authFetch(`/cases/${caseId}/analysis/runs`);

  await assertOk(response, "Failed to load the case's earlier analyses");

  return (await response.json()) as CaseAnalysisRunItem[];
}

// One analysis of the case, earlier or latest, whole.
export async function getCaseAnalysisRun(caseId: string, runId: string): Promise<CaseAnalysis> {
  const response = await authFetch(`/cases/${caseId}/analysis/runs/${runId}`);

  await assertOk(response, 'Failed to load that analysis');

  return (await response.json()) as CaseAnalysis;
}

export async function getCaseAnalysis(caseId: string): Promise<CaseAnalysis | null> {
  const response = await authFetch(`/cases/${caseId}/analysis`);

  if (response.status === 404) {
    return null;
  }
  await assertOk(response, 'Failed to fetch the case analysis');

  return (await response.json()) as CaseAnalysis;
}
