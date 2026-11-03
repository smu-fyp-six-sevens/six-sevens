import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import HeaderFilter, { type FilterOption } from '../components/HeaderFilter';
import { RefreshButton } from '../components/RefreshButton';
import { DashboardSkeleton } from '../components/Skeletons';
import { InfoIcon, InfoLabel } from '../components/InfoTip';
import { CasePipeline } from './CasePipeline';
import { DetectionAccuracyPanel, DetectionAccuracySummary } from './DetectionAccuracyPanel';
import { CoverageRings, FlagPills, RiskOverview } from './DashboardInsights';
import { CasesOverTime, MostRequestedDocuments, NeedsAttention, ProcessingAndCost, type AttentionCase } from './DashboardCasePanels';
import { CASE_STAGES, caseStageLabel, caseStageTone } from '../utils/caseStages';
import { StageMark } from '../components/StageMark';
import { formatDuration } from './upload/processingTimes';
import { formatDocumentType, type CaseStage, type DetectionMetricsFilters } from '../services/uploadService';
import { formatCheck } from '../utils/checkLabels';
import { Icon } from '../components/Icon';

// A case stage's label (utils/caseStages.ts), so the dashboard and the case queue name and colour cases the same way.
type Status = string;
type Risk = 'Critical' | 'High' | 'Medium' | 'Low';
type Role = 'Manager' | 'Analyst';

// Manager view is kept but hidden for now; flip to true to show the role switch again.
const SHOW_ROLE_SWITCH = false;
type DashboardTab = 'Overview' | 'Cases' | 'Flags' | 'Accuracy';
const DASHBOARD_TABS: DashboardTab[] = ['Overview', 'Cases', 'Flags', 'Accuracy'];

interface MockCase {
  id: string;
  routeId?: string;
  // A live case's id, for what the audit trail holds about it (its cost); absent for preview rows.
  caseId?: string;
  // A document held up past its time limit.
  stuck?: boolean;
  applicant: string;
  submitted: string;
  status: Status;
  // Null until the case's analysis has scored it.
  risk: Risk | null;
  score: number | null;
  // Titles of the documents to request; null until the case has been analysed.
  recommendedDocs?: string[] | null;
  // Automated processing time in ms; null until the case has been analysed.
  processingMs?: number | null;
  // Preview rows only: who the sample case is assigned to.
  analyst?: string;
  documentTypes: string[];
  flags: string[];
  // Per flag name: its worst severity in this case and the document types it's on; absent for preview rows.
  flagInfo?: Record<string, { severity: FlagSummary['severity']; documentTypes: string[] }>;
}

interface FlagSummary {
  name: string;
  severity: 'High' | 'Medium' | 'Low';
  cases: MockCase[];
  documentTypes: string[];
}

const MOCK_CASES: MockCase[] = [
  { id: 'GLDB-02481', applicant: 'Meridian Consulting Group', submitted: '2026-10-18', status: 'Ready to Finalise', risk: 'High', score: 82, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Payslip'], flags: ['Identity mismatch', 'Balance inconsistency', 'Unverified address'] },
  { id: 'GLDB-02480', applicant: 'Northstar Trading Pte Ltd', submitted: '2026-10-17', status: 'Processing Documents', risk: 'Medium', score: 58, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02479', applicant: 'Harbourlight Foods', submitted: '2026-10-16', status: 'Awaiting Documents', risk: 'High', score: 77, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Certificate'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02478', applicant: 'Cedar & Stone Pte Ltd', submitted: '2026-10-15', status: 'Failed', risk: 'Low', score: 18, analyst: 'M. Goh', documentTypes: ['Bank statement', 'Payslip'], flags: [] },
  { id: 'GLDB-02477', applicant: 'Orchid Systems', submitted: '2026-10-13', status: 'Ready to Finalise', risk: 'High', score: 91, analyst: 'A. Lim', documentTypes: ['Certificate', 'Proof of address'], flags: ['Invalid registration number', 'Identity mismatch', 'Font anomaly'] },
  { id: 'GLDB-02476', applicant: 'Brightpath Logistics', submitted: '2026-10-12', status: 'Processing Documents', risk: 'Medium', score: 49, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Certificate'], flags: ['Balance inconsistency'] },
  { id: 'GLDB-02475', applicant: 'Evergreen Medical Supplies', submitted: '2026-10-10', status: 'Processing Documents', risk: 'Low', score: 22, analyst: 'M. Goh', documentTypes: ['Payslip', 'Proof of address'], flags: [] },
  { id: 'GLDB-02474', applicant: 'Silverline Manufacturing', submitted: '2026-10-08', status: 'Awaiting Documents', risk: 'Medium', score: 64, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02473', applicant: 'Bluepeak Design Studio', submitted: '2026-09-29', status: 'Ready to Finalise', risk: 'Medium', score: 61, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Bank statement'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02472', applicant: 'Pioneer Green Energy', submitted: '2026-09-22', status: 'Processing Documents', risk: 'Low', score: 31, analyst: 'M. Goh', documentTypes: ['Certificate', 'Payslip'], flags: ['Company detail mismatch'] },
];
// Preview rows only: live cases carry their real time from the case list.
const SAMPLE_PROCESSING_MS = [184_000, 170_000, 237_000, 125_000, 245_000, 160_000, 188_000, 264_000, 143_000, 179_000];
const PROCESSING_TIME_HELP = 'Total time taken from upload to completed case analysis, excluding manual review. Shown once the case has been analysed.';

const STATUSES: Status[] = CASE_STAGES.map((stage) => caseStageLabel(stage));
// A stage's label back to the stage, for its colour and for the accuracy filter the backend applies.
const STAGE_BY_LABEL = new Map(CASE_STAGES.map((stage) => [caseStageLabel(stage), stage]));

function statusTone(status: Status): string {
  return caseStageTone(STAGE_BY_LABEL.get(status));
}

function statusStage(status: Status): string | undefined {
  return STAGE_BY_LABEL.get(status);
}
const SEVERITY_RANK: Record<FlagSummary['severity'], number> = { High: 3, Medium: 2, Low: 1 };
function flagSeverity(value: string): FlagSummary['severity'] {
  return value === 'high' ? 'High' : value === 'low' ? 'Low' : 'Medium';
}
// Preview rows only: live flags carry their own severity.
const FLAG_SEVERITY: Record<string, FlagSummary['severity']> = {
  'Identity mismatch': 'High',
  'Balance inconsistency': 'High',
  'Invalid registration number': 'High',
  'Invalid postal code': 'Medium',
  'Company detail mismatch': 'Medium',
  'Unverified address': 'Medium',
  'Unusual transaction': 'Medium',
  'Font anomaly': 'Medium',
};

const CASES_PER_PAGE = 10;
// Rows in each Overview insight panel, so the side-by-side panels stay the same height.
const INSIGHT_ROWS = 6;
// Document types are rings, four to a row: two rows before "Show all".
const DOCUMENT_TYPE_ROWS = 8;

// Page buttons to show: always the first and last, the current one and its neighbours, with gaps as null.
function pageNumbers(current: number, count: number): Array<number | null> {
  const wanted = [...new Set([1, current - 1, current, current + 1, count])].filter((page) => page >= 1 && page <= count).sort((a, b) => a - b);
  return wanted.flatMap((page, index) => (index > 0 && page - wanted[index - 1] > 1 ? [null, page] : [page]));
}

function CaseTable({ cases, heading, onOpenCase, showRiskAnalysis = false, showProcessingTime = false, isPreview = false }: { cases: MockCase[]; heading: string; onOpenCase: (item: MockCase) => void; showRiskAnalysis?: boolean; showProcessingTime?: boolean; isPreview?: boolean }) {
  // Back to the first page whenever the filters or sort hand over a different list.
  const [paging, setPaging] = useState({ cases, page: 1 });
  const pageCount = Math.max(1, Math.ceil(cases.length / CASES_PER_PAGE));
  const page = paging.cases === cases ? Math.min(paging.page, pageCount) : 1;
  const goTo = (next: number) => setPaging({ cases, page: next });
  const first = (page - 1) * CASES_PER_PAGE;
  const pageCases = cases.slice(first, first + CASES_PER_PAGE);
  return (
    <section className="dashboard-panel cases-panel">
      <div className="panel-heading"><div><h2>{heading}</h2>{showProcessingTime && isPreview && <p>Sample timings</p>}</div><Link className="text-button" to="/cases">Open case queue <span>→</span></Link></div>
      <div className="case-table-wrap">
        <table className={`dashboard-case-table${showProcessingTime ? ' case-register-table' : showRiskAnalysis ? ' case-overview-table' : ''}`}>
          <thead><tr>
            <th>CASE / APPLICANT</th><th className="center-col">SUBMITTED</th><th className="center-col">STATUS</th>
            <th className="center-col">RISK</th>
            {showProcessingTime && <th className="center-col"><InfoLabel label="PROCESSING TIME" tip={PROCESSING_TIME_HELP} /></th>}
            <th>{showRiskAnalysis ? 'RECOMMENDED ADDITIONAL DOCUMENTS' : 'TOP FLAGS'}</th>
          </tr></thead>
          <tbody>
            {pageCases.map((item) => (
              <tr
                key={item.id}
                className={item.routeId ? 'case-row-clickable' : undefined}
                onClick={() => item.routeId && onOpenCase(item)}
                onKeyDown={(event) => {
                  if (item.routeId && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    onOpenCase(item);
                  }
                }}
                role={item.routeId ? 'link' : undefined}
                tabIndex={item.routeId ? 0 : undefined}
                aria-label={item.routeId ? `Open case ${item.id}` : undefined}
              >
                <td data-label="Case / applicant"><strong>{item.id}</strong><span>{item.applicant}</span></td>
                <td className="center-col" data-label="Submitted">{new Date(`${item.submitted}T12:00:00`).toLocaleDateString('en-SG', { day: '2-digit', month: 'short' })}</td>
                <td className="center-col" data-label="Status"><span className={`case-stage-badge tone-${statusTone(item.status)}`}><StageMark stage={statusStage(item.status)} />{item.status}</span></td>
                <td className="center-col" data-label="Risk">
                  <div className="risk-cell">
                    {item.risk
                      ? <span className={`risk-score risk-score-${item.risk.toLowerCase()}`}><b>{item.score ?? '—'}</b><span className="risk-level">{item.risk}</span></span>
                      : <span className="processing-pending" title="Shown once the case has been analysed">—</span>}
                  </div>
                </td>
                {showProcessingTime ? <td className="center-col" data-label="Processing time">{item.processingMs != null ? <span className="processing-total">{formatDuration(item.processingMs)}</span> : <span className="processing-pending" title="Shown once the case has been analysed">—</span>}</td> : null}
                {showRiskAnalysis
                  ? <td data-label="Recommended additional documents">
                    {item.recommendedDocs?.length
                      ? <span className="recommended-documents">{item.recommendedDocs.join(', ')}</span>
                      : <span className="processing-pending">—</span>}
                  </td>
                  : <td data-label="Top flags">{item.flags.length ? <span className="table-flag">{item.flags[0]}{item.flags.length > 1 ? <small> +{item.flags.length - 1}</small> : null}</span> : <span className="no-flag">No flags</span>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {!cases.length ? <p className="empty-insight table-empty">No cases match the current filters.</p> : null}
      </div>
      <footer className="table-footer">
        <span>{cases.length > CASES_PER_PAGE ? <>Showing <strong>{first + 1}–{first + pageCases.length}</strong> of <strong>{cases.length}</strong> cases</> : <>Showing <strong>{cases.length}</strong> {cases.length === 1 ? 'case' : 'cases'}</>}</span>
        {pageCount > 1 && (
          <nav className="table-pagination" aria-label="Case pages">
            <button type="button" onClick={() => goTo(page - 1)} disabled={page === 1} aria-label="Previous page">‹</button>
            {pageNumbers(page, pageCount).map((number, index) => number === null
              ? <span key={`gap-${index}`} className="pagination-gap">…</span>
              : <button type="button" key={number} className={number === page ? 'active' : ''} aria-current={number === page ? 'page' : undefined} onClick={() => goTo(number)}>{number}</button>)}
            <button type="button" onClick={() => goTo(page + 1)} disabled={page === pageCount} aria-label="Next page">›</button>
          </nav>
        )}
      </footer>
    </section>
  );
}

// Preview rows only: live cases carry their analysis's recommendations from the case list.
function sampleRecommendedDocuments(item: MockCase): string[] {
  const recommendations = new Set<string>();
  item.flags.forEach((flag) => {
    if (flag === 'Identity mismatch') recommendations.add('NRIC / passport');
    if (flag === 'Balance inconsistency' || flag === 'Unusual transaction') recommendations.add('Updated bank statement');
    if (flag === 'Invalid postal code' || flag === 'Unverified address') recommendations.add('Recent proof of address');
    if (flag === 'Company detail mismatch' || flag === 'Invalid registration number') recommendations.add('Company registration documents');
    if (flag === 'Font anomaly') recommendations.add('Original document copy');
  });
  return [...recommendations];
}

function dateLimit(range: string): string | null {
  if (range === 'All time') return null;
  const days = Number(range.split(' ')[1]);
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

function dashboardStatus(label: string): Status {
  return STATUSES.includes(label) ? label : caseStageLabel('open');
}

export default function DashboardPage({
  cases: caseRecords,
  isLoading,
  isRefreshing,
  onRefresh,
}: {
  cases: CaseRecord[];
  isLoading: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [role, setRole] = useState<Role>('Analyst');
  // The tab lives in the URL, so a reload stays on it.
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = DASHBOARD_TABS.find((item) => item.toLowerCase() === searchParams.get('tab')) ?? 'Overview';
  const setTab = (next: DashboardTab) => setSearchParams((params) => {
    if (next === 'Overview') params.delete('tab');
    else params.set('tab', next.toLowerCase());
    return params;
  });
  const [range, setRange] = useState('Last 30 days');
  // Empty: every status, as on the case queue.
  const [statusFilter, setStatusFilter] = useState<Set<Status>>(new Set());
  const [riskOrder, setRiskOrder] = useState<'highest' | 'lowest'>('highest');
  const [showAllDocTypes, setShowAllDocTypes] = useState(false);
  // Null: no column chosen, so the default order (most cases first).
  const [flagSort, setFlagSort] = useState<{ key: 'severity' | 'cases'; descending: boolean } | null>(null);

  const dashboardCases = useMemo(() => caseRecords.length
    ? caseRecords.map((record, index) => {
      const example = MOCK_CASES[index % MOCK_CASES.length];
      return {
        ...example,
        id: record.caseNumber || record.id,
        routeId: record.caseNumber || record.id,
        caseId: record.id,
        stuck: record.stuck ?? false,
        applicant: record.applicant,
        submitted: record.created_at?.slice(0, 10) || example.submitted,
        status: dashboardStatus(record.status),
        risk: record.riskLevel ?? null,
        score: record.riskScore ?? null,
        processingMs: record.processingMs ?? null,
        recommendedDocs: record.recommendedDocuments ?? null,
        analyst: undefined,
        documentTypes: (record.documentTypes ?? []).map((type) => formatDocumentType(type)),
        flags: (record.flags ?? []).map((flag) => formatCheck(flag.check)),
        flagInfo: Object.fromEntries((record.flags ?? []).map((flag) => [formatCheck(flag.check), {
          severity: flagSeverity(flag.severity),
          documentTypes: flag.documentTypes.map((type) => formatDocumentType(type)),
        }])),
      };
    })
    : MOCK_CASES.map((item, index) => ({
      ...item,
      processingMs: SAMPLE_PROCESSING_MS[index],
      recommendedDocs: sampleRecommendedDocuments(item),
    })),
  [caseRecords]);

  // Every case, whoever created it: the date and status filters are the only narrowing.
  const casesInRange = useMemo(() => {
    const since = dateLimit(range);
    return dashboardCases.filter((item) => !since || item.submitted >= since);
  }, [dashboardCases, range]);
  const visibleCases = useMemo(
    () => casesInRange.filter((item) => !statusFilter.size || statusFilter.has(item.status)),
    [casesInRange, statusFilter],
  );

  // The same date and status choice for the accuracy figures, which the backend filters (cases are the reviews' own).
  const accuracyFilters = useMemo<DetectionMetricsFilters>(() => ({
    since: dateLimit(range),
    statuses: [...statusFilter].flatMap((status) => STAGE_BY_LABEL.get(status) ?? []),
  }), [range, statusFilter]);

  const statusCounts = useMemo(() => Object.fromEntries(
    STATUSES.map((status) => [status, visibleCases.filter((item) => item.status === status).length]),
  ) as Record<Status, number>, [visibleCases]);
  // The filter's counts ignore the status filter itself, like the case queue's.
  const stageCounts = useMemo(
    () => Object.fromEntries(CASE_STAGES.map((stage) => [stage, statusCounts[caseStageLabel(stage)]])) as Record<CaseStage, number>,
    [statusCounts],
  );
  const statusOptions: FilterOption[] = STATUSES.map((status) => ({
    value: status,
    text: status,
    count: casesInRange.filter((item) => item.status === status).length,
    label: <><StageMark stage={statusStage(status)} />{status}</>,
  }));

  const byDocumentType = useMemo(() => {
    const counts = new Map<string, number>();
    visibleCases.forEach((item) => item.documentTypes.forEach((type) => {
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [visibleCases]);

  const flagRows = useMemo<FlagSummary[]>(() => {
    const casesByFlag = new Map<string, MockCase[]>();
    visibleCases.forEach((item) => item.flags.forEach((flag) => {
      casesByFlag.set(flag, [...(casesByFlag.get(flag) ?? []), item]);
    }));
    return [...casesByFlag.entries()].map(([name, cases]) => ({
      name,
      cases,
      // The worst severity any case has for this flag, and only the document types it was found on.
      severity: cases.map((item) => item.flagInfo?.[name]?.severity ?? FLAG_SEVERITY[name] ?? 'Medium')
        .sort((a, b) => SEVERITY_RANK[b] - SEVERITY_RANK[a])[0],
      documentTypes: [...new Set(cases.flatMap((item) => item.flagInfo?.[name]?.documentTypes ?? item.documentTypes))],
    }));
  }, [visibleCases]);
  const sortedFlagRows = useMemo(() => {
    const severity = (flag: FlagSummary) => SEVERITY_RANK[flag.severity];
    const cases = (flag: FlagSummary) => flag.cases.length;
    const [first, second] = flagSort?.key === 'severity' ? [severity, cases] : [cases, severity];
    const sign = flagSort && !flagSort.descending ? 1 : -1;
    return [...flagRows].sort((a, b) => sign * (first(a) - first(b)) || sign * (second(a) - second(b)));
  }, [flagRows, flagSort]);
  // Each click on a column: high to low, then low to high, then back to no sort.
  const sortFlagsBy = (key: 'severity' | 'cases') => setFlagSort((current) => {
    if (current?.key !== key) return { key, descending: true };
    return current.descending ? { key, descending: false } : null;
  });
  // The case queue's sort header (CaseQueuePage SortHeader): both arrows, the current direction's one dark.
  const sortHeader = (key: 'severity' | 'cases', label: string) => {
    const direction = flagSort?.key === key ? (flagSort.descending ? 'desc' : 'asc') : undefined;
    return (
      <th aria-sort={direction === 'desc' ? 'descending' : direction === 'asc' ? 'ascending' : undefined}>
        <button
          type="button"
          className="case-sort"
          data-direction={direction}
          aria-label={`Sort by ${label.toLowerCase()} (${direction === 'desc' ? 'high to low' : direction === 'asc' ? 'low to high' : 'not sorted'})`}
          onClick={() => sortFlagsBy(key)}
        >
          {label}
          <svg className="case-sort-arrows" viewBox="0 0 8 14" aria-hidden="true">
            <path className="case-sort-up" d="M4 0 8 5H0z" />
            <path className="case-sort-down" d="M4 14 0 9h8z" />
          </svg>
        </button>
      </th>
    );
  };

  // The commonest flags, most cases first, for the Overview.
  const commonFlags = [...flagRows].sort((a, b) => b.cases.length - a.cases.length).slice(0, INSIGHT_ROWS);
  const flaggedCaseCount = visibleCases.filter((item) => item.flags.length > 0).length;
  const highFlagCount = flagRows.filter((item) => item.severity === 'High').length;
  // Each case as Needs attention shows it: why it waits, its most serious flag, and what to request.
  const attentionCases = useMemo<AttentionCase[]>(() => visibleCases.map((item) => {
    const topFlag = item.flags
      .map((name) => ({ name, severity: item.flagInfo?.[name]?.severity ?? FLAG_SEVERITY[name] ?? 'Medium' }))
      .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])[0] ?? null;
    return {
      id: item.id, routeId: item.routeId, applicant: item.applicant, status: item.status, tone: statusTone(item.status),
      stuck: item.stuck ?? false, submitted: item.submitted, risk: item.risk, score: item.score, topFlag,
      toRequest: item.recommendedDocs ?? [],
    };
  }), [visibleCases]);
  const openCase = (item: MockCase) => navigate(`/cases/${encodeURIComponent(item.routeId || item.id)}`);
  const sortedCaseRows = useMemo(() => [...visibleCases].sort((a, b) => {
    // Unscored cases rank below Low, so they sit last for "Critical first" and first for "Low first".
    const rank = (item: MockCase) => (item.risk ? { Critical: 4, High: 3, Medium: 2, Low: 1 }[item.risk] : 0);
    if (riskOrder === 'highest') return rank(b) - rank(a) || (b.score ?? 0) - (a.score ?? 0);
    return rank(a) - rank(b) || (a.score ?? 0) - (b.score ?? 0);
  }), [riskOrder, visibleCases]);

  return (
    <main className="shell dashboard-shell">
      <AppHeader />
      <div className="dashboard-app">
      <main className="dashboard-main">
        <div className="dashboard-sticky-shell">
          <div className="dashboard-content dashboard-sticky-content">
          <div className="dashboard-section-bar">
            <nav className="dashboard-tabs" aria-label="Dashboard sections">
              {DASHBOARD_TABS.map((item) => {
                // No count until the real cases arrive: before that the list is the sample cases.
                const count = isLoading ? null : item === 'Cases' ? visibleCases.length : item === 'Flags' ? flagRows.length : null;
                return (
                  <button key={item} className={tab === item ? 'active' : ''} aria-current={tab === item ? 'page' : undefined} onClick={() => setTab(item)}>
                    <span className="nav-icon" aria-hidden="true"><Icon name={item === 'Overview' ? 'overview' : item === 'Cases' ? 'documents' : item === 'Flags' ? 'flag' : 'approved'} size={15} /></span>{item}
                    {count !== null && <span className="nav-count">{count}</span>}
                  </button>
                );
              })}
            </nav>
            <div className="dashboard-toolbar-actions">
              <div className="dashboard-filters">
                <select aria-label="Date range" value={range} onChange={(event) => setRange(event.target.value)}><option>Last 7 days</option><option>Last 30 days</option><option>Last 90 days</option><option>All time</option></select>
                <HeaderFilter name="status" title="Status" options={statusOptions} selected={statusFilter} disabled={isLoading} onChange={(next) => setStatusFilter(next as Set<Status>)} />
                {tab === 'Cases' && <label className="risk-sort-filter"><span className="sr-only">Sort by risk</span><select value={riskOrder} onChange={(event) => setRiskOrder(event.target.value as 'highest' | 'lowest')}><option value="highest">Critical first</option><option value="lowest">Low first</option></select></label>}
                {/* The dashboard reads the cases on opening; this reads them again (no socket covers the whole list). */}
                <span className="dashboard-refresh"><RefreshButton isRefreshing={isRefreshing} onClick={onRefresh} label="cases" /></span>
              </div>
            {SHOW_ROLE_SWITCH && (
              <div className="role-switch" aria-label="Preview dashboard role">
                {(['Manager', 'Analyst'] as Role[]).map((item) => <button key={item} className={role === item ? 'selected' : ''} onClick={() => { setRole(item); setTab('Overview'); }}>{item}</button>)}
              </div>
            )}
            </div>
          </div>
          </div>
        </div>
        <div className="dashboard-content dashboard-body-content">
          {/* First load: placeholders in the dashboard's own layout, not the sample cases. The Accuracy tab loads its own figures. */}
          {isLoading && tab !== 'Accuracy' ? <DashboardSkeleton /> : <>
          {tab !== 'Flags' && tab !== 'Accuracy' && <CasePipeline counts={stageCounts} total={visibleCases.length} period={range} />}
          {isLoading && !caseRecords.length && <p className="dashboard-data-note" role="status">Loading live case links; preview rows are temporarily not clickable.</p>}
          {!isLoading && !caseRecords.length && <p className="dashboard-data-note">No live cases are loaded. Preview rows are illustrative and cannot open a case record.</p>}

          {tab === 'Overview' && <>
          <section className="dashboard-insight-grid">
            <article className="dashboard-panel flag-insight-panel">
              <div className="panel-heading"><div><h2>Most common flags</h2><p>Issues raised across the cases in view</p></div><button className="text-button" onClick={() => setTab('Flags')}>View flags <span>→</span></button></div>
              {commonFlags.length ? <FlagPills flags={commonFlags.map((flag) => ({ name: flag.name, severity: flag.severity, caseIds: flag.cases.map((item) => item.id) }))} caseCount={visibleCases.length} onOpen={() => setTab('Flags')} /> : <p className="empty-insight">No flags in this date and status selection.</p>}
            </article>
            <article className="dashboard-panel">
              <div className="panel-heading"><div><h2>Documents by type</h2><p>Share of the cases in view that include each document type</p></div>{byDocumentType.length > DOCUMENT_TYPE_ROWS && <button className="text-button" onClick={() => setShowAllDocTypes((value) => !value)}>{showAllDocTypes ? 'Show fewer' : `Show all ${byDocumentType.length}`}</button>}</div>
              {byDocumentType.length ? <CoverageRings types={showAllDocTypes ? byDocumentType : byDocumentType.slice(0, DOCUMENT_TYPE_ROWS)} caseCount={visibleCases.length} /> : <p className="empty-insight">No documents in this date and status selection.</p>}
            </article>
          </section>

          <section className="dashboard-lower-grid">
            <article className="dashboard-panel risk-snapshot-panel">
              <div className="panel-heading"><div><h2>Risk overview</h2><p>Current cases grouped by assessed risk level</p></div></div>
              <RiskOverview cases={visibleCases} />
              <p className="snapshot-note"><InfoIcon /> Consider document findings, case status and review context alongside the risk score.</p>
            </article>
          </section>
          <section className="dashboard-pair-grid">
            <NeedsAttention cases={attentionCases} onOpen={(item) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`)} />
            <MostRequestedDocuments cases={attentionCases} />
          </section>
          <section className="dashboard-pair-grid">
            <CasesOverTime cases={visibleCases} since={dateLimit(range)} />
            <ProcessingAndCost cases={visibleCases} isPreview={!caseRecords.length} />
          </section>
          <DetectionAccuracySummary filters={accuracyFilters} onViewAll={() => setTab('Accuracy')} />
          </>}

          {tab === 'Cases' && <>
            <CaseTable cases={sortedCaseRows} heading="All cases" onOpenCase={openCase} showRiskAnalysis showProcessingTime isPreview={!caseRecords.length} />
          </>}

          {tab === 'Flags' && <>
            <section className="flag-summary-grid">
              <article className="dashboard-stat-card"><span className="stat-caption">Cases with flags</span><span className="stat-number">{flaggedCaseCount}</span><span className="stat-caption">of {visibleCases.length} cases in view</span></article>
              <article className="dashboard-stat-card"><span className="stat-caption">Flag categories</span><span className="stat-number">{flagRows.length}</span><span className="stat-caption">distinct finding types</span></article>
              <article className="dashboard-stat-card"><span className="stat-caption">High severity categories</span><span className="stat-number">{highFlagCount}</span><span className="stat-caption">require closer review</span></article>
            </section>
            <section className="dashboard-panel flag-register">
              <div className="panel-heading"><div><h2>Recurring findings</h2><p>Frequency, severity and affected evidence in the selected case set</p></div><span className="panel-kicker">{range.toUpperCase()}</span></div>
              <div className="case-table-wrap"><table className="dashboard-case-table flag-table"><thead><tr><th>FLAG</th>{sortHeader('severity', 'SEVERITY')}{sortHeader('cases', 'CASES')}<th>DOCUMENT TYPES</th><th>EXAMPLE CASES</th></tr></thead><tbody>{sortedFlagRows.map((flag) => <tr key={flag.name}><td data-label="Flag"><strong className="flag-name">{flag.name}</strong></td><td data-label="Severity"><span className={`flag-severity ${flag.severity.toLowerCase()}`}>{flag.severity}</span></td><td data-label="Cases"><strong>{flag.cases.length}</strong></td><td data-label="Document types"><span className="flag-doc-types">{flag.documentTypes.join(', ')}</span></td><td data-label="Example cases"><span className="flag-case-ids">{flag.cases.slice(0, 2).map((item) => item.id).join(', ')}{flag.cases.length > 2 ? ` +${flag.cases.length - 2} more` : ''}</span></td></tr>)}</tbody></table>{!flagRows.length && <p className="empty-insight table-empty">No flags in this date and status selection.</p>}</div>
              <footer className="table-footer"><span>Showing <strong>{flagRows.length}</strong> flag categories</span></footer>
            </section>
          </>}
          {tab === 'Accuracy' && <DetectionAccuracyPanel filters={accuracyFilters} />}
          </>}
        </div>
      </main>
      </div>
    </main>
  );
}
