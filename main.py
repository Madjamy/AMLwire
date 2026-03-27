"""
AMLWire.com -- Main Orchestrator
Daily pipeline:
  1.  Global AML news (NewsAPI)
  2.  Tavily deep search -- full content, broad topic coverage (4 keys × functions)
  3.  Country-specific news (17 jurisdictions, NewsAPI 4 keys × country groups)
  4.  Regulatory RSS feeds (28 feeds incl. AUSTRAC, FCA, FinCEN, FATF, Egmont, Interpol, FSRBs)
  5.  GDELT global news (19 queries, catches regional sources)
  6.  Regulator page scrapers (BeautifulSoup, 5 regulator sites)
  7.  NewsData.io (crime category + country filter)
  8.  GNews (AU/UK/CA via Google News)
  9.  TheNewsAPI (precision AND/OR/NOT queries)
  10. Drop articles with no publish date or older than 14 days
  11. Title-similarity dedup (catch syndicated articles)
  12. Save all candidates to articles_staging (audit trail)
  13. Deduplicate (within batch + against Supabase articles table)
  14. AI analysis -- filter, summarise, extract typology + modus operandi (with full scrape)
  15. Curation -- country cap (USA/UK/AU/JP/SG/IN/UAE≤5, others≤2), quality rank, max 40
  16. Upload articles to Supabase articles table (final/published)
  17. Generate + upload typology summaries
  18. Send Telegram daily report (stats, top articles, alerts)

Usage:
    python main.py
"""

import re as _re
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

TOTAL_STEPS = 18


def _normalise_title(title: str) -> str:
    """Normalise title for similarity comparison: lowercase, strip punctuation, collapse whitespace."""
    t = title.lower().strip()
    t = _re.sub(r'[^\w\s]', '', t)  # strip punctuation
    t = _re.sub(r'\s+', ' ', t)      # collapse whitespace
    for prefix in ["breaking ", "exclusive ", "update "]:
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.strip()


def run_pipeline():
    log.info("=" * 65)
    log.info(f"AMLWire Pipeline started -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 65)

    # Report data dict — populated throughout pipeline, sent via Telegram at the end
    report_data = {
        "source_counts": {},
        "total_fetched": 0,
        "after_date_filter": 0,
        "after_title_dedup": 0,
        "after_supabase_dedup": 0,
        "ai_processed": 0,
        "ai_selected": 0,
        "after_curation": 0,
        "published": 0,
        "articles": [],
        "alerts": [],
    }

    try:  # Wrap entire pipeline so we can send failure alerts

        # Step 1: Global NewsAPI
        log.info(f"Step 1/{TOTAL_STEPS} -- Global fetch: NewsAPI...")
        try:
            from tools.fetch_newsapi import fetch_articles as fetch_newsapi
            newsapi_articles = fetch_newsapi()
            log.info(f"  NewsAPI: {len(newsapi_articles)} articles")
        except Exception as e:
            log.error(f"  NewsAPI failed: {e}")
            newsapi_articles = []
            report_data["alerts"].append(f"NewsAPI failed: {e}")

        # Step 2: Tavily deep search (4 keys × functions)
        log.info(f"Step 2/{TOTAL_STEPS} -- Tavily deep search...")
        try:
            from tools.fetch_tavily import fetch_articles as fetch_tavily
            tavily_articles = fetch_tavily()
            log.info(f"  Tavily: {len(tavily_articles)} articles")
        except Exception as e:
            log.error(f"  Tavily failed: {e}")
            tavily_articles = []
            report_data["alerts"].append(f"Tavily failed: {e}")

        # Step 3: Country-specific (4 keys × country groups via NewsAPI)
        log.info(f"Step 3/{TOTAL_STEPS} -- Country fetch: 17 jurisdictions...")
        try:
            from tools.fetch_country_news import fetch_country_articles
            country_articles = fetch_country_articles()
            log.info(f"  Country fetch: {len(country_articles)} articles")
        except Exception as e:
            log.error(f"  Country fetch failed: {e}")
            country_articles = []
            report_data["alerts"].append(f"Country fetch failed: {e}")

        # Step 4: Regulatory RSS feeds (28 feeds)
        log.info(f"Step 4/{TOTAL_STEPS} -- Regulatory RSS feeds...")
        try:
            from tools.fetch_rss_feeds import fetch_rss_articles
            rss_articles = fetch_rss_articles()
            log.info(f"  RSS feeds: {len(rss_articles)} articles")
        except Exception as e:
            log.error(f"  RSS fetch failed: {e}")
            rss_articles = []
            report_data["alerts"].append(f"RSS failed: {e}")

        # Step 5: GDELT global news (19 queries, regional coverage)
        log.info(f"Step 5/{TOTAL_STEPS} -- GDELT global news fetch...")
        try:
            from tools.fetch_gdelt import fetch_gdelt_articles
            gdelt_articles = fetch_gdelt_articles()
            log.info(f"  GDELT: {len(gdelt_articles)} articles")
        except Exception as e:
            log.error(f"  GDELT fetch failed: {e}")
            gdelt_articles = []
            report_data["alerts"].append(f"GDELT failed: {e}")

        # Step 6: Regulator page scrapers
        log.info(f"Step 6/{TOTAL_STEPS} -- Regulator page scrapers...")
        try:
            from tools.fetch_regulator_scrape import fetch_regulator_articles
            scraper_articles = fetch_regulator_articles()
            log.info(f"  Scrapers: {len(scraper_articles)} articles")
        except Exception as e:
            log.error(f"  Regulator scrapers failed: {e}")
            scraper_articles = []
            report_data["alerts"].append(f"Scrapers failed: {e}")

        # Step 7: NewsData.io (crime category + country filter)
        log.info(f"Step 7/{TOTAL_STEPS} -- NewsData.io...")
        try:
            from tools.fetch_newsdata import fetch_newsdata_articles
            newsdata_articles = fetch_newsdata_articles()
            log.info(f"  NewsData: {len(newsdata_articles)} articles")
        except Exception as e:
            log.error(f"  NewsData failed: {e}")
            newsdata_articles = []
            report_data["alerts"].append(f"NewsData failed: {e}")

        # Step 8: GNews (AU/UK/CA)
        log.info(f"Step 8/{TOTAL_STEPS} -- GNews (AU/UK/CA)...")
        try:
            from tools.fetch_gnews import fetch_gnews_articles
            gnews_articles = fetch_gnews_articles()
            log.info(f"  GNews: {len(gnews_articles)} articles")
        except Exception as e:
            log.error(f"  GNews failed: {e}")
            gnews_articles = []
            report_data["alerts"].append(f"GNews failed: {e}")

        # Step 9: TheNewsAPI (precision queries)
        log.info(f"Step 9/{TOTAL_STEPS} -- TheNewsAPI precision queries...")
        try:
            from tools.fetch_thenewsapi import fetch_thenewsapi_articles
            thenewsapi_articles = fetch_thenewsapi_articles()
            log.info(f"  TheNewsAPI: {len(thenewsapi_articles)} articles")
        except Exception as e:
            log.error(f"  TheNewsAPI failed: {e}")
            thenewsapi_articles = []
            report_data["alerts"].append(f"TheNewsAPI failed: {e}")

        # Capture per-source counts before any filtering (for stats logging)
        _stats_newsapi = len(newsapi_articles)
        _stats_tavily = len(tavily_articles)
        _stats_country = len(country_articles)
        _stats_rss = len(rss_articles)
        _stats_gdelt = len(gdelt_articles)
        _stats_scraper = len(scraper_articles)
        _stats_newsdata = len(newsdata_articles)
        _stats_gnews = len(gnews_articles)
        _stats_thenewsapi = len(thenewsapi_articles)

        report_data["source_counts"] = {
            "NewsAPI": _stats_newsapi,
            "Tavily": _stats_tavily,
            "Country": _stats_country,
            "RSS": _stats_rss,
            "GDELT": _stats_gdelt,
            "Scrapers": _stats_scraper,
            "NewsData": _stats_newsdata,
            "GNews": _stats_gnews,
            "TheNewsAPI": _stats_thenewsapi,
        }

        # Add alerts for sources that returned 0 articles
        for src_name, src_count in report_data["source_counts"].items():
            if src_count == 0:
                report_data["alerts"].append(f"{src_name}: 0 articles (possible rate limit or failure)")

        all_articles = (
            newsapi_articles + tavily_articles + country_articles + rss_articles
            + gdelt_articles + scraper_articles + newsdata_articles
            + gnews_articles + thenewsapi_articles
        )
        _stats_total_fetched = len(all_articles)  # Capture before any filtering
        report_data["total_fetched"] = _stats_total_fetched
        log.info(f"  Combined total: {_stats_total_fetched} candidate articles")

        if not all_articles:
            log.warning("No articles fetched. Exiting.")
            return

        # Step 10: Drop articles with no publish date or older than 14 days
        log.info(f"Step 10/{TOTAL_STEPS} -- Filtering articles with no publish date or older than 14 days...")
        date_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        with_date = []
        no_date_count = 0
        stale_count = 0
        for a in all_articles:
            pub = (a.get("published_at") or "").strip()
            if not pub:
                no_date_count += 1
                continue
            # Parse date robustly — handle multiple formats from different sources
            parsed_date = None
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                         "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%fZ",
                         "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed_date = datetime.strptime(pub[:30], fmt)
                    break
                except ValueError:
                    continue
            if parsed_date is None:
                # Last resort: try just the first 10 chars as YYYY-MM-DD
                try:
                    parsed_date = datetime.strptime(pub[:10], "%Y-%m-%d")
                except ValueError:
                    no_date_count += 1
                    continue
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            if parsed_date < date_cutoff:
                stale_count += 1
                continue
            with_date.append(a)
        if no_date_count:
            log.info(f"  Dropped {no_date_count} articles with no publish date")
        if stale_count:
            log.info(f"  Dropped {stale_count} articles older than 14 days")
        all_articles = with_date
        report_data["after_date_filter"] = len(all_articles)
        log.info(f"  {len(all_articles)} articles with valid, recent publish date")

        if not all_articles:
            log.warning("No articles with valid publish dates. Exiting.")
            return

        # Step 11: Title-similarity dedup (catch syndicated articles)
        log.info(f"Step 11/{TOTAL_STEPS} -- Title-similarity dedup...")
        seen_titles = {}
        title_deduped = []
        title_dup_count = 0
        for a in all_articles:
            title = a.get("title", "")
            norm = _normalise_title(title)
            if len(norm) < 15:
                title_deduped.append(a)
                continue
            short_key = norm[:60]
            if short_key in seen_titles:
                title_dup_count += 1
                continue
            seen_titles[short_key] = a
            title_deduped.append(a)
        if title_dup_count:
            log.info(f"  Removed {title_dup_count} syndicated duplicates by title similarity")
        all_articles = title_deduped
        report_data["after_title_dedup"] = len(all_articles)
        log.info(f"  {len(all_articles)} articles after title dedup")

        # Step 12: Save all candidates to staging table (audit trail)
        log.info(f"Step 12/{TOTAL_STEPS} -- Saving candidates to articles_staging...")
        try:
            from tools.upload_supabase import upload_staging
            staged = upload_staging(all_articles)
            log.info(f"  {staged} articles saved to staging")
        except Exception as e:
            log.error(f"  Staging upload failed: {e}")

        # Step 13: Deduplicate (URL dedup + against Supabase)
        log.info(f"Step 13/{TOTAL_STEPS} -- Deduplicating against Supabase articles table...")
        try:
            from tools.deduplicate import deduplicate
            clean_articles = deduplicate(all_articles)
            log.info(f"  {len(clean_articles)} unique new articles after dedup")
        except Exception as e:
            log.error(f"  Deduplication failed: {e}")
            clean_articles = all_articles
        _stats_after_dedup = len(clean_articles)
        report_data["after_supabase_dedup"] = _stats_after_dedup

        if not clean_articles:
            log.info("All articles already in Supabase. Nothing new to process.")
            return

        # Step 14: AI Analysis (with full article scraping)
        report_data["ai_processed"] = len(clean_articles)
        log.info(f"Step 14/{TOTAL_STEPS} -- AI analysis of {len(clean_articles)} articles (MiMo-V2-Pro + full scrape)...")
        try:
            from tools.analyze_articles import analyze_articles
            analyzed = analyze_articles(clean_articles)
            log.info(f"  {len(analyzed)} articles structured by AI")
            report_data["ai_selected"] = len(analyzed)
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
        # Also cross-check: if the AI summary mentions a year older than current year, flag as stale
        current_year = datetime.now(timezone.utc).year
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        fresh = []
        for article in analyzed:
            date_str = article.get("published_date", "")
            summary = (article.get("summary") or "") + " " + (article.get("modus_operandi") or "")

            # Content-date cross-check: detect stale articles the AI missed
            year_mentions = _re.findall(r'\b(20[0-9]{2})\b', summary)
            stale_years = [int(y) for y in year_mentions if int(y) < current_year - 1]
            recent_years = [int(y) for y in year_mentions if int(y) >= current_year - 1]
            if stale_years and not recent_years:
                oldest = min(stale_years)
                log.info(f"  Skipping stale (content references {oldest}): {article.get('title', '')[:60]}")
                continue

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

        # Step 15: Curation — country cap + quality ranking
        log.info(f"Step 15/{TOTAL_STEPS} -- Curating {len(analyzed)} articles (country cap + quality rank)...")
        try:
            from tools.curate_articles import curate_articles
            analyzed = curate_articles(analyzed)
            log.info(f"  {len(analyzed)} articles selected after curation")
        except Exception as e:
            log.error(f"  Curation failed (uploading all): {e}")

        report_data["after_curation"] = len(analyzed)

        if not analyzed:
            log.warning("No articles after curation. Exiting.")
            return

        # Step 16: Upload articles to final table
        log.info(f"Step 16/{TOTAL_STEPS} -- Uploading articles to Supabase articles table...")
        _stats_published = len(analyzed)
        report_data["published"] = _stats_published
        report_data["articles"] = analyzed
        try:
            from tools.upload_supabase import upload_articles
            uploaded = upload_articles(analyzed)
            log.info(f"  {uploaded}/{len(analyzed)} articles uploaded")
        except Exception as e:
            log.error(f"  Article upload failed: {e}")

        # Step 17: Typology summaries
        log.info(f"Step 17/{TOTAL_STEPS} -- Generating typology summaries (based on curated set)...")
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
                scraper_count=_stats_scraper,
                newsdata_count=_stats_newsdata,
                gnews_count=_stats_gnews,
                thenewsapi_count=_stats_thenewsapi,
                total_fetched=_stats_total_fetched,
                total_after_dedup=_stats_after_dedup,
                total_published=_stats_published,
            )
        except Exception as e:
            log.error(f"  Stats logging failed: {e}")

        # Step 18: Send Telegram daily report
        log.info(f"Step 18/{TOTAL_STEPS} -- Sending Telegram daily report...")
        try:
            from tools.send_telegram_report import send_pipeline_report
            send_pipeline_report(report_data)
        except Exception as e:
            log.error(f"  Telegram report failed: {e}")

        log.info("=" * 65)
        log.info(f"Pipeline complete -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        log.info("=" * 65)

    except Exception as e:
        log.error(f"Pipeline failed: {e}")
        try:
            from tools.send_telegram_report import send_pipeline_failure
            send_pipeline_failure(str(e), report_data)
        except Exception:
            pass  # Don't let Telegram failure mask the real error
        raise  # Re-raise so GitHub Actions sees the failure


if __name__ == "__main__":
    run_pipeline()
