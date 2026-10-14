-- The detection run stores each accepted finding as a row in public.flags
-- (see app/flags_repository.py). Tables made from the SQL editor grant the API
-- roles nothing, and the backend uses the anon role. The row level security
-- policies (backend insert and read) already exist; deleting a document's old
-- flags on a retry goes through the service client, so no delete policy is needed.
grant select, insert on public.flags to anon, authenticated;

-- A case's flags are read together.
create index if not exists idx_flags_case_id on public.flags using btree (case_id);
