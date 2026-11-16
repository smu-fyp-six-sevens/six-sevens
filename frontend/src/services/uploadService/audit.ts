import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type { AuditEvent } from './types';

// The most the backend returns in one read (GET /audit/events caps limit at 1000).
const AUDIT_LIMIT = 1000;
// A safety stop: past this many pages the oldest entries are left out.
const MAX_PAGES = 20;

// The case's audit trail, oldest first, read a page at a time (newest first, each page older than the last).
export async function listCaseAuditTrail(caseId: string): Promise<AuditEvent[]> {
  const events: AuditEvent[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const query = new URLSearchParams({ case_id: caseId, limit: String(AUDIT_LIMIT) });
    if (events.length > 0) query.set('before', events[events.length - 1].occurred_at);
    const response = await authFetch(`/audit/events?${query}`);

    await assertOk(response, 'Failed to load the audit trail');

    const batch = (await response.json()) as AuditEvent[];
    events.push(...batch);
    if (batch.length < AUDIT_LIMIT) break;
  }
  return events.reverse();
}

// Every case deletion on record, newest first (one page: deletions are rare).
export async function listDeletedCases(): Promise<AuditEvent[]> {
  const query = new URLSearchParams({ type: 'case.deleted', limit: String(AUDIT_LIMIT) });
  const response = await authFetch(`/audit/events?${query}`);

  await assertOk(response, 'Failed to load the deleted cases');

  return (await response.json()) as AuditEvent[];
}
