-- The audit trail (app/audit.py). Run once in the Supabase SQL editor.
-- Append-only: no foreign keys (events outlive the case and document they name),
-- and the backend's service role may only INSERT and SELECT.

create table if not exists public.audit_events (
  id           uuid primary key default gen_random_uuid(),
  occurred_at  timestamptz not null default now(),   -- when the action finished
  actor_type   text not null check (actor_type in ('user', 'agent', 'system')),
  actor        text not null,                        -- 'bob' | 'detection.lead' | 'queue'
  action       text not null,                        -- 'document.approved'
  case_id      uuid,
  document_id  uuid,
  outcome      text not null default 'success' check (outcome in ('success', 'failed', 'cancelled')),
  started_at   timestamptz,                          -- set for actions that take time
  duration_ms  integer,
  detail       jsonb not null default '{}'
);

create index if not exists audit_events_case_idx     on public.audit_events (case_id, occurred_at desc);
create index if not exists audit_events_document_idx on public.audit_events (document_id, occurred_at desc);

-- RLS on with no policy: the publishable (anon) key sees nothing; the service role bypasses RLS.
alter table public.audit_events enable row level security;
revoke all on public.audit_events from anon, authenticated;
grant select, insert on public.audit_events to service_role;
