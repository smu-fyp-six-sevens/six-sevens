import { useState, type CSSProperties } from 'react';
import SelectMenu from '../components/SelectMenu';
import { LEVELS, levelClass, type Risk } from './dashboardRisk';

// The dashboard's Flags tab: how much the checks flag, which checks and documents the flags land on, and how flags
// line up with the cases' risk. Each check opens to the cases it flagged.

type Severity = 'High' | 'Medium' | 'Low';
const SEVERITY_RANK: Record<Severity, number> = { High: 3, Medium: 2, Low: 1 };
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

export interface FlagCase {
  id: string;
  // The case's route, so its row opens it.
  routeId?: string;
  applicant: string;
  risk: Risk | null;
  // One entry per check that flagged the case: its worst severity, how many flags, and the document types.
  flags: { name: string; severity: Severity; count: number; documentTypes: string[] }[];
}

interface CheckSummary {
  name: string;
  // The worst severity any case has for it.
  severity: Severity;
  flags: number;
  cases: FlagCase[];
  documentTypes: Map<string, number>;
}

function summarise(cases: FlagCase[]): CheckSummary[] {
  const checks = new Map<string, CheckSummary>();
  for (const item of cases) {
    for (const flag of item.flags) {
      const check: CheckSummary = checks.get(flag.name) ?? { name: flag.name, severity: flag.severity, flags: 0, cases: [], documentTypes: new Map() };
      if (SEVERITY_RANK[flag.severity] > SEVERITY_RANK[check.severity]) check.severity = flag.severity;
      check.flags += flag.count;
      check.cases.push(item);
      flag.documentTypes.forEach((type) => check.documentTypes.set(type, (check.documentTypes.get(type) ?? 0) + 1));
      checks.set(flag.name, check);
    }
  }
  return [...checks.values()];
}

const flagTotal = (item: FlagCase) => item.flags.reduce((sum, flag) => sum + flag.count, 0);

function RiskMix({ cases }: { cases: FlagCase[] }) {
  const counts = LEVELS.map((level) => ({ level, count: cases.filter((item) => (item.risk ?? 'Not scored') === level).length })).filter((entry) => entry.count);
  return (
    <span className="flag-risk-mix" role="img" aria-label={counts.map((entry) => `${entry.level} ${entry.count}`).join(', ')}>
      {counts.map((entry) => <i key={entry.level} className={levelClass(entry.level)} style={{ flexGrow: entry.count }} title={`${entry.level}: ${entry.count}`} />)}
    </span>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Headline figures

function flagFigures(cases: FlagCase[]) {
  const flagged = cases.filter((item) => item.flags.length);
  const checks = summarise(cases);
  const total = flagged.reduce((sum, item) => sum + flagTotal(item), 0);
  const severe = checks.filter((check) => check.severity === 'High');
  return {
    flagged: flagged.length,
    checks: checks.length,
    total,
    average: flagged.length ? total / flagged.length : null,
    severe: severe.length,
    severeCases: new Set(severe.flatMap((check) => check.cases.map((item) => item.id))).size,
  };
}

// The figures, each with a caption.
export function FlagKpis({ cases }: { cases: FlagCase[] }) {
  const now = flagFigures(cases);
  const oneDecimal = (value: number) => value.toFixed(1);

  return (
    <section className="case-kpi-grid flag-kpi-grid" aria-label="Flag figures">
      <article className="dashboard-stat-card">
        <span className="stat-caption">Cases with flags</span>
        <span className="stat-number">{now.flagged}<small> / {cases.length}</small></span>
        <span className="stat-caption">{!cases.length ? 'No cases in view' : now.flagged === cases.length ? 'Every case in view has flags' : `${plural(cases.length - now.flagged, 'case')} with no flags`}</span>
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Flags raised</span>
        <span className="stat-number">{now.total}</span>
        <span className="stat-caption">by {plural(now.checks, 'check')}</span>
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Flags per flagged case</span>
        <span className="stat-number">{now.average != null ? oneDecimal(now.average) : '—'}</span>
        <span className="stat-caption">{now.flagged ? `averaged across ${plural(now.flagged, 'case')}` : 'No case flagged'}</span>
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">High severity checks</span>
        <span className="stat-number">{now.severe}<small> / {now.checks}</small></span>
        <span className="stat-caption">{now.severe ? `on ${plural(now.severeCases, 'case')}` : 'None in view'}</span>
      </article>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Checks

const CHECK_SORTS = ['Most cases', 'Most flags', 'Most severe'];

// A row per check: the share of cases it flagged, in its severity's colour, and its flag count. Opening a row shows
// the documents it was found on, the risk of the cases it flagged, and the cases themselves.
export function CheckBreakdown({ cases, onOpen }: { cases: FlagCase[]; onOpen: (item: FlagCase) => void }) {
  const [sort, setSort] = useState(CHECK_SORTS[0]);
  const [open, setOpen] = useState<string | null>(null);
  const checks = summarise(cases).sort((a, b) => {
    const byCases = b.cases.length - a.cases.length;
    const byFlags = b.flags - a.flags;
    const bySeverity = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
    if (sort === 'Most flags') return byFlags || byCases;
    if (sort === 'Most severe') return bySeverity || byCases || byFlags;
    return byCases || bySeverity || byFlags;
  });

  return (
    <section className="dashboard-panel check-panel">
      <div className="panel-heading">
        <div>
          <h2>Flags by check</h2>
          <p>Share of the cases in view each check flagged. Open a check for its cases.</p>
        </div>
        {checks.length > 1 && <SelectMenu name="Sort checks" options={CHECK_SORTS} value={sort} onChange={setSort} />}
      </div>
      {checks.length === 0 ? (
        <p className="empty-insight">No flags in this date and status selection.</p>
      ) : (
        <ul className="check-list">
          {checks.map((check) => {
            const isOpen = open === check.name;
            const share = cases.length ? check.cases.length / cases.length : 0;
            return (
              <li key={check.name} className={isOpen ? 'open' : undefined}>
                <button type="button" className="check-row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : check.name)}>
                  <span className="check-name">
                    <svg className="check-caret" viewBox="0 0 8 12" aria-hidden="true"><path d="m2 2 4 4-4 4" /></svg>
                    {check.name}
                  </span>
                  <span className={`flag-pill-severity flag-pill-severity--${check.severity.toLowerCase()}`}>{check.severity}</span>
                  <span className="check-track" aria-hidden="true">
                    <i className={`check-bar--${check.severity.toLowerCase()}`} style={{ width: `${share * 100}%` }} />
                  </span>
                  <span className="check-figure"><strong>{check.cases.length}</strong> {check.cases.length === 1 ? 'case' : 'cases'}<small>{Math.round(share * 100)}%</small></span>
                  <span className="check-figure"><strong>{check.flags}</strong> {check.flags === 1 ? 'flag' : 'flags'}</span>
                </button>
                {isOpen && (
                  <div className="check-detail">
                    <div>
                      <span className="check-detail-label">Found on</span>
                      <span className="check-chips">
                        {[...check.documentTypes.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                          <span key={type} className="check-chip">{type}{count > 1 && <small>{count}</small>}</span>
                        ))}
                      </span>
                    </div>
                    <div>
                      <span className="check-detail-label">Risk of these cases</span>
                      <RiskMix cases={check.cases} />
                    </div>
                    <div className="check-detail-cases">
                      <span className="check-detail-label">Cases</span>
                      <span className="check-chips">
                        {check.cases.map((item) => {
                          const count = item.flags.find((flag) => flag.name === check.name)?.count ?? 0;
                          const label = <><strong>{item.id}</strong> {item.applicant}{count > 1 && <small>{count} flags</small>}</>;
                          return item.routeId
                            ? <button key={item.id} type="button" className="check-case" onClick={() => onOpen(item)}><i className={levelClass(item.risk ?? 'Not scored')} aria-hidden="true" />{label}</button>
                            : <span key={item.id} className="check-case"><i className={levelClass(item.risk ?? 'Not scored')} aria-hidden="true" />{label}</span>;
                        })}
                      </span>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Check against document type

const SHOWN_TYPES = 7;

// How many cases each check flagged on each document type: darker is more. The busiest document types are columns.
export function CheckDocumentMatrix({ cases }: { cases: FlagCase[] }) {
  const checks = summarise(cases).sort((a, b) => b.cases.length - a.cases.length);
  const countOf = (check: CheckSummary, type: string) => check.documentTypes.get(type) ?? 0;
  const totals = new Map<string, number>();
  checks.forEach((check) => check.documentTypes.forEach((count, type) => totals.set(type, (totals.get(type) ?? 0) + count)));
  const types = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, SHOWN_TYPES).map(([type]) => type);
  const peak = Math.max(1, ...checks.flatMap((check) => types.map((type) => countOf(check, type))));

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Where each check flags</h2>
          <p>Cases flagged per check and document type; darker is more</p>
        </div>
      </div>
      {checks.length === 0 ? (
        <p className="empty-insight">No flags in this date and status selection.</p>
      ) : (
        <div className="flag-matrix-wrap">
          <table className="flag-matrix" style={{ '--columns': types.length } as CSSProperties}>
            <thead>
              <tr>
                <th scope="col"><span className="sr-only">Check</span></th>
                {types.map((type) => <th key={type} scope="col" title={type}>{type}</th>)}
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <tr key={check.name}>
                  <th scope="row">{check.name}</th>
                  {types.map((type) => {
                    const count = countOf(check, type);
                    return (
                      <td key={type} title={`${check.name} on ${type}: ${plural(count, 'case')}`}>
                        {count > 0 ? (
                          <span className={count / peak > 0.55 ? 'dark' : undefined} style={{ '--level': `${Math.round(18 + (count / peak) * 72)}%` } as CSSProperties}>
                            {count}
                          </span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {totals.size > types.length && <p className="insight-note">The {SHOWN_TYPES} most flagged of {totals.size} document types.</p>}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Flags and risk

// For each risk level: how many of its cases carry flags, and the flags an average case of that level has.
function flagsByLevel(cases: FlagCase[]) {
  return LEVELS.map((level) => {
    const inLevel = cases.filter((item) => (item.risk ?? 'Not scored') === level);
    const flagged = inLevel.filter((item) => item.flags.length).length;
    const flags = inLevel.reduce((sum, item) => sum + flagTotal(item), 0);
    return { level, cases: inLevel.length, flagged, average: inLevel.length ? flags / inLevel.length : null };
  });
}

export function FlagsByRisk({ cases }: { cases: FlagCase[] }) {
  const rows = flagsByLevel(cases).filter((row) => row.cases);
  const peak = Math.max(1, ...rows.map((row) => row.average ?? 0));

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Flags by risk level</h2>
          <p>Average flags per case at each risk level</p>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="empty-insight">No cases in this date and status selection.</p>
      ) : (
        <ul className="flag-risk-list">
          {rows.map((row) => (
            <li key={row.level}>
              <span className="flag-risk-level"><i className={levelClass(row.level)} aria-hidden="true" />{row.level}</span>
              <span className="request-track" aria-hidden="true">
                <i className={levelClass(row.level)} style={{ width: `${((row.average ?? 0) / peak) * 100}%` }} />
              </span>
              <strong>{row.average !== null ? row.average.toFixed(1) : '—'}</strong>
              <small>
                {row.flagged} of {plural(row.cases, 'case')} flagged
              </small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
