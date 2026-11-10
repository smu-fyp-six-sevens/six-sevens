# Learning from officers: open work

Branch `stateful-learning`. Officers' verdicts and missed issues are saved, scrubbed, to `finding_feedback`, and shown to the detection specialists and the case analysis on other cases. This lists what's left before it merges, then extending it to the classifier and the extractor.

**Rules that stay as they are:**
- feedback only informs the model, and code still enforces severity floors, impossible values, the citation ledger and `bias.py` afterwards;
- the score stays deterministic (`case_risk.py`);
- feedback never blocks a review or a run;
- no on/off setting for now (decided: too many edge cases). Reset by hand instead (section 5).

## 1. Before merging

| # | Change | Where | Size |
|---|---|---|---|
| 1.7 | **Untrack `frontend/tsconfig.tsbuildinfo`**: it's ignored but still tracked, so every `tsc -b` shows it changed | `git rm --cached` (the user) | S |
| 1.3b | **The last 6 months come first** (from 1.3, not built): lessons are ordered newest first, but older ones still count in full. Decide between a window (count only the last 6 months) and a weight, then add it to `feedback_counts` | `schema.sql`, `feedback_repository.counts` | S |
| 1.8 | **Live migration before the merge**: run `backend/sql/2026-11-10_finding_feedback_counts.sql` (the foreign keys, indexes and `feedback_counts`). `finding_feedback` and `findings.review_reason` are already live. Then check it with the seed and `preview_feedback.py` (section 5) | Supabase | S |

## 2. Classifier: learn the common confusions

**The data is already recorded.** `document.type_corrected` and `document.retyped` in `audit_events` both carry `from_type` and `to_type`. That's labels only, with no personal data.

- **No new table:** a view over `audit_events` counts corrections per `(from_type, to_type)` pair, with `count(distinct case_id)`.
- **A pair counts once at least 2 cases agree.** The same rule as a false alarm in detection feedback (`feedback_repository.MIN_FALSE_ALARM_CASES`): one officer's mistake shouldn't teach the classifier.
- **The classifier gets a short hint** when its guess is the `from` side of a recurring pair: "Officers often correct X to Y; Y usually has [signal]."
  - The signal is a short description per pair, written by the team in code (for example "an employment letter is addressed to the employee and has no pay period").
  - Without one, the hint just names the confusion.
- **Prompt caching:** the hint goes after the cache breakpoint.

**Tests:**
- a pair seen in 2 cases gives the hint, and in 1 case doesn't;
- a guess with no recurring pair gets no hint.

## 3. Extractor: learn which fields are added and deleted

**The data is already recorded.** `document.field_edited` records `added`, `deleted` and `saved` actions, with field names and changes. The learning uses field names only, never values:
- **added:** the extractor missed a field the document has;
- **deleted:** it produced a field the document doesn't have, or one that doesn't belong.

The plan:
1. **Add `document_type` to the `document.field_edited` detail.** It's missing today, and the document may since have been deleted. Events from before this change can't be grouped by type, so the counts start empty.
2. **A view over `audit_events`** counts additions and deletions per `(document_type, field_name)`, with `count(distinct case_id)`, applying the same at-least-2-cases rule. A `saved` change from `None` counts as an addition, and one to `None` as a deletion.
3. **The extractor gets a short hint per document type:** "On payslips, officers often add `employer_address`: look for it. They often delete `bonus`: only extract it when the payslip shows one."

**Tests:**
- additions and deletions are counted per document type and case;
- values never appear in the hint;
- a field with one case's edits gives no hint.

## 4. Later

- **An eval of whether feedback helps:** the same cases with the table empty and with a fixed seed, comparing precision and recall. Run on demand only.

## 5. Resetting for a test

There's no on/off setting, so reset by hand:
- **Empty it:** run `truncate public.finding_feedback;` in Supabase's SQL editor.
- **Re-seed a known set:** `backend/sql/dev/seed_feedback_test.sql`.
- **Check what an agent would be shown:** `backend/scripts/preview_feedback.py`.

Sections 2 and 3 read `audit_events`, which is never deleted, so they can't be reset this way. They count only events after a cut-off date, a constant in the Python query rather than in the view (decided), so a test moves it without a migration.
