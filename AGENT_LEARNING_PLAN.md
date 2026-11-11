# Learning from officers: open work

Branch `stateful-learning`. Officers' verdicts and missed issues teach the detection specialists and the case analysis (`finding_feedback`), and their type corrections and field edits teach the classifier and the extractor (`audit_events`). How it works is in `HOW_IT_WORKS.md` ("Learning from officers' verdicts"). Nothing is left before the merge; this lists what comes later.

**Rules that stay as they are:**
- feedback only informs the model, and code still enforces severity floors, impossible values, the citation ledger and `bias.py` afterwards;
- the score stays deterministic (`case_risk.py`);
- feedback never blocks a review or a run;
- no on/off setting for now (decided: too many edge cases). Reset by hand instead (section 2).

## 1. Later

- **An eval of whether feedback helps:** the same cases with the table empty and with a fixed seed, comparing precision and recall. Run on demand only.

## 2. Resetting for a test

There's no on/off setting, so reset by hand:
- **Empty it:** run `truncate public.finding_feedback;` in Supabase's SQL editor.
- **Re-seed a known set:** `backend/sql/dev/seed_feedback_test.sql`, and afterwards `delete from public.cases where id like 'test-case-%';` (its feedback goes with the cases).
- **Check what an agent would be shown:** `backend/scripts/preview_feedback.py`.

The classifier and the extractor read `audit_events`, which is never deleted, so they can't be reset this way. They count only corrections after `corrections_repository.LEARNING_SINCE` (a constant in code, passed to the SQL), so a test moves it without a migration.
