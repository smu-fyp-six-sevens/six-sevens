import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import AppHeader from '../components/AppHeader';
import CaseRow from '../components/CaseRow';
import DeletedCasesTable from '../components/DeletedCasesTable';
import ConfirmDialog from '../components/ConfirmDialog';
import HeaderFilter from '../components/HeaderFilter';
import type { FilterOption } from '../components/HeaderFilter';
import Pagination from '../components/Pagination';
import { deleteCase } from '../services/uploadService';
import type { CaseRiskFilter, CaseSort, CaseStage } from '../services/uploadService';
import type { CaseRecord } from '../utils/caseRecord';
import { CASE_STAGES, caseStageLabel, isCaseStage } from '../utils/caseStages';
import { StageMark } from '../components/StageMark';
import { caseNumberLabel } from '../utils/caseSearch';
import { useCaseQueue } from './useCaseQueue';
import { ErrorMessage } from '../components/ErrorMessage';
import { LoadErrorState } from '../components/LoadErrorState';
import { Icon } from '../components/Icon';

interface CaseQueuePageProps {
  onOpenCase: (caseRecord: CaseRecord) => void;
}

const PREFS_STORAGE_KEY = 'caseQueue.prefs';
const PAGE_SIZE = 25;
// Not a stage: deleted cases have no row, so this option reads them from the audit trail.
const DELETED = 'deleted';

// One sort at a time, picked from a column header: the first click puts the newest or highest first (arrow down),
// the second reverses it (arrow up). Equal risks are newest first.
const SORT_KEYS: CaseSort[] = ['newest', 'oldest', 'risk', 'lowest_risk'];

const DEFAULT_SORT: CaseSort = 'newest';

const SORT_CHOICES: { value: CaseSort; label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'risk', label: 'Highest risk first' },
  { value: 'lowest_risk', label: 'Lowest risk first' },
];

const RISK_OPTIONS: { value: CaseRiskFilter; label: string }[] = [
  { value: 'Critical', label: 'Critical' },
  { value: 'High', label: 'High' },
  { value: 'Medium', label: 'Medium' },
  { value: 'Low', label: 'Low' },
  { value: 'unscored', label: 'Not assessed' },
];

interface QueuePrefs {
  statuses: CaseStage[];
  risks: CaseRiskFilter[];
  sort: CaseSort;
}

function isSortKey(value: unknown): value is CaseSort {
  return SORT_KEYS.some((key) => key === value);
}

function savedRisk(item: unknown): CaseRiskFilter | undefined {
  return RISK_OPTIONS.find((option) => option.value === item)?.value;
}

// A column header that sorts: both arrows show, the one for the current direction is dark.
function SortHeader({ label, first, second, sort, disabled, onSort }: {
  label: string;
  first: CaseSort;
  second: CaseSort;
  sort: CaseSort;
  disabled: boolean;
  onSort: (next: CaseSort) => void;
}) {
  const direction = sort === first ? 'desc' : sort === second ? 'asc' : undefined;
  const state = direction === 'desc' ? 'high to low' : direction === 'asc' ? 'low to high' : 'not sorted';

  return (
    <button
      type="button"
      className="case-sort"
      data-direction={direction}
      disabled={disabled}
      aria-label={`Sort by ${label.toLowerCase()} (${state})`}
      onClick={() => onSort(sort === first ? second : first)}
    >
      {label}
      <svg className="case-sort-arrows" viewBox="0 0 8 14" aria-hidden="true">
        <path className="case-sort-up" d="M4 0 8 5H0z" />
        <path className="case-sort-down" d="M4 14 0 9h8z" />
      </svg>
    </button>
  );
}

function TableHead({ sort, disabled, statusFilter, riskFilter, onSort }: {
  sort: CaseSort;
  disabled: boolean;
  statusFilter: ReactNode;
  riskFilter: ReactNode;
  onSort: (next: CaseSort) => void;
}) {
  return (
    <div className="case-table-head">
      <span>Case</span>
      <span>Company name</span>
      <span className="case-table-head-cell">{statusFilter}</span>
      <span className="case-table-head-cell">
        <SortHeader label="Risk" first="risk" second="lowest_risk" sort={sort} disabled={disabled} onSort={onSort} />
        {riskFilter}
      </span>
      <span className="case-table-head-center">
        <SortHeader label="Created" first="newest" second="oldest" sort={sort} disabled={disabled} onSort={onSort} />
      </span>
    </div>
  );
}

// A saved status is a stage; older saves hold a documents status or label the queue no longer filters on.
function savedStatus(item: unknown): CaseStage | undefined {
  return typeof item === 'string' && isCaseStage(item) ? item : undefined;
}

// Per-browser (localStorage), not per-account — enough to stop having to
// re-apply the same filters and sort on every visit. Wrapped in try/catch
// since storage can throw (private mode, blocked site data) or hold junk.
function loadPrefs(): QueuePrefs {
  const defaults: QueuePrefs = { statuses: [], risks: [], sort: DEFAULT_SORT };

  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);

    if (!raw) return defaults;

    // The oldest shape kept a date direction and a separate "highest risk first" switch.
    const parsed = JSON.parse(raw) as { statuses?: unknown; risks?: unknown; sort?: unknown; sortOrder?: string; riskFirst?: boolean };
    const sort: CaseSort = isSortKey(parsed.sort)
      ? parsed.sort
      : parsed.riskFirst === true
        ? 'risk'
        : parsed.sortOrder === 'asc'
          ? 'oldest'
          : DEFAULT_SORT;

    return {
      statuses: Array.isArray(parsed.statuses)
        ? parsed.statuses.map(savedStatus).filter((status): status is CaseStage => status !== undefined)
        : [],
      risks: Array.isArray(parsed.risks)
        ? parsed.risks.map(savedRisk).filter((risk): risk is CaseRiskFilter => risk !== undefined)
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

export default function CaseQueuePage({ onOpenCase }: CaseQueuePageProps) {
  // Set by the header search's "See all results" (see AppHeader).
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const requestedPage = Math.max(1, Math.floor(Number(searchParams.get('page'))) || 1);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [caseToDelete, setCaseToDelete] = useState<CaseRecord | null>(null);
  // The Status filter's "Deleted": the table then lists deleted cases (from the audit trail) instead.
  const [showDeleted, setShowDeleted] = useState(false);
  const [deletedCount, setDeletedCount] = useState<number | null>(null);
  // Read localStorage once, on the first render; the settings start from it.
  const [initialPrefs] = useState(loadPrefs);
  const [savedStatuses, setSavedStatuses] = useState<Set<CaseStage>>(() => new Set(initialPrefs.statuses));
  const [risks, setRisks] = useState<Set<CaseRiskFilter>>(() => new Set(initialPrefs.risks));
  const [sort, setSort] = useState<CaseSort>(initialPrefs.sort);

  useEffect(() => {
    savePrefs({ statuses: Array.from(savedStatuses), risks: Array.from(risks), sort });
  }, [savedStatuses, risks, sort]);

  // The sidebar lists the statuses cases are in, so it learns them from the server's counts.
  const [counts, setCounts] = useState<Map<CaseStage, number> | null>(null);

  // A saved status no case has right now has no chip to switch it off, so it must not filter anything: only
  // statuses that currently exist count as active. Until the counts arrive, the saved ones are used as they are.
  const statusOptions = useMemo(
    () => (counts ? CASE_STAGES.filter((status) => (counts.get(status) ?? 0) > 0) : []),
    [counts],
  );
  const activeStatuses = useMemo(
    () => new Set(counts ? statusOptions.filter((status) => savedStatuses.has(status)) : savedStatuses),
    [counts, statusOptions, savedStatuses],
  );
  const activeList = useMemo(() => CASE_STAGES.filter((status) => activeStatuses.has(status)), [activeStatuses]);
  const riskList = useMemo(() => RISK_OPTIONS.map((option) => option.value).filter((risk) => risks.has(risk)), [risks]);

  const queue = useCaseQueue({
    q: search.trim(), sort, statuses: activeList, risks: riskList, page: requestedPage, pageSize: PAGE_SIZE,
  });
  const { items, total, hasLoaded, isFetching, error } = queue;

  useEffect(() => {
    if (queue.counts) {
      setCounts(new Map(Object.entries(queue.counts.by_status) as [CaseStage, number][]));
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
  function changeStatuses(next: Set<CaseStage>) {
    setSavedStatuses(next);
    goToPage(1);
  }

  function changeRisks(next: Set<CaseRiskFilter>) {
    setRisks(next);
    goToPage(1);
  }

  function changeSort(next: CaseSort) {
    setSort(next);
    goToPage(1);
  }

  const statusFilterOptions: FilterOption[] = [
    ...statusOptions.map((status) => ({
      value: status,
      text: caseStageLabel(status),
      count: counts?.get(status) ?? 0,
      label: (
        <>
          <StageMark stage={status} />
          {caseStageLabel(status)}
        </>
      ),
    })),
    { value: DELETED, text: 'Deleted', label: 'Deleted', separate: true },
  ];
  const riskFilterOptions: FilterOption[] = RISK_OPTIONS.map((option) => ({
    value: option.value,
    text: option.label,
    label: (
      <span className={`risk-badge risk-badge--${option.value === 'unscored' ? 'none' : option.value.toLowerCase()} queue-risk-badge`}>
        {option.label}
      </span>
    ),
  }));

  const statusFilter = (
    <HeaderFilter
      name="status"
      title="Status"
      options={statusFilterOptions}
      selected={showDeleted ? new Set([DELETED]) : activeStatuses}
      disabled={!counts}
      onChange={(next) => {
        setShowDeleted(next.has(DELETED));
        if (!next.has(DELETED)) changeStatuses(next as Set<CaseStage>);
      }}
    />
  );
  // The header's own risk filter is just a funnel next to the sort arrows; the small-screen row has no header, so it is titled.
  const riskFilterWith = (title?: string) => (
    <HeaderFilter
      name="risk"
      title={title}
      options={riskFilterOptions}
      selected={risks}
      disabled={!hasLoaded}
      onChange={(next) => changeRisks(next as Set<CaseRiskFilter>)}
    />
  );
  const riskFilter = riskFilterWith();

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
        <div className="queue-bar">
          <h1 className="queue-title">
            {showDeleted
              ? deletedCount === null ? 'Deleted cases' : `Deleted cases (${deletedCount})`
              : hasLoaded ? `Cases (${total})` : 'Cases'}
          </h1>

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
                <Icon name="x" size={13} />
              </button>
            </span>
          )}

          {!error && !showDeleted && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
              loading={!hasLoaded}
              onChange={(next) => {
                goToPage(next);
                window.scrollTo({ top: 0 });
              }}
            />
          )}
        </div>

        {/* Where the table has no column headers (narrow screens), the same filters and the sort live here. */}
        <div className="queue-controls">
          {statusFilter}
          {!showDeleted && (
            <>
              {riskFilterWith('Risk')}

              <label className="queue-sort-select">
                <span className="sr-only">Sort cases</span>
                <select value={sort} onChange={(event) => changeSort(event.target.value as CaseSort)}>
                  {SORT_CHOICES.map((choice) => (
                    <option key={choice.value} value={choice.value}>{choice.label}</option>
                  ))}
                </select>
              </label>
            </>
          )}
        </div>

        {deleteError && <ErrorMessage className="toast-error" message={deleteError} />}

        {showDeleted ? (
          <DeletedCasesTable search={search.trim()} statusFilter={statusFilter} onCount={setDeletedCount} />
        ) : error ? (
          <LoadErrorState message={error} onRetry={queue.reload} />
        ) : !hasLoaded ? (
          <div className="case-table" aria-busy="true">
            <span className="sr-only" role="status">Loading cases…</span>

            <TableHead sort={sort} disabled statusFilter={statusFilter} riskFilter={riskFilter} onSort={changeSort} />

            <ul className="case-table-body" aria-hidden="true">
              {Array.from({ length: 8 }, (_, row) => (
                <li key={row} className="case-row">
                  <div className="case-row-open">
                    <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--id" /></span>
                    <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--name" /></span>
                    <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--status" /></span>
                    <span className="skeleton-cell"><span className="skeleton-bar skeleton-bar--badge" /></span>
                    <span className="skeleton-cell skeleton-cell--center"><span className="skeleton-bar skeleton-bar--date" /></span>
                  </div>
                  <span className="case-row-menu-space" />
                </li>
              ))}
            </ul>
          </div>
        ) : items.length > 0 ? (
          <div className={`case-table${isFetching ? ' case-table--busy' : ''}`} aria-busy={isFetching}>
            <TableHead sort={sort} disabled={false} statusFilter={statusFilter} riskFilter={riskFilter} onSort={changeSort} />

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
        ) : activeList.length > 0 || riskList.length > 0 ? (
          // A filter that matches nothing keeps the header, the only place on a wide screen to switch it off.
          <div className="case-table">
            <TableHead sort={sort} disabled={false} statusFilter={statusFilter} riskFilter={riskFilter} onSort={changeSort} />

            <div className="case-table-empty">
              <span>No cases match these filters.</span>
              <button
                type="button"
                className="case-table-clear"
                onClick={() => {
                  changeStatuses(new Set());
                  changeRisks(new Set());
                }}
              >
                Clear filters
              </button>
            </div>
          </div>
        ) : (
          <div className="no-results">
            {search
              ? `No case matches "${search}". Check the case number and try again.`
              : 'No cases to show.'}
          </div>
        )}
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
