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
    result        jsonb not null default '{}'::jsonb
);

create index if not exists case_analysis_runs_case_started_idx
    on public.case_analysis_runs (case_id, started_at desc);
