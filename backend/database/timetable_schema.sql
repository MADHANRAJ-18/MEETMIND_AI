-- =============================================================================
-- Smart Timetable Scheduler - Supabase schema
-- Run this in the Supabase SQL editor AFTER running supabase_schema.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- TIMETABLES
-- -----------------------------------------------------------------------------
create table if not exists timetables (
    id                uuid primary key default gen_random_uuid(),
    created_by        uuid not null references users(id) on delete cascade,
    participant_name  text not null,
    participant_email text default '',
    department        text default '',
    class_or_team     text default '',
    organization_type text default 'company'
                      check (organization_type in ('school','college','university','company')),
    day               text not null
                      check (day in ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')),
    start_time        time not null,
    end_time          time not null,
    subject_or_task   text not null,
    room              text default '',
    created_at        timestamptz default now(),
    updated_at        timestamptz default now(),
    constraint timetables_time_order check (end_time > start_time)
);

create index if not exists idx_timetables_created_by   on timetables (created_by);
create index if not exists idx_timetables_day          on timetables (day);
create index if not exists idx_timetables_dept         on timetables (department);
create index if not exists idx_timetables_participant  on timetables (participant_name);
create index if not exists idx_timetables_class        on timetables (class_or_team);

-- Prevent exact duplicate entries for the same person/day/time
create unique index if not exists uq_timetable_slot
    on timetables (created_by, participant_name, day, start_time, end_time);

-- Auto-update updated_at
drop trigger if exists trg_timetables_updated_at on timetables;
create trigger trg_timetables_updated_at
before update on timetables
for each row execute function set_updated_at();

-- Row Level Security
alter table timetables enable row level security;

drop policy if exists "Service role full access to timetables" on timetables;
create policy "Service role full access to timetables"
    on timetables for all
    using (auth.role() = 'service_role')
    with check (auth.role() = 'service_role');
