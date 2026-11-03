import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import HeaderFilter, { type FilterOption } from '../components/HeaderFilter';
import PeriodMenu, { CUSTOM_RANGE } from '../components/PeriodMenu';
import CompareMenu from '../components/CompareMenu';
import CompareDelta from '../components/CompareDelta';
import { RefreshButton } from '../components/RefreshButton';
import { DashboardSkeleton } from '../components/Skeletons';
import { InfoIcon } from '../components/InfoTip';
import { DetectionAccuracyPanel, DetectionAccuracySummary } from './DetectionAccuracyPanel';
import { CoverageRings, FlagPills, RiskOverview } from './DashboardInsights';
import { CasesOverTime, MostRequestedDocuments, NeedsAttention, ProcessingAndCost, type AttentionCase } from './DashboardCasePanels';
import { useCaseCosts } from './dashboardCosts';
import { CheckBreakdown, CheckDocumentMatrix, FlagKpis, FlagsByRisk, type FlagCase } from './DashboardFlagAnalytics';
import { CaseKpis, CostAgainstTime, RiskScoreSpread, StageBreakdown, TimeAndCostByCase, type AnalyticsCase, type CostState } from './DashboardCaseAnalytics';
import { CASE_STAGES, caseStageLabel, caseStageTone } from '../utils/caseStages';
import { formatDocumentType, type DetectionMetricsFilters } from '../services/uploadService';
import { formatCheck } from '../utils/checkLabels';
import { PRESETS, formatPeriod, inPeriod, isoDay, overlap, presetPeriod, type Period } from '../utils/dateRange';

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
  flagInfo?: Record<string, { severity: FlagSummary['severity']; count: number; documentTypes: string[] }>;
}

interface FlagSummary {
  name: string;
  severity: 'High' | 'Medium' | 'Low';
  cases: MockCase[];
  documentTypes: string[];
}

const MOCK_CASES: MockCase[] = [
  { id: 'GLDB-02481', applicant: 'Meridian Consulting Group', submitted: '2026-10-18', status: 'Case Analysed', risk: 'High', score: 82, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Payslip'], flags: ['Identity mismatch', 'Balance inconsistency', 'Unverified address'] },
  { id: 'GLDB-02480', applicant: 'Northstar Trading Pte Ltd', submitted: '2026-10-17', status: 'Processing Documents', risk: 'Medium', score: 58, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02479', applicant: 'Harbourlight Foods', submitted: '2026-10-16', status: 'Open', risk: 'High', score: 77, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Certificate'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02478', applicant: 'Cedar & Stone Pte Ltd', submitted: '2026-10-15', status: 'Failed', risk: 'Low', score: 18, analyst: 'M. Goh', documentTypes: ['Bank statement', 'Payslip'], flags: [] },
  { id: 'GLDB-02477', applicant: 'Orchid Systems', submitted: '2026-10-13', status: 'Case Analysed', risk: 'High', score: 91, analyst: 'A. Lim', documentTypes: ['Certificate', 'Proof of address'], flags: ['Invalid registration number', 'Identity mismatch', 'Font anomaly'] },
  { id: 'GLDB-02476', applicant: 'Brightpath Logistics', submitted: '2026-10-12', status: 'Processing Documents', risk: 'Medium', score: 49, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Certificate'], flags: ['Balance inconsistency'] },
  { id: 'GLDB-02475', applicant: 'Evergreen Medical Supplies', submitted: '2026-10-10', status: 'Processing Documents', risk: 'Low', score: 22, analyst: 'M. Goh', documentTypes: ['Payslip', 'Proof of address'], flags: [] },
  { id: 'GLDB-02474', applicant: 'Silverline Manufacturing', submitted: '2026-10-08', status: 'Open', risk: 'Medium', score: 64, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02473', applicant: 'Bluepeak Design Studio', submitted: '2026-09-29', status: 'Case Analysed', risk: 'Medium', score: 61, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Bank statement'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02472', applicant: 'Pioneer Green Energy', submitted: '2026-09-22', status: 'Processing Documents', risk: 'Low', score: 31, analyst: 'M. Goh', documentTypes: ['Certificate', 'Payslip'], flags: ['Company detail mismatch'] },
];
// Preview rows only: live cases carry their real time from the case list.
const SAMPLE_PROCESSING_MS = [184_000, 170_000, 237_000, 125_000, 245_000, 160_000, 188_000, 264_000, 143_000, 179_000];

const STATUSES: Status[] = CASE_STAGES.map((stage) => caseStageLabel(stage));
// A stage's label back to the stage, for its colour and for the accuracy filter the backend applies.
const STAGE_BY_LABEL = new Map(CASE_STAGES.map((stage) => [caseStageLabel(stage), stage]));

function statusTone(status: Status): string {
  return caseStageTone(STAGE_BY_LABEL.get(status));
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

// Rows in each Overview insight panel, so the side-by-side panels stay the same height.
const INSIGHT_ROWS = 6;
// Document types are rings, four to a row: two rows before "Show all".
const DOCUMENT_TYPE_ROWS = 8;

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

// Everything the panels read from a set of cases, worked out the same way for the period and the comparison.
function deriveView(cases: DashboardCase[], costs: Record<string, number> | null) {
  const statusCounts = Object.fromEntries(STATUSES.map((status) => [status, cases.filter((item) => item.status === status).length])) as Record<Status, number>;

  const typeCounts = new Map<string, number>();
  cases.forEach((item) => item.documentTypes.forEach((type) => typeCounts.set(type, (typeCounts.get(type) ?? 0) + 1)));
  const byDocumentType = [...typeCounts.entries()].sort((a, b) => b[1] - a[1]);

  const casesByFlag = new Map<string, DashboardCase[]>();
  cases.forEach((item) => item.flags.forEach((flag) => casesByFlag.set(flag, [...(casesByFlag.get(flag) ?? []), item])));
  const flagRows: FlagSummary[] = [...casesByFlag.entries()].map(([name, flagged]) => ({
    name,
    cases: flagged,
    // The worst severity any case has for this flag, and only the document types it was found on.
    severity: flagged.map((item) => item.flagInfo?.[name]?.severity ?? FLAG_SEVERITY[name] ?? 'Medium')
      .sort((a, b) => SEVERITY_RANK[b] - SEVERITY_RANK[a])[0],
    documentTypes: [...new Set(flagged.flatMap((item) => item.flagInfo?.[name]?.documentTypes ?? item.documentTypes))],
  }));

  // Each case's flags as the Flags tab counts them: per check, its severity, flag count and document types.
  const flagCases: FlagCase[] = cases.map((item) => ({
    id: item.id, routeId: item.routeId, applicant: item.applicant, risk: item.risk,
    flags: item.flags.map((name) => ({
      name,
      severity: item.flagInfo?.[name]?.severity ?? FLAG_SEVERITY[name] ?? 'Medium',
      count: item.flagInfo?.[name]?.count ?? 1,
      documentTypes: item.flagInfo?.[name]?.documentTypes ?? item.documentTypes,
    })),
  }));

  // Each case as Needs attention shows it: why it waits, its most serious flag, and what to request.
  const attentionCases: AttentionCase[] = cases.map((item) => {
    const topFlag = item.flags
      .map((name) => ({ name, severity: item.flagInfo?.[name]?.severity ?? FLAG_SEVERITY[name] ?? 'Medium' }))
      .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])[0] ?? null;
    return {
      id: item.id, routeId: item.routeId, applicant: item.applicant, status: item.status, tone: statusTone(item.status),
      stuck: item.stuck ?? false, submitted: item.submitted, risk: item.risk, score: item.score, topFlag,
      toRequest: item.recommendedDocs ?? [],
    };
  });

  const analyticsCases: AnalyticsCase[] = cases.map((item) => ({
    id: item.id, routeId: item.routeId, applicant: item.applicant, status: item.status, tone: statusTone(item.status),
    risk: item.risk, score: item.score, processingMs: item.processingMs ?? null,
    cost: item.caseId && costs?.[item.caseId] != null ? costs[item.caseId] : null,
  }));

  return { cases, statusCounts, byDocumentType, flagRows, flagCases, attentionCases, analyticsCases };
}
type DashboardView = ReturnType<typeof deriveView>;

type DashboardCase = MockCase;

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
  // A preset's name, or "Custom range" with the custom period.
  const [rangeChoice, setRangeChoice] = useState<string>('Last 30 days');
  const [customPeriod, setCustomPeriod] = useState<Period | null>(null);
  // The period picked on the Compare calendar; null when not comparing.
  const [comparePeriod, setComparePeriod] = useState<Period | null>(null);
  // Empty: every status, as on the case queue.
  const [statusFilter, setStatusFilter] = useState<Set<Status>>(new Set());
  const [showAllDocTypes, setShowAllDocTypes] = useState(false);

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
        // The day it was created in the officer's time zone, as the date range and calendar count days.
        submitted: record.created_at ? isoDay(new Date(record.created_at)) : example.submitted,
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
          count: flag.count,
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

  const period = useMemo(() => (rangeChoice === CUSTOM_RANGE && customPeriod ? customPeriod : presetPeriod(rangeChoice)), [rangeChoice, customPeriod]);
  const rangeLabel = rangeChoice === CUSTOM_RANGE && customPeriod ? formatPeriod(customPeriod) : rangeChoice;

  // Every case, whoever created it: the date and status filters are the only narrowing.
  const casesInRange = useMemo(() => dashboardCases.filter((item) => inPeriod(item.submitted, period)), [dashboardCases, period]);
  const visibleCases = useMemo(
    () => casesInRange.filter((item) => !statusFilter.size || statusFilter.has(item.status)),
    [casesInRange, statusFilter],
  );
  const compareCases = useMemo(
    () => (comparePeriod ? dashboardCases.filter((item) => inPeriod(item.submitted, comparePeriod) && (!statusFilter.size || statusFilter.has(item.status))) : null),
    [dashboardCases, comparePeriod, statusFilter],
  );

  // The same date and status choice for the accuracy figures, which the backend filters (cases are the reviews' own).
  const statuses = useMemo(() => [...statusFilter].flatMap((status) => STAGE_BY_LABEL.get(status) ?? []), [statusFilter]);
  const accuracyFilters = useMemo<DetectionMetricsFilters>(() => ({ since: period.from, until: period.to, statuses }), [period, statuses]);
  const compareAccuracyFilters = useMemo<DetectionMetricsFilters | null>(
    () => (comparePeriod ? { since: comparePeriod.from, until: comparePeriod.to, statuses } : null),
    [comparePeriod, statuses],
  );

  // The filter's counts ignore the status filter itself, like the case queue's.
  const statusOptions: FilterOption[] = STATUSES.map((status) => ({
    value: status,
    text: status,
    count: casesInRange.filter((item) => item.status === status).length,
    label: <><span className={`case-status-dot tone-${statusTone(status)}`} aria-hidden="true" />{status}</>,
  }));

  // Claude spend for the live cases in view and in the comparison, for the totals and the Cases tab's charts.
  const costIds = useMemo(
    () => [...new Set([...visibleCases, ...(compareCases ?? [])].flatMap((item) => (item.caseId ? [item.caseId] : [])))],
    [visibleCases, compareCases],
  );
  const { costs, error: costError } = useCaseCosts(costIds);
  const costState: CostState = !caseRecords.length ? 'unavailable' : costError ? 'error' : costs === null ? 'loading' : 'loaded';

  const view = useMemo(() => deriveView(visibleCases, costs), [visibleCases, costs]);
  const before: DashboardView | null = useMemo(() => (compareCases ? deriveView(compareCases, costs) : null), [compareCases, costs]);
  const { statusCounts, byDocumentType, flagRows, flagCases, attentionCases, analyticsCases } = view;
  // The commonest flags, most cases first, for the Overview. When comparing, a flag raised only in the comparison
  // is listed at 0 after them, so a drop to none shows rather than the row disappearing.
  const wasFlagged = new Map(before?.flagRows.map((row) => [row.name, row]));
  const commonFlags = [
    ...[...flagRows].sort((a, b) => b.cases.length - a.cases.length),
    ...[...wasFlagged.values()].filter((row) => !flagRows.some((now) => now.name === row.name))
      .sort((a, b) => b.cases.length - a.cases.length).map((row) => ({ ...row, cases: [] })),
  ].slice(0, INSIGHT_ROWS);
  // Each document type in view, then any only in the comparison at 0, for the same reason.
  const documentTypes: [string, number][] = [
    ...byDocumentType,
    ...(before?.byDocumentType ?? []).filter(([type]) => !byDocumentType.some(([now]) => now === type)).map(([type]): [string, number] => [type, 0]),
  ];
  const openAnalyticsCase = (item: AnalyticsCase) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`);
  const stages = useMemo(() => STATUSES.map((status) => ({ label: status, tone: statusTone(status) })), []);

  return (
    <main className="shell dashboard-shell">
      {/* White stripes laid over a comparison mark drawn in SVG (rings, dots), as CSS stripes are on the bars. */}
      <svg className="sr-only" aria-hidden="true">
        <defs>
          <pattern id="compare-stripes" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
            <rect width="2.2" height="5" fill="rgba(255, 255, 255, .65)" />
          </pattern>
        </defs>
      </svg>
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
                    <span className="nav-icon" aria-hidden="true">{item === 'Overview' ? '▦' : item === 'Cases' ? '▤' : item === 'Flags' ? '⚑' : item === 'Accuracy' ? '✓' : '◎'}</span>{item}
                    {count !== null && <span className="nav-count">{count}</span>}
                  </button>
                );
              })}
            </nav>
            <div className="dashboard-toolbar-actions">
              <div className="dashboard-filters">
                <PeriodMenu
                  name="Date range"
                  trigger={rangeLabel}
                  options={PRESETS.map((preset) => ({ label: preset, onSelect: () => setRangeChoice(preset) }))}
                  selected={rangeChoice}
                  custom={customPeriod}
                  onCustom={(picked) => { setCustomPeriod(picked); setRangeChoice(CUSTOM_RANGE); }}
                />
                <CompareMenu
                  period={period}
                  compare={comparePeriod}
                  onCompare={(first, second) => {
                    // The later period is always "This period", so every change reads as now against before.
                    const [later, earlier] = second.to > first.to ? [second, first] : [first, second];
                    setCustomPeriod(later);
                    setRangeChoice(CUSTOM_RANGE);
                    setComparePeriod(earlier);
                  }}
                  onStop={() => setComparePeriod(null)}
                />
                <HeaderFilter name="status" title="Status" options={statusOptions} selected={statusFilter} disabled={isLoading} onChange={(next) => setStatusFilter(next as Set<Status>)} />
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
          {comparePeriod && before && (
            <div className="compare-banner">
              <span className="compare-banner-period">
                <i className="compare-swatch" aria-hidden="true" />
                <span><small>This period</small><span><strong>{formatPeriod(period)}</strong> · {visibleCases.length} {visibleCases.length === 1 ? 'case' : 'cases'}</span></span>
              </span>
              <span className="compare-banner-vs">vs</span>
              <span className="compare-banner-period">
                <i className="compare-swatch compare-swatch--was" aria-hidden="true" />
                <span><small>Comparison</small><span><strong>{formatPeriod(comparePeriod)}</strong> · {before.cases.length} {before.cases.length === 1 ? 'case' : 'cases'}</span></span>
              </span>
              <span className="compare-banner-key">
                In the charts, solid is this period and striped is the comparison. Each period's cases are those created
                in it, counted by their status now.
              </span>
              {/* How to read the figures, in the periods' own dates. The comparison is normally the earlier period; a
                  date range changed afterwards can make it the later one, and the wording says so rather than "since". */}
              <p className="compare-banner-note">
                Big numbers are <strong>{formatPeriod(period)}</strong>.{' '}
                {comparePeriod.to < (period.from ?? period.to)
                  ? <>Pills show the change since <strong>{formatPeriod(comparePeriod)}</strong>: for example, "+1" means 1 more than in {formatPeriod(comparePeriod)}.</>
                  : <>Pills show how it differs from <strong>{formatPeriod(comparePeriod)}</strong>: for example, "+1" means 1 more than in {formatPeriod(comparePeriod)}.</>}
                {' '}Green is better, red is worse.
              </p>
              {overlap(period, comparePeriod) && (
                <p className="compare-banner-warning compare-banner-overlap" role="alert">
                  The periods overlap ({formatPeriod(overlap(period, comparePeriod)!)}): cases created then count in both.
                </p>
              )}
            </div>
          )}
          {tab === 'Overview' && <section className="dashboard-stat-grid" aria-label="Case counts by status">
            <article className="dashboard-stat-card stat-total"><div className="stat-top"><span className="stat-period">{rangeLabel}</span></div><span className="stat-number">{visibleCases.length}</span>{before ? <CompareDelta value={visibleCases.length} previous={before.cases.length} /> : <span className="stat-caption">Cases in view</span>}</article>
            {STATUSES.map((status) => <article key={status} className={`dashboard-stat-card${before && !statusCounts[status] && !before.statusCounts[status] ? ' stat-card--quiet' : ''}`}><div className="stat-top"><span className="stat-caption">{status}</span><span className={`case-status-dot tone-${statusTone(status)}`} /></div><span className="stat-number">{statusCounts[status]}</span>{before ? <CompareDelta value={statusCounts[status]} previous={before.statusCounts[status]} /> : <span className="stat-caption">{visibleCases.length ? `${Math.round(statusCounts[status] / visibleCases.length * 100)}% of cases` : '0% of cases'}</span>}</article>)}
          </section>}
          {isLoading && !caseRecords.length && <p className="dashboard-data-note" role="status">Loading live case links; preview rows are temporarily not clickable.</p>}
          {!isLoading && !caseRecords.length && <p className="dashboard-data-note">No live cases are loaded. Preview rows are illustrative and cannot open a case record.</p>}

          {tab === 'Overview' && <>
          <section className="dashboard-insight-grid">
            <article className="dashboard-panel flag-insight-panel">
              <div className="panel-heading"><div><h2>Most common flags</h2><p>Issues raised across the cases in view</p></div><button className="text-button" onClick={() => setTab('Flags')}>View flags <span>→</span></button></div>
              {commonFlags.length ? <FlagPills flags={commonFlags.map((flag) => ({ name: flag.name, severity: flag.severity, caseIds: flag.cases.map((item) => item.id), previous: before ? wasFlagged.get(flag.name)?.cases.length ?? 0 : undefined }))} caseCount={visibleCases.length} compareCaseCount={before?.cases.length} onOpen={() => setTab('Flags')} /> : <p className="empty-insight">No flags in this date and status selection.</p>}
            </article>
            <article className="dashboard-panel">
              <div className="panel-heading"><div><h2>Documents by type</h2><p>Share of the cases in view that include each document type</p></div>{documentTypes.length > DOCUMENT_TYPE_ROWS && <button className="text-button" onClick={() => setShowAllDocTypes((value) => !value)}>{showAllDocTypes ? 'Show fewer' : `Show all ${documentTypes.length}`}</button>}</div>
              {documentTypes.length ? <CoverageRings types={showAllDocTypes ? documentTypes : documentTypes.slice(0, DOCUMENT_TYPE_ROWS)} caseCount={visibleCases.length} compare={before ? { types: new Map(before.byDocumentType), caseCount: before.cases.length } : undefined} /> : <p className="empty-insight">No documents in this date and status selection.</p>}
            </article>
          </section>

          <section className="dashboard-lower-grid">
            <article className="dashboard-panel risk-snapshot-panel">
              <div className="panel-heading"><div><h2>Risk overview</h2><p>Current cases grouped by assessed risk level</p></div></div>
              <RiskOverview cases={visibleCases} compare={before?.cases} />
              <p className="snapshot-note"><InfoIcon /> Consider document findings, case status and review context alongside the risk score.</p>
            </article>
          </section>
          <section className="dashboard-pair-grid">
            <NeedsAttention cases={attentionCases} onOpen={(item) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`)} />
            <MostRequestedDocuments cases={attentionCases} compare={before?.attentionCases} />
          </section>
          <section className="dashboard-pair-grid">
            <CasesOverTime cases={visibleCases} period={period} compare={before && comparePeriod ? { cases: before.cases, period: comparePeriod } : undefined} />
            <ProcessingAndCost cases={visibleCases} compare={before?.cases} costs={costs} costError={costError} isPreview={!caseRecords.length} />
          </section>
          <DetectionAccuracySummary filters={accuracyFilters} compareFilters={compareAccuracyFilters} onViewAll={() => setTab('Accuracy')} />
          </>}

          {tab === 'Cases' && <>
            <CaseKpis cases={analyticsCases} compare={before?.analyticsCases} costState={costState} />
            <section className="dashboard-pair-grid">
              <TimeAndCostByCase cases={analyticsCases} compare={before?.analyticsCases} costState={costState} onOpen={openAnalyticsCase} />
              <StageBreakdown cases={analyticsCases} compare={before?.analyticsCases} stages={stages} />
            </section>
            <section className="dashboard-pair-grid">
              <CostAgainstTime cases={analyticsCases} compare={before?.analyticsCases} costState={costState} onOpen={openAnalyticsCase} />
              <RiskScoreSpread cases={analyticsCases} compare={before?.analyticsCases} />
            </section>
            {!caseRecords.length && <p className="dashboard-data-note">Sample cases: processing times are illustrative and Claude cost is recorded for live cases only.</p>}
          </>}

          {tab === 'Flags' && <>
            <FlagKpis cases={flagCases} compare={before?.flagCases} />
            <CheckBreakdown cases={flagCases} compare={before?.flagCases} onOpen={(item) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`)} />
            <section className="dashboard-pair-grid">
              <CheckDocumentMatrix cases={flagCases} compare={before?.flagCases} />
              <FlagsByRisk cases={flagCases} compare={before?.flagCases} />
            </section>
          </>}
          {tab === 'Accuracy' && <DetectionAccuracyPanel filters={accuracyFilters} compareFilters={compareAccuracyFilters} />}
          </>}
        </div>
      </main>
      </div>
    </main>
  );
}
