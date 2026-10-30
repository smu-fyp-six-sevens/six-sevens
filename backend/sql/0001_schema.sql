-- Snapshot of the live Supabase schema (public schema, the documents bucket), read from the catalogs on 2026-10-30.
-- It reproduces the database as it is, including its known problems (REFACTORING_AUDIT.md); fixes go in later migrations.

create extension if not exists pgcrypto;
create extension if not exists "uuid-ossp";

-- Tables ------------------------------------------------------------------------------------------------------------

create table public.cases (
    id text not null default ''::text,
    status text not null default 'open'::text,
    submission_finalized boolean not null default false,
    finalized_at timestamptz,
    recommended_additional_documents jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    company_name text,
    greenlink_case_id text,
    constraint cases_pkey primary key (id)
);

create table public.documents (
    id uuid not null default gen_random_uuid(),
    filename text not null,
    document_type text,
    storage_path text,
    status text not null default 'uploaded'::text,
    uploaded_at timestamptz not null default now(),
    retry_count integer not null default 0,
    queued_at timestamptz,
    processing_started_at timestamptz,
    failure_reason text,
    case_id text,
    classification_confidence double precision,
    routing_status text,
    evidence jsonb not null default '[]'::jsonb,
    fulfilled_requirements jsonb not null default '[]'::jsonb,
    escalation_reason text,
    metadata jsonb,
    extraction_confidence double precision,
    fields_reviewed_at timestamptz,
    guard_result jsonb,
    constraint documents_pkey primary key (id),
    constraint documents_case_id_fkey foreign key (case_id) references public.cases(id) on update cascade on delete cascade
);

create table public.extracted_fields (
    id uuid not null default gen_random_uuid(),
    document_id uuid not null,
    field_name text not null,
    field_value jsonb,
    confidence numeric(5,2),
    extracted_at timestamptz not null default now(),
    field_category text,
    field_type text,
    constraint extracted_fields_pkey primary key (id),
    constraint extracted_fields_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade
);

create table public.flags (
    id uuid not null default gen_random_uuid(),
    document_id uuid not null,
    detection_dimension text not null,
    description text not null,
    severity text not null default 'medium'::text,
    created_at timestamptz not null default now(),
    case_id text,
    related_document_ids jsonb not null default '[]'::jsonb,
    check_name text not null,
    confidence numeric(4,3),
    evidence jsonb not null default '{}'::jsonb,
    status text not null default 'open'::text,
    reviewed_by text,
    reviewed_at timestamptz,
    constraint flags_pkey primary key (id),
    constraint flags_case_id_fkey foreign key (case_id) references public.cases(id),
    constraint flags_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade
);

-- No primary key and no foreign key to documents (STABILITY_AUDIT D5).
create table public.langgraph_trace (
    document_id uuid,
    trace jsonb,
    constraint langgraph_trace_document_id_key unique (document_id)
);

-- No foreign key to cases (STABILITY_AUDIT D2).
create table public.case_analysis_runs (
    run_id text not null,
    case_id text not null,
    status text not null,
    fingerprint text not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    error text,
    result jsonb not null default '{}'::jsonb,
    trace jsonb not null default '[]'::jsonb,
    constraint case_analysis_runs_pkey primary key (run_id)
);

create table public.risk_scores (
    id uuid not null default gen_random_uuid(),
    overall_score numeric(5,2) not null,
    risk_level text not null,
    reasoning text,
    scored_at timestamptz not null default now(),
    case_id text,
    flag_ids jsonb,
    constraint risk_scores_pkey primary key (id),
    constraint risk_scores_case_id_fkey foreign key (case_id) references public.cases(id)
);

create table public.finding_reviews (
    id uuid not null default gen_random_uuid(),
    case_id text not null,
    document_id uuid,
    scope text not null,
    kind text not null,
    finding_id text,
    check_name text not null,
    check_code text,
    summary text not null default ''::text,
    verdict text,
    note text,
    reviewer text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint finding_reviews_pkey primary key (id),
    constraint finding_reviews_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade,
    constraint finding_reviews_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade,
    constraint finding_reviews_kind_check check (kind = any (array['verdict'::text, 'missed'::text])),
    constraint finding_reviews_kind_shape check (
        (kind = 'verdict'::text and finding_id is not null and verdict is not null)
        or (kind = 'missed'::text and verdict is null)
    ),
    constraint finding_reviews_scope_check check (scope = any (array['document'::text, 'case'::text])),
    constraint finding_reviews_scope_shape check ((scope = 'document'::text) = (document_id is not null)),
    constraint finding_reviews_verdict_check check (verdict = any (array['correct'::text, 'wrong'::text]))
);

-- case_id is uuid while cases.id is text, so there is no foreign key to cases.
create table public.case_notes (
    id uuid not null default gen_random_uuid(),
    case_id uuid not null,
    document_id uuid,
    parent_id uuid,
    author text not null,
    body text not null,
    created_at timestamptz not null default now(),
    edited_at timestamptz,
    deleted_at timestamptz,
    constraint case_notes_pkey primary key (id),
    constraint case_notes_parent_id_fkey foreign key (parent_id) references public.case_notes(id),
    constraint case_notes_body_check check (char_length(body) >= 1 and char_length(body) <= 4000)
);

create table public.case_status_history (
    id uuid not null,
    case_id text not null,
    from_status text,
    to_status text not null,
    changed_by text not null,
    changed_at timestamptz not null default now(),
    constraint case_status_history_pkey primary key (id),
    constraint case_status_history_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade
);

-- case_id is uuid while cases.id is text; no foreign keys, so events outlive what they name.
create table public.audit_events (
    id uuid not null default gen_random_uuid(),
    occurred_at timestamptz not null default now(),
    actor_type text not null,
    actor text not null,
    action text not null,
    case_id uuid,
    document_id uuid,
    outcome text not null default 'success'::text,
    started_at timestamptz,
    duration_ms integer,
    detail jsonb not null default '{}'::jsonb,
    constraint audit_events_pkey primary key (id),
    constraint audit_events_actor_type_check check (actor_type = any (array['user'::text, 'agent'::text, 'system'::text])),
    constraint audit_events_outcome_check check (outcome = any (array['success'::text, 'failed'::text, 'cancelled'::text]))
);

create table public.app_settings (
    key text not null,
    value jsonb not null,
    updated_by text,
    updated_at timestamptz not null default now(),
    constraint app_settings_pkey primary key (key)
);

-- Indexes -----------------------------------------------------------------------------------------------------------

create unique index cases_greenlink_case_id_key on public.cases using btree (greenlink_case_id) where (greenlink_case_id is not null);
create index idx_extracted_fields_document_id on public.extracted_fields using btree (document_id);
create index idx_flags_case_id on public.flags using btree (case_id);
create index idx_flags_document_id on public.flags using btree (document_id);
create index case_analysis_runs_case_started_idx on public.case_analysis_runs using btree (case_id, started_at desc);
create index finding_reviews_case_id on public.finding_reviews using btree (case_id);
create index finding_reviews_document_id on public.finding_reviews using btree (document_id);
create unique index finding_reviews_one_verdict on public.finding_reviews using btree (case_id, finding_id) where (kind = 'verdict'::text);
create index case_notes_case_idx on public.case_notes using btree (case_id, created_at);
create index case_status_history_case_id_idx on public.case_status_history using btree (case_id, changed_at);
create index audit_events_case_idx on public.audit_events using btree (case_id, occurred_at desc);
create index audit_events_document_idx on public.audit_events using btree (document_id, occurred_at desc);

-- Case numbers (GLDB-<n>) -------------------------------------------------------------------------------------------

-- Live: last value 152. Truncating the tables doesn't reset it.
create sequence public.greenlink_case_id_seq start with 7 increment by 1;

create or replace function public.generate_greenlink_case_id()
returns trigger
language plpgsql
as $function$
declare
    candidate text;
begin
    if new.greenlink_case_id is null
       or btrim(new.greenlink_case_id) = '' then
        loop
            candidate := 'GLDB-' || nextval('public.greenlink_case_id_seq');
            exit when not exists (
                select 1 from public.cases where greenlink_case_id = candidate
            );
        end loop;
        new.greenlink_case_id := candidate;
    end if;

    return new;
end;
$function$;

create trigger set_greenlink_case_id before insert on public.cases
    for each row execute function public.generate_greenlink_case_id();

-- Views -------------------------------------------------------------------------------------------------------------

-- No security_invoker: it runs as its owner, so it ignores the RLS of risk_scores.
create view public.latest_risk_scores as
select distinct on (case_id) id, case_id, overall_score, risk_level, reasoning, scored_at, flag_ids
from public.risk_scores
where case_id is not null
order by case_id, scored_at desc;

-- Row-level security ------------------------------------------------------------------------------------------------

alter table public.cases enable row level security;
alter table public.documents enable row level security;
alter table public.extracted_fields enable row level security;
alter table public.flags enable row level security;
alter table public.langgraph_trace enable row level security;
alter table public.case_analysis_runs enable row level security;
alter table public.risk_scores enable row level security;
alter table public.finding_reviews enable row level security;
alter table public.case_notes enable row level security;
alter table public.case_status_history enable row level security;
alter table public.audit_events enable row level security;
alter table public.app_settings enable row level security;

-- finding_reviews, case_notes, audit_events and app_settings have no policies: only service_role reaches them.
create policy "backend can insert cases" on public.cases for insert to anon with check (true);
create policy "backend can read cases" on public.cases for select to anon using (true);
create policy "backend can update case status" on public.cases for update to public using (true) with check (true);

create policy "anon can insert documents" on public.documents for insert to anon with check (true);
create policy "anon can read documents" on public.documents for select to anon using (true);
create policy "anon can update documents" on public.documents for update to anon using (true) with check (true);

create policy "backend can delete extracted fields" on public.extracted_fields for delete to anon using (true);
create policy "backend can insert extracted fields" on public.extracted_fields for insert to anon with check (true);
create policy "backend can read extracted fields" on public.extracted_fields for select to anon using (true);

create policy "backend can insert flags" on public.flags for insert to anon with check (true);
create policy "backend can read flags" on public.flags for select to anon using (true);

create policy trace_all on public.langgraph_trace for all to anon, authenticated using (true) with check (true);

create policy "case_analysis_runs insert" on public.case_analysis_runs for insert to anon, authenticated with check (true);
create policy "case_analysis_runs read" on public.case_analysis_runs for select to anon, authenticated using (true);
create policy "case_analysis_runs update" on public.case_analysis_runs for update to anon, authenticated using (true) with check (true);

create policy "backend can insert risk scores" on public.risk_scores for insert to anon with check (true);
create policy "backend can read risk scores" on public.risk_scores for select to anon using (true);

create policy "add history" on public.case_status_history for insert to anon, authenticated with check (true);
create policy "read history" on public.case_status_history for select to anon, authenticated using (true);

-- Grants ------------------------------------------------------------------------------------------------------------

-- As they are live. Note the gaps: service_role has no SELECT on case_status_history, no UPDATE on case_notes,
-- and no INSERT or UPDATE on case_analysis_runs; anon holds TRUNCATE on most tables.
do $$
declare t text;
begin
    foreach t in array array[
        'cases', 'documents', 'extracted_fields', 'flags', 'langgraph_trace', 'case_analysis_runs', 'risk_scores',
        'finding_reviews', 'case_notes', 'case_status_history', 'audit_events', 'app_settings', 'latest_risk_scores'
    ] loop
        execute format('revoke all on public.%I from anon, authenticated, service_role', t);
    end loop;
end $$;

grant select, insert, update, delete, truncate, references, trigger, maintain
    on public.cases, public.documents, public.extracted_fields, public.flags, public.langgraph_trace, public.risk_scores
    to anon, authenticated, service_role;
grant select, insert, update, truncate, references, trigger, maintain on public.case_analysis_runs to anon, authenticated;
grant select, delete, truncate, references, trigger, maintain on public.case_analysis_runs to service_role;
grant truncate, references, trigger, maintain on public.finding_reviews to anon, authenticated;
grant select, insert, update, delete, truncate, references, trigger, maintain on public.finding_reviews to service_role;
grant select, insert, delete on public.case_notes to service_role;
grant select, insert, truncate, references, trigger, maintain on public.case_status_history to anon, authenticated;
grant delete, truncate, references, trigger, maintain on public.case_status_history to service_role;
grant select, insert, truncate, references, trigger, maintain on public.audit_events to service_role;
grant truncate, references, trigger, maintain on public.app_settings to anon, authenticated;
grant select, insert, update, truncate, references, trigger, maintain on public.app_settings to service_role;
grant select, truncate, references, trigger, maintain on public.latest_risk_scores to anon, authenticated;
grant truncate, references, trigger, maintain on public.latest_risk_scores to service_role;
grant select, usage on sequence public.greenlink_case_id_seq to anon, authenticated, service_role;

-- Realtime ----------------------------------------------------------------------------------------------------------

alter publication supabase_realtime add table public.documents, public.cases, public.case_analysis_runs;

-- Storage -----------------------------------------------------------------------------------------------------------

insert into storage.buckets (id, name, public) values ('documents', 'documents', false) on conflict (id) do nothing;

create policy "anon can read documents bucket" on storage.objects for select to anon using (bucket_id = 'documents'::text);
create policy "anon can upload to documents bucket" on storage.objects for insert to anon with check (bucket_id = 'documents'::text);
