"""
Fetch AML-related news articles from SerpAPI (Google News).
Used as secondary/supplementary source for regional and niche coverage.
Returns a list of raw article dicts for the last 7 days.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Two API keys for fallback — if key 1 hits rate limit, key 2 is used automatically
SERPAPI_KEYS = [k for k in [os.getenv("SERPAPI_KEY_1"), os.getenv("SERPAPI_KEY_2")] if k]
SERPAPI_URL = "https://serpapi.com/search"

# Regional + niche queries to supplement NewsAPI
SERP_QUERIES = [
    "money laundering enforcement action",
    "AML typology case 2025",
    "sanctions evasion Asia Pacific",
    "financial crime Australia 2025",
    "tax fraud money laundering India",
    "anti-money laundering China enforcement",
    "shell company beneficial ownership fraud",
    "crypto laundering DeFi",
    "human trafficking financial crime",
    "organized crime money laundering Europe",
]

TOPIC_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "sanctions", "tax evasion",
    "tax fraud", "financial crime", "typology", "suspicious transaction", "fatf",
    "shell company", "beneficial ownership", "smurfing", "structuring", "layering",
    "trade-based money laundering", "crypto mixing", "mule account", "terror finance",
    "human trafficking", "drug trafficking", "organized crime", "cybercrime",
    "enforcement action", "compliance failure", "suspicious activity report",
    "darknet", "sanctions evasion",
]

CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=7)


def is_topic_relevant(article: dict) -> bool:
    text = (
        (article.get("title") or "") + " " +
        (article.get("snippet") or "")
    ).lower()
    return any(kw in text for kw in TOPIC_KEYWORDS)


def parse_serp_date(date_str: str | None) -> str:
    """Return ISO date string or empty string if unparseable."""
    if not date_str:
        return ""
    # SerpAPI typically returns dates like "3 days ago", "Mar 8, 2025", or ISO
    try:
        # Try ISO first
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        pass
    # Try common format
    try:
        dt = datetime.strptime(date_str, "%b %d, %Y")
        return dt.isoformat()
    except ValueError:
        pass
    return date_str


def _is_rate_limited(resp: requests.Response) -> bool:
    """Detect SerpAPI rate limit: HTTP 429 or error message in response."""
    if resp.status_code == 429:
        return True
    try:
        data = resp.json()
        error = data.get("error", "").lower()
        return "rate" in error or "limit" in error or "quota" in error
    except Exception:
        return False


def _fetch_query(query: str, api_key: str) -> list | None:
    """
    Fetch a single query with a given key.
    Returns news_results list on success, None on rate limit, raises on other errors.
    """
    params = {
        "engine": "google_news",
        "q": query,
        "api_key": api_key,
        "hl": "en",
        "gl": "us",
        "num": 10,
        "tbs": "qdr:w",  # Restrict to last 7 days on Google's side
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=15)
    if _is_rate_limited(resp):
        return None  # Signal to try next key
    resp.raise_for_status()
    return resp.json().get("news_results", [])


def fetch_articles() -> list[dict]:
    """
    Query SerpAPI Google News for AML-related articles.
    Automatically falls back from key 1 to key 2 if rate limited.
    Returns a deduplicated list of raw article dicts.
    """
    if not SERPAPI_KEYS:
        raise ValueError("No SERPAPI_KEY_1 or SERPAPI_KEY_2 set in .env")

    seen_urls = set()
    results = []
    active_key_idx = 0

    for query in SERP_QUERIES:
        news_results = None
        for idx in range(active_key_idx, len(SERPAPI_KEYS)):
            try:
                news_results = _fetch_query(query, SERPAPI_KEYS[idx])
                if news_results is None:
                    print(f"[SerpAPI] Key {idx + 1} rate limited — switching to key {idx + 2}")
                    active_key_idx = idx + 1
                    continue
                active_key_idx = idx  # Stick with this key
                break
            except Exception as e:
                print(f"[SerpAPI] Key {idx + 1} error on '{query}': {e}")
                break

        if news_results is None:
            print(f"[SerpAPI] All keys exhausted for query '{query}' — skipping")
            continue

        for item in news_results:
            url = item.get("link", "")
            if not url or url in seen_urls:
                continue
            if not is_topic_relevant(item):
                continue
            seen_urls.add(url)
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "source": item.get("source", {}).get("name", "") if isinstance(item.get("source"), dict) else item.get("source", ""),
                "published_at": parse_serp_date(item.get("date", "")),
                "description": item.get("snippet", ""),
                "content": "",
                "api_source": "serpapi",
            })

    print(f"[SerpAPI] Fetched {len(results)} relevant articles (used key {active_key_idx + 1})")
    return results


if __name__ == "__main__":
    articles = fetch_articles()
    for a in articles[:5]:
        print(f"  - {a['title']} ({a['source']})")
