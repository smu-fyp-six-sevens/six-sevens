-- Agent learning, before stateful-learning merges (AGENT_LEARNING_PLAN.md 1.5, 1.6, 1.8, 2, 3). Run once in the
-- Supabase SQL editor. finding_feedback and findings.review_reason are already live (read 2026-11-10); this adds what
-- schema.sql has since gained. Safe to re-run.

begin;

-- 1.5: feedback goes with its case; a deleted document leaves its case's lessons in place. Rows used to outlive their
-- case on purpose, so clear the orphans first or the constraints won't add.
delete from public.finding_feedback f where not exists (select 1 from public.cases c where c.id = f.case_id);
update public.finding_feedback f set document_id = null
where document_id is not null and not exists (select 1 from public.documents d where d.id = f.document_id);
alter table public.finding_feedback drop constraint if exists finding_feedback_case_id_fkey;
alter table public.finding_feedback
    add constraint finding_feedback_case_id_fkey foreign key (case_id) references public.cases(id) on delete cascade;
alter table public.finding_feedback drop constraint if exists finding_feedback_document_id_fkey;
alter table public.finding_feedback
    add constraint finding_feedback_document_id_fkey foreign key (document_id) references public.documents(id) on delete set null;

-- 1.6: the lookup by check, and the two foreign keys.
create index if not exists finding_feedback_check_idx on public.finding_feedback (check_name, scope, updated_at desc);
create index if not exists finding_feedback_case_idx on public.finding_feedback (case_id);
create index if not exists finding_feedback_document_idx on public.finding_feedback (document_id);

-- 1.6: the counting, in the database (feedback_repository.counts).
create or replace function public.feedback_counts(p_scope text, p_check_names text[], p_exclude_case_id text)
returns table (
    check_name text, check_code text, lesson text, cases integer, reasons jsonb, notes text[], summaries text[],
    ids uuid[], latest timestamptz
)
language sql
stable
set search_path = ''
as $$
    with lessons as (
        select f.id, f.case_id, f.check_name, f.check_code, f.reason, f.note, f.summary, f.updated_at,
            case
                when f.kind = 'missed' then 'missed'
                when f.verdict = 'wrong' then 'false_alarm'
                when f.reason is not null then 'set_aside_wrongly'
                else 'confirmed'
            end as lesson
        from public.finding_feedback f
        where f.scope = p_scope and f.check_name = any(p_check_names) and f.case_id <> p_exclude_case_id
    ),
    by_reason as (
        select l.check_name, l.check_code, l.lesson, l.reason, count(distinct l.case_id)::integer as cases
        from lessons l
        where l.reason is not null
        group by l.check_name, l.check_code, l.lesson, l.reason
    )
    select
        l.check_name, l.check_code, l.lesson, count(distinct l.case_id)::integer,
        coalesce((
            select jsonb_object_agg(r.reason, r.cases) from by_reason r
            where r.check_name = l.check_name and r.check_code is not distinct from l.check_code and r.lesson = l.lesson
        ), '{}'::jsonb),
        coalesce((array_agg(l.note order by l.updated_at desc) filter (where coalesce(l.note, '') <> ''))[1:5], '{}'),
        coalesce((array_agg(l.summary order by l.updated_at desc) filter (where l.summary <> ''))[1:5], '{}'),
        (array_agg(l.id order by l.updated_at desc))[1:10],
        max(l.updated_at)
    from lessons l
    group by l.check_name, l.check_code, l.lesson
    order by max(l.updated_at) desc;
$$;

revoke all on function public.feedback_counts(text, text[], text) from public, anon, authenticated;
grant execute on function public.feedback_counts(text, text[], text) to service_role;
grant select, insert, update, delete on public.finding_feedback to service_role;


-- Sections 2 and 3: what the classifier and the extractor learn.
-- What the classifier learns (corrections_repository.type_corrections): each pair of types officers changed a
-- document from and to since p_since, with the cases it happened in. Labels only; a type confirmed unchanged is not a
-- correction, and neither is one to or from unrecognised.
create or replace function public.type_corrections(p_since timestamptz)
returns table (from_type text, to_type text, cases integer)
language sql
stable
set search_path = ''
as $$
    select e.detail->>'from_type', e.detail->>'to_type', count(distinct e.case_id)::integer
    from public.audit_events e
    where e.action = 'document.retyped' and e.actor_type = 'user' and e.outcome = 'success'
        and e.occurred_at >= p_since
        and coalesce(e.detail->>'from_type', '') not in ('', 'unrecognised')
        and coalesce(e.detail->>'to_type', '') not in ('', 'unrecognised')
        and e.detail->>'from_type' <> e.detail->>'to_type'
    group by 1, 2;
$$;

-- What the extractor learns (corrections_repository.field_edits): per document type, the fields officers added (a
-- change from nothing) and deleted (a change to nothing) since p_since, with the cases. Field names only, never values.
-- Events from before document.field_edited carried document_type can't be grouped, so they are left out.
create or replace function public.field_edit_counts(p_since timestamptz, p_document_type text)
returns table (document_type text, field_name text, edit text, cases integer)
language sql
stable
set search_path = ''
as $$
    select
        e.detail->>'document_type', c->>'field',
        case when coalesce(c->'from', 'null'::jsonb) = 'null'::jsonb then 'added' else 'deleted' end,
        count(distinct e.case_id)::integer
    from public.audit_events e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.detail->'changes') = 'array' then e.detail->'changes' else '[]'::jsonb end
    ) c
    where e.action = 'document.field_edited' and e.actor_type = 'user' and e.occurred_at >= p_since
        and e.detail->>'document_type' = p_document_type
        and coalesce(c->>'field', '') <> ''
        -- Exactly one side absent: an added or a deleted field, not an edited value.
        and (coalesce(c->'from', 'null'::jsonb) = 'null'::jsonb) <> (coalesce(c->'to', 'null'::jsonb) = 'null'::jsonb)
    group by 1, 2, 3;
$$;

revoke all on function public.type_corrections(timestamptz) from public, anon, authenticated;
grant execute on function public.type_corrections(timestamptz) to service_role;
revoke all on function public.field_edit_counts(timestamptz, text) from public, anon, authenticated;
grant execute on function public.field_edit_counts(timestamptz, text) to service_role;

commit;
