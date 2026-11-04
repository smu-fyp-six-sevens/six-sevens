import { useState, type CSSProperties } from 'react';
import SelectMenu from '../components/SelectMenu';
import CompareDelta from '../components/CompareDelta';
import { LEVELS, levelClass, type Risk } from './dashboardRisk';

// The dashboard's Flags tab: how much the checks flag, which checks and documents the flags land on, and how flags
// line up with the cases' risk. Each check opens to the cases it flagged.

type Severity = 'High' | 'Medium' | 'Low';
const SEVERITY_RANK: Record<Severity, number> = { High: 3, Medium: 2, Low: 1 };
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

export interface FlagCase {
  id: string;
  // Set for a live case, which opens; sample rows don't.
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

// The figures, and when comparing, each one's change in place of its caption.
export function FlagKpis({ cases, compare }: { cases: FlagCase[]; compare?: FlagCase[] }) {
  const now = flagFigures(cases);
  const before = compare ? flagFigures(compare) : null;
  const oneDecimal = (value: number) => value.toFixed(1);

  return (
    <section className="case-kpi-grid flag-kpi-grid" aria-label="Flag figures">
      <article className="dashboard-stat-card">
        <span className="stat-caption">Cases with flags</span>
        <span className="stat-number">{now.flagged}<small> / {cases.length}</small></span>
        {before ? <CompareDelta value={now.flagged} previous={before.flagged} /> : <span className="stat-caption">{!cases.length ? 'No cases in view' : now.flagged === cases.length ? 'Every case in view has flags' : `${plural(cases.length - now.flagged, 'case')} with no flags`}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Flags raised</span>
        <span className="stat-number">{now.total}</span>
        {before ? <CompareDelta value={now.total} previous={before.total} /> : <span className="stat-caption">by {plural(now.checks, 'check')}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Flags per flagged case</span>
        <span className="stat-number">{now.average != null ? oneDecimal(now.average) : '—'}</span>
        {before ? <CompareDelta value={now.average} previous={before.average} format={oneDecimal} /> : <span className="stat-caption">{now.flagged ? `averaged across ${plural(now.flagged, 'case')}` : 'No case flagged'}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">High severity checks</span>
        <span className="stat-number">{now.severe}<small> / {now.checks}</small></span>
        {before ? <CompareDelta value={now.severe} previous={before.severe} /> : <span className="stat-caption">{now.severe ? `on ${plural(now.severeCases, 'case')}` : 'None in view'}</span>}
      </article>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Checks

const CHECK_SORTS = ['Most cases', 'Most flags', 'Most severe'];

// A row per check: the share of cases it flagged, in its severity's colour, and its flag count. Opening a row shows
// the documents it was found on, the risk of the cases it flagged, and the cases themselves.
export function CheckBreakdown({ cases, compare, onOpen }: { cases: FlagCase[]; compare?: FlagCase[]; onOpen: (item: FlagCase) => void }) {
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
  const before = compare ? new Map(summarise(compare).map((check) => [check.name, check])) : null;
  // A check that flagged cases only in the comparison is listed after them at 0, so a drop to none shows.
  for (const was of before?.values() ?? []) {
    if (!checks.some((check) => check.name === was.name)) {
      checks.push({ name: was.name, severity: was.severity, flags: 0, cases: [], documentTypes: new Map() });
    }
  }

  return (
    <section className={`dashboard-panel check-panel${before ? ' check-panel--compare' : ''}`}>
      <div className="panel-heading">
        <div>
          <h2>Flags by check</h2>
          <p>Share of the cases in view each check flagged{before ? '; the striped marker is its share in the comparison period' : ''}. Open a check for its cases.</p>
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
            const was = before?.get(check.name);
            const wasShare = compare?.length ? (was?.cases.length ?? 0) / compare.length : null;
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
                    {wasShare !== null && <span className="compare-marker" style={{ left: `${wasShare * 100}%` }} title={`Comparison period: ${Math.round(wasShare * 100)}% of cases`} />}
                  </span>
                  <span className="check-figure"><strong>{check.cases.length}</strong> {check.cases.length === 1 ? 'case' : 'cases'}{before ? <CompareDelta value={check.cases.length} previous={was?.cases.length ?? 0} compact /> : <small>{Math.round(share * 100)}%</small>}</span>
                  <span className="check-figure"><strong>{check.flags}</strong> {check.flags === 1 ? 'flag' : 'flags'}{before && <CompareDelta value={check.flags} previous={was?.flags ?? 0} compact />}</span>
                </button>
                {isOpen && !check.cases.length && (
                  <div className="check-detail"><p className="empty-insight">Not flagged on any case in this period; {plural(was?.cases.length ?? 0, 'case')} in the comparison.</p></div>
                )}
                {isOpen && check.cases.length > 0 && (
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
// When comparing, each cell has its change, and a check or document type flagged only in the comparison still shows.
export function CheckDocumentMatrix({ cases, compare }: { cases: FlagCase[]; compare?: FlagCase[] }) {
  const now = summarise(cases);
  const before = compare ? new Map(summarise(compare).map((check) => [check.name, check])) : null;
  const names = [...new Set([...now.map((check) => check.name), ...(before?.keys() ?? [])])];
  const byName = new Map(now.map((check) => [check.name, check]));
  const countOf = (check: CheckSummary | undefined, type: string) => check?.documentTypes.get(type) ?? 0;
  const checks = names
    .map((name) => ({ name, now: byName.get(name), was: before?.get(name) }))
    .sort((a, b) => (b.now?.cases.length ?? 0) - (a.now?.cases.length ?? 0) || (b.was?.cases.length ?? 0) - (a.was?.cases.length ?? 0));
  const totals = new Map<string, number>();
  checks.forEach((check) => [check.now, check.was].forEach((summary) => summary?.documentTypes.forEach((count, type) => totals.set(type, (totals.get(type) ?? 0) + count))));
  const types = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, SHOWN_TYPES).map(([type]) => type);
  const peak = Math.max(1, ...checks.flatMap((check) => types.map((type) => countOf(check.now, type))));

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Where each check flags</h2>
          <p>Cases flagged per check and document type; darker is more{before && ', with the change from the comparison'}</p>
        </div>
      </div>
      {checks.length === 0 ? (
        <p className="empty-insight">No flags in this date and status selection.</p>
      ) : (
        <div className="flag-matrix-wrap">
          <table className={`flag-matrix${before ? ' flag-matrix--compare' : ''}`} style={{ '--columns': types.length } as CSSProperties}>
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
                    const count = countOf(check.now, type);
                    const was = countOf(check.was, type);
                    const delta = before && count !== was ? <CompareDelta value={count} previous={was} compact /> : null;
                    return (
                      <td key={type} title={`${check.name} on ${type}: ${plural(count, 'case')}${before ? ` (vs ${was})` : ''}`}>
                        {count > 0 ? (
                          <span className={count / peak > 0.55 ? 'dark' : undefined} style={{ '--level': `${Math.round(18 + (count / peak) * 72)}%` } as CSSProperties}>
                            {count}{delta}
                          </span>
                        ) : delta && <span className="flag-matrix-gone">{delta}</span>}
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

export function FlagsByRisk({ cases, compare }: { cases: FlagCase[]; compare?: FlagCase[] }) {
  const before = compare ? flagsByLevel(compare) : null;
  const rows = flagsByLevel(cases)
    .map((row, index) => ({ ...row, was: before?.[index].average ?? null }))
    .filter((row) => row.cases || row.was !== null);
  const peak = Math.max(1, ...rows.map((row) => Math.max(row.average ?? 0, row.was ?? 0)));

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Flags by risk level</h2>
          <p>Average flags per case at each risk level{before ? '; the striped marker is the comparison period' : ''}</p>
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
                {row.was !== null && <span className="compare-marker" style={{ left: `${(row.was / peak) * 100}%` }} title={`Comparison period: ${row.was.toFixed(1)} a case`} />}
              </span>
              <strong>{row.average !== null ? row.average.toFixed(1) : '—'}</strong>
              <small>
                {row.flagged} of {plural(row.cases, 'case')} flagged
                {before && ` · was ${row.was !== null ? row.was.toFixed(1) : '—'} a case`}
              </small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
