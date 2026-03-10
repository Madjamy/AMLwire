-- ============================================================
-- AMLWire.com — Supabase Database Setup
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Enable UUID extension
create extension if not exists "pgcrypto";

-- ============================================================
-- TABLE: articles
-- Populated daily by the backend pipeline
-- ============================================================
create table if not exists articles (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  summary       text,
  raw_snippet   text,
  image_url     text,
  source_url    text not null unique,
  source_name   text,
  category      text check (category in ('news', 'typology')),
  aml_typology  text,
  country       text,
  region        text,
  tags          text[],
  published_at  timestamptz,
  fetched_at    timestamptz,
  created_at    timestamptz default now()
);

-- Index for fast filtering by country, typology, date
create index if not exists idx_articles_country on articles(country);
create index if not exists idx_articles_aml_typology on articles(aml_typology);
create index if not exists idx_articles_published_at on articles(published_at desc);
create index if not exists idx_articles_category on articles(category);

-- Row Level Security: public read, service role write
alter table articles enable row level security;

create policy "Public read articles"
  on articles for select
  using (true);

-- ============================================================
-- TABLE: typology_summaries
-- AI-synthesized typology analysis, generated each daily run
-- ============================================================
create table if not exists typology_summaries (
  id                  uuid primary key default gen_random_uuid(),
  typology_name       text not null,
  summary             text,
  countries_involved  text[],
  article_count       int default 0,
  digest_date         date not null,
  created_at          timestamptz default now()
);

create index if not exists idx_typology_date on typology_summaries(digest_date desc);

alter table typology_summaries enable row level security;

create policy "Public read typology_summaries"
  on typology_summaries for select
  using (true);

-- ============================================================
-- TABLE: profiles
-- For authenticated users of the Lovable frontend
-- ============================================================
create table if not exists profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  full_name   text,
  avatar_url  text,
  created_at  timestamptz default now()
);

alter table profiles enable row level security;

create policy "Users can read own profile"
  on profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on profiles for update
  using (auth.uid() = id);

-- Auto-create profile on user signup
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, full_name, avatar_url)
  values (new.id, new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'avatar_url');
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
