-- Cached contributor playbooks per repository. Written by the backend via
-- service-role key (bypasses RLS). The playbook is repo-scoped (not per-user)
-- because the analysis is identical regardless of who requests it.
create table if not exists public.playbooks (
  id bigint generated always as identity primary key,
  repo text not null,
  playbook jsonb not null default '{}'::jsonb,
  prs_analyzed integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (repo)
);

create index if not exists playbooks_repo_idx on public.playbooks (repo);

-- Enable RLS. No policies are needed because:
--   • The backend writes via the service-role key, which bypasses RLS.
--   • The frontend reads playbooks through the backend API, not directly.
-- With RLS on and no policies, all direct client access is blocked.
alter table public.playbooks enable row level security;
