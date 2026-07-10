-- User-managed GitHub repositories, keyed by the authenticated Supabase user.
create table if not exists public.repositories (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  owner text not null,
  name text not null,
  is_active boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists repositories_user_idx on public.repositories (user_id);

-- Only one repository may be active per user at a time.
create unique index if not exists repositories_one_active_idx
  on public.repositories (user_id)
  where is_active;

-- Row Level Security: a user can only see and modify their own repositories.
alter table public.repositories enable row level security;

drop policy if exists "Repositories are viewable by owner" on public.repositories;
create policy "Repositories are viewable by owner"
  on public.repositories for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert their own repository" on public.repositories;
create policy "Users can insert their own repository"
  on public.repositories for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update their own repository" on public.repositories;
create policy "Users can update their own repository"
  on public.repositories for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own repository" on public.repositories;
create policy "Users can delete their own repository"
  on public.repositories for delete
  using (auth.uid() = user_id);
