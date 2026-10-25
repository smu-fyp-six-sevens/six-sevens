import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import AppHeader from '../components/AppHeader';
import CaseRow from '../components/CaseRow';
import ConfirmDialog from '../components/ConfirmDialog';
import Pagination from '../components/Pagination';
import { deleteCase } from '../services/uploadService';
import type { CaseDocumentsStatus, CaseSort } from '../services/uploadService';
import type { CaseRecord } from '../App';
import { caseStatusTone } from '../utils/caseStatusTone';
import { STATUS_ORDER, statusLabel, statusValue } from '../utils/caseStatus';
import { caseNumberLabel } from '../utils/caseSearch';
import { useCaseQueue } from './useCaseQueue';

interface CaseQueuePageProps {
  onOpenCase: (caseRecord: CaseRecord) => void;
  onCreateCase: () => void;
}

const PREFS_STORAGE_KEY = 'caseQueue.prefs';
const PAGE_SIZE = 25;

// One sort at a time. Risk puts the highest first and the newest first among equals.
const SORT_OPTIONS: { value: CaseSort; label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'risk', label: 'Highest risk first' },
];

const DEFAULT_SORT: CaseSort = 'newest';

interface QueuePrefs {
  statuses: CaseDocumentsStatus[];
  sort: CaseSort;
}

function isSortKey(value: unknown): value is CaseSort {
  return SORT_OPTIONS.some((option) => option.value === value);
}

// A saved status is the backend's value; older saves hold the label ("In progress") the queue used to store.
function savedStatus(item: unknown): CaseDocumentsStatus | undefined {
  if (typeof item !== 'string') return undefined;

  return STATUS_ORDER.find((value) => value === item) ?? statusValue(item);
}

// Per-browser (localStorage), not per-account — enough to stop having to
// re-apply the same filters and sort on every visit. Wrapped in try/catch
// since storage can throw (private mode, blocked site data) or hold junk.
function loadPrefs(): QueuePrefs {
  const defaults: QueuePrefs = { statuses: [], sort: DEFAULT_SORT };

  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);

    if (!raw) return defaults;

    // The oldest shape kept a date direction and a separate "highest risk first" switch.
    const parsed = JSON.parse(raw) as { statuses?: unknown; sort?: unknown; sortOrder?: string; riskFirst?: boolean };
    const sort: CaseSort = isSortKey(parsed.sort)
      ? parsed.sort
      : parsed.riskFirst === true
        ? 'risk'
        : parsed.sortOrder === 'asc'
          ? 'oldest'
          : DEFAULT_SORT;

    return {
      statuses: Array.isArray(parsed.statuses)
        ? parsed.statuses.map(savedStatus).filter((status): status is CaseDocumentsStatus => status !== undefined)
        : [],
      sort,
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

export default function CaseQueuePage({ onOpenCase, onCreateCase }: CaseQueuePageProps) {
  // Set by the header search's "See all results" (see AppHeader).
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const requestedPage = Math.max(1, Math.floor(Number(searchParams.get('page'))) || 1);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [caseToDelete, setCaseToDelete] = useState<CaseRecord | null>(null);
  // Read localStorage once, on the first render; the settings start from it.
  const [initialPrefs] = useState(loadPrefs);
  const [savedStatuses, setSavedStatuses] = useState<Set<CaseDocumentsStatus>>(() => new Set(initialPrefs.statuses));
  const [sort, setSort] = useState<CaseSort>(initialPrefs.sort);

  useEffect(() => {
    savePrefs({ statuses: Array.from(savedStatuses), sort });
  }, [savedStatuses, sort]);

  // The sidebar lists the statuses cases are in, so it learns them from the server's counts.
  const [counts, setCounts] = useState<Map<CaseDocumentsStatus, number> | null>(null);

  // A saved status no case has right now has no chip to switch it off, so it must not filter anything: only
  // statuses that currently exist count as active. Until the counts arrive, the saved ones are used as they are.
  const statusOptions = useMemo(
    () => (counts ? STATUS_ORDER.filter((status) => (counts.get(status) ?? 0) > 0) : []),
    [counts],
  );
  const activeStatuses = useMemo(
    () => new Set(counts ? statusOptions.filter((status) => savedStatuses.has(status)) : savedStatuses),
    [counts, statusOptions, savedStatuses],
  );
  const activeList = useMemo(() => STATUS_ORDER.filter((status) => activeStatuses.has(status)), [activeStatuses]);

  const queue = useCaseQueue({ q: search.trim(), sort, statuses: activeList, page: requestedPage, pageSize: PAGE_SIZE });
  const { items, total, hasLoaded, isFetching, error } = queue;

  useEffect(() => {
    if (queue.counts) {
      setCounts(new Map(Object.entries(queue.counts.by_status) as [CaseDocumentsStatus, number][]));
    }
  }, [queue.counts]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = hasLoaded ? Math.min(requestedPage, pageCount) : requestedPage;

  // The page is in the URL (?page=2), so a refresh or the back button lands on the same one.
  function goToPage(next: number) {
    const params = new URLSearchParams(searchParams);
    if (next <= 1) params.delete('page');
    else params.set('page', String(next));
    setSearchParams(params, { replace: true });
  }

  // After the last case of the last page is deleted (or a stale ?page=9), fall back to the last page there is.
  useEffect(() => {
    if (hasLoaded && !isFetching && items.length === 0 && total > 0 && requestedPage > pageCount) {
      goToPage(pageCount);
    }
    // goToPage only reads the URL's own params.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasLoaded, isFetching, items.length, total, requestedPage, pageCount]);

  // Changing a filter or the sort starts again from the first page.
  function changeStatuses(next: Set<CaseDocumentsStatus>) {
    setSavedStatuses(next);
    goToPage(1);
  }

  function changeSort(next: CaseSort) {
    setSort(next);
    goToPage(1);
  }

  function toggleStatus(status: CaseDocumentsStatus) {
    const next = new Set(activeStatuses);

    if (next.has(status)) {
      next.delete(status);
    } else {
      next.add(status);
    }

    changeStatuses(next);
  }

  async function confirmDeleteCase() {
    if (!caseToDelete) return;

    const caseRecord = caseToDelete;
    setDeletingCaseId(caseRecord.id);
    setDeleteError(null);

    try {
      await deleteCase(caseRecord.id);
      setCaseToDelete(null);
      queue.reload();
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
                    onClick={() => changeStatuses(new Set())}
                  >
                    <span className="queue-filter-label">All cases</span>
                    <span className="queue-filter-count">{queue.counts ? queue.counts.total : ''}</span>
                  </button>
                </li>

                {/* The statuses are only known once the counts arrive: hold their place so nothing below moves. */}
                {!counts && [0, 1, 2].map((slot) => (
                  <li key={slot} aria-hidden="true">
                    <div className="queue-filter queue-filter--skeleton">
                      <span className="skeleton-bar skeleton-bar--dot" />
                      <span className="skeleton-bar skeleton-bar--label" />
                    </div>
                  </li>
                ))}

                {statusOptions.map((status) => (
                  <li key={status}>
                    <button
                      type="button"
                      aria-pressed={activeStatuses.has(status)}
                      className="queue-filter status-filter"
                      onClick={() => toggleStatus(status)}
                    >
                      <span className={`case-status-dot case-status-dot--${caseStatusTone(statusLabel(status))}`} aria-hidden="true" />
                      <span className="queue-filter-label">{statusLabel(status)}</span>
                      <span className="queue-filter-count">{counts?.get(status) ?? 0}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <div className="queue-sidebar-group">
              <span className="filter-label">Sort</span>

              <ul className="queue-filter-list">
                {SORT_OPTIONS.map((option) => (
                  <li key={option.value}>
                    <button
                      type="button"
                      className="queue-filter"
                      aria-pressed={sort === option.value}
                      onClick={() => changeSort(option.value)}
                    >
                      <span className="queue-filter-label">{option.label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </aside>

          <div className="queue-main">
            <div className="queue-toolbar">
              <h1 className="queue-title">{hasLoaded ? `Assigned to you (${total})` : 'Assigned to you'}</h1>

              {search && (
                <span className="queue-search-chip">
                  <span className="queue-search-chip-text">Matching “{search}”</span>
                  <button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => {
                      const params = new URLSearchParams(searchParams);
                      params.delete('q');
                      params.delete('page');
                      setSearchParams(params, { replace: true });
                    }}
                  >
                    ✕
                  </button>
                </span>
              )}

              <button type="button" className="create-case-btn queue-new-case" onClick={onCreateCase}>
                <span aria-hidden="true">+ </span>New case
              </button>
            </div>

            {deleteError && (
              <div className="toast show mixed" role="alert">
                ⚠ {deleteError}
              </div>
            )}

            {error ? (
              <div className="no-results">
                <div>{error}</div>

                <button
                  type="button"
                  className="go-btn queue-retry"
                  onClick={queue.reload}
                >
                  Retry
                </button>
              </div>
            ) : !hasLoaded ? (
              <div className="case-table" aria-busy="true">
                <span className="sr-only" role="status">Loading cases…</span>

                <div className="case-table-head" aria-hidden="true">
                  <span>Case</span>
                  <span>Company name</span>
                  <span>Status</span>
                  <span className="case-table-head-center">Risk</span>
                  <span className="case-table-head-center">Created</span>
                </div>

                <ul className="case-table-body" aria-hidden="true">
                  {Array.from({ length: 8 }, (_, row) => (
                    <li key={row} className="case-row">
                      <div className="case-row-open">
                        <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--id" /></span>
                        <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--name" /></span>
                        <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--status" /></span>
                        <span className="skeleton-cell skeleton-cell--center"><span className="skeleton-bar skeleton-bar--pill" /></span>
                        <span className="skeleton-cell skeleton-cell--center"><span className="skeleton-bar skeleton-bar--date" /></span>
                      </div>
                      <span className="case-row-menu-space" />
                    </li>
                  ))}
                </ul>
              </div>
            ) : items.length > 0 ? (
              <div className={`case-table${isFetching ? ' case-table--busy' : ''}`} aria-busy={isFetching}>
                <div className="case-table-head" aria-hidden="true">
                  <span>Case</span>
                  <span>Company name</span>
                  <span>Status</span>
                  <span className="case-table-head-center">Risk</span>
                  <span className="case-table-head-center">Created</span>
                </div>

                <ul className="case-table-body">
                  {items.map((caseRecord) => (
                    <CaseRow
                      key={caseRecord.id}
                      caseRecord={caseRecord}
                      onOpen={onOpenCase}
                      onDelete={setCaseToDelete}
                      onCaseUpdated={(caseId, companyName) => queue.patchItem(caseId, { applicant: companyName })}
                      isDeleting={deletingCaseId === caseRecord.id}
                    />
                  ))}
                </ul>
              </div>
            ) : (
              <div className="no-results">
                {search
                  ? `No case matches "${search}". Check the case number and try again.`
                  : 'No cases to show.'}
              </div>
            )}

            {hasLoaded && !error && (
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={total}
                onChange={(next) => {
                  goToPage(next);
                  window.scrollTo({ top: 0 });
                }}
              />
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
