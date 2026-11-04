import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getFindingFeedback } from '../../services/uploadService';
import type { FeedbackDetail } from '../../services/uploadService';
import { formatCheck } from '../../utils/checkLabels';
import { falseAlarmReasonLabel } from '../../utils/falseAlarmReasons';
import { officerName } from '../../lib/officers';

// What an officer decided on the finding this item is about, in a few words.
function itemVerdict(item: FeedbackDetail): string {
  if (item.kind === 'missed') return 'Missed by this check';
  if (item.verdict === 'correct') return 'Confirmed real';
  return 'False alarm';
}

function FeedbackItem({ item }: { item: FeedbackDetail }) {
  const reason = item.reason_label ?? falseAlarmReasonLabel(item.reason as never);
  return (
    <li>
      <details className="applied-feedback-item">
        <summary>
          <span className={`applied-feedback-verdict applied-feedback-verdict--${item.kind === 'missed' ? 'missed' : item.verdict}`}>
            {itemVerdict(item)}
          </span>
          <span className="applied-feedback-summary">{item.summary || item.note || formatCheck(item.check_name)}</span>
        </summary>
        <dl className="applied-feedback-detail">
          {reason && (
            <>
              <dt>Reason</dt>
              <dd>{reason}</dd>
            </>
          )}
          {item.note && (
            <>
              <dt>Officer's note</dt>
              <dd>{item.note}</dd>
            </>
          )}
          <dt>Reviewed by</dt>
          <dd>{officerName(item.reviewer)}</dd>
          <dt>Original case</dt>
          <dd>
            {item.greenlink_case_id ? (
              <Link to={`/cases/${item.greenlink_case_id}`}>{item.greenlink_case_id}</Link>
            ) : (
              'No longer available'
            )}
          </dd>
        </dl>
      </details>
    </li>
  );
}

// Under a finding: the officer feedback the agent was shown for its check, opened on demand (each item is fetched with
// its reason and original case). Nothing shows for a finding the agent applied no feedback to.
export function AppliedFeedback({ ids }: { ids: string[] | undefined }) {
  const [items, setItems] = useState<FeedbackDetail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!ids || ids.length === 0) return null;

  function load(open: boolean) {
    if (!open || items !== null || !ids) return;
    getFindingFeedback(ids)
      .then(setItems)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Failed to load the feedback'));
  }

  return (
    <details className="applied-feedback" onToggle={(event) => load(event.currentTarget.open)}>
      <summary>
        Applied {ids.length} {ids.length === 1 ? 'piece' : 'pieces'} of officer feedback from other cases
      </summary>
      {error && <p className="applied-feedback-note" role="alert">{error}</p>}
      {!error && items === null && <p className="applied-feedback-note">Loading…</p>}
      {items !== null && items.length === 0 && (
        <p className="applied-feedback-note">That feedback has since been withdrawn.</p>
      )}
      {items !== null && items.length > 0 && (
        <ul className="applied-feedback-list">
          {items.map((item) => (
            <FeedbackItem key={item.id} item={item} />
          ))}
        </ul>
      )}
    </details>
  );
}
