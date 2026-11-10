# Learning from officers: open work

Branch `stateful-learning`. Officers' verdicts and missed issues teach the detection specialists and the case analysis (`finding_feedback`), and their type corrections and field edits teach the classifier and the extractor (`audit_events`). How it works is in `HOW_IT_WORKS.md` ("Learning from officers' verdicts"). This lists what's left.

**Rules that stay as they are:**
- feedback only informs the model, and code still enforces severity floors, impossible values, the citation ledger and `bias.py` afterwards;
- the score stays deterministic (`case_risk.py`);
- feedback never blocks a review or a run;
- no on/off setting for now (decided: too many edge cases). Reset by hand instead (section 3).

## 1. Before merging

| # | Change | Where | Size |
|---|---|---|---|
| 1.7 | **Untrack `frontend/tsconfig.tsbuildinfo`**: it's ignored but still tracked, so every `tsc -b` shows it changed | `git rm --cached` (the user) | S |
| 1.3b | **The last 6 months come first** (not built): lessons are ordered newest first, but older ones still count in full. Decide between a window (count only the last 6 months) and a weight, then add it to `feedback_counts` | `schema.sql`, `feedback_repository.counts` | S |
| 1.8 | **Live migration before the merge**: run `backend/sql/2026-11-10_finding_feedback_counts.sql` (the foreign keys, indexes, `feedback_counts`, `type_corrections` and `field_edit_counts`). `finding_feedback` and `findings.review_reason` are already live. Then check it with the seed and `preview_feedback.py` (section 3), and delete the file | Supabase | S |
| 1.9 | **Regenerate the regression fixtures**: `document.field_edited` now carries `document_type`, so `audit-trail-sprint5.json` is out of date (`python scripts/regenerate_regression_fixtures.py` in `backend/`) | `tests/fixtures/regression/` | S |
| 1.10 | **Review the classifier's signals**: `correction_hints.SIGNALS` (what tells each commonly confused pair apart) is a first draft, written before any correction was recorded | `langgraph/correction_hints.py` | S |

## 2. Later

- **An eval of whether feedback helps:** the same cases with the table empty and with a fixed seed, comparing precision and recall. Run on demand only.

## 3. Resetting for a test

There's no on/off setting, so reset by hand:
- **Empty it:** run `truncate public.finding_feedback;` in Supabase's SQL editor.
- **Re-seed a known set:** `backend/sql/dev/seed_feedback_test.sql`.
- **Check what an agent would be shown:** `backend/scripts/preview_feedback.py`.

The classifier and the extractor read `audit_events`, which is never deleted, so they can't be reset this way. They count only corrections after `corrections_repository.LEARNING_SINCE` (a constant in code, passed to the SQL), so a test moves it without a migration.
