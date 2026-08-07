-- Run this once in Supabase SQL Editor to enable email/password login.
alter table users add column if not exists password_hash text;
alter table users add column if not exists auth_provider text default 'google';

alter table users drop constraint if exists users_auth_provider_check;
alter table users add constraint users_auth_provider_check
  check (auth_provider in ('google', 'password', 'both'));
