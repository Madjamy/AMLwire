-- One story, one article: other outlets' reports of a story are kept as links on
-- the published article, and copies already published point at the article they
-- were folded into instead of being deleted (old links keep working).
alter table public.articles
  add column if not exists other_sources jsonb not null default '[]'::jsonb,
  add column if not exists merged_into uuid references public.articles(id) on delete set null;

create index if not exists articles_merged_into_idx
  on public.articles (merged_into) where merged_into is not null;
