import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import AppHeader from '../components/AppHeader';
import CaseRow from '../components/CaseRow';
import ConfirmDialog from '../components/ConfirmDialog';
import { deleteCase } from '../services/uploadService';
import type { CaseRecord } from '../App';
import { caseStatusTone } from '../utils/caseStatusTone';
import { caseNumberLabel, matchesCaseQuery } from '../utils/caseSearch';

interface CaseQueuePageProps {
  cases: CaseRecord[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenCase: (caseRecord: CaseRecord) => void;
  onCreateCase: () => void;
  onCaseUpdated: (caseId: string, companyName: string) => void;
  onCaseDeleted: (caseId: string) => void;
}

const PREFS_STORAGE_KEY = 'caseQueue.prefs';

interface QueuePrefs {
  statuses: string[];
  sortOrder: 'asc' | 'desc';
  // Highest risk first, with creation date (sortOrder) breaking ties.
  riskFirst: boolean;
}

// Per-browser (localStorage), not per-account — enough to stop having to
// re-apply the same filters and sort on every visit. Wrapped in try/catch
// since storage can throw (private mode, blocked site data) or hold junk.
function loadPrefs(): QueuePrefs {
  const defaults: QueuePrefs = { statuses: [], sortOrder: 'asc', riskFirst: false };

  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);

    if (!raw) return defaults;

    const parsed = JSON.parse(raw) as Partial<QueuePrefs>;

    return {
      statuses: Array.isArray(parsed.statuses)
        ? parsed.statuses.filter((item) => typeof item === 'string')
        : [],
      sortOrder: parsed.sortOrder === 'desc' ? 'desc' : 'asc',
      riskFirst: parsed.riskFirst === true,
    };
  } catch {
    return defaults;
  }
}

function savePrefs(prefs: QueuePrefs) {
  try {
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Not persisting is fine — the filters still work for this visit.
  }
}

// Unscored cases (riskLevel null) rank below Low.
const RISK_RANK: Record<string, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };

function riskRank(caseRecord: CaseRecord): number {
  return caseRecord.riskLevel ? RISK_RANK[caseRecord.riskLevel] : 0;
}

function creationTime(caseRecord: CaseRecord): number | null {
  if (!caseRecord.created_at) return null;

  const time = Date.parse(caseRecord.created_at);

  return Number.isNaN(time) ? null : time;
}

export default function CaseQueuePage({
  cases,
  isLoading,
  error,
  onRetry,
  onOpenCase,
  onCreateCase,
  onCaseUpdated,
  onCaseDeleted,
}: CaseQueuePageProps) {
  // Set by the header search's "See all results" (see AppHeader).
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [caseToDelete, setCaseToDelete] = useState<CaseRecord | null>(null);
  const [savedStatuses, setSavedStatuses] = useState<Set<string>>(
    () => new Set(loadPrefs().statuses),
  );
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(
    () => loadPrefs().sortOrder,
  );

  const [riskFirst, setRiskFirst] = useState(() => loadPrefs().riskFirst);

  useEffect(() => {
    savePrefs({ statuses: Array.from(savedStatuses), sortOrder, riskFirst });
  }, [savedStatuses, sortOrder, riskFirst]);

  const statusOptions = useMemo(() => {
    const statuses = cases
      .map((item) => item.status)
      .filter(Boolean);

    return Array.from(new Set(statuses));
  }, [cases]);

  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of cases) counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
    return counts;
  }, [cases]);

  // A saved status no case has right now (e.g. nothing is "stuck" today)
  // has no chip to switch it off, so it must not filter anything — only
  // statuses that currently exist count as active.
  const activeStatuses = useMemo(
    () => new Set(statusOptions.filter((status) => savedStatuses.has(status))),
    [statusOptions, savedStatuses],
  );

  function toggleStatus(status: string) {
    const next = new Set(activeStatuses);

    if (next.has(status)) {
      next.delete(status);
    } else {
      next.add(status);
    }

    setSavedStatuses(next);
  }

  const matches = useMemo(() => {
    const query = search.trim().toLowerCase();

    const filteredCases = cases.filter((item) => {
      const matchesSearch = matchesCaseQuery(item, query);

      // No chip active = no status filter; otherwise a case matches if its
      // status is any of the active ones.
      const matchesStatus =
        activeStatuses.size === 0 ||
        activeStatuses.has(item.status);

      return matchesSearch && matchesStatus;
    });

    return [...filteredCases].sort((a, b) => {
      if (riskFirst && riskRank(a) !== riskRank(b)) {
        return riskRank(b) - riskRank(a);
      }

      const timeA = creationTime(a);
      const timeB = creationTime(b);

      // Cases with no creation date sink to the bottom in either direction.
      if (timeA === null && timeB === null) return 0;
      if (timeA === null) return 1;
      if (timeB === null) return -1;

      return sortOrder === 'asc' ? timeA - timeB : timeB - timeA;
    });
  }, [cases, search, activeStatuses, sortOrder, riskFirst]);

  async function confirmDeleteCase() {
    if (!caseToDelete) return;

    const caseRecord = caseToDelete;
    setDeletingCaseId(caseRecord.id);
    setDeleteError(null);

    try {
      await deleteCase(caseRecord.id);
      setCaseToDelete(null);
      onCaseDeleted(caseRecord.id);
    } catch (err) {
      setCaseToDelete(null);
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete case.');
    } finally {
      setDeletingCaseId(null);
    }
  }

  return (
    <main className="shell shell--queue">
      <AppHeader />

      <section className="queue-view">
        <div className="case-queue-header">
          <div>
            <p className="eyebrow">My case queue</p>
            <h1>Find a case</h1>
          </div>

          <button
            type="button"
            className="create-case-btn"
            onClick={onCreateCase}
          >
            <span aria-hidden="true">+ </span>New case
          </button>
        </div>

        <p className="sub">
          Pick from your assigned cases below, or search by case number or company name above.
        </p>

        <div className="queue-layout">
          <aside className="queue-sidebar" aria-label="Filter and sort cases">
            <div className="queue-sidebar-group">
              <span className="filter-label">Status</span>

              <ul className="queue-filter-list">
                <li>
                  <button
                    type="button"
                    aria-pressed={activeStatuses.size === 0}
                    className="queue-filter"
                    onClick={() => setSavedStatuses(new Set())}
                  >
                    <span className="queue-filter-label">All cases</span>
                    <span className="queue-filter-count">{cases.length}</span>
                  </button>
                </li>

                {statusOptions.map((status) => (
                  <li key={status}>
                    <button
                      type="button"
                      aria-pressed={activeStatuses.has(status)}
                      className="queue-filter status-filter"
                      onClick={() => toggleStatus(status)}
                    >
                      <span className={`case-status-dot case-status-dot--${caseStatusTone(status)}`} aria-hidden="true" />
                      <span className="queue-filter-label">{status}</span>
                      <span className="queue-filter-count">{statusCounts.get(status) ?? 0}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <div className="queue-sidebar-group">
              <span className="filter-label">Sort</span>

              <ul className="queue-filter-list">
                <li>
                  <button
                    type="button"
                    className="queue-filter"
                    aria-label={
                      sortOrder === 'asc'
                        ? 'Sorted by creation date, oldest first. Click for newest first.'
                        : 'Sorted by creation date, newest first. Click for oldest first.'
                    }
                    onClick={() =>
                      setSortOrder((current) => (current === 'asc' ? 'desc' : 'asc'))
                    }
                  >
                    <span className="queue-filter-label">
                      {sortOrder === 'asc' ? 'Oldest first' : 'Newest first'}
                    </span>
                    <span className="sort-toggle-arrow" aria-hidden="true">
                      {sortOrder === 'asc' ? '↑' : '↓'}
                    </span>
                  </button>
                </li>

                <li>
                  <button
                    type="button"
                    className="queue-filter"
                    aria-pressed={riskFirst}
                    onClick={() => setRiskFirst((current) => !current)}
                  >
                    <span className="queue-filter-label">Highest risk first</span>
                  </button>
                </li>
              </ul>
            </div>
          </aside>

          <div className="queue-main">
            <div className="queue-label">
              <span>Assigned to you ({matches.length})</span>

              {search && (
                <span className="queue-search-chip">
                  <span className="queue-search-chip-text">Matching “{search}”</span>
                  <button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => {
                      const params = new URLSearchParams(searchParams);
                      params.delete('q');
                      setSearchParams(params, { replace: true });
                    }}
                  >
                    ✕
                  </button>
                </span>
              )}
            </div>

            {deleteError && (
              <div className="toast show mixed" role="alert">
                ⚠ {deleteError}
              </div>
            )}

            {isLoading ? (
              <div className="no-results">
                Loading cases…
              </div>
            ) : error ? (
              <div className="no-results">
                <div>{error}</div>

                <button
                  type="button"
                  className="go-btn queue-retry"
                  onClick={onRetry}
                >
                  Retry
                </button>
              </div>
            ) : matches.length > 0 ? (
              <div className="case-table">
                <div className="case-table-head" aria-hidden="true">
                  <span>Case</span>
                  <span>Company name</span>
                  <span>Status</span>
                  <span className="case-table-head-center">Risk</span>
                  <span className="case-table-head-center">Created</span>
                </div>

                <ul className="case-table-body">
                  {matches.map((caseRecord) => (
                    <CaseRow
                      key={caseRecord.id}
                      caseRecord={caseRecord}
                      onOpen={onOpenCase}
                      onDelete={setCaseToDelete}
                      onCaseUpdated={onCaseUpdated}
                      isDeleting={deletingCaseId === caseRecord.id}
                    />
                  ))}
                </ul>
              </div>
            ) : (
              <div className="no-results">
                No case matches "{search}". Check the case number and try again.
              </div>
            )}
          </div>
        </div>
      </section>

      {caseToDelete && (
        <ConfirmDialog
          title="Delete this case?"
          message={`Case ${caseNumberLabel(caseToDelete)} (${caseToDelete.applicant}) and its documents will be removed and cannot be recovered.`}
          isConfirming={deletingCaseId === caseToDelete.id}
          onCancel={() => {
            if (deletingCaseId === null) setCaseToDelete(null);
          }}
          onConfirm={() => void confirmDeleteCase()}
        />
      )}
    </main>
  );
}
