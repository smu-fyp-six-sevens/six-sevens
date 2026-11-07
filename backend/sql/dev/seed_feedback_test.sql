-- Test feedback for trying out agent learning without calling a model. Run in the Supabase SQL editor AFTER
-- backend/sql/schema.sql's finding_feedback table exists. Every row's source_key starts with 'test:', so the cleanup at
-- the bottom removes exactly these and nothing an officer saved. case_id has no foreign key, so these need no real case.
--
-- Then: python scripts/preview_feedback.py --check metadata_tool --case-id <any other case id>

insert into public.finding_feedback
    (source_key, case_id, document_id, scope, kind, check_name, document_type, verdict, reason, summary, note, reviewer)
values
    -- A false alarm with a one-click reason.
    ('test:fa-1', 'test-case-a', null, 'document', 'verdict', 'metadata_tool', 'payslip', 'wrong', 'tool_misread',
     'Creator tool is an image editor', 'The scanner software writes this on every payslip we scan in-house.', 'test-officer'),
    -- A false alarm with the "Other" reason and its note.
    ('test:fa-2', 'test-case-b', null, 'document', 'verdict', 'metadata_tool', 'payslip', 'wrong', 'other',
     'Modification date differs from creation date', 'Employer re-exports payslips from the payroll system, so the dates always differ.', 'test-officer'),
    -- A confirmation: the finding was real.
    ('test:ok-1', 'test-case-c', null, 'document', 'verdict', 'metadata_tool', 'bank_statement', 'correct', null,
     'Edited in a PDF editor after issue', null, 'test-officer'),
    -- An issue the check missed.
    ('test:miss-1', 'test-case-d', null, 'document', 'missed', 'metadata_tool', 'payslip', null, null,
     '', 'Producer string was stripped, so the check found nothing.', 'test-officer')
on conflict (source_key) do nothing;

-- The same for the cross-document case analysis (scope 'case'; the check is the finding's category). Only the categories the
-- model decides, behavioural_anomaly and unsupported_claim, are used.
insert into public.finding_feedback
    (source_key, case_id, document_id, scope, kind, check_name, document_type, verdict, reason, summary, note, reviewer)
values
    ('test:xd-fa-1', 'test-case-a', null, 'case', 'verdict', 'behavioural_anomaly', null, 'wrong', 'explained_elsewhere',
     'Round-number deposits look like structuring', 'The company is a landlord; rent is always a round amount.', 'test-officer'),
    ('test:xd-ok-1', 'test-case-c', null, 'case', 'verdict', 'behavioural_anomaly', null, 'correct', null,
     'Funds sent out and straight back in the same week', null, 'test-officer'),
    ('test:xd-fa-2', 'test-case-b', null, 'case', 'verdict', 'unsupported_claim', null, 'wrong', 'other',
     'Stated turnover is higher than the statements show', 'Turnover includes a subsidiary that is not in this application.', 'test-officer'),
    ('test:xd-miss-1', 'test-case-d', null, 'case', 'missed', 'unsupported_claim', null, null, null,
     '', 'The source-of-wealth letter claims a sale the bank statements never show.', 'test-officer')
on conflict (source_key) do nothing;

-- Cleanup (run when done):
-- delete from public.finding_feedback where source_key like 'test:%';
