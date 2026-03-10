-- AMLWire Staging Table
-- Stores all raw fetched articles before dedup/AI processing.
-- Frontend reads from `articles` only. This table is for audit/debug.

CREATE TABLE IF NOT EXISTS articles_staging (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title       text,
    url         text UNIQUE NOT NULL,
    source      text,
    published_at text,          -- raw string as received from API
    description text,           -- raw snippet/description
    api_source  text,           -- 'newsapi' or 'tavily'
    country     text,           -- country tag if set by fetch tool
    fetched_at  timestamptz DEFAULT now()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_staging_url ON articles_staging(url);
CREATE INDEX IF NOT EXISTS idx_staging_fetched_at ON articles_staging(fetched_at DESC);

-- RLS: no public access needed (internal use only)
ALTER TABLE articles_staging ENABLE ROW LEVEL SECURITY;
