import { useState } from 'react';
import type { FindingReview, MetricsSummary, ReviewScope } from '../../services/uploadService';
import ConfirmDialog from '../../components/ConfirmDialog';
import { Rate } from '../../components/Rate';
import { RATE_MEANING, belowTargetNote, formatCheck } from '../../utils/checkLabels';
import { InfoLabel } from '../../components/InfoTip';
import { officerName } from '../../lib/officers';

export type GroupReviewStatus = 'not_reviewed' | 'saved' | 'unsaved';

// Above a group's findings: tick all of them at once, and save the group's review. Saving is what makes an
// unticked finding count as wrong; before that, nothing in the group counts.
export function GroupReviewBar({
  checkedCount,
  total,
  status,
  saving,
  onToggleAll,
  onSave,
}: {
  checkedCount: number;
  total: number;
  status: GroupReviewStatus;
  saving: boolean;
  onToggleAll: (checked: boolean) => void;
  onSave: () => void;
}) {
  const allChecked = total > 0 && checkedCount === total;
  return (
    <div className="group-review-bar">
      <label className="group-review-select-all">
        <input
          type="checkbox"
          className="review-tick"
          checked={allChecked}
          ref={(input) => {
            if (input) input.indeterminate = checkedCount > 0 && !allChecked;
          }}
          disabled={saving}
          onChange={(event) => onToggleAll(event.target.checked)}
        />
        Select all
      </label>
      <span className="group-review-hint">
        <strong>Officer review:</strong> tick the real issues. Unticked ones are saved as false alarms.
      </span>
      <span className={`group-review-status group-review-status--${status}`}>
        {status === 'saved' ? 'Review saved' : status === 'unsaved' ? 'Unsaved changes' : 'Not reviewed yet'}
      </span>
      <button type="button" className="group-review-save" disabled={saving || status === 'saved'} onClick={onSave}>
        {saving ? 'Saving…' : 'Save review'}
      </button>
    </div>
  );
}

// "Precision 67% · Recall 50%", once anything in this group has been reviewed.
export function MetricsLine({ summary }: { summary: MetricsSummary | undefined }) {
  if (!summary || summary.correct + summary.wrong + summary.missed === 0) return null;
  return (
    <span className="review-metrics-line">
      Precision <Rate rate={summary.precision} kind="precision" /> · Recall <Rate rate={summary.recall} kind="recall" />
    </span>
  );
}

// The form to report a missed issue, or to correct one already reported.
function MissedIssueForm({
  scope,
  checks,
  saving,
  initialCheck = '',
  initialNote = '',
  submitLabel,
  onSubmit,
  onCancel,
}: {
  scope: ReviewScope;
  checks: string[];
  saving: boolean;
  initialCheck?: string;
  initialNote?: string;
  submitLabel: string;
  onSubmit: (checkName: string, note: string) => void;
  onCancel: () => void;
}) {
  const [checkName, setCheckName] = useState(initialCheck);
  const [note, setNote] = useState(initialNote);
  const unchanged = checkName === initialCheck && note.trim() === initialNote.trim();

  return (
    <form
      className="missed-issue-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (checkName) onSubmit(checkName, note.trim());
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
        <button type="button" className="missed-issue-cancel" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="missed-issue-save" disabled={!checkName || saving || (initialCheck !== '' && unchanged)}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

// Issues the officer found that detection didn't raise: each can be corrected or removed (after confirming),
// and a new one reported.
export function MissedIssues({
  scope,
  missed,
  checks,
  saving,
  onAdd,
  onEdit,
  onRemove,
  readOnly = false,
}: {
  scope: ReviewScope;
  missed: FindingReview[];
  checks: string[];
  saving: boolean;
  onAdd: (checkName: string, note: string) => void;
  onEdit: (reviewId: string, checkName: string, note: string) => void;
  onRemove: (reviewId: string) => void;
  // A finalised case: reported issues show, but none can be added, corrected or removed.
  readOnly?: boolean;
}) {
  // 'new' while reporting one, a review's id while correcting it, null otherwise.
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<FindingReview | null>(null);

  return (
    <div className="missed-issues">
      {missed.length > 0 && (
        <ul className="missed-issue-list">
          {missed.map((review) =>
            editing === review.id ? (
              <li key={review.id}>
                <MissedIssueForm
                  scope={scope}
                  checks={checks}
                  saving={saving}
                  initialCheck={review.check_name}
                  initialNote={review.note ?? ''}
                  submitLabel="Save changes"
                  onSubmit={(checkName, note) => {
                    onEdit(review.id, checkName, note);
                    setEditing(null);
                  }}
                  onCancel={() => setEditing(null)}
                />
              </li>
            ) : (
              <li key={review.id}>
                {/* Laid out like a finding: the tick's space, a badge in the severity's place, then the check,
                    what was missed and who reported it. */}
                <span className="missed-issue-spacer" aria-hidden="true" />
                <span className="missed-issue-tag">Missed</span>
                <span className="risk-finding-text">
                  <span className="risk-finding-check">{formatCheck(review.check_name)}</span>
                  <span className="risk-finding-summary">{review.note || 'No description given'}</span>
                  <span className="risk-finding-rationale">Reported by {officerName(review.reviewer)}</span>
                </span>
                {!readOnly && (
                <>
                <button
                  type="button"
                  className="missed-issue-edit"
                  disabled={saving || editing !== null}
                  onClick={() => setEditing(review.id)}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="missed-issue-remove"
                  aria-label="Remove this missed issue"
                  disabled={saving}
                  onClick={() => setConfirmRemove(review)}
                >
                  ×
                </button>
                </>
                )}
              </li>
            ),
          )}
        </ul>
      )}

      {editing === 'new' ? (
        <MissedIssueForm
          scope={scope}
          checks={checks}
          saving={saving}
          submitLabel="Save"
          onSubmit={(checkName, note) => {
            onAdd(checkName, note);
            setEditing(null);
          }}
          onCancel={() => setEditing(null)}
        />
      ) : readOnly ? null : (
        <button type="button" className="missed-issue-add" disabled={editing !== null} onClick={() => setEditing('new')}>
          + Report a missed issue
        </button>
      )}

      {confirmRemove && (
        <ConfirmDialog
          title="Remove this missed issue?"
          message={`"${formatCheck(confirmRemove.check_name)}${confirmRemove.note ? ` — ${confirmRemove.note}` : ''}" will no longer count as a miss, so this check's recall goes up. This can't be undone; you can report it again.`}
          confirmLabel="Remove"
          isConfirming={saving}
          onCancel={() => setConfirmRemove(null)}
          onConfirm={() => {
            onRemove(confirmRemove.id);
            setConfirmRemove(null);
          }}
        />
      )}
    </div>
  );
}

// The case's precision and recall: overall, per document vs across documents, and per check.
export function ReviewSummary({
  caseSummary, targetPrecision, targetRecall,
}: { caseSummary: MetricsSummary; targetPrecision: number; targetRecall: number }) {
  const reviewed = caseSummary.correct + caseSummary.wrong + caseSummary.missed;
  return (
    <section className="review-summary" aria-label="Detection accuracy for this case">
      <div className="review-summary-head">
        <h3>Detection accuracy</h3>
        {reviewed > 0 && (
          <span className="review-summary-hint">
            {`${caseSummary.correct} correct · ${caseSummary.wrong} wrong · ${caseSummary.missed} missed`}
          </span>
        )}
      </div>
      {reviewed === 0 && (
        <div className="review-summary-empty">
          <span className="review-summary-empty-icon" aria-hidden="true">✓</span>
          <div className="review-summary-empty-copy">
            <strong>No findings reviewed yet</strong>
            <p>Precision and recall for this case appear here once its findings are reviewed.</p>
          </div>
          <ol className="review-summary-empty-steps">
            <li>Open a document</li>
            <li>Tick the findings that are real issues and report anything detection missed</li>
            <li>Save the reviews</li>
          </ol>
        </div>
      )}
      {reviewed > 0 && (
        <>
          <div className="review-summary-figures">
            <div>
              <Rate rate={caseSummary.precision} kind="precision" className="review-summary-value" target={targetPrecision} />
              <span className="review-summary-caption"><InfoLabel label="Precision" tip={RATE_MEANING.precision} /></span>
            </div>
            <div>
              <Rate rate={caseSummary.recall} kind="recall" className="review-summary-value" target={targetRecall} />
              <span className="review-summary-caption"><InfoLabel label="Recall" tip={RATE_MEANING.recall} /></span>
            </div>
          </div>
          <table className="review-check-table">
            <thead>
              <tr>
                <th>Check</th>
                <th>Correct</th>
                <th>Wrong</th>
                <th>Missed</th>
                <th><InfoLabel label="Precision" tip={RATE_MEANING.precision} /></th>
                <th><InfoLabel label="Recall" tip={RATE_MEANING.recall} /></th>
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
                  <td><Rate rate={check.precision} kind="precision" target={targetPrecision} /></td>
                  <td><Rate rate={check.recall} kind="recall" target={targetRecall} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="review-summary-note">{belowTargetNote(targetPrecision, targetRecall)}</p>
        </>
      )}
    </section>
  );
}
