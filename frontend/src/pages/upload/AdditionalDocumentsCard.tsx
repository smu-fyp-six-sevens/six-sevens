import type { AdditionalDocument, AdditionalDocuments, RecommendationItem } from '../../services/uploadService';
import { useEffect, useRef } from 'react';
import { Icon } from '../../components/Icon';
import { requestKey } from './requestedDocument';

const formatMoney = (value: number) =>
  value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// "+54,080.00 (+18.3%)"
function formatChange(previous: number | null, current: number | null): string {
  if (previous == null || current == null) return '';
  const increase = current - previous;
  const sign = increase >= 0 ? '+' : '-';
  const percent = previous ? ` (${sign}${Math.abs((increase / previous) * 100).toFixed(1)}%)` : '';
  return `${sign}${formatMoney(Math.abs(increase))}${percent}`;
}

// The lines a total is made of, with the total as its last row — laid out like the
// balance sheet it comes from.
function BreakdownTable({ total }: { total: RecommendationItem }) {
  return (
    <table className="supporting-document-breakdown">
      <thead>
        <tr>
          <th scope="col">Breakdown</th>
          <th scope="col">Prior year</th>
          <th scope="col">This year</th>
          <th scope="col">Change</th>
        </tr>
      </thead>
      <tbody>
        {[...total.breakdown, total].map((line, index) => (
          <tr key={index} className={line === total ? 'supporting-document-breakdown-total' : undefined}>
            <th scope="row">{line.description}</th>
            <td>{line.prior_amount == null ? '' : formatMoney(line.prior_amount)}</td>
            <td>{line.amount == null ? '' : formatMoney(line.amount)}</td>
            <td>{formatChange(line.prior_amount, line.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LevelBadge({ level }: { level: string | null }) {
  if (!level) return null;
  return <span className={`risk-badge risk-badge--${level.toLowerCase()}`}>{level}</span>;
}

// The score now and at best (every request supports the client), large, and how far the documents could take it down.
function ScoreOutlook({ now, nowLevel, best, bestLevel }: { now: number; nowLevel: string | null; best: number; bestLevel: string | null }) {
  const drop = now - best;
  return (
    <div className="additional-outlook">
      <div className="additional-outlook-figures">
        <div className="additional-outlook-figure">
          <span className="additional-outlook-label">Score now</span>
          <span className="additional-outlook-value">
            <strong>{now}</strong>
            <LevelBadge level={nowLevel} />
          </span>
        </div>
        <div className="additional-outlook-figure">
          <span className="additional-outlook-label">If every request supports the client</span>
          <span className="additional-outlook-value">
            <strong>{best}</strong>
            <LevelBadge level={bestLevel} />
          </span>
        </div>
        <div className="additional-outlook-figure additional-outlook-figure--drop">
          <span className="additional-outlook-label">Could come down by</span>
          <span className="additional-outlook-value">
            <strong>{drop}</strong>
            <span className="additional-outlook-unit">{drop === 1 ? 'point' : 'points'}</span>
          </span>
        </div>
      </div>
    </div>
  );
}

// Beside each request: the score it would leave if it supports the client, and how much that takes off.
function ScoreEffect({ item, current }: { item: AdditionalDocument; current: number | null }) {
  if (!item.affects_score || item.score_if_supports == null) {
    return (
      <div className="additional-effect additional-effect--none">
        <span className="additional-effect-label">No effect on score</span>
      </div>
    );
  }
  const drop = current == null ? null : current - item.score_if_supports;
  return (
    <div className="additional-effect">
      <span className="additional-effect-label">If it supports the client</span>
      <span className="additional-effect-value">
        <strong>{item.score_if_supports}</strong>
        <LevelBadge level={item.level_if_supports} />
      </span>
      {drop != null && drop > 0 && (
        <span className="additional-effect-drop">
          {drop} {drop === 1 ? 'point' : 'points'} lower
        </span>
      )}
    </div>
  );
}

// The requests that move the score most first; the rest keep their order.
function byEffect(items: AdditionalDocument[]): AdditionalDocument[] {
  const score = (item: AdditionalDocument) =>
    item.affects_score && item.score_if_supports != null ? item.score_if_supports : Number.POSITIVE_INFINITY;
  return [...items].sort((a, b) => score(a) - score(b));
}

// Produced by the case analysis run (backend risk/additional_documents.py): checklist rules and the agent's requests.
// With `onUpload` (left out once the case is finalised), the officer goes to the Documents tab to upload what the
// client sends back: the upload, extraction and approval all happen there, where these requests are listed too.
export function AdditionalDocumentsCard({
  additionalDocuments,
  analysed,
  onUpload,
  highlightedRequest = null,
}: {
  additionalDocuments: AdditionalDocuments | null;
  analysed: boolean;
  onUpload?: () => void;
  // The request the officer followed a link to (requestKey), from the Documents tab's Requested group.
  highlightedRequest?: string | null;
}) {
  // The linked request is scrolled into view once it is on the page (the requests can arrive after the card mounts).
  const highlightedRef = useRef<HTMLDivElement>(null);
  const highlightedPresent = (additionalDocuments?.items ?? []).some((item) => requestKey(item) === highlightedRequest);
  useEffect(() => {
    if (highlightedPresent) highlightedRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  }, [highlightedPresent, highlightedRequest]);

  const items: AdditionalDocument[] = byEffect(additionalDocuments?.items ?? []);
  const reason = additionalDocuments?.reason ?? null;
  const canMoveScore = items.some((item) => item.affects_score);
  const best = additionalDocuments;

  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit"><Icon name="additional" size={20} /></span>
          <h2>Additional Documents</h2>
        </div>

        {items.length > 0 && (
          <span className="additional-documents-actions">
            <span className="summary-count">
              {items.length} {items.length === 1 ? 'request' : 'requests'}
            </span>

            {onUpload && (
              <button type="button" className="analysis-action" onClick={onUpload}>
                <Icon name="upload" size={15} />
                Upload in Documents
              </button>
            )}
          </span>
        )}
      </div>

      {!analysed ? (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon">ⓘ</span>

          <div>
            <strong>Not checked yet.</strong>

            <p>The case analysis says which further documents to request from the client, once it has run.</p>
          </div>
        </div>
      ) : (
        <>
          {(reason === 'model_failed' || reason === 'budget') && (
            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>Document requests from the findings could not be generated.</strong>

                <p>
                  {reason === 'budget'
                    ? 'The analysis ran out of its model-call allowance. '
                    : 'The request to the model failed. '}
                  Run the case analysis again to retry. Checklist items below are unaffected.
                </p>
              </div>
            </div>
          )}

          {reason === 'no_reducible_findings' && (
            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>No document can change this score.</strong>

                <p>Every finding is a tampering, registry or identity result, which more evidence does not undo.</p>
              </div>
            </div>
          )}

          {canMoveScore &&
            best?.best_case_score != null &&
            best.current_score != null &&
            best.best_case_score < best.current_score && (
              <ScoreOutlook
                now={best.current_score}
                nowLevel={best.current_level}
                best={best.best_case_score}
                bestLevel={best.best_case_level}
              />
            )}

          {items.length === 0 && (reason === null || reason === 'none_needed') ? (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon"><Icon name="check" size={18} /></span>

              <div>
                <strong>No additional documents needed.</strong>

                <p>
                  If anything in the submitted documents needs further evidence, the documents to
                  request from the client will be listed here.
                </p>
              </div>
            </div>
          ) : (
            <div className="flagged-list">
              {items.map((item) => (
                <div
                  key={requestKey(item)}
                  ref={requestKey(item) === highlightedRequest ? highlightedRef : undefined}
                  className={`flagged-item additional-request${requestKey(item) === highlightedRequest ? ' flagged-item--highlighted' : ''}`}
                  aria-current={requestKey(item) === highlightedRequest ? 'true' : undefined}
                >
                  <div className="flagged-item-main additional-request-main">
                    <h3 className="additional-request-title">{item.title}</h3>
                    <span className="additional-request-source">
                      {item.source === 'checklist' ? 'Checklist requirement' : 'Asked for by the findings'}
                    </span>

                    <p className="additional-request-why">{item.why}</p>

                    {item.recommendation && (
                      <>
                        <p className="supporting-document-request">{item.recommendation.request}</p>

                        <ul className="supporting-document-items">
                          {item.recommendation.items.map((line, index) => (
                            <li key={index} title={line.description}>
                              {line.reason}

                              {line.breakdown.length > 0 && <BreakdownTable total={line} />}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>

                  <ScoreEffect item={item} current={best?.current_score ?? null} />
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
