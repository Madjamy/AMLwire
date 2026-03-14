"""
AMLWire Backfill Script
Fetches articles for a specific historical date range and runs the full pipeline.
Usage:
    python backfill.py 2026-03-01 2026-03-09
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
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


def fetch_newsapi_range(from_date: str, to_date: str) -> list[dict]:
    """Fetch NewsAPI articles for a specific date range."""
    import os
    import requests
    from dotenv import load_dotenv
    load_dotenv()

    NEWSAPI_KEYS = [k for k in [os.getenv("NEWSAPI_KEY_1"), os.getenv("NEWSAPI_KEY_2")] if k]
    NEWSAPI_URL = "https://newsapi.org/v2/everything"
    AML_QUERIES = [
        "money laundering",
        "AML enforcement anti-money laundering",
        "financial crime sanctions",
        "tax evasion fraud",
        "shell company beneficial ownership",
        "crypto laundering",
        "FATF compliance",
        "suspicious transaction report",
        "terror finance hawala",
        "organized crime financial",
    ]
    TOPIC_KEYWORDS = [
        "money laundering", "aml", "anti-money laundering", "sanctions", "tax evasion",
        "tax fraud", "financial crime", "typology", "suspicious transaction", "fatf",
        "shell company", "beneficial ownership", "smurfing", "structuring", "layering",
        "crypto mixing", "mule account", "terror finance", "human trafficking",
        "drug trafficking", "organized crime", "cybercrime", "enforcement action",
        "compliance failure", "darknet", "sanctions evasion",
    ]

    seen_urls = set()
    results = []

    for query in AML_QUERIES:
        for key in NEWSAPI_KEYS:
            try:
                params = {
                    "q": query,
                    "from": from_date,
                    "to": to_date,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 20,
                    "apiKey": key,
                }
                resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
                if resp.status_code == 429 or resp.json().get("code") == "rateLimited":
                    continue
                resp.raise_for_status()
                for a in resp.json().get("articles", []):
                    url = a.get("url", "")
                    text = (a.get("title", "") + " " + a.get("description", "")).lower()
                    if not url or url in seen_urls:
                        continue
                    if not any(kw in text for kw in TOPIC_KEYWORDS):
                        continue
                    seen_urls.add(url)
                    results.append({
                        "title": a.get("title", ""),
                        "url": url,
                        "source": a.get("source", {}).get("name", ""),
                        "published_at": a.get("publishedAt", ""),
                        "description": a.get("description", ""),
                        "content": a.get("content", ""),
                        "api_source": "newsapi",
                    })
                break
            except Exception as e:
                log.warning(f"NewsAPI error on '{query}': {e}")
                continue

    log.info(f"  [NewsAPI backfill] {len(results)} articles for {from_date} to {to_date}")
    return results


def fetch_serpapi_range(from_date: str, to_date: str) -> list[dict]:
    """Fetch SerpAPI articles for a specific date range using custom date filter."""
    import os
    import requests
    from dotenv import load_dotenv
    load_dotenv()

    SERPAPI_KEYS = [k for k in [os.getenv("SERPAPI_KEY_1"), os.getenv("SERPAPI_KEY_2")] if k]
    SERPAPI_URL = "https://serpapi.com/search"
    SERP_QUERIES = [
        "money laundering enforcement action",
        "AML financial crime 2026",
        "sanctions evasion Asia Pacific",
        "financial crime Australia",
        "tax fraud money laundering India",
        "shell company beneficial ownership fraud",
        "crypto laundering DeFi",
        "human trafficking financial crime",
        "organized crime money laundering Europe",
        "financial crime Singapore UAE UK USA",
    ]
    TOPIC_KEYWORDS = [
        "money laundering", "aml", "anti-money laundering", "sanctions", "tax evasion",
        "tax fraud", "financial crime", "typology", "suspicious transaction", "fatf",
        "shell company", "beneficial ownership", "smurfing", "structuring", "layering",
        "crypto mixing", "mule account", "terror finance", "human trafficking",
        "drug trafficking", "organized crime", "cybercrime", "enforcement action",
        "darknet", "sanctions evasion",
    ]

    # Convert YYYY-MM-DD to M/D/YYYY for SerpAPI tbs param
    d_from = datetime.strptime(from_date, "%Y-%m-%d")
    d_to = datetime.strptime(to_date, "%Y-%m-%d")
    tbs = f"cdr:1,cd_min:{d_from.month}/{d_from.day}/{d_from.year},cd_max:{d_to.month}/{d_to.day}/{d_to.year}"

    seen_urls = set()
    results = []
    active_key_idx = 0

    for query in SERP_QUERIES:
        for idx in range(active_key_idx, len(SERPAPI_KEYS)):
            try:
                params = {
                    "engine": "google_news",
                    "q": query,
                    "api_key": SERPAPI_KEYS[idx],
                    "hl": "en",
                    "gl": "us",
                    "num": 10,
                    "tbs": tbs,
                }
                resp = requests.get(SERPAPI_URL, params=params, timeout=15)
                if resp.status_code == 429:
                    active_key_idx = idx + 1
                    continue
                resp.raise_for_status()
                data = resp.json()
                error = data.get("error", "").lower()
                if "rate" in error or "limit" in error or "quota" in error:
                    active_key_idx = idx + 1
                    continue
                active_key_idx = idx
                for item in data.get("news_results", []):
                    url = item.get("link", "")
                    text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
                    if not url or url in seen_urls:
                        continue
                    if not any(kw in text for kw in TOPIC_KEYWORDS):
                        continue
                    seen_urls.add(url)
                    source = item.get("source", {})
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "source": source.get("name", "") if isinstance(source, dict) else str(source),
                        "published_at": item.get("date", ""),
                        "description": item.get("snippet", ""),
                        "content": "",
                        "api_source": "serpapi",
                    })
                break
            except Exception as e:
                log.warning(f"SerpAPI error on '{query}': {e}")
                break

    log.info(f"  [SerpAPI backfill] {len(results)} articles for {from_date} to {to_date}")
    return results


def fetch_country_range(from_date: str, to_date: str) -> list[dict]:
    """Fetch country-specific articles for a date range."""
    import os
    import requests
    from dotenv import load_dotenv
    load_dotenv()

    NEWSAPI_KEYS = [k for k in [os.getenv("NEWSAPI_KEY_1"), os.getenv("NEWSAPI_KEY_2")] if k]
    NEWSAPI_URL = "https://newsapi.org/v2/everything"
    COUNTRY_QUERIES = {
        "Australia": ["Australia AUSTRAC money laundering", "Australian AML financial crime enforcement"],
        "USA": ["US FinCEN OFAC money laundering enforcement", "American AML financial crime"],
        "UK": ["UK FCA NCA money laundering enforcement", "Britain AML financial crime"],
        "India": ["India Enforcement Directorate money laundering PMLA", "India ED hawala sanctions"],
        "Singapore": ["Singapore MAS CAD money laundering enforcement", "Singapore AML financial crime"],
        "UAE": ["UAE CBUAE Dubai money laundering enforcement", "UAE AML financial crime"],
    }
    TOPIC_KEYWORDS = ["money laundering", "aml", "financial crime", "sanctions", "enforcement",
                      "tax fraud", "typology", "shell company", "crypto", "fraud"]

    seen_urls = set()
    results = []

    for country, queries in COUNTRY_QUERIES.items():
        country_articles = []
        for query in queries:
            if len(country_articles) >= 5:
                break
            for key in NEWSAPI_KEYS:
                try:
                    params = {
                        "q": query,
                        "from": from_date,
                        "to": to_date,
                        "sortBy": "publishedAt",
                        "language": "en",
                        "pageSize": 10,
                        "apiKey": key,
                    }
                    resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
                    if resp.status_code == 429 or resp.json().get("code") == "rateLimited":
                        continue
                    resp.raise_for_status()
                    for a in resp.json().get("articles", []):
                        url = a.get("url", "")
                        text = (a.get("title", "") + " " + a.get("description", "")).lower()
                        if not url or url in seen_urls or not any(kw in text for kw in TOPIC_KEYWORDS):
                            continue
                        seen_urls.add(url)
                        country_articles.append({
                            "title": a.get("title", ""),
                            "url": url,
                            "source": a.get("source", {}).get("name", ""),
                            "published_at": a.get("publishedAt", ""),
                            "description": a.get("description", ""),
                            "content": a.get("content", ""),
                            "api_source": "newsapi",
                            "country": country,
                        })
                        if len(country_articles) >= 5:
                            break
                    break
                except Exception as e:
                    log.warning(f"[Country:{country}] NewsAPI error: {e}")
        log.info(f"  {country}: {len(country_articles[:5])} articles")
        results.extend(country_articles[:5])

    return results


def run_backfill(from_date: str, to_date: str):
    log.info("=" * 65)
    log.info(f"AMLWire BACKFILL: {from_date} to {to_date}")
    log.info("=" * 65)

    # Fetch
    log.info("Fetching NewsAPI...")
    newsapi = fetch_newsapi_range(from_date, to_date)

    log.info("Fetching SerpAPI...")
    serpapi = fetch_serpapi_range(from_date, to_date)

    log.info("Fetching country-specific articles...")
    country = fetch_country_range(from_date, to_date)

    all_articles = newsapi + serpapi + country
    log.info(f"Combined: {len(all_articles)} candidate articles")

    if not all_articles:
        log.warning("No articles fetched.")
        return

    # Deduplicate
    log.info("Deduplicating...")
    from tools.deduplicate import deduplicate
    clean = deduplicate(all_articles)
    log.info(f"  {len(clean)} unique new articles")

    if not clean:
        log.info("All articles already in Supabase.")
        return

    # AI Analysis
    log.info(f"AI analysis of {len(clean)} articles...")
    from tools.analyze_articles import analyze_articles
    analyzed = analyze_articles(clean)
    log.info(f"  {len(analyzed)} articles structured")

    if not analyzed:
        log.warning("AI returned no articles.")
        return

    # Enrich
    url_to_raw = {a["url"]: a.get("description", "") for a in clean}
    url_to_country = {a["url"]: a.get("country", "") for a in clean}
    for article in analyzed:
        src = article.get("source_url") or article.get("url", "")
        article["raw_snippet"] = url_to_raw.get(src, "")
        if not article.get("country"):
            article["country"] = url_to_country.get(src) or None

    # Date gate — only keep articles within the requested range (+ 1 day buffer)
    d_from = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d_to = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    fresh = []
    for article in analyzed:
        date_str = article.get("published_date", "")
        if date_str:
            try:
                for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
                        if d_from <= parsed <= d_to:
                            fresh.append(article)
                        else:
                            log.info(f"  Skipping out-of-range ({date_str}): {article.get('title','')[:50]}")
                        break
                    except ValueError:
                        continue
                else:
                    fresh.append(article)
            except Exception:
                fresh.append(article)
        else:
            fresh.append(article)

    log.info(f"  {len(fresh)} articles within date range {from_date} to {to_date}")
    analyzed = fresh

    if not analyzed:
        log.warning("No articles passed date gate.")
        return

    # Upload articles
    log.info("Uploading articles to Supabase...")
    from tools.upload_supabase import upload_articles
    uploaded = upload_articles(analyzed)
    log.info(f"  {uploaded}/{len(analyzed)} articles uploaded")

    # Typology summaries
    log.info("Generating typology summaries...")
    from tools.generate_typology_summary import generate_typology_summaries
    from tools.upload_supabase import upload_typology_summaries
    summaries = generate_typology_summaries(analyzed)
    if summaries:
        upload_typology_summaries(summaries)
        log.info(f"  {len(summaries)} typology summaries uploaded")

    log.info("=" * 65)
    log.info(f"Backfill complete: {uploaded} articles added for {from_date} to {to_date}")
    log.info("=" * 65)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python backfill.py YYYY-MM-DD YYYY-MM-DD")
        print("Example: python backfill.py 2026-03-01 2026-03-09")
        sys.exit(1)
    run_backfill(sys.argv[1], sys.argv[2])
