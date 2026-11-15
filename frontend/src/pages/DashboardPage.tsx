import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { CaseRecord } from '../utils/caseRecord';
import type { Risk } from './dashboardRisk';
import AppHeader from '../components/AppHeader';
import { ErrorMessage } from '../components/ErrorMessage';
import HeaderFilter, { type FilterOption } from '../components/HeaderFilter';
import PeriodMenu, { CUSTOM_RANGE } from '../components/PeriodMenu';
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
import { StageMark } from '../components/StageMark';
import { Icon } from '../components/Icon';
import { CasePipeline } from './CasePipeline';
import { formatDocumentType, type CaseStage, type DetectionMetricsFilters } from '../services/uploadService';
import { formatCheck } from '../utils/checkLabels';
import { PRESETS, formatPeriod, inPeriod, isoDay, presetPeriod, type Period } from '../utils/dateRange';

// A case stage's label (utils/caseStages.ts), so the dashboard and the case queue name and colour cases the same way.
type Status = string;
type Role = 'Manager' | 'Analyst';

// Manager view is kept but hidden for now; flip to true to show the role switch again.
const SHOW_ROLE_SWITCH = false;
type DashboardTab = 'Overview' | 'Cases' | 'Flags' | 'Accuracy';
const DASHBOARD_TABS: DashboardTab[] = ['Overview', 'Cases', 'Flags', 'Accuracy'];

interface DashboardCase {
  id: string;
  routeId?: string;
  // The case's id, for what the audit trail holds about it (its cost).
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
  documentTypes: string[];
  flags: string[];
  // Per flag name: its worst severity in this case and the document types it's on.
  flagInfo?: Record<string, { severity: FlagSummary['severity']; count: number; documentTypes: string[] }>;
}

interface FlagSummary {
  name: string;
  severity: 'High' | 'Medium' | 'Low';
  cases: DashboardCase[];
  documentTypes: string[];
}

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

// Rows in each Overview insight panel, so the side-by-side panels stay the same height.
const INSIGHT_ROWS = 6;
// Document types are rings, four to a row: two rows before "Show all".
const DOCUMENT_TYPE_ROWS = 8;

// Everything the panels read from the cases in view.
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
    severity: flagged.map((item) => item.flagInfo?.[name]?.severity ?? 'Medium')
      .sort((a, b) => SEVERITY_RANK[b] - SEVERITY_RANK[a])[0],
    documentTypes: [...new Set(flagged.flatMap((item) => item.flagInfo?.[name]?.documentTypes ?? item.documentTypes))],
  }));

  // Each case's flags as the Flags tab counts them: per check, its severity, flag count and document types.
  const flagCases: FlagCase[] = cases.map((item) => ({
    id: item.id, routeId: item.routeId, applicant: item.applicant, risk: item.risk,
    flags: item.flags.map((name) => ({
      name,
      severity: item.flagInfo?.[name]?.severity ?? 'Medium',
      count: item.flagInfo?.[name]?.count ?? 1,
      documentTypes: item.flagInfo?.[name]?.documentTypes ?? item.documentTypes,
    })),
  }));

  // Each case as Needs attention shows it: why it waits, its most serious flag, and what to request.
  const attentionCases: AttentionCase[] = cases.map((item) => {
    const topFlag = item.flags
      .map((name) => ({ name, severity: item.flagInfo?.[name]?.severity ?? 'Medium' }))
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

function dashboardStatus(label: string): Status {
  return STATUSES.includes(label) ? label : caseStageLabel('open');
}

export default function DashboardPage({
  cases: caseRecords,
  isLoading,
  isRefreshing,
  error,
  onRefresh,
}: {
  cases: CaseRecord[];
  isLoading: boolean;
  isRefreshing: boolean;
  // The last load's failure, if it failed.
  error?: string | null;
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
  // Empty: every status, as on the case queue.
  const [statusFilter, setStatusFilter] = useState<Set<Status>>(new Set());
  const [showAllDocTypes, setShowAllDocTypes] = useState(false);

  const dashboardCases = useMemo<DashboardCase[]>(() => caseRecords.map((record) => ({
    id: record.caseNumber || record.id,
    routeId: record.caseNumber || record.id,
    caseId: record.id,
    stuck: record.stuck ?? false,
    applicant: record.applicant,
    // The day it was created in the officer's time zone, as the date range and calendar count days.
    submitted: record.created_at ? isoDay(new Date(record.created_at)) : '',
    status: dashboardStatus(record.status),
    risk: record.riskLevel ?? null,
    score: record.riskScore ?? null,
    processingMs: record.processingMs ?? null,
    recommendedDocs: record.recommendedDocuments ?? null,
    documentTypes: (record.documentTypes ?? []).map((type) => formatDocumentType(type)),
    flags: (record.flags ?? []).map((flag) => formatCheck(flag.check)),
    flagInfo: Object.fromEntries((record.flags ?? []).map((flag) => [formatCheck(flag.check), {
      severity: flagSeverity(flag.severity),
      count: flag.count,
      documentTypes: flag.documentTypes.map((type) => formatDocumentType(type)),
    }])),
  })), [caseRecords]);

  const period = useMemo(() => (rangeChoice === CUSTOM_RANGE && customPeriod ? customPeriod : presetPeriod(rangeChoice)), [rangeChoice, customPeriod]);
  const rangeLabel = rangeChoice === CUSTOM_RANGE && customPeriod ? formatPeriod(customPeriod) : rangeChoice;

  // Every case, whoever created it: the date and status filters are the only narrowing.
  const casesInRange = useMemo(() => dashboardCases.filter((item) => inPeriod(item.submitted, period)), [dashboardCases, period]);
  const visibleCases = useMemo(
    () => casesInRange.filter((item) => !statusFilter.size || statusFilter.has(item.status)),
    [casesInRange, statusFilter],
  );

  // The same date and status choice for the accuracy figures, which the backend filters (cases are the reviews' own).
  const statuses = useMemo(() => [...statusFilter].flatMap((status) => STAGE_BY_LABEL.get(status) ?? []), [statusFilter]);
  const accuracyFilters = useMemo<DetectionMetricsFilters>(() => ({ since: period.from, until: period.to, statuses }), [period, statuses]);

  // The filter's counts ignore the status filter itself, like the case queue's.
  const statusOptions: FilterOption[] = STATUSES.map((status) => ({
    value: status,
    text: status,
    count: casesInRange.filter((item) => item.status === status).length,
    label: <><StageMark stage={statusStage(status)} />{status}</>,
  }));

  // Claude spend for the live cases in view, for the totals and the Cases tab's charts.
  const costIds = useMemo(
    () => [...new Set(visibleCases.flatMap((item) => (item.caseId ? [item.caseId] : [])))],
    [visibleCases],
  );
  const { costs, error: costError } = useCaseCosts(costIds);
  const costState: CostState = !caseRecords.length ? 'unavailable' : costError ? 'error' : costs === null ? 'loading' : 'loaded';

  const view = useMemo(() => deriveView(visibleCases, costs), [visibleCases, costs]);
  const { statusCounts, byDocumentType, flagRows, flagCases, attentionCases, analyticsCases } = view;
  const countsByStage = (counts: Record<Status, number>) =>
    Object.fromEntries(CASE_STAGES.map((stage) => [stage, counts[caseStageLabel(stage)]])) as Record<CaseStage, number>;
  const stageCounts = countsByStage(statusCounts);
  // The commonest flags, most cases first, for the Overview.
  const commonFlags = [...flagRows].sort((a, b) => b.cases.length - a.cases.length).slice(0, INSIGHT_ROWS);
  const documentTypes: [string, number][] = byDocumentType;
  const openAnalyticsCase = (item: AnalyticsCase) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`);
  const stages = useMemo(() => STATUSES.map((status) => ({ label: status, tone: statusTone(status), stage: statusStage(status) })), []);

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
                // No count until the cases arrive.
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
                <PeriodMenu
                  name="Date range"
                  trigger={rangeLabel}
                  options={PRESETS.map((preset) => ({ label: preset, onSelect: () => setRangeChoice(preset) }))}
                  selected={rangeChoice}
                  custom={customPeriod}
                  onCustom={(picked) => { setCustomPeriod(picked); setRangeChoice(CUSTOM_RANGE); }}
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
          {/* First load: placeholders in the dashboard's own layout. The Accuracy tab loads its own figures. */}
          {isLoading && tab !== 'Accuracy' ? <DashboardSkeleton /> : <>
          {error && <ErrorMessage className="dashboard-load-error" message={`${error} Use Refresh to try again.`} />}
          {tab === 'Overview' && <CasePipeline counts={stageCounts} total={visibleCases.length} period={rangeLabel} />}
          {!isLoading && !caseRecords.length && !error && <p className="dashboard-data-note">No cases yet. The dashboard fills in as cases are created.</p>}

          {tab === 'Overview' && <>
          <section className="dashboard-insight-grid">
            <article className="dashboard-panel flag-insight-panel">
              <div className="panel-heading"><div><h2>Most common flags</h2><p>Issues raised across the cases in view</p></div><button className="text-button" onClick={() => setTab('Flags')}>View flags <span>→</span></button></div>
              {commonFlags.length ? <FlagPills flags={commonFlags.map((flag) => ({ name: flag.name, severity: flag.severity, caseIds: flag.cases.map((item) => item.id) }))} caseCount={visibleCases.length} onOpen={() => setTab('Flags')} /> : <p className="empty-insight">No flags in this date and status selection.</p>}
            </article>
            <article className="dashboard-panel">
              <div className="panel-heading"><div><h2>Documents by type</h2><p>Share of the cases in view that include each document type</p></div>{documentTypes.length > DOCUMENT_TYPE_ROWS && <button className="text-button" onClick={() => setShowAllDocTypes((value) => !value)}>{showAllDocTypes ? 'Show fewer' : `Show all ${documentTypes.length}`}</button>}</div>
              {documentTypes.length ? <CoverageRings types={showAllDocTypes ? documentTypes : documentTypes.slice(0, DOCUMENT_TYPE_ROWS)} caseCount={visibleCases.length} /> : <p className="empty-insight">No documents in this date and status selection.</p>}
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
            <CasesOverTime cases={visibleCases} period={period} />
            <ProcessingAndCost cases={visibleCases} costs={costs} costError={costError} />
          </section>
          <DetectionAccuracySummary filters={accuracyFilters} onViewAll={() => setTab('Accuracy')} />
          </>}

          {tab === 'Cases' && <>
            <CaseKpis cases={analyticsCases} costState={costState} />
            <section className="dashboard-pair-grid">
              <TimeAndCostByCase cases={analyticsCases} costState={costState} onOpen={openAnalyticsCase} />
              <StageBreakdown cases={analyticsCases} stages={stages} />
            </section>
            <section className="dashboard-pair-grid">
              <CostAgainstTime cases={analyticsCases} costState={costState} onOpen={openAnalyticsCase} />
              <RiskScoreSpread cases={analyticsCases} />
            </section>
          </>}

          {tab === 'Flags' && <>
            <FlagKpis cases={flagCases} />
            <CheckBreakdown cases={flagCases} onOpen={(item) => item.routeId && navigate(`/cases/${encodeURIComponent(item.routeId)}`)} />
            <section className="dashboard-pair-grid">
              <CheckDocumentMatrix cases={flagCases} />
              <FlagsByRisk cases={flagCases} />
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
