-- Schema changes of the integration branch (2026-10-31), after 0001_schema.sql.
-- Part 1 was run before the branch's code was deployed; part 2 drops what that code no longer uses, so the code
-- running against the database must already be this branch's.

-- Part 1 ------------------------------------------------------------------------------------------------------------

-- Every case read filters documents by case.
create index documents_case_id_idx on public.documents (case_id);

-- Set once, when a case is created: whether every required document must be approved before analysis (from the
-- environment's REQUIRE_COMPLETE_CHECKLIST), and whether officers review its findings before finalising.
alter table public.cases add column checklist_enforced boolean not null default false;
alter table public.cases add column reviews_required boolean not null default true;

-- Each document's latest detection report, or why its last run failed (detection_results.py). Backend only.
create table public.detection_results (
    document_id uuid primary key references public.documents(id) on delete cascade,
    report jsonb,
    error text,
    updated_at timestamptz not null default now()
);
alter table public.detection_results enable row level security;
revoke all on public.detection_results from anon, authenticated;
grant select, insert, update, delete on public.detection_results to service_role;

-- Part 2 ------------------------------------------------------------------------------------------------------------

-- A flag nothing read; the stage now covers it (case_stage.py).
alter table public.cases drop column submission_finalized, drop column finalized_at;
-- Stage changes are no longer recorded; the audit trail records who finalised a case.
drop table public.case_status_history;
-- The admin switch for finding reviews, now chosen per case (cases.reviews_required).
drop table public.app_settings;
