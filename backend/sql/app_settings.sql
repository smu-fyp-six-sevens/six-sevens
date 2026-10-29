-- App-wide settings an admin changes on the Settings page (app/app_settings_repository.py), one row per key.
-- finding_reviews_enabled: whether loan officers review detection findings (and see precision and recall).
create table if not exists public.app_settings (
  key text primary key,
  value jsonb not null,
  updated_by text,
  updated_at timestamptz not null default now()
);

insert into public.app_settings (key, value) values ('finding_reviews_enabled', 'true')
  on conflict (key) do nothing;

alter table public.app_settings enable row level security;

grant select, insert, update on public.app_settings to service_role;
