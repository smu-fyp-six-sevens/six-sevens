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
