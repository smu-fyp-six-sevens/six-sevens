-- Test feedback for trying out agent learning without calling a model. Run in the Supabase SQL editor AFTER
-- backend/sql/schema.sql's finding_feedback table and feedback_counts function exist. Feedback belongs to a case (a
-- foreign key), so this adds four test cases first; every id starts with 'test-case-', and the cleanup at the bottom
-- deletes those cases, which takes their feedback with them and nothing an officer saved.
--
-- Then: python scripts/preview_feedback.py --check numerical_checks_tool --case-id <any other case id>
--
-- What it shows: a false alarm is shown only once 2 cases agree, so the PAY.NET false alarm (cases a and b) is shown
-- and the single STAT.ROUND_NUMBERS one (case c) isn't; a confirmation and a missed issue show from their first case.

insert into public.cases (id, company_name, reviews_required)
values
    ('test-case-a', 'Test Case A Pte Ltd', true),
    ('test-case-b', 'Test Case B Pte Ltd', true),
    ('test-case-c', 'Test Case C Pte Ltd', true),
    ('test-case-d', 'Test Case D Pte Ltd', true)
on conflict (id) do nothing;

insert into public.finding_feedback
    (source_key, case_id, document_id, scope, kind, check_name, check_code, document_type, verdict, reason, summary, note,
     reviewer)
values
    -- The same false alarm in two cases: shown, with its count.
    ('test:fa-1', 'test-case-a', null, 'document', 'verdict', 'numerical_checks_tool', 'PAY.NET', 'payslip', 'wrong',
     'within_tolerance', 'Net pay is 0.40 off gross less deductions', 'Rounded per line by the payroll system.', 'test-officer'),
    ('test:fa-2', 'test-case-b', null, 'document', 'verdict', 'numerical_checks_tool', 'PAY.NET', 'payslip', 'wrong',
     'within_tolerance', 'Net pay is 0.20 off gross less deductions', null, 'test-officer'),
    -- A false alarm in one case only: not shown.
    ('test:fa-3', 'test-case-c', null, 'document', 'verdict', 'numerical_checks_tool', 'STAT.ROUND_NUMBERS',
     'bank_statement', 'wrong', 'normal_for_document', 'Many round-number deposits', 'The company is a landlord.', 'test-officer'),
    -- A confirmation: shown from its first case.
    ('test:ok-1', 'test-case-c', null, 'document', 'verdict', 'numerical_checks_tool', 'PAY.NET', 'payslip', 'correct', null,
     'Net pay is 900.00 more than gross less deductions', null, 'test-officer'),
    -- An issue the tool missed (no check code): shown with every result of the tool.
    ('test:miss-1', 'test-case-d', null, 'document', 'missed', 'numerical_checks_tool', null, 'payslip', null, null,
     '', 'CPF was deducted on the bonus above the ceiling, and no check flagged it.', 'test-officer')
on conflict (source_key) do nothing;

-- The same for the cross-document case analysis (scope 'case'; the check is the finding's category). Only the categories
-- the model decides, behavioural_anomaly and unsupported_claim, are used.
insert into public.finding_feedback
    (source_key, case_id, document_id, scope, kind, check_name, document_type, verdict, reason, summary, note, reviewer)
values
    ('test:xd-fa-1', 'test-case-a', null, 'case', 'verdict', 'behavioural_anomaly', null, 'wrong', 'explained_elsewhere',
     'Round-number deposits look like structuring', 'The company is a landlord; rent is always a round amount.', 'test-officer'),
    ('test:xd-fa-2', 'test-case-b', null, 'case', 'verdict', 'behavioural_anomaly', null, 'wrong', 'explained_elsewhere',
     'Round-number transfers in and out', 'Rent collected and paid on to the property manager.', 'test-officer'),
    ('test:xd-ok-1', 'test-case-c', null, 'case', 'verdict', 'behavioural_anomaly', null, 'correct', null,
     'Funds sent out and straight back in the same week', null, 'test-officer'),
    ('test:xd-miss-1', 'test-case-d', null, 'case', 'missed', 'unsupported_claim', null, null, null,
     '', 'The source-of-wealth letter claims a sale the bank statements never show.', 'test-officer')
on conflict (source_key) do nothing;

-- Cleanup (run when done): deleting the cases deletes their feedback.
-- delete from public.cases where id like 'test-case-%';
