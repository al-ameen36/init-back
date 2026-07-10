create table if not exists public.analyses (
    id bigint generated always as identity primary key,
    repo text not null,
    issue_number integer not null,
    profile_key text not null default 'anon',
    title text,
    language text,
    match_score integer not null default 0,
    match_reasons jsonb not null default '[]'::jsonb,
    scored_files jsonb not null default '[]'::jsonb,
    guide jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (repo, issue_number, profile_key)
);

create index if not exists analyses_repo_idx on public.analyses (repo);
create index if not exists analyses_profile_idx on public.analyses (profile_key);

alter table public.analyses enable row level security;
