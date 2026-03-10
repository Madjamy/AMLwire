# Workflow: AMLWire.com — Daily News Pipeline

## Objective
Fetch, analyze, and publish a daily digest of AML/financial crime news to Supabase.
The AMLWire.com frontend (built in Lovable) reads from Supabase and displays articles,
country-specific feeds, and synthesized typology briefings.

## Schedule
- **Frequency**: Once daily
- **Recommended time**: 06:00 UTC
- **Cron**: `0 6 * * * cd /path/to/project && source venv/bin/activate && python main.py >> logs/amlwire.log 2>&1`

## Required Inputs (.env)
| Key | Source |
|-----|--------|
| `NEWSAPI_KEY_1` | newsapi.org (primary) |
| `NEWSAPI_KEY_2` | newsapi.org (fallback if key 1 rate-limited) |
| `SERPAPI_KEY_1` | serpapi.com (primary) |
| `SERPAPI_KEY_2` | serpapi.com (fallback) |
| `OPENROUTER_API_KEY` | openrouter.ai — used for AI analysis AND image generation |
| `OPENROUTER_MODEL` | Default: `anthropic/claude-3.5-sonnet` |
| `SUPABASE_URL` | Supabase project settings |
| `SUPABASE_SERVICE_KEY` | Supabase → Project Settings → API → service_role (JWT) |

## Pipeline Steps (8 steps)

### Step 1 — Global NewsAPI fetch
- **Script**: `tools/fetch_newsapi.py`
- **Queries**: 10 AML/financial crime keyword queries, last 7 days
- **Key fallback**: key 1 → key 2 on rate limit

### Step 2 — Global SerpAPI fetch
- **Script**: `tools/fetch_serpapi.py`
- **Purpose**: Supplement with niche/regional Google News coverage
- **Key fallback**: Same dual-key fallback

### Step 3 — Country-specific fetch (Top 5 per country)
- **Script**: `tools/fetch_country_news.py`
- **Countries**: Australia, USA, UK, India, Singapore, UAE
- **Logic**: NewsAPI first; SerpAPI supplements if fewer than 3 results
- **Output**: Up to 30 tagged articles (country field pre-set)

### Step 4 — Deduplicate
- **Script**: `tools/deduplicate.py`
- **Logic**: URL dedup within batch + check Supabase source_url + drop articles older than 7 days

### Step 5 — AI Analysis
- **Script**: `tools/analyze_articles.py`
- **Model**: OpenRouter → Claude 3.5 Sonnet (configurable via OPENROUTER_MODEL)
- **Output per article**: title, published_date, country, region, source_name, source_url, summary, aml_typology, category ("news"/"typology"), tags[]

### Step 6 — Image generation
- **Script**: `tools/generate_image.py`
- **Service**: Gemini 2.5 Flash via OpenRouter — same API key, no extra cost
- **Output**: PNG saved to .tmp/images/, path in image_url
- **Failure**: Returns null — pipeline continues

### Step 7 — Upload articles
- **Script**: `tools/upload_supabase.py` → `upload_articles()`
- **Table**: `articles`, upsert on `source_url`

### Step 8 — Typology summaries
- **Script**: `tools/generate_typology_summary.py` + `upload_typology_summaries()`
- **Logic**: Groups articles by aml_typology, AI synthesizes a briefing per typology
- **Table**: `typology_summaries`

---

## Supabase Schema (see setup_database.sql)

### articles
id, title, summary, raw_snippet, image_url, source_url (unique), source_name, category, aml_typology, country, region, tags[], published_at, fetched_at, created_at

### typology_summaries
id, typology_name, summary, countries_involved[], article_count, digest_date, created_at

### profiles
id (→ auth.users), full_name, avatar_url, created_at

---

## Lovable Frontend Queries

```javascript
// Latest articles
supabase.from('articles').select('*').order('published_at', { ascending: false }).limit(50)

// By country
.eq('country', 'Australia')

// Typology articles only
.eq('category', 'typology')

// Today's typology summaries
supabase.from('typology_summaries').select('*')
  .eq('digest_date', new Date().toISOString().split('T')[0])
  .order('article_count', { ascending: false })
```

---

## Edge Cases & Lessons Learned

### Rate Limits
- NewsAPI: Each key = 100 req/day. 10 global + 18 country queries = 28 req per key — well within limits.
- SerpAPI: 10 global + up to 18 country queries. Check your plan quota.
- OpenRouter: Batches of 50 articles are safe.

### Failures
- If one fetch source fails → others continue independently
- Image generation failure → image_url = null, article still uploaded
- Per-article upload errors → others continue (try/except per article)
- AI returns 0 articles → pipeline warns and exits cleanly

### Deduplication
- Conflict key: source_url (unique constraint)
- Re-runs on same day are safe — upsert prevents duplicates

---

## VPS Deployment Checklist
- [ ] Run setup_database.sql in Supabase SQL Editor
- [ ] Copy project to VPS
- [ ] python3 -m venv venv && source venv/bin/activate
- [ ] pip install -r requirements.txt
- [ ] Create .env with all keys (service_role JWT for Supabase)
- [ ] Test: python main.py
- [ ] Verify rows in Supabase articles + typology_summaries tables
- [ ] Set cron: 0 6 * * * with full path
- [ ] Connect Lovable to Supabase with anon key (read-only)

## Updating
- Countries: edit COUNTRY_QUERIES in tools/fetch_country_news.py
- Search queries: edit AML_QUERIES in tools/fetch_newsapi.py
- AI model: change OPENROUTER_MODEL in .env — no code change needed
