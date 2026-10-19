import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  CaseAnalysis,
  CaseOption,
  CaseRequirementCoverage,
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

export async function getCaseAnalysis(caseId: string): Promise<CaseAnalysis | null> {
  const response = await authFetch(`/cases/${caseId}/analysis`);

  if (response.status === 404) {
    return null;
  }
  await assertOk(response, 'Failed to fetch the case analysis');

  return (await response.json()) as CaseAnalysis;
}

// Closes an analysed case; recorded in its stage history under the signed-in username.
export async function finaliseCase(caseId: string): Promise<CaseRequirementCoverage> {
  const response = await authFetch(`/cases/${caseId}/finalise`, { method: 'POST' });

  await assertOk(response, 'Failed to finalise the case');

  return (await response.json()) as CaseRequirementCoverage;
}

// Reopens a finalised case; it returns to the stage its documents and analysis put it at.
export async function reopenCase(caseId: string): Promise<CaseRequirementCoverage> {
  const response = await authFetch(`/cases/${caseId}/reopen`, { method: 'POST' });

  await assertOk(response, 'Failed to reopen the case');

  return (await response.json()) as CaseRequirementCoverage;
}
