"""
Log daily pipeline run stats to Supabase pipeline_run_stats table.
Upserts on run_date — safe to re-run or call multiple times in one day.
"""

import os
from datetime import date
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


def log_pipeline_stats(
    newsapi_count: int = 0,
    tavily_count: int = 0,
    country_news_count: int = 0,
    rss_count: int = 0,
    gdelt_count: int = 0,
    scraper_count: int = 0,
    newsdata_count: int = 0,
    gnews_count: int = 0,
    thenewsapi_count: int = 0,
    discovery_count: int = 0,
    total_fetched: int = 0,
    total_after_dedup: int = 0,
    total_published: int = 0,
) -> bool:
    """
    Upsert today's pipeline run stats into pipeline_run_stats.
    Returns True on success, False on failure.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[Stats] SUPABASE_URL or SUPABASE_SERVICE_KEY not set — skipping stats log")
        return False

    # Core row — columns that exist in the Supabase table
    row = {
        "run_date": date.today().isoformat(),
        "newsapi_count": newsapi_count,
        "tavily_count": tavily_count,
        "country_news_count": country_news_count,
        "rss_count": rss_count,
        "gdelt_count": gdelt_count,
        "total_fetched": total_fetched,
        "total_after_dedup": total_after_dedup,
        "total_published": total_published,
    }

    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        client.table("pipeline_run_stats").upsert(
            row, on_conflict="run_date"
        ).execute()
        print(
            f"[Stats] Logged: NewsAPI={newsapi_count} Tavily={tavily_count} "
            f"Country={country_news_count} RSS={rss_count} GDELT={gdelt_count} "
            f"Scrapers={scraper_count} NewsData={newsdata_count} "
            f"GNews={gnews_count} TheNewsAPI={thenewsapi_count} "
            f"Discovery={discovery_count} "
            f"| fetched={total_fetched} dedup={total_after_dedup} published={total_published}"
        )
        return True
    except Exception as e:
        print(f"[Stats] Failed to log pipeline stats: {e}")
        return False
