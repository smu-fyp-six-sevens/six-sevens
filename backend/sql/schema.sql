-- The live Supabase schema (public, plus the storage bucket and its policies), as read on 2026-11-01, plus the case
-- list columns, view and indexes of the same day.
-- A snapshot, not a migration: it rebuilds an empty database (staging, a fresh project). Keep it in step by hand
-- when the schema changes.

-- Sequences and functions -------------------------------------------------------------------------------------------

create sequence public.greenlink_case_id_seq start 7 increment 1;

create function public.generate_greenlink_case_id()
returns trigger
language plpgsql
as $$
declare
    candidate text;
begin
    if new.greenlink_case_id is null or btrim(new.greenlink_case_id) = '' then
        loop
            candidate := 'GLDB-' || nextval('public.greenlink_case_id_seq');
            exit when not exists (select 1 from public.cases where greenlink_case_id = candidate);
        end loop;
        new.greenlink_case_id := candidate;
    end if;
    return new;
end;
$$;

-- Tables ------------------------------------------------------------------------------------------------------------

-- status is the case's stage (app/case_stage.py). checklist_enforced, reviews_required and created_by are set once, at
-- creation. Kept for the case list: risk_rank (0 unscored, 1 Low to 4 Critical) from each run's saved score, and
-- processing_ms, which audit-metrics rewrites after each timed run.
create table public.cases (
    id text not null default ''::text,
    status text not null default 'open'::text,
    recommended_additional_documents jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    company_name text,
    greenlink_case_id text,
    checklist_enforced boolean not null default false,
    reviews_required boolean not null default true,
    created_by text,
    processing_ms integer,
    risk_rank smallint not null default 0,
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

-- Each document's latest detection report, or why its last run failed (app/detection_results.py).
create table public.detection_results (
    document_id uuid not null,
    report jsonb,
    error text,
    updated_at timestamptz not null default now(),
    constraint detection_results_pkey primary key (document_id),
    constraint detection_results_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade
);

-- Every finding, what the agents decided and what an officer said about it (app/findings_repository.py).
--   source 'detection':     a detection tool's finding on one document (scope 'document'), kept or dismissed by its
--                           specialist (agent_decision).
--   source 'case_analysis': a cross-document finding of the case's newest complete analysis (scope 'case'). Linked to
--                           the case only; the documents it rests on are in evidence.
--   source 'officer':       an issue an officer found that no finding raised (a missed issue, for recall).
-- A finished run replaces the previous run's findings outright (sync_findings), with their verdicts. fingerprint
-- names a finding within a run: the report and the risk carry it too.
-- agent_decision: accepted (assessed and kept), not_assessed (kept, never assessed), overridden (dismissed, but a
-- severity floor or an impossible value kept it), dismissed (dropped as a false positive).
create table public.findings (
    id uuid not null default gen_random_uuid(),
    case_id text not null,
    document_id uuid,
    scope text not null,
    source text not null,
    fingerprint text,
    -- The finding's id in the report or run that raised it last ("font_consistency-2", "xd-h1").
    report_finding_id text,
    -- The tool for a document finding, the category for a case finding or a case-level missed issue.
    check_name text not null,
    check_code text,
    category text,
    severity text,
    summary text not null default ''::text,
    -- The specialist's (or the judge's) reasoning; for a dismissed finding, why it was dismissed.
    rationale text,
    confidence numeric(4,3),
    evidence jsonb not null default '{}'::jsonb,
    agent_decision text,
    verdict text,
    reviewer text,
    review_note text,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint findings_pkey primary key (id),
    constraint findings_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade,
    constraint findings_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade,
    constraint findings_scope_check check (scope = any (array['document'::text, 'case'::text])),
    constraint findings_source_check check (source = any (array['detection'::text, 'case_analysis'::text, 'officer'::text])),
    constraint findings_agent_decision_check check (
        agent_decision = any (array['accepted'::text, 'not_assessed'::text, 'overridden'::text, 'dismissed'::text])
    ),
    constraint findings_verdict_check check (verdict = any (array['correct'::text, 'wrong'::text])),
    constraint findings_scope_shape check ((scope = 'document'::text) = (document_id is not null)),
    constraint findings_source_shape check (
        (source = 'detection'::text and scope = 'document'::text and fingerprint is not null and agent_decision is not null)
        or (source = 'case_analysis'::text and scope = 'case'::text and fingerprint is not null and agent_decision is null)
        or (source = 'officer'::text and fingerprint is null and agent_decision is null and verdict is null
            and reviewer is not null)
    ),
    constraint findings_review_shape check (verdict is null or (reviewer is not null and reviewed_at is not null))
);

-- An officer's saved review of a group with no findings to mark: a document, or the case's cross-document group.
-- group_key is the document id, or 'case'.
create table public.review_sign_offs (
    case_id text not null,
    group_key text not null,
    scope text not null,
    document_id uuid,
    reviewer text not null,
    reviewed_at timestamptz not null default now(),
    constraint review_sign_offs_pkey primary key (case_id, group_key),
    constraint review_sign_offs_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade,
    constraint review_sign_offs_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade,
    constraint review_sign_offs_scope_check check (scope = any (array['document'::text, 'case'::text])),
    constraint review_sign_offs_scope_shape check ((scope = 'document'::text) = (document_id is not null))
);

-- Debug trace only, one row per document.
create table public.langgraph_trace (
    document_id uuid not null,
    trace jsonb,
    constraint langgraph_trace_pkey primary key (document_id),
    constraint langgraph_trace_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade
);

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
    constraint case_analysis_runs_pkey primary key (run_id),
    constraint case_analysis_runs_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade
);

create table public.risk_scores (
    id uuid not null default gen_random_uuid(),
    overall_score numeric(5,2) not null,
    risk_level text not null,
    reasoning text,
    scored_at timestamptz not null default now(),
    case_id text,
    constraint risk_scores_pkey primary key (id),
    constraint risk_scores_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade
);

create table public.case_notes (
    id uuid not null default gen_random_uuid(),
    case_id text not null,
    document_id uuid,
    parent_id uuid,
    author text not null,
    body text not null,
    created_at timestamptz not null default now(),
    edited_at timestamptz,
    deleted_at timestamptz,
    constraint case_notes_pkey primary key (id),
    constraint case_notes_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade,
    constraint case_notes_document_id_fkey foreign key (document_id) references public.documents(id) on delete cascade,
    constraint case_notes_parent_id_fkey foreign key (parent_id) references public.case_notes(id) on delete cascade,
    constraint case_notes_body_check check (char_length(body) >= 1 and char_length(body) <= 4000)
);

-- Written only by app/audit.py, and never deleted: no foreign keys, so it outlives what it names (the cost of a
-- deleted document stays on record). case_id is uuid here although cases.id is text.
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

-- Indexes -----------------------------------------------------------------------------------------------------------

create unique index cases_greenlink_case_id_key on public.cases (greenlink_case_id) where greenlink_case_id is not null;
create index documents_case_id_idx on public.documents (case_id);
create index idx_extracted_fields_document_id on public.extracted_fields (document_id);
create unique index findings_document_fingerprint on public.findings (document_id, fingerprint) where source = 'detection'::text;
create unique index findings_case_fingerprint on public.findings (case_id, fingerprint) where source = 'case_analysis'::text;
create index findings_case_id on public.findings (case_id);
create index findings_document_id on public.findings (document_id);
create index case_analysis_runs_case_started_idx on public.case_analysis_runs (case_id, started_at desc);
create index case_notes_case_idx on public.case_notes (case_id, created_at);
create index audit_events_case_idx on public.audit_events (case_id, occurred_at desc);
create index audit_events_document_idx on public.audit_events (document_id, occurred_at desc);
create index cases_created_idx on public.cases (created_at desc, id);
create index cases_status_idx on public.cases (status);
create index documents_status_idx on public.documents (status);
create index case_analysis_runs_running_idx on public.case_analysis_runs (started_at) where status = 'running'::text;
create index risk_scores_case_scored_idx on public.risk_scores (case_id, scored_at desc);
create index case_notes_document_idx on public.case_notes (document_id);
create index case_notes_parent_idx on public.case_notes (parent_id);
create index review_sign_offs_document_idx on public.review_sign_offs (document_id);
create index cases_risk_idx on public.cases (risk_rank, created_at desc);
create index findings_reviewed_idx on public.findings (case_id) where reviewer is not null;

-- Trigger -----------------------------------------------------------------------------------------------------------

create trigger set_greenlink_case_id before insert on public.cases
    for each row execute function public.generate_greenlink_case_id();

-- View --------------------------------------------------------------------------------------------------------------

-- No security_invoker: it runs as its owner, so it ignores the RLS of risk_scores.
create view public.latest_risk_scores as
select distinct on (case_id) id, case_id, overall_score, risk_level, reasoning, scored_at
from public.risk_scores
where case_id is not null
order by case_id, scored_at desc;

-- The case queue's stage counts, counted here rather than by reading every case.
create view public.case_stage_counts as
select status, count(*)::integer as cases
from public.cases
group by status;

-- Write functions (backend only) ------------------------------------------------------------------------------------

-- Replaces one document's detection findings (p_document_id set) or one case's analysis findings (p_document_id
-- null) with p_rows, in one transaction: the previous run's findings go, with the verdicts on them; the missed
-- issues officers reported stay. Returns how many findings p_rows held.
create function public.sync_findings(p_case_id text, p_document_id uuid, p_rows jsonb)
returns integer
language plpgsql
set search_path = ''
as $$
begin
    -- Two runs finishing together for the same document or case write one after the other.
    perform pg_advisory_xact_lock(hashtext('findings:' || coalesce(p_document_id::text, p_case_id)));

    if p_document_id is null then
        delete from public.findings where case_id = p_case_id and source = 'case_analysis';
    else
        delete from public.findings where document_id = p_document_id and source = 'detection';
    end if;

    insert into public.findings (
        case_id, document_id, scope, source, fingerprint, report_finding_id, check_name, check_code, category,
        severity, summary, rationale, confidence, evidence, agent_decision
    )
    select
        p_case_id, p_document_id, case when p_document_id is null then 'case' else 'document' end,
        case when p_document_id is null then 'case_analysis' else 'detection' end,
        r.fingerprint, r.report_finding_id, r.check_name, r.check_code, r.category, r.severity,
        coalesce(r.summary, ''), r.rationale, r.confidence, coalesce(r.evidence, '{}'::jsonb), r.agent_decision
    from jsonb_to_recordset(p_rows) as r(
        fingerprint text, report_finding_id text, check_name text, check_code text, category text,
        severity text, summary text, rationale text, confidence numeric, evidence jsonb, agent_decision text
    );

    return jsonb_array_length(p_rows);
end;
$$;

-- A document's field set replaced in one transaction, so a failure between the delete and the insert can't leave the
-- document with no fields (extracted_fields_repository.replace_extracted_fields).
create function public.replace_extracted_fields(p_document_id uuid, p_rows jsonb)
returns void
language plpgsql
set search_path = ''
as $$
begin
    delete from public.extracted_fields where document_id = p_document_id;
    insert into public.extracted_fields (document_id, field_name, field_value, confidence, field_category, field_type)
    select p_document_id, r.field_name, r.field_value, r.confidence, r.field_category, r.field_type
    from jsonb_to_recordset(p_rows) as r(
        field_name text, field_value jsonb, confidence numeric, field_category text, field_type text
    );
end;
$$;

revoke all on function public.sync_findings(text, uuid, jsonb) from public, anon, authenticated;
grant execute on function public.sync_findings(text, uuid, jsonb) to service_role;
revoke all on function public.replace_extracted_fields(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.replace_extracted_fields(uuid, jsonb) to service_role;

-- Row-level security ------------------------------------------------------------------------------------------------

alter table public.cases enable row level security;
alter table public.documents enable row level security;
alter table public.extracted_fields enable row level security;
alter table public.detection_results enable row level security;
alter table public.findings enable row level security;
alter table public.review_sign_offs enable row level security;
grant select, insert, update, delete on public.review_sign_offs to service_role;
alter table public.langgraph_trace enable row level security;
alter table public.case_analysis_runs enable row level security;
alter table public.risk_scores enable row level security;
alter table public.case_notes enable row level security;
alter table public.audit_events enable row level security;

-- detection_results, findings, case_notes and audit_events have no policies: only service_role reaches them.
create policy "backend can insert cases" on public.cases for insert to anon with check (true);
create policy "backend can read cases" on public.cases for select to anon using (true);
create policy "backend can update case status" on public.cases for update to public using (true) with check (true);

create policy "anon can insert documents" on public.documents for insert to anon with check (true);
create policy "anon can read documents" on public.documents for select to anon using (true);
create policy "anon can update documents" on public.documents for update to anon using (true) with check (true);

create policy "backend can delete extracted fields" on public.extracted_fields for delete to anon using (true);
create policy "backend can insert extracted fields" on public.extracted_fields for insert to anon with check (true);
create policy "backend can read extracted fields" on public.extracted_fields for select to anon using (true);

create policy trace_all on public.langgraph_trace for all to anon, authenticated using (true) with check (true);

create policy "case_analysis_runs insert" on public.case_analysis_runs for insert to anon, authenticated with check (true);
create policy "case_analysis_runs read" on public.case_analysis_runs for select to anon, authenticated using (true);
create policy "case_analysis_runs update" on public.case_analysis_runs for update to anon, authenticated using (true) with check (true);

create policy "backend can insert risk scores" on public.risk_scores for insert to anon with check (true);
create policy "backend can read risk scores" on public.risk_scores for select to anon using (true);

-- Grants ------------------------------------------------------------------------------------------------------------

-- As they are live. Note: anon holds full CRUD and TRUNCATE on most tables (the open security item), and
-- service_role has no INSERT or UPDATE on case_analysis_runs (the backend writes runs with the publishable key).
do $$
declare t text;
begin
    foreach t in array array[
        'cases', 'documents', 'extracted_fields', 'detection_results', 'findings', 'langgraph_trace',
        'case_analysis_runs', 'risk_scores', 'case_notes', 'audit_events', 'latest_risk_scores', 'case_stage_counts'
    ] loop
        execute format('revoke all on public.%I from anon, authenticated, service_role', t);
    end loop;
end $$;

grant select, insert, update, delete, truncate, references, trigger
    on public.cases, public.documents, public.extracted_fields, public.langgraph_trace, public.risk_scores
    to anon, authenticated, service_role;
grant select, insert, update, delete, truncate, references, trigger
    on public.detection_results, public.findings to service_role;
grant select, insert, update, truncate, references, trigger on public.case_analysis_runs to anon, authenticated;
grant select, delete, truncate, references, trigger on public.case_analysis_runs to service_role;
grant select, insert, update, delete on public.case_notes to service_role;
grant select, insert, truncate, references, trigger on public.audit_events to service_role;
grant select, truncate, references, trigger on public.latest_risk_scores to anon, authenticated;
grant truncate, references, trigger on public.latest_risk_scores to service_role;
grant select on public.case_stage_counts to anon, authenticated;
grant select, usage on sequence public.greenlink_case_id_seq to anon, authenticated, service_role;

-- Realtime ----------------------------------------------------------------------------------------------------------

alter publication supabase_realtime add table public.documents, public.cases, public.case_analysis_runs;

-- Storage -----------------------------------------------------------------------------------------------------------

insert into storage.buckets (id, name, public) values ('documents', 'documents', false) on conflict (id) do nothing;

create policy "anon can read documents bucket" on storage.objects for select to anon using (bucket_id = 'documents'::text);
create policy "anon can upload to documents bucket" on storage.objects for insert to anon with check (bucket_id = 'documents'::text);
