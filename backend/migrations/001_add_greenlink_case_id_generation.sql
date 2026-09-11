-- Generate unique GreenLink Case IDs such as GLDB-7, GLDB-8, etc.
-- Existing manually-created IDs are preserved.

create sequence if not exists public.greenlink_case_id_seq
    start with 7
    increment by 1;


-- Keep the sequence ahead of any existing numeric GLDB IDs.
select setval(
    'public.greenlink_case_id_seq',
    greatest(
        6,
        coalesce(
            (
                select max(
                    substring(greenlink_case_id from '^GLDB-([0-9]+)$')::bigint
                )
                from public.cases
                where greenlink_case_id ~ '^GLDB-[0-9]+$'
            ),
            0
        )
    ),
    true
);


-- Generate a GreenLink Case ID automatically when one is not provided.
create or replace function public.generate_greenlink_case_id()
returns trigger
language plpgsql
as $$
begin
    if new.greenlink_case_id is null
       or btrim(new.greenlink_case_id) = '' then
        new.greenlink_case_id :=
            'GLDB-' || nextval('public.greenlink_case_id_seq');
    end if;

    return new;
end;
$$;


-- Remove any existing trigger before recreating it.
drop trigger if exists set_greenlink_case_id on public.cases;


-- Generate the ID before inserting a new case.
create trigger set_greenlink_case_id
before insert on public.cases
for each row
execute function public.generate_greenlink_case_id();


-- Allow the Supabase API roles to use the sequence.
grant usage, select on sequence public.greenlink_case_id_seq
to anon, authenticated, service_role;


-- Allow the Supabase API roles to execute the trigger function.
grant execute on function public.generate_greenlink_case_id()
to anon, authenticated, service_role;


-- Enforce uniqueness for non-null GreenLink Case IDs.
create unique index if not exists cases_greenlink_case_id_key
on public.cases (greenlink_case_id)
where greenlink_case_id is not null;