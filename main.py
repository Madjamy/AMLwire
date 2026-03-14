"""
AMLWire.com -- Main Orchestrator
Daily pipeline:
  1.  Global AML news (NewsAPI)
  2.  Tavily deep search -- full content, broad topic coverage
  3.  Country-specific news (17 jurisdictions -- top 5 each, NewsAPI + Tavily)
  4.  Regulatory RSS feeds (AUSTRAC, FCA, FinCEN, FATF, Egmont, Interpol, FSRBs, etc.)
  5.  GDELT global news (catches regional sources missed by Tavily/NewsAPI)
  6.  Drop articles with no publish date
  7.  Save all candidates to articles_staging (audit trail)
  8.  Deduplicate (within batch + against Supabase articles table)
  9.  AI analysis -- filter, summarise, extract typology + modus operandi (with full scrape)
  10. Curation -- country cap (USA/UK/AU/JP/SG/IN/UAE≤5, others≤2), quality rank, max 40
  11. Upload articles to Supabase articles table (final/published)
  12. Generate + upload typology summaries

Usage:
    python main.py
"""

import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def run_pipeline():
    log.info("=" * 65)
    log.info(f"AMLWire Pipeline started -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 65)

    # Step 1: Global NewsAPI
    log.info("Step 1/10 -- Global fetch: NewsAPI...")
    try:
        from tools.fetch_newsapi import fetch_articles as fetch_newsapi
        newsapi_articles = fetch_newsapi()
        log.info(f"  NewsAPI: {len(newsapi_articles)} articles")
    except Exception as e:
        log.error(f"  NewsAPI failed: {e}")
        newsapi_articles = []

    # Step 2: Tavily deep search (full content, broad topic coverage)
    log.info("Step 2/10 -- Tavily deep search...")
    try:
        from tools.fetch_tavily import fetch_articles as fetch_tavily
        tavily_articles = fetch_tavily()
        log.info(f"  Tavily: {len(tavily_articles)} articles")
    except Exception as e:
        log.error(f"  Tavily failed: {e}")
        tavily_articles = []

    # Step 3: Country-specific (top 5 per country via NewsAPI)
    log.info("Step 3/12 -- Country fetch: 17 jurisdictions...")
    try:
        from tools.fetch_country_news import fetch_country_articles
        country_articles = fetch_country_articles()
        log.info(f"  Country fetch: {len(country_articles)} articles")
    except Exception as e:
        log.error(f"  Country fetch failed: {e}")
        country_articles = []

    # Step 4: Regulatory RSS feeds (AUSTRAC, FCA, FinCEN, FATF, Egmont, Interpol, FSRBs)
    log.info("Step 4/12 -- Regulatory RSS feeds...")
    try:
        from tools.fetch_rss_feeds import fetch_rss_articles
        rss_articles = fetch_rss_articles()
        log.info(f"  RSS feeds: {len(rss_articles)} articles")
    except Exception as e:
        log.error(f"  RSS fetch failed: {e}")
        rss_articles = []

    # Step 5: GDELT global news (regional coverage)
    log.info("Step 5/12 -- GDELT global news fetch...")
    try:
        from tools.fetch_gdelt import fetch_gdelt_articles
        gdelt_articles = fetch_gdelt_articles()
        log.info(f"  GDELT: {len(gdelt_articles)} articles")
    except Exception as e:
        log.error(f"  GDELT fetch failed: {e}")
        gdelt_articles = []

    # Capture per-source counts before any filtering (for stats logging)
    _stats_newsapi = len(newsapi_articles)
    _stats_tavily = len(tavily_articles)
    _stats_country = len(country_articles)
    _stats_rss = len(rss_articles)
    _stats_gdelt = len(gdelt_articles)

    all_articles = newsapi_articles + tavily_articles + country_articles + rss_articles + gdelt_articles
    log.info(f"  Combined total: {len(all_articles)} candidate articles")

    if not all_articles:
        log.warning("No articles fetched. Exiting.")
        return

    # Step 6: Drop articles with no publish date
    log.info("Step 6/12 -- Filtering articles with no publish date...")
    with_date = []
    no_date_articles = []
    for a in all_articles:
        if (a.get("published_at") or "").strip():
            with_date.append(a)
        else:
            no_date_articles.append(a)
    if no_date_articles:
        log.info(f"  Dropped {len(no_date_articles)} articles with no publish date:")
        for a in no_date_articles[:10]:  # Log first 10 for audit
            log.info(f"    - [{a.get('source', '?')}] {a.get('title', '')[:70]}")
        if len(no_date_articles) > 10:
            log.info(f"    ... and {len(no_date_articles) - 10} more")
    all_articles = with_date
    log.info(f"  {len(all_articles)} articles with valid publish date")

    if not all_articles:
        log.warning("No articles with publish dates. Exiting.")
        return

    # Step 7: Save all candidates to staging table (audit trail)
    log.info("Step 7/12 -- Saving candidates to articles_staging...")
    try:
        from tools.upload_supabase import upload_staging
        staged = upload_staging(all_articles)
        log.info(f"  {staged} articles saved to staging")
    except Exception as e:
        log.error(f"  Staging upload failed: {e}")

    # Step 8: Deduplicate
    log.info("Step 8/12 -- Deduplicating against Supabase articles table...")
    try:
        from tools.deduplicate import deduplicate
        clean_articles = deduplicate(all_articles)
        log.info(f"  {len(clean_articles)} unique new articles after dedup")
    except Exception as e:
        log.error(f"  Deduplication failed: {e}")
        clean_articles = all_articles
    _stats_after_dedup = len(clean_articles)

    if not clean_articles:
        log.info("All articles already in Supabase. Nothing new to process.")
        return

    # Step 9: AI Analysis (with full article scraping)
    log.info(f"Step 9/12 -- AI analysis of {len(clean_articles)} articles (Grok 4.1 Fast + full scrape)...")
    try:
        from tools.analyze_articles import analyze_articles
        analyzed = analyze_articles(clean_articles)
        log.info(f"  {len(analyzed)} articles structured by AI")
    except Exception as e:
        log.error(f"  AI analysis failed: {e}")
        return

    if not analyzed:
        log.warning("AI returned no structured articles.")
        return

    # Enrich with raw_snippet + country from original fetch
    url_to_raw = {a["url"]: a.get("description", "") for a in clean_articles}
    url_to_country = {a["url"]: a.get("country", "") for a in clean_articles}
    for article in analyzed:
        src = article.get("source_url") or article.get("url", "")
        article["raw_snippet"] = url_to_raw.get(src, "")
        if not article.get("country"):
            article["country"] = url_to_country.get(src) or None

    # Drop articles the AI dated older than 7 days (final gate)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    fresh = []
    for article in analyzed:
        date_str = article.get("published_date", "")
        if date_str:
            try:
                for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
                        if parsed < cutoff:
                            log.info(f"  Skipping old ({date_str}): {article.get('title', '')[:60]}")
                        else:
                            fresh.append(article)
                        break
                    except ValueError:
                        continue
                else:
                    fresh.append(article)
            except Exception:
                fresh.append(article)
        else:
            log.info(f"  Skipping (no AI date): {article.get('title', '')[:60]}")
    if len(fresh) < len(analyzed):
        log.info(f"  Dropped {len(analyzed) - len(fresh)} articles (old or no date)")
    analyzed = fresh

    if not analyzed:
        log.warning("No fresh articles to upload.")
        return

    # Step 10: Curation — country cap + quality ranking
    log.info(f"Step 10/12 -- Curating {len(analyzed)} articles (country cap + quality rank)...")
    try:
        from tools.curate_articles import curate_articles
        analyzed = curate_articles(analyzed)
        log.info(f"  {len(analyzed)} articles selected after curation")
    except Exception as e:
        log.error(f"  Curation failed (uploading all): {e}")

    if not analyzed:
        log.warning("No articles after curation. Exiting.")
        return

    # Step 11: Upload articles to final table
    log.info("Step 11/12 -- Uploading articles to Supabase articles table...")
    _stats_published = len(analyzed)
    try:
        from tools.upload_supabase import upload_articles
        uploaded = upload_articles(analyzed)
        log.info(f"  {uploaded}/{len(analyzed)} articles uploaded")
    except Exception as e:
        log.error(f"  Article upload failed: {e}")

    # Step 12: Typology summaries
    log.info("Step 12/12 -- Generating typology summaries (based on curated set)...")
    try:
        from tools.generate_typology_summary import generate_typology_summaries
        from tools.upload_supabase import upload_typology_summaries
        summaries = generate_typology_summaries(analyzed)
        if summaries:
            upload_typology_summaries(summaries)
            log.info(f"  {len(summaries)} typology summaries uploaded")
        else:
            log.info("  No typology summaries generated")
    except Exception as e:
        log.error(f"  Typology summary step failed: {e}")

    # Log pipeline stats to Supabase for dashboard
    try:
        from tools.log_pipeline_stats import log_pipeline_stats
        log_pipeline_stats(
            newsapi_count=_stats_newsapi,
            tavily_count=_stats_tavily,
            country_news_count=_stats_country,
            rss_count=_stats_rss,
            gdelt_count=_stats_gdelt,
            total_fetched=len(all_articles),
            total_after_dedup=_stats_after_dedup,
            total_published=_stats_published,
        )
    except Exception as e:
        log.error(f"  Stats logging failed: {e}")

    log.info("=" * 65)
    log.info(f"Pipeline complete -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 65)


if __name__ == "__main__":
    run_pipeline()
