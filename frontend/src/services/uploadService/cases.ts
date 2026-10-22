import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  CaseAnalysis,
  CaseCost,
  CaseOption,
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

export async function listCaseOptions(): Promise<CaseOption[]> {
  const response = await authFetch('/cases');

  await assertOk(response, 'Failed to list cases');

  return (await response.json()) as CaseOption[];
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

export async function getCaseAnalysis(caseId: string): Promise<CaseAnalysis | null> {
  const response = await authFetch(`/cases/${caseId}/analysis`);

  if (response.status === 404) {
    return null;
  }
  await assertOk(response, 'Failed to fetch the case analysis');

  return (await response.json()) as CaseAnalysis;
}
