-- One row per per-case analysis run (cross-document findings, risk outlook, summary).
-- See CROSS_DOCUMENT_AND_RISK_PLAN.md. The newest row for a case is its current run.

create table if not exists public.case_analysis_runs (
    run_id        text primary key,
    case_id       text not null,
    status        text not null,
    fingerprint   text not null,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    error         text,
    result        jsonb not null default '{}'::jsonb,
    -- Step-by-step log of the run, written as it goes (debugging aid, see case_pipeline/trace.py).
    trace         jsonb not null default '[]'::jsonb
);

create index if not exists case_analysis_runs_case_started_idx
    on public.case_analysis_runs (case_id, started_at desc);

-- Tables made from the SQL editor grant the API roles nothing. The backend reads and writes
-- through the publishable key (the anon role), like the other tables.
grant select, insert, update on public.case_analysis_runs to anon, authenticated;

-- For a table created before the trace column was added to this file.
alter table public.case_analysis_runs
    add column if not exists trace jsonb not null default '[]'::jsonb;
