-- Pipeline Run Stats — tracks daily article counts per source
-- Run once in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS pipeline_run_stats (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    run_at          TIMESTAMPTZ DEFAULT NOW(),

    -- Per-source raw fetch counts (before dedup/AI)
    newsapi_count       INT DEFAULT 0,
    tavily_count        INT DEFAULT 0,
    country_news_count  INT DEFAULT 0,
    rss_count           INT DEFAULT 0,
    gdelt_count         INT DEFAULT 0,

    -- Pipeline funnel counts
    total_fetched       INT DEFAULT 0,   -- after date filter, before dedup
    total_after_dedup   INT DEFAULT 0,   -- after dedup
    total_published     INT DEFAULT 0,   -- final articles uploaded

    CONSTRAINT pipeline_run_stats_run_date_key UNIQUE (run_date)
);

-- Public read-only access (same policy pattern as articles table)
ALTER TABLE pipeline_run_stats ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read pipeline_run_stats"
    ON pipeline_run_stats FOR SELECT
    USING (true);

-- Index for dashboard queries (latest-first)
CREATE INDEX IF NOT EXISTS idx_pipeline_run_stats_run_date
    ON pipeline_run_stats (run_date DESC);
