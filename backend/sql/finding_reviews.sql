create table if not exists public.finding_reviews (
  id uuid primary key default gen_random_uuid(),
  -- cases.id is text (documents.id is uuid), the same pairing as the flags table.
  case_id text not null references public.cases (id) on delete cascade,
  document_id uuid references public.documents (id) on delete cascade,
  scope text not null check (scope in ('document', 'case')),
  kind text not null check (kind in ('verdict', 'missed')),
  finding_id text,
  check_name text not null,
  check_code text,
  summary text not null default '',
  verdict text check (verdict in ('correct', 'wrong')),
  note text,
  reviewer text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint finding_reviews_kind_shape check (
    (kind = 'verdict' and finding_id is not null and verdict is not null)
    or (kind = 'missed' and verdict is null)
  ),
  constraint finding_reviews_scope_shape check ((scope = 'document') = (document_id is not null))
);

-- One verdict per finding in a case.
create unique index if not exists finding_reviews_one_verdict
  on public.finding_reviews (case_id, finding_id) where kind = 'verdict';

create index if not exists finding_reviews_case_id on public.finding_reviews (case_id);
create index if not exists finding_reviews_document_id on public.finding_reviews (document_id);

alter table public.finding_reviews enable row level security;