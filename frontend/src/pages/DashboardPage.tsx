import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';

type Status = 'Needs review' | 'In progress' | 'Awaiting documents' | 'Finalised';
type Risk = 'Critical' | 'High' | 'Medium' | 'Low';
type Role = 'Manager' | 'Analyst';
type DashboardTab = 'Overview' | 'Cases' | 'Flags';
const DASHBOARD_TABS: DashboardTab[] = ['Overview', 'Cases', 'Flags'];

interface MockCase {
  id: string;
  routeId?: string;
  applicant: string;
  submitted: string;
  status: Status;
  risk: Risk;
  score: number;
  riskDescription?: string;
  uploadToExtraction?: string;
  detectionToCompletion?: string;
  analyst: string;
  documentTypes: string[];
  flags: string[];
}

interface FlagSummary {
  name: string;
  severity: 'High' | 'Medium';
  cases: MockCase[];
  documentTypes: string[];
}

const MOCK_CASES: MockCase[] = [
  { id: 'GLDB-02481', applicant: 'Meridian Consulting Group', submitted: '2026-10-18', status: 'Needs review', risk: 'High', score: 82, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Payslip'], flags: ['Identity mismatch', 'Balance inconsistency', 'Unverified address'] },
  { id: 'GLDB-02480', applicant: 'Northstar Trading Pte Ltd', submitted: '2026-10-17', status: 'In progress', risk: 'Medium', score: 58, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02479', applicant: 'Harbourlight Foods', submitted: '2026-10-16', status: 'Awaiting documents', risk: 'High', score: 77, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Certificate'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02478', applicant: 'Cedar & Stone Pte Ltd', submitted: '2026-10-15', status: 'Finalised', risk: 'Low', score: 18, analyst: 'M. Goh', documentTypes: ['Bank statement', 'Payslip'], flags: [] },
  { id: 'GLDB-02477', applicant: 'Orchid Systems', submitted: '2026-10-13', status: 'Needs review', risk: 'High', score: 91, analyst: 'A. Lim', documentTypes: ['Certificate', 'Proof of address'], flags: ['Invalid registration number', 'Identity mismatch', 'Font anomaly'] },
  { id: 'GLDB-02476', applicant: 'Brightpath Logistics', submitted: '2026-10-12', status: 'In progress', risk: 'Medium', score: 49, analyst: 'J. Tan', documentTypes: ['Bank statement', 'Certificate'], flags: ['Balance inconsistency'] },
  { id: 'GLDB-02475', applicant: 'Evergreen Medical Supplies', submitted: '2026-10-10', status: 'Finalised', risk: 'Low', score: 22, analyst: 'M. Goh', documentTypes: ['Payslip', 'Proof of address'], flags: [] },
  { id: 'GLDB-02474', applicant: 'Silverline Manufacturing', submitted: '2026-10-08', status: 'Awaiting documents', risk: 'Medium', score: 64, analyst: 'A. Lim', documentTypes: ['Certificate', 'Bank statement'], flags: ['Company detail mismatch', 'Unusual transaction'] },
  { id: 'GLDB-02473', applicant: 'Bluepeak Design Studio', submitted: '2026-09-29', status: 'Needs review', risk: 'Medium', score: 61, analyst: 'J. Tan', documentTypes: ['Proof of address', 'Bank statement'], flags: ['Invalid postal code', 'Identity mismatch'] },
  { id: 'GLDB-02472', applicant: 'Pioneer Green Energy', submitted: '2026-09-22', status: 'In progress', risk: 'Low', score: 31, analyst: 'M. Goh', documentTypes: ['Certificate', 'Payslip'], flags: ['Company detail mismatch'] },
];
const SAMPLE_PROCESSING_TIMES: Array<[string, string]> = [
  ['2m 18s', '46s'], ['1m 42s', '1m 08s'], ['3m 05s', '52s'], ['1m 26s', '39s'], ['2m 51s', '1m 14s'],
  ['1m 57s', '43s'], ['2m 11s', '57s'], ['3m 22s', '1m 02s'], ['1m 35s', '48s'], ['2m 04s', '55s'],
];

const STATUSES: Status[] = ['Needs review', 'In progress', 'Awaiting documents', 'Finalised'];
const STATUS_CLASS: Record<Status, string> = {
  'Needs review': 'review',
  'In progress': 'progress',
  'Awaiting documents': 'awaiting',
  Finalised: 'cleared',
};
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

function CaseTable({ cases, heading, onOpenCase, showRiskAnalysis = false, showProcessingTime = false }: { cases: MockCase[]; heading: string; onOpenCase: (item: MockCase) => void; showRiskAnalysis?: boolean; showProcessingTime?: boolean }) {
  return (
    <section className="dashboard-panel cases-panel">
      <div className="panel-heading"><div><h2>{heading}</h2><p>Case records matching the selected filters{showProcessingTime ? ' · sample timings; manual review excluded' : ''}</p></div><Link className="text-button" to="/cases">Open case queue <span>→</span></Link></div>
      <div className="case-table-wrap">
        <table className={`dashboard-case-table${showProcessingTime ? ' case-register-table' : showRiskAnalysis ? ' case-overview-table' : ''}`}>
          <thead><tr>
            <th>CASE / APPLICANT</th><th>SUBMITTED</th><th>STATUS</th>
            <th>{showRiskAnalysis ? 'RISK / BASIS' : 'RISK'}</th>
            {showProcessingTime && <th>PROCESSING TIME</th>}
            <th>{showRiskAnalysis ? 'RECOMMENDED ADDITIONAL DOCUMENTS' : 'TOP FLAGS'}</th>
          </tr></thead>
          <tbody>
            {cases.map((item) => (
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
                <td data-label="Submitted">{new Date(`${item.submitted}T12:00:00`).toLocaleDateString('en-SG', { day: '2-digit', month: 'short' })}</td>
                <td data-label="Status"><span className={`case-status ${STATUS_CLASS[item.status]}`}>{item.status}</span></td>
                <td data-label={showRiskAnalysis ? 'Risk / basis' : 'Risk'}>
                  <div className={showRiskAnalysis ? 'risk-cell risk-cell-detailed' : 'risk-cell'}>
                    <span className={`risk-score risk-score-${item.risk.toLowerCase()}`}><b>{item.score}</b> {item.risk}</span>
                    {showRiskAnalysis ? <span className="risk-basis">{conciseRiskDescription(item.riskDescription || 'Risk explanation is not available yet.')}</span> : null}
                  </div>
                </td>
                {showProcessingTime ? <td data-label="Processing time"><div className="processing-time"><span><small>Upload → extraction</small><b>{item.uploadToExtraction}</b></span><span><small>Detection → completion</small><b>{item.detectionToCompletion}</b></span></div></td> : null}
                {showRiskAnalysis
                  ? <td data-label="Recommended additional documents">
                    {recommendedDocuments(item).length
                      ? <span className="recommended-documents">{recommendedDocuments(item).join(', ')}</span>
                      : <span className="no-flag">No additional documents suggested</span>}
                  </td>
                  : <td data-label="Top flags">{item.flags.length ? <span className="table-flag">{item.flags[0]}{item.flags.length > 1 ? <small> +{item.flags.length - 1}</small> : null}</span> : <span className="no-flag">No flags</span>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {!cases.length ? <p className="empty-insight table-empty">No cases match the current filters.</p> : null}
      </div>
      <footer className="table-footer"><span>Showing <strong>{cases.length}</strong> cases</span><span className="mock-data-note"><i /> Mock dashboard data · for layout review</span></footer>
    </section>
  );
}

function fallbackRiskDescription(risk: Risk): string {
  if (risk === 'Critical' || risk === 'High') return 'Multiple high-severity findings need close review.';
  if (risk === 'Medium') return 'Some inconsistencies need analyst review.';
  return 'No significant findings in the current assessment.';
}

function conciseRiskDescription(description: string): string {
  return description.replace(/^\s*Rated\s+(?:Low|Medium|High|Critical)(?:\s+risk)?\s*:\s*/i, '');
}

function recommendedDocuments(item: MockCase): string[] {
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
  const date = new Date('2026-10-19T12:00:00');
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

function dashboardStatus(status: string): Status {
  if (status === 'In progress') return 'In progress';
  if (status === 'Documents pending') return 'Awaiting documents';
  if (status === 'Ready for review') return 'Needs review';
  if (status === 'Finalised' || status === 'Cleared') return 'Finalised';
  return 'Needs review';
}

export default function DashboardPage({ cases: caseRecords, isLoading }: { cases: CaseRecord[]; isLoading: boolean }) {
  const navigate = useNavigate();
  const [role, setRole] = useState<Role>('Manager');
  const [tab, setTab] = useState<DashboardTab>('Overview');
  const [range, setRange] = useState('Last 30 days');
  const [statusFilter, setStatusFilter] = useState('All statuses');
  const [riskOrder, setRiskOrder] = useState<'highest' | 'lowest'>('highest');

  const dashboardCases = useMemo(() => caseRecords.length
    ? caseRecords.map((record, index) => {
      const example = MOCK_CASES[index % MOCK_CASES.length];
      const risk = record.riskLevel ?? example.risk;
      return {
        ...example,
        id: record.caseNumber || record.id,
        routeId: record.caseNumber || record.id,
        applicant: record.applicant,
        submitted: record.created_at?.slice(0, 10) || example.submitted,
        status: dashboardStatus(record.status),
        risk,
        score: record.riskScore ?? (risk === 'Critical' ? 96 : risk === 'High' ? 82 : risk === 'Medium' ? 58 : 27),
        riskDescription: record.riskDescription || fallbackRiskDescription(risk),
        uploadToExtraction: SAMPLE_PROCESSING_TIMES[index % SAMPLE_PROCESSING_TIMES.length][0],
        detectionToCompletion: SAMPLE_PROCESSING_TIMES[index % SAMPLE_PROCESSING_TIMES.length][1],
      };
    })
    : MOCK_CASES.map((item, index) => ({
      ...item,
      riskDescription: fallbackRiskDescription(item.risk),
      uploadToExtraction: SAMPLE_PROCESSING_TIMES[index][0],
      detectionToCompletion: SAMPLE_PROCESSING_TIMES[index][1],
    })),
  [caseRecords]);

  const visibleCases = useMemo(() => {
    const since = dateLimit(range);
    return dashboardCases.filter((item) =>
      (role === 'Manager' || item.analyst === 'J. Tan') &&
      (statusFilter === 'All statuses' || item.status === statusFilter) &&
      (!since || item.submitted >= since),
    );
  }, [dashboardCases, range, role, statusFilter]);

  const statusCounts = useMemo(() => Object.fromEntries(
    STATUSES.map((status) => [status, visibleCases.filter((item) => item.status === status).length]),
  ) as Record<Status, number>, [visibleCases]);

  const commonFlags = useMemo(() => {
    const counts = new Map<string, number>();
    visibleCases.forEach((item) => item.flags.forEach((flag) => counts.set(flag, (counts.get(flag) ?? 0) + 1)));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [visibleCases]);

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
      severity: FLAG_SEVERITY[name] ?? 'Medium',
      documentTypes: [...new Set(cases.flatMap((item) => item.documentTypes))],
    })).sort((a, b) => b.cases.length - a.cases.length);
  }, [visibleCases]);

  const maxFlagCount = Math.max(1, ...commonFlags.map(([, count]) => count));
  const maxDocCount = Math.max(1, ...byDocumentType.map(([, count]) => count));
  const flaggedCaseCount = visibleCases.filter((item) => item.flags.length > 0).length;
  const highFlagCount = flagRows.filter((item) => item.severity === 'High').length;
  const openCase = (item: MockCase) => navigate(`/cases/${encodeURIComponent(item.routeId || item.id)}`);
  const sortedCaseRows = useMemo(() => [...visibleCases].sort((a, b) => {
    const rank: Record<Risk, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };
    if (riskOrder === 'highest') return rank[b.risk] - rank[a.risk] || b.score - a.score;
    return rank[a.risk] - rank[b.risk] || a.score - b.score;
  }), [riskOrder, visibleCases]);

  return (
    <main className="shell dashboard-shell">
      <AppHeader />
      <div className="dashboard-app">
      <aside className="dashboard-sidebar">
        <div className="dashboard-sidebar-label">WORKSPACE</div>
        <nav className="dashboard-nav" aria-label="Dashboard sections">
          {DASHBOARD_TABS.map((item) => (
            <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>
              <span className="nav-icon">{item === 'Overview' ? '▦' : item === 'Cases' ? '▤' : item === 'Flags' ? '⚑' : '◎'}</span>{item}
              {item === 'Cases' && <span className="nav-count">{visibleCases.length}</span>}
            </button>
          ))}
        </nav>
        <div className="dashboard-sidebar-bottom">
          <div className="sidebar-user-avatar">{role === 'Manager' ? 'MT' : 'JT'}</div>
          <div><strong>{role === 'Manager' ? 'M. Tan' : 'J. Tan'}</strong><span>{role}</span></div>
          <span className="sidebar-more">•••</span>
        </div>
      </aside>

      <main className="dashboard-main">
        <header className="dashboard-topbar">
          <div className="dashboard-breadcrumb">Workspace <span>/</span> {tab}</div>
          <div className="role-switch" aria-label="Preview dashboard role">
            {(['Manager', 'Analyst'] as Role[]).map((item) => <button key={item} className={role === item ? 'selected' : ''} onClick={() => { setRole(item); setTab('Overview'); }}>{item}</button>)}
          </div>
        </header>

        <div className="dashboard-content">
          <section className="dashboard-title-row">
            <div>
              <p className="dashboard-eyebrow">{role === 'Manager' ? 'PORTFOLIO MONITORING' : 'YOUR WORKSPACE'}</p>
              <h1>{tab === 'Overview' ? (role === 'Manager' ? 'Portfolio overview' : 'My review overview') : tab === 'Cases' ? (role === 'Manager' ? 'Case register' : 'My assigned cases') : 'Flag explorer'}</h1>
              <p className="dashboard-subtitle">{tab === 'Overview' ? 'A summary of case progress, risk signals and common findings.' : tab === 'Cases' ? 'Review case status and priority across the selected period.' : 'Explore recurring findings and the cases and documents they affect.'}</p>
            </div>
            <div className="dashboard-filters">
              <label><span>Date range</span><select value={range} onChange={(event) => setRange(event.target.value)}><option>Last 7 days</option><option>Last 30 days</option><option>Last 90 days</option><option>All time</option></select></label>
              <label><span>Status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option>All statuses</option>{STATUSES.map((status) => <option key={status}>{status}</option>)}</select></label>
              {tab === 'Cases' && <label className="risk-sort-filter"><span>Sort by risk</span><select value={riskOrder} onChange={(event) => setRiskOrder(event.target.value as 'highest' | 'lowest')}><option value="highest">Critical first</option><option value="lowest">Low first</option></select></label>}
            </div>
          </section>
          {isLoading && !caseRecords.length && <p className="dashboard-data-note" role="status">Loading live case links; preview rows are temporarily not clickable.</p>}
          {!isLoading && !caseRecords.length && <p className="dashboard-data-note">No live cases are loaded. Preview rows are illustrative and cannot open a case record.</p>}

          {tab !== 'Flags' && <section className="dashboard-stat-grid" aria-label="Case counts by status">
            <article className="dashboard-stat-card stat-total"><div className="stat-top"><span className="stat-icon">▤</span><span className="stat-period">{range}</span></div><span className="stat-number">{visibleCases.length}</span><span className="stat-caption">Cases in view</span></article>
            {STATUSES.map((status) => <article key={status} className={`dashboard-stat-card stat-${STATUS_CLASS[status]}`}><div className="stat-top"><span className="stat-caption">{status}</span><span className={`status-dot ${STATUS_CLASS[status]}`} /></div><span className="stat-number">{statusCounts[status]}</span><span className="stat-caption">{visibleCases.length ? `${Math.round(statusCounts[status] / visibleCases.length * 100)}% of cases` : '0% of cases'}</span></article>)}
          </section>}

          {tab === 'Overview' && <>
          <section className="dashboard-insight-grid">
            <article className="dashboard-panel">
              <div className="panel-heading"><div><h2>Most common flags</h2><p>Issues raised across the cases in view</p></div><button className="text-button" onClick={() => setTab('Flags')}>View flags <span>→</span></button></div>
              {commonFlags.length ? <div className="bar-list">{commonFlags.map(([flag, count], index) => <div className="bar-row" key={flag}><span className="bar-rank">0{index + 1}</span><span className="bar-label">{flag}</span><span className="bar-track"><i style={{ width: `${count / maxFlagCount * 100}%` }} /></span><strong>{count}</strong></div>)}</div> : <p className="empty-insight">No flags in this date and status selection.</p>}
            </article>
            <article className="dashboard-panel">
              <div className="panel-heading"><div><h2>Documents by type</h2><p>Number of cases containing each document type</p></div><span className="panel-kicker">CASE COUNT</span></div>
              <div className="document-risk-list">{byDocumentType.map(([type, count]) => <div className="document-risk-row" key={type}><div className="document-risk-label"><span>{type}</span><strong>{count} <small>{count === 1 ? 'case' : 'cases'}</small></strong></div><div className="document-risk-track"><i style={{ width: `${count / maxDocCount * 100}%` }} /></div></div>)}</div>
            </article>
          </section>

          <section className="dashboard-lower-grid">
            <article className="dashboard-panel risk-snapshot-panel">
              <div className="panel-heading"><div><h2>Risk overview</h2><p>Current cases grouped by assessed risk level</p></div><span className="panel-kicker">RISK IS ONE REVIEW SIGNAL</span></div>
              <div className="risk-snapshot">{(['Critical', 'High', 'Medium', 'Low'] as Risk[]).map((risk) => { const count = visibleCases.filter((item) => item.risk === risk).length; return <div key={risk} className={`risk-snapshot-row risk-${risk.toLowerCase()}`}><span className="risk-marker" /><span>{risk} risk</span><div className="risk-snapshot-track"><i style={{ width: `${visibleCases.length ? count / visibleCases.length * 100 : 0}%` }} /></div><strong>{count}</strong></div>; })}</div>
              <div className="snapshot-note"><span>i</span> Consider document findings, case status and review context alongside the risk score.</div>
            </article>
          </section>
          <section className="dashboard-panel classification-panel">
            <div className="panel-heading"><div><h2>Classification metrics</h2><p>Quality measures by predicted risk level</p></div><span className="metric-placeholder-label">PLACEHOLDERS</span></div>
            <div className="classification-overall"><span>Overall accuracy</span><strong>—</strong></div>
            <div className="classification-grid">{(['Low', 'Medium', 'High', 'Critical'] as Risk[]).map((risk) => <article key={risk} className={`classification-class class-${risk.toLowerCase()}`}><div className="classification-risk"><i />{risk} risk</div><div className="classification-values"><span>Precision <b>—</b></span><span>Recall <b>—</b></span></div></article>)}</div>
            <p className="classification-note">Placeholder values only. Metric definitions and calculations are pending.</p>
          </section>
          <CaseTable cases={visibleCases.slice(0, 5)} heading="Recent cases" onOpenCase={openCase} showRiskAnalysis />
          </>}

          {tab === 'Cases' && <>
            <CaseTable cases={sortedCaseRows} heading={role === 'Manager' ? 'All cases' : 'Cases assigned to you'} onOpenCase={openCase} showRiskAnalysis showProcessingTime />
          </>}

          {tab === 'Flags' && <>
            <section className="flag-summary-grid">
              <article className="dashboard-stat-card"><span className="stat-caption">Cases with flags</span><span className="stat-number">{flaggedCaseCount}</span><span className="stat-caption">of {visibleCases.length} cases in view</span></article>
              <article className="dashboard-stat-card"><span className="stat-caption">Flag categories</span><span className="stat-number">{flagRows.length}</span><span className="stat-caption">distinct finding types</span></article>
              <article className="dashboard-stat-card"><span className="stat-caption">High severity categories</span><span className="stat-number">{highFlagCount}</span><span className="stat-caption">require closer review</span></article>
            </section>
            <section className="dashboard-panel flag-register">
              <div className="panel-heading"><div><h2>Recurring findings</h2><p>Frequency, severity and affected evidence in the selected case set</p></div><span className="panel-kicker">{range.toUpperCase()}</span></div>
              <div className="case-table-wrap"><table className="dashboard-case-table flag-table"><thead><tr><th>FLAG</th><th>SEVERITY</th><th>CASES</th><th>DOCUMENT TYPES</th><th>EXAMPLE CASES</th></tr></thead><tbody>{flagRows.map((flag) => <tr key={flag.name}><td data-label="Flag"><strong className="flag-name">{flag.name}</strong></td><td data-label="Severity"><span className={`flag-severity ${flag.severity.toLowerCase()}`}>{flag.severity}</span></td><td data-label="Cases"><strong>{flag.cases.length}</strong></td><td data-label="Document types"><span className="flag-doc-types">{flag.documentTypes.join(', ')}</span></td><td data-label="Example cases"><span className="flag-case-ids">{flag.cases.slice(0, 2).map((item) => item.id).join(', ')}{flag.cases.length > 2 ? ` +${flag.cases.length - 2} more` : ''}</span></td></tr>)}</tbody></table>{!flagRows.length && <p className="empty-insight table-empty">No flags in this date and status selection.</p>}</div>
              <footer className="table-footer"><span>Showing <strong>{flagRows.length}</strong> flag categories</span><span className="mock-data-note"><i /> Illustrative data for mockup review</span></footer>
            </section>
          </>}
          <footer className="dashboard-page-footer">GLDB Fraud Review <span>·</span> Dashboard concept preview <span>·</span> Data shown is illustrative</footer>
        </div>
      </main>
      </div>
    </main>
  );
}
