import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  CaseOption,
  CaseRequirementCoverage,
  UpdatedCase,
} from './types';

export async function getCaseRequirementCoverage(caseId: string): Promise<CaseRequirementCoverage> {
  const response = await authFetch(`/cases/${caseId}`);

  await assertOk(response, 'Failed to fetch case');

  return (await response.json()) as CaseRequirementCoverage;
}

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
