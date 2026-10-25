import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type { AuditEvent } from './types';

// The most the backend returns in one read (GET /audit/events caps limit at 1000).
const AUDIT_LIMIT = 1000;

// The case's audit trail, oldest first. Past the limit, the oldest entries are left out.
export async function listCaseAuditTrail(caseId: string): Promise<AuditEvent[]> {
  const query = new URLSearchParams({ case_id: caseId, limit: String(AUDIT_LIMIT) });
  const response = await authFetch(`/audit/events?${query}`);

  await assertOk(response, 'Failed to load the audit trail');

  // Served newest first.
  return ((await response.json()) as AuditEvent[]).reverse();
}
