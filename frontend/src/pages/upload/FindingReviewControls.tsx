import { useState } from 'react';
import type { FindingReview, MetricsSummary, ReviewScope, ReviewVerdict } from '../../services/uploadService';
import { formatCheck, formatRate } from '../../utils/checkLabels';

// ✓ Correct / ✗ Wrong on one finding. Pressing the chosen one again clears it.
export function VerdictButtons({
  verdict,
  disabled,
  onChange,
}: {
  verdict: ReviewVerdict | null;
  disabled: boolean;
  onChange: (verdict: ReviewVerdict | null) => void;
}) {
  return (
    <span className="verdict-buttons" role="group" aria-label="Is this finding correct?">
      <button
        type="button"
        className={`verdict-button verdict-button--correct${verdict === 'correct' ? ' verdict-button--on' : ''}`}
        aria-pressed={verdict === 'correct'}
        disabled={disabled}
        title="This finding is correct: a real issue"
        onClick={() => onChange(verdict === 'correct' ? null : 'correct')}
      >
        ✓ Correct
      </button>
      <button
        type="button"
        className={`verdict-button verdict-button--wrong${verdict === 'wrong' ? ' verdict-button--on' : ''}`}
        aria-pressed={verdict === 'wrong'}
        disabled={disabled}
        title="This finding is wrong: not a real issue"
        onClick={() => onChange(verdict === 'wrong' ? null : 'wrong')}
      >
        ✗ Wrong
      </button>
    </span>
  );
}

// "Precision 67% · Recall 50%", once anything in this group has been reviewed.
export function MetricsLine({ summary, label }: { summary: MetricsSummary | undefined; label?: string }) {
  if (!summary || summary.correct + summary.wrong + summary.missed === 0) return null;
  return (
    <span className="review-metrics-line">
      {label && <span className="review-metrics-label">{label}</span>}
      Precision {formatRate(summary.precision, 'precision')} · Recall {formatRate(summary.recall, 'recall')}
    </span>
  );
}

// Issues the officer found that detection didn't raise, and the form to report one.
export function MissedIssues({
  scope,
  missed,
  checks,
  saving,
  onAdd,
  onRemove,
}: {
  scope: ReviewScope;
  missed: FindingReview[];
  checks: string[];
  saving: boolean;
  onAdd: (checkName: string, note: string) => void;
  onRemove: (reviewId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [checkName, setCheckName] = useState('');
  const [note, setNote] = useState('');

  return (
    <div className="missed-issues">
      {missed.length > 0 && (
        <ul className="missed-issue-list">
          {missed.map((review) => (
            <li key={review.id}>
              <span className="missed-issue-tag">Missed</span>
              <span className="missed-issue-text">
                <strong>{formatCheck(review.check_name)}</strong>
                {review.note && <span> — {review.note}</span>}
                <small> · reported by {review.reviewer}</small>
              </span>
              <button
                type="button"
                className="missed-issue-remove"
                aria-label="Remove this missed issue"
                disabled={saving}
                onClick={() => onRemove(review.id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {open ? (
        <form
          className="missed-issue-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!checkName) return;
            onAdd(checkName, note.trim());
            setOpen(false);
            setCheckName('');
            setNote('');
          }}
        >
          <label>
            <span>Check that should have caught it</span>
            <select value={checkName} onChange={(event) => setCheckName(event.target.value)} required>
              <option value="" disabled>
                Choose a check…
              </option>
              {checks.map((check) => (
                <option key={check} value={check}>
                  {formatCheck(check)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>What was missed (optional)</span>
            <input
              value={note}
              maxLength={1000}
              placeholder={scope === 'case' ? 'e.g. Director NRIC differs between the CBR and the NRIC' : 'e.g. Closing balance was edited'}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <div className="missed-issue-actions">
            <button type="button" className="missed-issue-cancel" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="missed-issue-save" disabled={!checkName || saving}>
              Save
            </button>
          </div>
        </form>
      ) : (
        <button type="button" className="missed-issue-add" onClick={() => setOpen(true)}>
          + Report a missed issue
        </button>
      )}
    </div>
  );
}

// The case's precision and recall: overall, per document vs across documents, and per check.
export function ReviewSummary({ caseSummary, crossDocument }: { caseSummary: MetricsSummary; crossDocument: MetricsSummary }) {
  const reviewed = caseSummary.correct + caseSummary.wrong + caseSummary.missed;
  return (
    <section className="review-summary" aria-label="Detection accuracy for this case">
      <div className="review-summary-head">
        <h3>Detection accuracy</h3>
        <span className="review-summary-hint">
          {reviewed === 0
            ? 'Mark each finding correct or wrong, and report anything detection missed.'
            : `${caseSummary.correct} correct · ${caseSummary.wrong} wrong · ${caseSummary.missed} missed`}
        </span>
      </div>
      {reviewed > 0 && (
        <>
          <div className="review-summary-figures">
            <div>
              <span className="review-summary-value">{formatRate(caseSummary.precision, 'precision')}</span>
              <span className="review-summary-caption">Precision</span>
            </div>
            <div>
              <span className="review-summary-value">{formatRate(caseSummary.recall, 'recall')}</span>
              <span className="review-summary-caption">Recall</span>
            </div>
            <MetricsLine summary={crossDocument} label="Across documents" />
          </div>
          <table className="review-check-table">
            <thead>
              <tr>
                <th>Check</th>
                <th>Correct</th>
                <th>Wrong</th>
                <th>Missed</th>
                <th>Precision</th>
                <th>Recall</th>
              </tr>
            </thead>
            <tbody>
              {caseSummary.by_check.map((check) => (
                <tr key={`${check.scope}-${check.check_name}`}>
                  <td>
                    {formatCheck(check.check_name)}
                    {check.scope === 'case' && <small> (across documents)</small>}
                  </td>
                  <td>{check.correct}</td>
                  <td>{check.wrong}</td>
                  <td>{check.missed}</td>
                  <td>{formatRate(check.precision, 'precision')}</td>
                  <td>{formatRate(check.recall, 'recall')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
