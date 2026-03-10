"""
AMLWire.com — Main Orchestrator
Daily pipeline:
  1. Global AML news (NewsAPI)
  2. Tavily deep search — full article content, covers human trafficking, hawala, TBML, etc.
  3. Country-specific news (AU, USA, UK, India, Singapore, UAE — top 5 each, NewsAPI + Tavily)
  4. Deduplicate (within batch + against Supabase)
  5. AI analysis (OpenRouter / Grok 4.1 Fast) — enriches with country, category, tags, typology
  6. Image generation per article (Gemini 2.5 Flash via OpenRouter -> Supabase Storage)
  7. Upload articles to Supabase
  8. Generate + upload typology summaries

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
    log.info("Step 1/8 -- Global fetch: NewsAPI...")
    try:
        from tools.fetch_newsapi import fetch_articles as fetch_newsapi
        newsapi_articles = fetch_newsapi()
        log.info(f"  NewsAPI: {len(newsapi_articles)} articles")
    except Exception as e:
        log.error(f"  NewsAPI failed: {e}")
        newsapi_articles = []

    # Step 2: Tavily deep search (full content, broad topic coverage)
    log.info("Step 2/8 -- Tavily deep search: human trafficking, hawala, TBML, crypto, sanctions...")
    try:
        from tools.fetch_tavily import fetch_articles as fetch_tavily
        tavily_articles = fetch_tavily()
        log.info(f"  Tavily: {len(tavily_articles)} articles")
    except Exception as e:
        log.error(f"  Tavily failed: {e}")
        tavily_articles = []

    # Step 3: Country-specific (top 5 per country via NewsAPI)
    log.info("Step 3/8 -- Country fetch: AU, USA, UK, India, Singapore, UAE (top 5 each)...")
    try:
        from tools.fetch_country_news import fetch_country_articles
        country_articles = fetch_country_articles()
        log.info(f"  Country fetch: {len(country_articles)} articles")
    except Exception as e:
        log.error(f"  Country fetch failed: {e}")
        country_articles = []

    all_articles = newsapi_articles + tavily_articles + country_articles
    log.info(f"  Combined total: {len(all_articles)} candidate articles")

    if not all_articles:
        log.warning("No articles fetched. Exiting.")
        return

    # Step 4: Deduplicate
    log.info("Step 4/8 -- Deduplicating...")
    try:
        from tools.deduplicate import deduplicate
        clean_articles = deduplicate(all_articles)
        log.info(f"  {len(clean_articles)} unique new articles")
    except Exception as e:
        log.error(f"  Deduplication failed: {e}")
        clean_articles = all_articles

    if not clean_articles:
        log.info("All articles already in Supabase. Nothing new to process.")
        return

    # Step 5: AI Analysis
    log.info(f"Step 5/8 -- AI analysis of {len(clean_articles)} articles...")
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
            fresh.append(article)
    if len(fresh) < len(analyzed):
        log.info(f"  Dropped {len(analyzed) - len(fresh)} articles older than 7 days")
    analyzed = fresh

    if not analyzed:
        log.warning("No fresh articles to upload.")
        return

    # Step 6: Image generation
    log.info(f"Step 6/8 -- Generating cover images for {len(analyzed)} articles...")
    try:
        from tools.generate_image import generate_image
        for article in analyzed:
            try:
                article["image_url"] = generate_image(
                    title=article.get("title", ""),
                    summary=article.get("summary", ""),
                    region=article.get("region", ""),
                    typology=article.get("aml_typology", ""),
                )
            except Exception as e:
                log.warning(f"  Image failed: {e}")
                article["image_url"] = None
    except Exception as e:
        log.error(f"  Image generation step failed: {e}")
        for article in analyzed:
            article.setdefault("image_url", None)

    # Step 7: Upload articles
    log.info("Step 7/8 -- Uploading articles to Supabase...")
    try:
        from tools.upload_supabase import upload_articles
        uploaded = upload_articles(analyzed)
        log.info(f"  {uploaded}/{len(analyzed)} articles uploaded")
    except Exception as e:
        log.error(f"  Article upload failed: {e}")

    # Step 8: Typology summaries
    log.info("Step 8/8 -- Generating typology summaries...")
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

    log.info("=" * 65)
    log.info(f"Pipeline complete -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 65)


if __name__ == "__main__":
    run_pipeline()
