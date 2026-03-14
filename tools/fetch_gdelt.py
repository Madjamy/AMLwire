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
    '"money laundering" arrest OR convicted OR fined OR enforcement',
    '"AML" "enforcement action" OR "compliance failure" OR "suspicious transaction"',
    '"anti-money laundering" penalty OR sanction OR investigation',

    # Crypto financial crime
    '"crypto" "money laundering" OR "ransomware" OR "pig butchering" enforcement',
    '"cryptocurrency" fraud OR laundering arrest OR seizure',

    # Regional coverage — South Asia / Southeast Asia
    '"money laundering" India OR Pakistan OR Bangladesh OR Sri Lanka',
    '"financial crime" Singapore OR Malaysia OR Indonesia OR Philippines OR Vietnam',
    '"AML" Japan OR "South Korea" OR "Hong Kong" OR Taiwan',

    # Africa / Middle East
    '"money laundering" Nigeria OR "South Africa" OR Kenya OR Ghana OR Egypt',
    '"financial crime" UAE OR "Saudi Arabia" OR Qatar OR Kuwait',

    # Emerging crime
    '"pig butchering" OR "romance scam" crypto fraud',
    '"business email compromise" OR "BEC fraud" wire transfer',
    '"hawala" OR "informal value transfer" money laundering',

    # Regulatory actions
    'FATF "grey list" OR "mutual evaluation" AML',
    '"FinCEN" OR "AUSTRAC" OR "FCA" enforcement penalty fine',
    '"Interpol" OR "Europol" financial crime arrest seizure',

    # TBML and trade crime
    '"trade-based money laundering" OR "invoice fraud" customs',
    '"shell company" beneficial owner fraud prosecution',

    # Sanctions
    '"sanctions evasion" OR "sanctions violation" enforcement',
    '"OFAC" sanctions designation OR violation',
]


def _gdelt_search(query: str, lookback_days: int) -> list[dict]:
    """Run a single GDELT Doc 2.0 API query. Returns list of article dicts."""
    # GDELT uses YYYYMMDDHHMMSS format for time range
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=lookback_days)
    startdatetime = start_dt.strftime("%Y%m%d%H%M%S")
    enddatetime = end_dt.strftime("%Y%m%d%H%M%S")

    params = {
        "query":         query,
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
        data = resp.json()
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
                    pass

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
                "fetch_source": "gdelt",
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
