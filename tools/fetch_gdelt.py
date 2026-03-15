"""
Fetch AML/financial crime news from GDELT Project API.
GDELT indexes news from 65+ languages and thousands of sources globally,
including regional outlets (uniindia.com, novanews.co.za, etc.) missed by Tavily/NewsAPI.
Free — no API key required.

API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

import time
import requests
from datetime import datetime, timedelta, timezone

GDELT_RATE_LIMIT_SECS = 5  # GDELT enforces 1 request per 5 seconds

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
LOOKBACK_DAYS = 3      # GDELT is near-real-time; 3 days is sufficient
MAX_RESULTS = 50       # per query
REQUEST_TIMEOUT = 15

# AML-focused queries for GDELT
# Each query targets a specific typology/theme
GDELT_QUERIES = [
    # Core AML enforcement
    '"money laundering" arrest',
    '"money laundering" convicted',
    '"money laundering" enforcement fine',
    '"anti-money laundering" penalty investigation',

    # Crypto financial crime
    '"crypto" "money laundering" enforcement',
    '"pig butchering" fraud arrest',

    # Regional — South Asia / Southeast Asia
    '"money laundering" India arrest',
    '"money laundering" Singapore Malaysia enforcement',

    # Africa / Middle East
    '"money laundering" Nigeria enforcement',
    '"money laundering" UAE enforcement',

    # Regulatory
    'FATF "grey list" AML',
    'AUSTRAC enforcement penalty',

    # Sanctions
    '"sanctions evasion" enforcement',
    '"OFAC" sanctions designation',
]


def _gdelt_search(query: str, lookback_days: int) -> list[dict]:
    """Run a single GDELT Doc 2.0 API query. Returns list of article dicts."""
    # GDELT uses YYYYMMDDHHMMSS format for time range
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=lookback_days)
    startdatetime = start_dt.strftime("%Y%m%d%H%M%S")
    enddatetime = end_dt.strftime("%Y%m%d%H%M%S")

    params = {
        "query":         f"{query} sourcelang:eng",  # English articles only
        "mode":          "artlist",          # article list mode
        "maxrecords":    MAX_RESULTS,
        "startdatetime": startdatetime,
        "enddatetime":   enddatetime,
        "sort":          "DateDesc",
        "format":        "json",
    }

    try:
        resp = requests.get(GDELT_API, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []  # GDELT returned non-JSON (rate limit page, maintenance, etc.)
        articles_raw = data.get("articles", []) or []

        results = []
        for item in articles_raw:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue

            # Parse GDELT date: "20260313T150000Z" or similar
            raw_date = item.get("seendate", "")
            published = ""
            if raw_date:
                try:
                    # GDELT format: YYYYMMDDTHHMMSSZ
                    dt = datetime.strptime(raw_date[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                    published = dt.strftime("%Y-%m-%d")
                except Exception:
                    print(f"  [GDELT] Date parse failed for '{title[:60]}' (raw: '{raw_date}') — URL: {url[:80]}")
            else:
                print(f"  [GDELT] No date for '{title[:60]}' — URL: {url[:80]}")

            source_name = item.get("domain", "")
            language = item.get("language", "English")

            # Skip non-English for now (translation adds complexity)
            if language and language.lower() not in ("english", "en", ""):
                continue

            results.append({
                "title":        title,
                "url":          url,
                "source":       source_name,
                "country":      None,   # Let AI determine from content
                "region":       None,
                "published_at": published,
                "description":  "",     # GDELT doesn't return snippets in artlist mode
                "content":      "",     # Will be scraped by analyze_articles.py
                "api_source": "gdelt",
            })
        return results

    except Exception as e:
        print(f"[GDELT] Query failed '{query[:50]}': {e}")
        return []


def fetch_gdelt_articles() -> list[dict]:
    """
    Run all GDELT queries and return deduplicated article list.
    Designed to catch regional news that Tavily/NewsAPI miss.
    """
    all_articles = []
    seen_urls: set[str] = set()

    print(f"[GDELT] Running {len(GDELT_QUERIES)} queries (last {LOOKBACK_DAYS} days, 5s between requests)...")
    for i, query in enumerate(GDELT_QUERIES):
        if i > 0:
            time.sleep(GDELT_RATE_LIMIT_SECS)
        articles = _gdelt_search(query, LOOKBACK_DAYS)
        new = [a for a in articles if a["url"] not in seen_urls]
        seen_urls.update(a["url"] for a in new)
        if new:
            print(f"  [GDELT] '{query[:55]}' -> {len(new)} articles")
        all_articles.extend(new)

    print(f"[GDELT] Total: {len(all_articles)} unique articles")
    return all_articles


if __name__ == "__main__":
    articles = fetch_gdelt_articles()
    for a in articles[:10]:
        print(f"  [{a['source']}] {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
