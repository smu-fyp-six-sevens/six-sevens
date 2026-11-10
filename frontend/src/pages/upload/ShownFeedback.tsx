import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getFindingFeedback } from '../../services/uploadService';
import type { FeedbackDetail } from '../../services/uploadService';
import { formatCheck } from '../../utils/checkLabels';
import { reviewReasonLabel } from '../../utils/reviewReasons';
import { officerName } from '../../lib/officers';

// What an officer decided on the finding this item is about, in a few words.
function itemVerdict(item: FeedbackDetail): string {
  if (item.kind === 'missed') return 'Missed by this check';
  // A "real" verdict with a reason is a finding the specialists set aside that the officer found real after all.
  if (item.verdict === 'correct') return item.reason ? 'Set aside wrongly' : 'Confirmed real';
  return 'False alarm';
}

// The most ids the agent records per lesson (feedback_counts): a lesson that hit it may have come from more cases.
const IDS_PER_LESSON = 10;

// The items in lessons, as the agent read them: "False alarm in 3 cases".
function lessons(items: FeedbackDetail[]): { label: string; items: FeedbackDetail[] }[] {
  const groups = new Map<string, FeedbackDetail[]>();
  for (const item of items) groups.set(itemVerdict(item), [...(groups.get(itemVerdict(item)) ?? []), item]);
  return [...groups].map(([verdict, grouped]) => {
    const cases = new Set(grouped.map((item) => item.case_id)).size;
    const atLeast = grouped.length >= IDS_PER_LESSON ? 'at least ' : '';
    return { label: `${verdict} in ${atLeast}${cases} ${cases === 1 ? 'case' : 'cases'}`, items: grouped };
  });
}

function FeedbackItem({ item }: { item: FeedbackDetail }) {
  const reason = item.reason_label ?? reviewReasonLabel(item.reason as never);
  return (
    <li>
      <details className="shown-feedback-item">
        <summary>
          <span className={`shown-feedback-verdict shown-feedback-verdict--${item.kind === 'missed' ? 'missed' : item.verdict}`}>
            {itemVerdict(item)}
          </span>
          <span className="shown-feedback-summary">{item.summary || item.note || formatCheck(item.check_name)}</span>
        </summary>
        <dl className="shown-feedback-detail">
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

// Under a finding: the officer feedback the agent was shown for its check code, opened on demand (each item is fetched
// with its reason and original case). Being shown it is not the same as acting on it. Nothing shows when there was none.
export function ShownFeedback({ ids }: { ids: string[] | undefined }) {
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
    <details className="shown-feedback" onToggle={(event) => load(event.currentTarget.open)}>
      <summary>
        Feedback the agent was shown: {ids.length} {ids.length === 1 ? 'item' : 'items'} from other cases
      </summary>
      {error && <p className="shown-feedback-note" role="alert">{error}</p>}
      {!error && items === null && <p className="shown-feedback-note">Loading…</p>}
      {items !== null && items.length === 0 && (
        <p className="shown-feedback-note">That feedback has since been withdrawn.</p>
      )}
      {items !== null &&
        lessons(items).map((lesson) => (
          <section key={lesson.label} className="shown-feedback-lesson">
            <p className="shown-feedback-lesson-label">{lesson.label}</p>
            <ul className="shown-feedback-list">
              {lesson.items.map((item) => (
                <FeedbackItem key={item.id} item={item} />
              ))}
            </ul>
          </section>
        ))}
    </details>
  );
}
