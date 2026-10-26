import { useEffect, useState } from 'react';
import type { DetectionMetrics } from '../services/uploadService';
import { getDetectionMetrics } from '../services/uploadService';
import { Rate } from '../components/Rate';
import { RATE_MEANING, formatCheck } from '../utils/checkLabels';

// The dashboard's Accuracy tab: precision and recall per detection check, from officers' reviews on case pages.
export function DetectionAccuracyPanel() {
  const [metrics, setMetrics] = useState<DetectionMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDetectionMetrics()
      .then((loaded) => {
        if (!cancelled) setMetrics(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load the detection metrics');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="dashboard-data-note" role="alert">{error}</p>;
  if (!metrics) return <p className="dashboard-data-note" role="status">Loading detection accuracy…</p>;

  const { overall } = metrics;
  const reviewed = overall.correct + overall.wrong + overall.missed;
  const target = (rate: number) => `${Math.round(rate * 100)}%`;

  return (
    <>
      <section className="flag-summary-grid" aria-label="Detection accuracy overall">
        <article className="dashboard-stat-card">
          <span className="stat-caption" title={RATE_MEANING.precision}>Precision</span>
          <Rate rate={overall.precision} kind="precision" className="stat-number" />
          <span className="stat-caption">{overall.correct} correct of {overall.correct + overall.wrong} reviewed findings</span>
        </article>
        <article className="dashboard-stat-card">
          <span className="stat-caption" title={RATE_MEANING.recall}>Recall</span>
          <Rate rate={overall.recall} kind="recall" className="stat-number" />
          <span className="stat-caption">{overall.correct} caught of {overall.correct + overall.missed} real issues</span>
        </article>
        <article className="dashboard-stat-card">
          <span className="stat-caption">Reviewed</span>
          <span className="stat-number">{metrics.cases_reviewed}</span>
          <span className="stat-caption">
            {metrics.cases_reviewed === 1 ? 'case' : 'cases'} · {metrics.documents_reviewed}{' '}
            {metrics.documents_reviewed === 1 ? 'document' : 'documents'}
          </span>
        </article>
      </section>

      <section className="dashboard-panel flag-register">
        <div className="panel-heading">
          <div>
            <h2>Precision and recall per check</h2>
            <p>
              From officers marking findings correct or wrong, and reporting what detection missed. Targets:
              precision {target(metrics.target_precision)}, recall {target(metrics.target_recall)}.
            </p>
          </div>
          <span className="panel-kicker">ALL REVIEWED CASES</span>
        </div>
        <div className="case-table-wrap">
          <table className="dashboard-case-table flag-table accuracy-table">
            <thead>
              <tr>
                <th>CHECK</th>
                <th>CORRECT</th>
                <th>WRONG</th>
                <th>MISSED</th>
                <th title={RATE_MEANING.precision}>PRECISION</th>
                <th title={RATE_MEANING.recall}>RECALL</th>
                <th>FLAG</th>
              </tr>
            </thead>
            <tbody>
              {overall.by_check.map((check) => (
                <tr key={`${check.scope}-${check.check_name}`}>
                  <td data-label="Check">
                    <strong className="flag-name">{formatCheck(check.check_name)}</strong>
                    {check.scope === 'case' && <span className="flag-doc-types"> Across documents</span>}
                  </td>
                  <td data-label="Correct">{check.correct}</td>
                  <td data-label="Wrong">{check.wrong}</td>
                  <td data-label="Missed">{check.missed}</td>
                  <td data-label="Precision"><strong><Rate rate={check.precision} kind="precision" /></strong></td>
                  <td data-label="Recall"><strong><Rate rate={check.recall} kind="recall" /></strong></td>
                  <td data-label="Flag">
                    {check.flags.length === 0
                      ? <span className="no-flag">On target</span>
                      : check.flags.map((flag) => (
                          <span key={flag} className={`flag-severity ${flag === 'Few reviews' ? 'medium' : 'high'}`}>
                            {flag}
                          </span>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {reviewed === 0 && (
            <p className="empty-insight table-empty">
              No findings reviewed yet. On a case's Risk section, mark each finding correct or wrong and report anything
              detection missed.
            </p>
          )}
        </div>
        <footer className="table-footer">
          <span>
            A check with fewer than {metrics.min_reviews} reviews is flagged “Few reviews”: its rates aren't meaningful yet.
          </span>
        </footer>
      </section>
    </>
  );
}

// The dashboard Overview's panel: overall precision and recall, and the checks most in need of attention
// (flagged first, then the most reviewed). The Accuracy tab has the full table.
export function DetectionAccuracySummary({ onViewAll }: { onViewAll: () => void }) {
  const [metrics, setMetrics] = useState<DetectionMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDetectionMetrics()
      .then((loaded) => {
        if (!cancelled) setMetrics(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load the detection metrics');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const overall = metrics?.overall;
  const reviewed = overall ? overall.correct + overall.wrong + overall.missed : 0;
  const reviews = (check: { correct: number; wrong: number; missed: number }) => check.correct + check.wrong + check.missed;
  const attention = [...(overall?.by_check ?? [])]
    .sort((a, b) => b.flags.filter((f) => f !== 'Few reviews').length - a.flags.filter((f) => f !== 'Few reviews').length || reviews(b) - reviews(a))
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
          <div className="classification-overall">
            <span title={RATE_MEANING.precision}>Precision <strong><Rate rate={overall?.precision ?? null} kind="precision" /></strong></span>
            <span title={RATE_MEANING.recall}>Recall <strong><Rate rate={overall?.recall ?? null} kind="recall" /></strong></span>
          </div>
          {attention.length > 0 && (
            <div className="classification-grid">
              {attention.map((check) => (
                <article key={`${check.scope}-${check.check_name}`} className="classification-class class-check">
                  <div className="classification-risk"><i />{formatCheck(check.check_name)}</div>
                  <div className="classification-values">
                    <span>Precision <b><Rate rate={check.precision} kind="precision" /></b></span>
                    <span>Recall <b><Rate rate={check.recall} kind="recall" /></b></span>
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
                : `${metrics.cases_reviewed} ${metrics.cases_reviewed === 1 ? 'case' : 'cases'} reviewed · ${overall!.correct} correct · ${overall!.wrong} wrong · ${overall!.missed} missed`}
          </p>
        </>
      )}
    </section>
  );
}
