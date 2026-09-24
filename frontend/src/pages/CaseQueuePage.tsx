import { useEffect, useMemo, useState } from 'react';
import AppHeader from '../components/AppHeader';
import CaseCard from '../components/CaseCard';
import { hideDevFeatures } from '../lib/api';
import type { CaseRecord } from '../App';

interface CaseQueuePageProps {
  cases: CaseRecord[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenCase: (caseRecord: CaseRecord) => void;
  onOpenTestAnomaly: () => void;
  onCreateCase: () => void;
}

// One colour per status label (see mapStatus in App.tsx); anything else
// falls back to neutral.
const STATUS_CHIP_CLASS: Record<string, string> = {
  'Ready for review': 'status-chip--green',
  'In progress': 'status-chip--blue',
  'Documents failed': 'status-chip--red',
  'Processing stuck': 'status-chip--amber',
  'Documents pending': 'status-chip--grey',
};

const PREFS_STORAGE_KEY = 'caseQueue.prefs';

interface QueuePrefs {
  statuses: string[];
  sortOrder: 'asc' | 'desc';
}

// Per-browser (localStorage), not per-account — enough to stop having to
// re-apply the same filters and sort on every visit. Wrapped in try/catch
// since storage can throw (private mode, blocked site data) or hold junk.
function loadPrefs(): QueuePrefs {
  const defaults: QueuePrefs = { statuses: [], sortOrder: 'asc' };

  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);

    if (!raw) return defaults;

    const parsed = JSON.parse(raw) as Partial<QueuePrefs>;

    return {
      statuses: Array.isArray(parsed.statuses)
        ? parsed.statuses.filter((item) => typeof item === 'string')
        : [],
      sortOrder: parsed.sortOrder === 'desc' ? 'desc' : 'asc',
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

function displayCaseNumber(caseRecord: CaseRecord): string {
  return caseRecord.caseNumber || caseRecord.id;
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
  onOpenTestAnomaly,
  onCreateCase,
}: CaseQueuePageProps) {
  const [search, setSearch] = useState('');
  const [savedStatuses, setSavedStatuses] = useState<Set<string>>(
    () => new Set(loadPrefs().statuses),
  );
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(
    () => loadPrefs().sortOrder,
  );

  useEffect(() => {
    savePrefs({ statuses: Array.from(savedStatuses), sortOrder });
  }, [savedStatuses, sortOrder]);

  const statusOptions = useMemo(() => {
    const statuses = cases
      .map((item) => item.status)
      .filter(Boolean);

    return Array.from(new Set(statuses));
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
      const matchesSearch =
        !query ||
        [displayCaseNumber(item), item.applicant]
          .some((value) => value.toLowerCase().includes(query));

      // No chip active = no status filter; otherwise a case matches if its
      // status is any of the active ones.
      const matchesStatus =
        activeStatuses.size === 0 ||
        activeStatuses.has(item.status);

      return matchesSearch && matchesStatus;
    });

    return [...filteredCases].sort((a, b) => {
      const timeA = creationTime(a);
      const timeB = creationTime(b);

      // Cases with no creation date sink to the bottom in either direction.
      if (timeA === null && timeB === null) return 0;
      if (timeA === null) return 1;
      if (timeB === null) return -1;

      return sortOrder === 'asc' ? timeA - timeB : timeB - timeA;
    });
  }, [cases, search, activeStatuses, sortOrder]);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const query = search.trim().toLowerCase();

    if (!query) return;

    const exact = cases.find((item) =>
      [displayCaseNumber(item), item.id]
        .some((value) => value.toLowerCase() === query),
    );

    if (exact) {
      onOpenCase(exact);
    }
  }

  return (
    <main className="shell">
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
            + Create new case
          </button>
        </div>

        <p className="sub">
          Search by case number or applicant, or pick from your assigned cases below.
        </p>

        <form className="search-row" onSubmit={handleSubmit}>
          <label className="search-box">
            <span aria-hidden="true">⌕</span>

            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Enter case number, e.g. GLDB-22841"
              aria-label="Search case number or applicant"
            />
          </label>

          <button className="go-btn" type="submit">
            Go
          </button>
        </form>

        <div className="queue-filters">
          <div className="filter-row">
            <span className="filter-label">Filter by status</span>

            <div className="status-chips">
              {statusOptions.map((status) => {
                const isActive = activeStatuses.has(status);

                return (
                  <button
                    key={status}
                    type="button"
                    aria-pressed={isActive}
                    className={`status-chip ${
                      STATUS_CHIP_CLASS[status] ?? 'status-chip--grey'
                    } ${isActive ? 'status-chip--active' : ''}`}
                    onClick={() => toggleStatus(status)}
                  >
                    {status}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="filter-row">
            <span className="filter-label">Creation date</span>

            <button
              type="button"
              className="sort-arrow-btn"
              aria-label={
                sortOrder === 'asc'
                  ? 'Sorted by creation date, oldest first. Click for newest first.'
                  : 'Sorted by creation date, newest first. Click for oldest first.'
              }
              title={sortOrder === 'asc' ? 'Oldest first' : 'Newest first'}
              onClick={() =>
                setSortOrder((current) => (current === 'asc' ? 'desc' : 'asc'))
              }
            >
              {sortOrder === 'asc' ? '↑' : '↓'}
            </button>
          </div>
        </div>

        <div className="queue-label">
          Assigned to you ({matches.length})
          {!hideDevFeatures && (
            <button type="button" className="dev-tool-link" onClick={onOpenTestAnomaly}>
              Test anomaly detection →
            </button>
          )}
        </div>

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
          matches.map((caseRecord) => (
            <CaseCard
              key={caseRecord.id}
              caseRecord={caseRecord}
              onOpen={onOpenCase}
            />
          ))
        ) : (
          <div className="no-results">
            No case matches "{search}". Check the case number and try again.
          </div>
        )}
      </section>
    </main>
  );
}
