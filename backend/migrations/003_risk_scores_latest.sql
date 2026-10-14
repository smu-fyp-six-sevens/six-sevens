-- The case risk score is saved to the existing public.risk_scores table
-- (see app/case_pipeline/risk/store.py). This lets the backend read it and gives it
-- a "latest per case" view so the case list needn't recompute every case.

-- Tables made from the SQL editor grant the API roles nothing; the backend uses the anon role.
grant select, insert on public.risk_scores to anon, authenticated;

create or replace view public.latest_risk_scores as
select distinct on (case_id)
    id, case_id, overall_score, risk_level, reasoning, scored_at, flag_ids
from public.risk_scores
where case_id is not null
order by case_id, scored_at desc;

grant select on public.latest_risk_scores to anon, authenticated;

-- Only if row level security is switched on for risk_scores:
-- alter table public.risk_scores enable row level security;
-- create policy "risk_scores read"   on public.risk_scores for select to anon, authenticated using (true);
-- create policy "risk_scores insert" on public.risk_scores for insert to anon, authenticated with check (true);
