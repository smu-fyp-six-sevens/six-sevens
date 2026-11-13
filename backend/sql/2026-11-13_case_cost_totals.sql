-- Each case's Claude spend at list price, added up in the database instead of in Python (audit.cost_usd_many).
-- Run once in the Supabase SQL editor BEFORE deploying the code that calls it. Safe to re-run.
-- p_actions are the end-of-run event types that carry a run's cost (audit.py _COSTED).
create or replace function public.case_cost_totals(p_case_ids uuid[], p_actions text[])
returns table (case_id uuid, cost_usd numeric)
language sql
stable
set search_path = ''
as $$
    select e.case_id, sum((e.detail->>'llm_cost_usd')::numeric)
    from public.audit_events e
    where e.case_id = any(p_case_ids) and e.action = any(p_actions)
        and jsonb_typeof(e.detail->'llm_cost_usd') = 'number'
    group by e.case_id;
$$;

revoke all on function public.case_cost_totals(uuid[], text[]) from public, anon, authenticated;
grant execute on function public.case_cost_totals(uuid[], text[]) to service_role;
