import { useEffect, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { getCaseCosts } from '../services/uploadService';
import { formatDuration } from './upload/processingTimes';

// The dashboard Overview's case panels: what is waiting on an officer, how many cases came in over time, which
// documents the analyses ask for most, and what the processing took in time and Claude spend. None lists cases the
// way the case queue does; each answers a question about them.

type Risk = 'Critical' | 'High' | 'Medium' | 'Low';
type FlagSeverity = 'High' | 'Medium' | 'Low';

const RISK_RANK: Record<Risk, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };
const DAY_MS = 24 * 60 * 60 * 1000;

// "today", "1 day", "5 days": how long ago a case came in.
function age(submitted: string): string {
  const days = Math.max(0, Math.floor((Date.now() - new Date(`${submitted}T12:00:00`).getTime()) / DAY_MS));
  return days === 0 ? 'today' : `${days} ${days === 1 ? 'day' : 'days'} ago`;
}

// ---------------------------------------------------------------------------------------------------------------
// Needs attention

export interface AttentionCase {
  id: string;
  // Set for a live case, which opens; sample rows don't.
  routeId?: string;
  applicant: string;
  // The stage's label, and its tone (utils/caseStages.ts).
  status: string;
  tone: string;
  stuck: boolean;
  submitted: string;
  risk: Risk | null;
  score: number | null;
  // The case's most serious flag.
  topFlag: { name: string; severity: FlagSeverity } | null;
  // The documents its analysis recommends requesting.
  toRequest: string[];
}

// What the officer has to do for a case at each stage that waits on them; the others (the system is working, or
// the case is closed) don't need attention.
const WAITING_ON_OFFICER: Record<string, string> = {
  Failed: 'Failed: open it to see what to fix',
  'Pending Review': 'Documents waiting for review',
  'Documents Incomplete': 'Required documents missing',
  'Ready for Analysis': 'Ready for the case analysis',
  'Pending Evaluation': 'Findings waiting for review',
  'Case Analysed': 'Analysed: ready to finalise',
  Open: 'No documents uploaded yet',
};

const SHOWN_WAITING = 6;

// The cases waiting on an officer (or stuck), riskiest first, then the highest score, then the longest waiting:
// each with why it waits, its risk, its most serious flag and how many documents to request.
export function NeedsAttention({ cases, onOpen }: { cases: AttentionCase[]; onOpen: (item: AttentionCase) => void }) {
  const waiting = cases
    .filter((item) => item.stuck || item.status in WAITING_ON_OFFICER)
    .sort(
      (a, b) =>
        (b.risk ? RISK_RANK[b.risk] : 0) - (a.risk ? RISK_RANK[a.risk] : 0) ||
        (b.score ?? -1) - (a.score ?? -1) ||
        a.submitted.localeCompare(b.submitted),
    );
  const shown = waiting.slice(0, SHOWN_WAITING);

  return (
    <section className="dashboard-panel attention-panel">
      <div className="panel-heading">
        <div>
          <h2>Needs attention</h2>
          <p>Cases waiting on an officer, riskiest first</p>
        </div>
        <Link className="text-button" to="/cases">Open case queue <span>→</span></Link>
      </div>
      {shown.length === 0 ? (
        <p className="empty-insight">Nothing is waiting on an officer in this date and status selection.</p>
      ) : (
        <ul className="attention-list">
          {shown.map((item) => {
            const reason = item.stuck ? `Stuck in ${item.status}` : WAITING_ON_OFFICER[item.status];
            const content = (
              <>
                <span className="attention-case">
                  <strong>{item.id}</strong>
                  <span>{item.applicant}</span>
                </span>
                <span className="attention-reason">
                  <span className={`case-status-dot tone-${item.stuck ? 'red' : item.tone}`} aria-hidden="true" />
                  <span>
                    {reason}
                    <small>Submitted {age(item.submitted)}</small>
                  </span>
                </span>
                <span className="attention-flag">
                  {item.topFlag ? (
                    <>
                      <span className={`flag-pill-severity flag-pill-severity--${item.topFlag.severity.toLowerCase()}`}>{item.topFlag.severity}</span>
                      <span className="attention-flag-name">{item.topFlag.name}</span>
                    </>
                  ) : (
                    <span className="attention-none">No flags</span>
                  )}
                </span>
                <span className="attention-requests">
                  {item.toRequest.length > 0 ? (
                    <span className="attention-chip" title={item.toRequest.join('\n')}>
                      {item.toRequest.length} to request
                    </span>
                  ) : (
                    <span className="attention-none">—</span>
                  )}
                </span>
                <span className="attention-risk">
                  {item.risk ? (
                    <span className={`risk-score risk-score-${item.risk.toLowerCase()}`}>
                      <b>{item.score ?? '—'}</b>
                      <span className="risk-level">{item.risk}</span>
                    </span>
                  ) : (
                    <span className="attention-none" title="Shown once the case has been analysed">Not scored</span>
                  )}
                </span>
              </>
            );
            return (
              <li key={item.id}>
                {item.routeId ? (
                  <button type="button" className="attention-row" onClick={() => onOpen(item)} aria-label={`Open case ${item.id}: ${reason}`}>
                    {content}
                  </button>
                ) : (
                  <div className="attention-row">{content}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {waiting.length > shown.length && (
        <p className="insight-note">
          +{waiting.length - shown.length} more waiting, in the case queue.
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Cases over time

type Level = Risk | 'Not scored';
const LEVELS: Level[] = ['Critical', 'High', 'Medium', 'Low', 'Not scored'];
const levelClass = (level: Level) => (level === 'Not scored' ? 'risk-overview--none' : `risk-gauge-band--${level.toLowerCase()}`);

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

// The start of the day, week (Monday) or month a date falls in.
function bucketStart(day: string, unit: 'day' | 'week' | 'month'): string {
  const date = new Date(`${day}T00:00:00Z`);
  if (unit === 'week') date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  if (unit === 'month') date.setUTCDate(1);
  return isoDay(date);
}

function nextBucket(start: string, unit: 'day' | 'week' | 'month'): string {
  const date = new Date(`${start}T00:00:00Z`);
  if (unit === 'day') date.setUTCDate(date.getUTCDate() + 1);
  if (unit === 'week') date.setUTCDate(date.getUTCDate() + 7);
  if (unit === 'month') date.setUTCMonth(date.getUTCMonth() + 1);
  return isoDay(date);
}

function bucketLabel(start: string, unit: 'day' | 'week' | 'month'): string {
  const date = new Date(`${start}T12:00:00Z`);
  return unit === 'month'
    ? date.toLocaleDateString('en-SG', { month: 'short', year: '2-digit' })
    : date.toLocaleDateString('en-SG', { day: 'numeric', month: 'short' });
}

// Cases submitted per day, week or month across the date range (whichever keeps the columns readable), each column
// split by the cases' risk level.
export function CasesOverTime({ cases, since }: { cases: { submitted: string; risk: Risk | null }[]; since: string | null }) {
  const today = isoDay(new Date());
  const first = since ?? cases.map((item) => item.submitted).sort()[0] ?? today;
  const days = Math.round((new Date(today).getTime() - new Date(first).getTime()) / DAY_MS) + 1;
  const unit = days <= 31 ? 'day' : days <= 140 ? 'week' : 'month';

  const buckets: { start: string; counts: Record<Level, number>; total: number }[] = [];
  for (let start = bucketStart(first, unit); start <= today; start = nextBucket(start, unit)) {
    buckets.push({ start, counts: { Critical: 0, High: 0, Medium: 0, Low: 0, 'Not scored': 0 }, total: 0 });
  }
  const byStart = new Map(buckets.map((bucket) => [bucket.start, bucket]));
  for (const item of cases) {
    const bucket = byStart.get(bucketStart(item.submitted, unit));
    if (!bucket) continue;
    bucket.counts[item.risk ?? 'Not scored'] += 1;
    bucket.total += 1;
  }
  const peak = Math.max(1, ...buckets.map((bucket) => bucket.total));
  // Three labels under the axis: the first column, the middle one and the last.
  const labelled = new Set([0, Math.floor((buckets.length - 1) / 2), buckets.length - 1]);

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Cases over time</h2>
          <p>Cases submitted per {unit}, by risk level</p>
        </div>
      </div>
      <ul className="timeline-legend" aria-label="Risk levels">
        {LEVELS.map((level) => (
          <li key={level}>
            <i className={levelClass(level)} aria-hidden="true" />
            {level}
          </li>
        ))}
      </ul>
      {cases.length === 0 ? (
        <p className="empty-insight">No cases submitted in this date and status selection.</p>
      ) : (
        <div className="timeline" role="img" aria-label={`Cases submitted per ${unit}, peaking at ${peak}`}>
          <span className="timeline-peak" aria-hidden="true">{peak}</span>
          <div className="timeline-columns">
            {buckets.map((bucket, index) => (
              <div key={bucket.start} className="timeline-column" style={{ '--column': index } as CSSProperties}>
                <div className="timeline-stack" style={{ height: `${(bucket.total / peak) * 100}%` }}>
                  {LEVELS.map((level) =>
                    bucket.counts[level] ? <i key={level} className={levelClass(level)} style={{ flexGrow: bucket.counts[level] }} /> : null,
                  )}
                </div>
                {/* Hover: the column's date and its count per level. */}
                <span className="timeline-tip" role="tooltip">
                  <strong>
                    {unit === 'week' ? 'Week of ' : ''}
                    {bucketLabel(bucket.start, unit)} · {bucket.total} {bucket.total === 1 ? 'case' : 'cases'}
                  </strong>
                  {LEVELS.filter((level) => bucket.counts[level]).map((level) => (
                    <span key={level}>
                      <i className={levelClass(level)} aria-hidden="true" />
                      {level} {bucket.counts[level]}
                    </span>
                  ))}
                </span>
                <span className="timeline-label" aria-hidden="true">
                  {labelled.has(index) ? bucketLabel(bucket.start, unit) : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Most requested documents

// The analysis words its requests freely ("Bank-issued statement for BA account June to August 2026"), so they're
// grouped by what they ask for. The first group whose pattern matches a request takes it.
const REQUEST_GROUPS: { name: string; pattern: RegExp }[] = [
  { name: 'ACRA business profile', pattern: /acra|bizfile|business profile|uen/i },
  { name: 'Certificate of incorporation', pattern: /incorporation/i },
  { name: 'NRIC / passport', pattern: /nric|passport|identity/i },
  { name: 'Bank statement', pattern: /bank.*statement|statement.*bank|giro/i },
  { name: 'Loan statements', pattern: /loan/i },
  { name: 'Tenancy and rent', pattern: /tenancy|rent|landlord|lease/i },
  { name: 'Proof of address', pattern: /address|utility/i },
  { name: 'Payslip / salary', pattern: /payslip|salary|cpf/i },
  { name: 'Tax assessment', pattern: /assessment|iras|tax/i },
  { name: 'Purchase order / invoice', pattern: /purchase order|invoice/i },
];
const SHOWN_GROUPS = 6;

// The documents the analyses ask for most, grouped by kind: how many cases ask for each, with examples on hover.
export function MostRequestedDocuments({ cases }: { cases: { id: string; toRequest: string[] }[] }) {
  const groups = new Map<string, { cases: Set<string>; examples: string[] }>();
  for (const item of cases) {
    for (const request of item.toRequest) {
      const name = REQUEST_GROUPS.find((group) => group.pattern.test(request))?.name ?? 'Other';
      const group = groups.get(name) ?? { cases: new Set<string>(), examples: [] };
      group.cases.add(item.id);
      if (group.examples.length < 3 && !group.examples.includes(request)) group.examples.push(request);
      groups.set(name, group);
    }
  }
  // Other last, whatever its size: it isn't one kind of document.
  const ranked = [...groups.entries()]
    .sort(([a, x], [b, y]) => Number(a === 'Other') - Number(b === 'Other') || y.cases.size - x.cases.size)
    .slice(0, SHOWN_GROUPS);
  const asking = cases.filter((item) => item.toRequest.length > 0).length;
  const peak = Math.max(1, ...ranked.map(([, group]) => group.cases.size));

  return (
    <section className="dashboard-panel request-panel">
      <div className="panel-heading">
        <div>
          <h2>Most requested documents</h2>
          <p>What the case analyses ask the applicants for, by kind</p>
        </div>
      </div>
      {ranked.length === 0 ? (
        <p className="empty-insight">No analysis in this selection asks for more documents.</p>
      ) : (
        <ul className="request-list">
          {ranked.map(([name, group]) => (
            <li key={name} className="request-row" title={`e.g. ${group.examples.join('; ')}`}>
              <span className="request-name">{name}</span>
              <span className="request-track" aria-hidden="true">
                <i style={{ width: `${(group.cases.size / peak) * 100}%` }} />
              </span>
              <strong>
                {group.cases.size}
                <small> {group.cases.size === 1 ? 'case' : 'cases'}</small>
              </strong>
            </li>
          ))}
        </ul>
      )}
      <p className="insight-note">
        {asking} of {cases.length} {cases.length === 1 ? 'case asks' : 'cases ask'} for more documents. Grouped by keyword, so a
        request worded unusually may sit under Other.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Processing time and cost

const usd = (value: number) => `$${value.toFixed(value < 1 ? 3 : 2)}`;

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// How long the automated processing took and what Claude cost, across the analysed cases in view: the middle and
// the extremes. Costs come from the audit trail, so only for live cases.
export function ProcessingAndCost({
  cases,
  isPreview,
}: {
  cases: { id: string; caseId?: string; processingMs?: number | null }[];
  isPreview: boolean;
}) {
  const timed = cases.flatMap((item) => (item.processingMs != null ? [{ id: item.id, value: item.processingMs }] : []));
  const [costs, setCosts] = useState<Record<string, number> | null>(null);
  const [costError, setCostError] = useState<string | null>(null);
  const live = cases.flatMap((item) => (item.caseId ? [item.caseId] : []));
  const key = live.join(',');

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    setCostError(null);
    getCaseCosts(key.split(','))
      .then((loaded) => {
        if (!cancelled) setCosts(loaded);
      })
      .catch((err: unknown) => {
        // The panel says only that the costs didn't load (a missing route reads as "no longer exists" otherwise).
        console.error('Failed to load the case costs:', err);
        if (!cancelled) setCostError("Couldn't load the costs. Try refreshing.");
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const costed = cases.flatMap((item) =>
    item.caseId && costs?.[item.caseId] != null ? [{ id: item.id, value: costs[item.caseId] }] : [],
  );
  const slowest = timed.length ? timed.reduce((a, b) => (b.value > a.value ? b : a)) : null;
  const priciest = costed.length ? costed.reduce((a, b) => (b.value > a.value ? b : a)) : null;
  const total = costed.reduce((sum, item) => sum + item.value, 0);

  return (
    <section className="dashboard-panel processing-panel">
      <div className="panel-heading">
        <div>
          <h2>Processing time &amp; cost</h2>
          <p>Automated processing and Claude spend across the cases in view{isPreview ? ' (sample timings)' : ''}</p>
        </div>
      </div>

      <div className="processing-metric">
        <span className="processing-metric-label">Median processing time</span>
        {timed.length ? (
          <>
            <strong>{formatDuration(median(timed.map((item) => item.value)))}</strong>
            <span className="processing-metric-detail">
              {timed.length} analysed {timed.length === 1 ? 'case' : 'cases'} · slowest {slowest!.id}, {formatDuration(slowest!.value)}
            </span>
          </>
        ) : (
          <span className="processing-metric-empty">Shown once a case has been analysed</span>
        )}
      </div>

      <div className="processing-metric">
        <span className="processing-metric-label">Claude cost</span>
        {costError ? (
          <span className="processing-metric-empty" role="alert">{costError}</span>
        ) : isPreview || !live.length ? (
          <span className="processing-metric-empty">Recorded for live cases only</span>
        ) : costs === null ? (
          <span className="processing-metric-empty">Loading…</span>
        ) : costed.length ? (
          <>
            <strong>{usd(total)}</strong>
            <span className="processing-metric-detail">
              {usd(total / costed.length)} a case on average · highest {priciest!.id}, {usd(priciest!.value)}
            </span>
          </>
        ) : (
          <span className="processing-metric-empty">No Claude spend recorded for these cases</span>
        )}
      </div>
    </section>
  );
}
