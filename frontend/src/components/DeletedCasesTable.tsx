import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { listDeletedCases } from '../services/uploadService';
import type { AuditEvent } from '../services/uploadService';
import { LoadErrorState } from './LoadErrorState';

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

function formatWhen(value: string): string {
  return new Date(value).toLocaleString('en-SG', {
    day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function matches(event: AuditEvent, search: string): boolean {
  if (!search) return true;
  const needle = search.toLowerCase();
  return [event.detail.greenlink_case_id, event.detail.company_name, event.actor]
    .some((value) => typeof value === 'string' && value.toLowerCase().includes(needle));
}

// A deleted case leaves no row behind, so the queue's "Deleted" status reads the audit trail's case.deleted entries.
export default function DeletedCasesTable({ search, statusFilter, onCount }: {
  search: string;
  // The Status column's filter, so it can be switched back from this view.
  statusFilter: ReactNode;
  onCount: (count: number | null) => void;
}) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    listDeletedCases()
      .then((found) => { if (active) setEvents(found); })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Failed to load the deleted cases.');
      });
    return () => { active = false; };
  }, [attempt]);

  const shown = events?.filter((event) => matches(event, search)) ?? null;

  useEffect(() => {
    onCount(shown ? shown.length : null);
  }, [shown?.length, onCount]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <LoadErrorState message={error} onRetry={() => setAttempt((n) => n + 1)} />;

  return (
    <div className="case-table" aria-busy={!shown}>
      <div className="case-table-head">
        <span>Case</span>
        <span>Company name</span>
        <span className="case-table-head-cell">{statusFilter}</span>
        <span>Deleted by</span>
        <span className="case-table-head-center">Deleted</span>
      </div>

      {!shown ? (
        <span className="sr-only" role="status">Loading deleted cases…</span>
      ) : shown.length === 0 ? (
        <div className="case-table-empty">
          <span>{search ? `No deleted case matches "${search}".` : 'No case has been deleted.'}</span>
        </div>
      ) : (
        <ul className="case-table-body">
          {shown.map((event) => (
            <li key={event.event_id} className="case-row" data-testid="deleted-case-row">
              <div className="case-row-open case-row-open--static">
                <span className="case-row-id">{text(event.detail.greenlink_case_id) ?? '—'}</span>
                <span className="case-row-name">{text(event.detail.company_name) ?? '—'}</span>
                <span className="case-row-status">
                  Deleted
                  {typeof event.detail.documents === 'number' && (
                    <span className="deleted-case-docs">
                      {event.detail.documents} document{event.detail.documents === 1 ? '' : 's'}
                    </span>
                  )}
                </span>
                <span className="case-row-name">{event.actor}</span>
                <span className="case-row-created">
                  <time dateTime={event.occurred_at}>{formatWhen(event.occurred_at)}</time>
                </span>
              </div>
              <span className="case-row-menu-space" />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
