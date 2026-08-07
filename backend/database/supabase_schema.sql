-- =============================================================================
-- MeetMind AI - Supabase schema
-- Run this in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- =============================================================================

create extension if not exists "pgcrypto";

-- -----------------------------------------------------------------------------
-- USERS
-- -----------------------------------------------------------------------------
create table if not exists users (
    id                    uuid primary key default gen_random_uuid(),
    name                  text not null,
    email                 text not null unique,
    profile_picture       text,
    password_hash         text,
    auth_provider         text default 'google' check (auth_provider in ('google', 'password', 'both')),
    google_access_token   text,
    google_refresh_token  text,
    google_token_expiry   timestamptz,
    timezone              text default 'UTC',
    created_at            timestamptz default now(),
    updated_at            timestamptz default now()
);

create index if not exists idx_users_email on users (email);

-- -----------------------------------------------------------------------------
-- MEETINGS
-- -----------------------------------------------------------------------------
create table if not exists meetings (
    id               uuid primary key default gen_random_uuid(),
    title            text not null,
    description      text default '',
    meeting_date     date not null,
    start_time       time not null,
    end_time         time not null,
    timezone         text default 'UTC',
    participants     jsonb default '[]'::jsonb,
    meeting_link     text,
    google_event_id  text,
    conflict_status  text default 'none' check (conflict_status in ('none', 'detected', 'resolved')),
    status           text default 'scheduled' check (status in ('scheduled', 'completed', 'cancelled')),
    created_by       uuid not null references users(id) on delete cascade,
    created_at       timestamptz default now(),
    updated_at       timestamptz default now()
);

create index if not exists idx_meetings_created_by on meetings (created_by);
create index if not exists idx_meetings_date on meetings (meeting_date);
create index if not exists idx_meetings_google_event_id on meetings (google_event_id);

-- -----------------------------------------------------------------------------
-- AUTO-UPDATE updated_at ON EVERY ROW UPDATE
-- -----------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_users_updated_at on users;
create trigger trg_users_updated_at
before update on users
for each row execute function set_updated_at();

drop trigger if exists trg_meetings_updated_at on meetings;
create trigger trg_meetings_updated_at
before update on meetings
for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- ROW LEVEL SECURITY
-- -----------------------------------------------------------------------------
-- The Flask backend connects using the service_role key, which bypasses RLS
-- entirely - so these policies only matter if you ever let a client
-- (browser/mobile) query Supabase directly with the anon key. Enabled here
-- as a safe default; tighten or remove based on your needs.
alter table users enable row level security;
alter table meetings enable row level security;

drop policy if exists "Service role full access to users" on users;
create policy "Service role full access to users"
    on users for all
    using (auth.role() = 'service_role')
    with check (auth.role() = 'service_role');

drop policy if exists "Service role full access to meetings" on meetings;
create policy "Service role full access to meetings"
    on meetings for all
    using (auth.role() = 'service_role')
    with check (auth.role() = 'service_role');

