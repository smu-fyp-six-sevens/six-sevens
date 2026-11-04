import { useEffect, useState } from 'react';
import type { CheckMetrics, DetectionMetrics, DetectionMetricsFilters } from '../services/uploadService';
import { getDetectionMetrics } from '../services/uploadService';
import { Rate } from '../components/Rate';
import { RATE_MEANING, formatCheck } from '../utils/checkLabels';
import { InfoLabel, InfoTip } from '../components/InfoTip';
import CompareDelta from '../components/CompareDelta';

type RateKind = 'precision' | 'recall';
type CheckStatus = 'below' | 'few' | 'ok';

const FEW_REVIEWS = 'Few reviews';
const STATUS_RANK: Record<CheckStatus, number> = { below: 2, few: 1, ok: 0 };

// The metrics for the dashboard's current date and status filters, reloaded whenever they change; none for null
// (no comparison).
function useDetectionMetrics(filters: DetectionMetricsFilters | null) {
  const [metrics, setMetrics] = useState<DetectionMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = filters ? JSON.stringify([filters.since ?? null, filters.until ?? null, filters.statuses ?? []]) : null;

  useEffect(() => {
    if (!key) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    const [since, until, statuses] = JSON.parse(key) as [string | null, string | null, DetectionMetricsFilters['statuses']];
    setError(null);
    getDetectionMetrics({ since, until, statuses })
      .then((loaded) => {
        if (!cancelled) setMetrics(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load the detection metrics');
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return { metrics, error };
}

const reviewCount = (check: Pick<CheckMetrics, 'correct' | 'wrong' | 'missed'>) => check.correct + check.wrong + check.missed;

function checkStatus(check: CheckMetrics): CheckStatus {
  if (check.flags.some((flag) => flag !== FEW_REVIEWS)) return 'below';
  return check.flags.includes(FEW_REVIEWS) ? 'few' : 'ok';
}

const asPercent = (rate: number) => `${Math.round(rate * 100)}%`;
const checksOnTarget = (metrics: DetectionMetrics) => metrics.overall.by_check.filter((check) => checkStatus(check) === 'ok').length;
const checkKey = (check: CheckMetrics) => `${check.scope}-${check.check_name}`;
// Each check in the comparison period, to find a check's rates then; none when not comparing.
const checksBefore = (metrics: DetectionMetrics | null) => (metrics ? new Map(metrics.overall.by_check.map((check) => [checkKey(check), check])) : null);

// A rate as a bar against its target line, green at or above it and red below. With nothing to measure yet, only
// the reason (Rate explains it on hover).
// When comparing (previous isn't undefined), a striped marker above the bar marks the comparison's rate and a pill gives the change,
// green up and red down; "no data before" when the comparison had nothing to measure.
function RateMeter({ rate, kind, target, large = false, previous }: { rate: number | null; kind: RateKind; target: number; large?: boolean; previous?: number | null }) {
  const comparing = previous !== undefined;
  const change = !comparing ? null : previous === null
    ? <span className="rate-meter-new" title="Nothing reviewed for this check in the comparison period">no data before</span>
    : <CompareDelta value={rate} previous={previous} format={asPercent} better="up" points compact />;
  if (rate === null) {
    return (
      <div className={`rate-meter rate-meter--empty${comparing ? ' rate-meter--compare' : ''}`}>
        <Rate rate={null} kind={kind} className="rate-meter-value" />
        {comparing && previous !== null && <span className="rate-meter-new">was {asPercent(previous)}</span>}
      </div>
    );
  }
  return (
    <div className={`rate-meter${large ? ' rate-meter--large' : ''}${comparing && !large ? ' rate-meter--compare' : ''}`}>
      <Rate rate={rate} kind={kind} className="rate-meter-value" />
      <span className="rate-meter-track">
        <i className={rate >= target ? 'rate-fill--met' : 'rate-fill--below'} style={{ width: `${Math.max(rate * 100, 2)}%` }} />
        <span className="rate-meter-target" style={{ left: `${target * 100}%` }}>
          {large && <span className="rate-meter-target-label">Target {asPercent(target)}</span>}
        </span>
        {comparing && previous !== null && <span className="compare-marker" style={{ left: `${previous * 100}%` }} title={`Comparison period: ${asPercent(previous)}`} />}
      </span>
      {!large && change}
    </div>
  );
}

// A rate as a ring filled to it, green at or above its target and red below, with a tick at the target and the
// rate (red below target) in the middle. With
// nothing to measure yet, an empty ring and Rate's "Not measured".
function RateRing({ rate, kind, target, label, previous }: { rate: number | null; kind: RateKind; target: number; label: string; previous?: number | null }) {
  // The target's tick across the ring. The ring is drawn from 3 o'clock and turned a quarter back (CSS) so it
  // starts at the top; the tick is placed the same way.
  const angle = target * 2 * Math.PI;
  const at = (radius: number) => [50 + radius * Math.cos(angle), 50 + radius * Math.sin(angle)];
  const [[x1, y1], [x2, y2]] = [at(31), at(49)];
  return (
    <div className="rate-ring">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle className="rate-ring-track" cx="50" cy="50" r="40" />
        {rate !== null && (
          <circle
            className={`rate-ring-fill ${rate >= target ? 'rate-fill--met' : 'rate-fill--below'}`}
            cx="50"
            cy="50"
            r="40"
            pathLength={100}
            strokeDasharray={`${Math.max(rate * 100, 1)} 100`}
          />
        )}
        <line className="rate-ring-target" x1={x1} y1={y1} x2={x2} y2={y2} />
      </svg>
      <span className="rate-ring-value">
        <Rate rate={rate} kind={kind} target={target} />
      </span>
      <span className="rate-ring-label">
        <InfoLabel label={label} tip={RATE_MEANING[kind]} />
        <small>Target {asPercent(target)}</small>
        {previous !== undefined && <CompareDelta value={rate} previous={previous} format={asPercent} better="up" points />}
      </span>
    </div>
  );
}

// Correct, wrong and missed as one stacked bar with their total beside it, and the counts beneath.
// When comparing, the comparison's total beside this one and each count's change; previous null means the check
// had no reviews then.
type ReviewCounts = Pick<CheckMetrics, 'correct' | 'wrong' | 'missed'>;
function ReviewMix({ check, previous }: { check: ReviewCounts; previous?: ReviewCounts | null }) {
  const total = reviewCount(check);
  const comparing = previous !== undefined;
  const was = previous ?? { correct: 0, wrong: 0, missed: 0 };
  const parts = [
    { key: 'correct', count: check.correct, label: 'correct' },
    { key: 'wrong', count: check.wrong, label: 'wrong' },
    { key: 'missed', count: check.missed, label: 'missed' },
  ];
  return (
    <div className="review-mix">
      <span className="review-mix-top">
        <span className="review-mix-bar" aria-hidden="true">
          {total > 0 && parts.map((part) => part.count > 0 && (
            <i key={part.key} className={`review-mix-${part.key}`} style={{ width: `${part.count / total * 100}%` }} />
          ))}
        </span>
        <span className="review-mix-total">
          <strong>{total}</strong> {total === 1 ? 'review' : 'reviews'}
          {comparing && <span className="compare-was">vs {reviewCount(was)}</span>}
          {comparing && <CompareDelta value={total} previous={reviewCount(was)} compact />}
        </span>
      </span>
      <span className="review-mix-counts">
        {parts.map((part) => (
          <span key={part.key}>
            <i className={`review-mix-dot review-mix-${part.key}`} aria-hidden="true" />{part.count} {part.label}
            {comparing && <CompareDelta value={part.count} previous={was[part.key as keyof ReviewCounts]} compact />}
          </span>
        ))}
      </span>
    </div>
  );
}

// Where a check stands, in plain text; the tip gives each reason with its figure and target.
function CheckStanding({ check, targets }: { check: CheckMetrics; targets: { precision: number; recall: number; minReviews: number } }) {
  const status = checkStatus(check);
  const few = check.flags.includes(FEW_REVIEWS);
  const reasons = [
    check.precision !== null && check.precision < targets.precision
      && `Precision ${asPercent(check.precision)}, below the ${asPercent(targets.precision)} target.`,
    check.recall !== null && check.recall < targets.recall
      && `Recall ${asPercent(check.recall)}, below the ${asPercent(targets.recall)} target.`,
    few && `Only ${reviewCount(check)} of ${targets.minReviews} reviews needed for reliable rates.`,
  ].filter(Boolean);
  return (
    <InfoTip
      className={`accuracy-standing accuracy-standing--${status}`}
      tip={reasons.length ? reasons.join(' ') : `Precision and recall both meet the ${asPercent(targets.precision)} and ${asPercent(targets.recall)} targets.`}
    >
      <strong>{status === 'ok' ? 'On target' : status === 'below' ? 'Below target' : 'Few reviews'}</strong>
      {status === 'below' && few && <small>Few reviews</small>}
    </InfoTip>
  );
}

// A check's rate against the comparison, under the rate in the table: "vs 94%" and its change, or why there's none.
function RateChange({ rate, previous }: { rate: number | null; previous: number | null }) {
  if (previous === null) return <span className="rate-change rate-meter-new">not measured before</span>;
  return (
    <span className="rate-change">
      <CompareDelta value={rate} previous={previous} format={asPercent} better="up" points />
    </span>
  );
}

// The dashboard's Accuracy tab: precision and recall per detection check, from officers' reviews on case pages.
export function DetectionAccuracyPanel({ filters, compareFilters }: { filters: DetectionMetricsFilters; compareFilters: DetectionMetricsFilters | null }) {
  const { metrics, error } = useDetectionMetrics(filters);
  const { metrics: before } = useDetectionMetrics(compareFilters);

  if (error) return <p className="dashboard-data-note" role="alert">{error}</p>;
  if (!metrics) return <p className="dashboard-data-note" role="status">Loading detection accuracy…</p>;

  const { overall } = metrics;
  const reviewed = reviewCount(overall);
  const checks = [...overall.by_check].sort(
    (a, b) => STATUS_RANK[checkStatus(b)] - STATUS_RANK[checkStatus(a)] || reviewCount(b) - reviewCount(a),
  );
  const onTarget = checks.filter((check) => checkStatus(check) === 'ok').length;
  const fewReviews = checks.filter((check) => checkStatus(check) === 'few').length;
  const earlier = checksBefore(before);
  // The table's rows: a check reviewed only in the comparison is listed after them with no reviews now, so a drop
  // to none shows. Not counted in the figures above, which are this period's.
  const rows: CheckMetrics[] = [
    ...checks,
    ...[...(earlier?.values() ?? [])]
      .filter((was) => !checks.some((check) => checkKey(check) === checkKey(was)))
      .map((was) => ({ ...was, correct: 0, wrong: 0, missed: 0, precision: null, recall: null, flags: [FEW_REVIEWS] })),
  ];

  return (
    <>
      <section className="flag-summary-grid" aria-label="Detection accuracy overall">
        <article className="dashboard-stat-card accuracy-card">
          <span className="stat-caption"><InfoLabel label="Precision" tip={RATE_MEANING.precision} /></span>
          <RateMeter rate={overall.precision} kind="precision" target={metrics.target_precision} large />
          {before && <span className="accuracy-card-change"><CompareDelta value={overall.precision} previous={before.overall.precision} format={asPercent} better="up" points /></span>}
          {/* The counts stay when comparing, so a change on a handful of reviews reads as one. */}
          <span className="stat-caption">{overall.correct} correct of {overall.correct + overall.wrong} reviewed findings{before && ` (vs ${before.overall.correct} of ${before.overall.correct + before.overall.wrong})`}</span>
        </article>
        <article className="dashboard-stat-card accuracy-card">
          <span className="stat-caption"><InfoLabel label="Recall" tip={RATE_MEANING.recall} /></span>
          <RateMeter rate={overall.recall} kind="recall" target={metrics.target_recall} large />
          {before && <span className="accuracy-card-change"><CompareDelta value={overall.recall} previous={before.overall.recall} format={asPercent} better="up" points /></span>}
          <span className="stat-caption">{overall.correct} caught of {overall.correct + overall.missed} real issues{before && ` (vs ${before.overall.correct} of ${before.overall.correct + before.overall.missed})`}</span>
        </article>
        <article className="dashboard-stat-card accuracy-card">
          <span className="stat-caption">
            <InfoLabel
              label="Checks passing accuracy targets"
              tip={`Detection checks meeting both targets: precision of at least ${asPercent(metrics.target_precision)} (flags officers confirmed) and recall of at least ${asPercent(metrics.target_recall)} (real issues caught), from at least ${metrics.min_reviews} reviews.`}
            />
          </span>
          <span className="stat-number">{onTarget}<small className="stat-of"> / {checks.length}</small></span>
          {before && <span className="accuracy-card-change"><CompareDelta value={onTarget} previous={checksOnTarget(before)} better="up" previousLabel={`vs ${checksOnTarget(before)} / ${before.overall.by_check.length}`} /></span>}
          {/* A check short of reviews isn't passing, but it isn't failing either: say how many are only waiting. */}
          {fewReviews > 0 && <span className="stat-caption">{fewReviews} of {checks.length} {fewReviews === 1 ? 'has' : 'have'} fewer than {metrics.min_reviews} reviews, too few to judge</span>}
          <span className="stat-caption">
            From {metrics.cases_reviewed} {metrics.cases_reviewed === 1 ? 'case' : 'cases'}{before && ` (vs ${before.cases_reviewed})`} · {metrics.documents_reviewed}{' '}
            {metrics.documents_reviewed === 1 ? 'document' : 'documents'}{before && ` (vs ${before.documents_reviewed})`} reviewed
          </span>
        </article>
      </section>

      <section className="dashboard-panel flag-register">
        <div className="panel-heading">
          <div>
            <h2>Precision and recall per check</h2>
            <p>From officers marking findings correct or wrong, and reporting what detection missed.</p>
          </div>
        </div>
        <div className="case-table-wrap">
          <table className="dashboard-case-table flag-table accuracy-table">
            <thead>
              <tr>
                <th>CHECK</th>
                <th>REVIEWS</th>
                <th><InfoLabel label="PRECISION" tip={RATE_MEANING.precision} /></th>
                <th><InfoLabel label="RECALL" tip={RATE_MEANING.recall} /></th>
                <th>
                  <InfoLabel
                    label="FLAGS"
                    tip={`Below target: precision under ${asPercent(metrics.target_precision)} or recall under ${asPercent(metrics.target_recall)}. Few reviews: fewer than ${metrics.min_reviews}, so the rates aren't reliable yet.`}
                  />
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((check) => (
                <tr key={`${check.scope}-${check.check_name}`}>
                  <td data-label="Check">
                    <strong className="flag-name">{formatCheck(check.check_name)}</strong>
                    {check.scope === 'case' && <span className="accuracy-scope">Across documents</span>}
                  </td>
                  <td data-label="Reviews"><ReviewMix check={check} previous={earlier ? earlier.get(checkKey(check)) ?? null : undefined} /></td>
                  <td data-label="Precision">
                    <Rate rate={check.precision} kind="precision" target={metrics.target_precision} className="accuracy-rate" />
                    {earlier && <RateChange rate={check.precision} previous={earlier.get(checkKey(check))?.precision ?? null} />}
                  </td>
                  <td data-label="Recall">
                    <Rate rate={check.recall} kind="recall" target={metrics.target_recall} className="accuracy-rate" />
                    {earlier && <RateChange rate={check.recall} previous={earlier.get(checkKey(check))?.recall ?? null} />}
                  </td>
                  <td data-label="Flags"><CheckStanding check={check} targets={{ precision: metrics.target_precision, recall: metrics.target_recall, minReviews: metrics.min_reviews }} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {reviewed === 0 && (
            <p className="empty-insight table-empty">
              No findings reviewed for these cases yet. On a case's Risk section, mark each finding correct or wrong and
              report anything detection missed.
            </p>
          )}
        </div>
      </section>
    </>
  );
}

// The dashboard Overview's panel: overall precision and recall as rings against their targets with the reviews'
// mix beside them, and the checks most in need of attention (flagged first, then the most reviewed), each rate a
// bar against its target. The Accuracy tab has the full table.
export function DetectionAccuracySummary({ filters, compareFilters, onViewAll }: { filters: DetectionMetricsFilters; compareFilters: DetectionMetricsFilters | null; onViewAll: () => void }) {
  const { metrics, error } = useDetectionMetrics(filters);
  const { metrics: before } = useDetectionMetrics(compareFilters);

  const overall = metrics?.overall;
  const reviewed = overall ? reviewCount(overall) : 0;
  const earlier = checksBefore(before);
  const attention = [...(overall?.by_check ?? [])]
    .sort((a, b) => b.flags.filter((f) => f !== FEW_REVIEWS).length - a.flags.filter((f) => f !== FEW_REVIEWS).length || reviewCount(b) - reviewCount(a))
    .slice(0, 4);

  return (
    <section className="dashboard-panel classification-panel">
      <div className="panel-heading">
        <div><h2>Detection accuracy</h2><p>Precision and recall per check, from officers' reviews of findings</p></div>
        <button className="text-button" onClick={onViewAll}>View accuracy <span>→</span></button>
      </div>
      {error ? (
        <p className="empty-insight" role="alert">{error}</p>
      ) : (
        <>
          <div className="accuracy-rings">
            <RateRing rate={overall?.precision ?? null} kind="precision" target={metrics?.target_precision ?? 0.8} label="Precision" previous={before ? before.overall.precision : undefined} />
            <RateRing rate={overall?.recall ?? null} kind="recall" target={metrics?.target_recall ?? 0.8} label="Recall" previous={before ? before.overall.recall : undefined} />
            {overall && reviewed > 0 && <ReviewMix check={overall} previous={before ? before.overall : undefined} />}
          </div>
          {attention.length > 0 && metrics && earlier && (
            <p className="accuracy-key">
              <span><i className="accuracy-key-bar" aria-hidden="true" />This period</span>
              <span><i className="compare-marker compare-marker--key" aria-hidden="true" />Comparison period</span>
              <span><i className="accuracy-key-target" aria-hidden="true" />Target ({asPercent(metrics.target_precision)})</span>
            </p>
          )}
          {attention.length > 0 && metrics && (
            <div className="classification-grid">
              {attention.map((check) => (
                <article key={`${check.scope}-${check.check_name}`} className="classification-class class-check">
                  <div className="classification-risk"><i />{formatCheck(check.check_name)}</div>
                  <div className="accuracy-check-meters">
                    <span>Precision</span>
                    <RateMeter rate={check.precision} kind="precision" target={metrics.target_precision} previous={earlier ? earlier.get(checkKey(check))?.precision ?? null : undefined} />
                    <span>Recall</span>
                    <RateMeter rate={check.recall} kind="recall" target={metrics.target_recall} previous={earlier ? earlier.get(checkKey(check))?.recall ?? null : undefined} />
                  </div>
                </article>
              ))}
            </div>
          )}
          <p className="classification-note">
            {!metrics
              ? 'Loading…'
              : reviewed === 0
                ? 'No findings reviewed yet. Officers review findings in each case’s Risk Summary.'
                : `From ${metrics.cases_reviewed} reviewed ${metrics.cases_reviewed === 1 ? 'case' : 'cases'}${before ? ` (vs ${before.cases_reviewed})` : ''}`}
          </p>
        </>
      )}
    </section>
  );
}
