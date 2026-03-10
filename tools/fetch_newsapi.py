"""
Fetch AML-related news articles from NewsAPI.
Returns a list of raw article dicts for the last 7 days.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Two API keys for fallback — if key 1 hits rate limit, key 2 is used automatically
NEWSAPI_KEYS = [k for k in [os.getenv("NEWSAPI_KEY_1"), os.getenv("NEWSAPI_KEY_2")] if k]
NEWSAPI_URL = "https://newsapi.org/v2/everything"

AML_QUERIES = [
    # Core AML
    "money laundering",
    "AML enforcement",
    "anti-money laundering compliance",
    "financial crime typology",
    "FATF compliance evaluation",
    "suspicious transaction report",
    # Sanctions
    "sanctions violation evasion",
    "OFAC SDN sanctions enforcement",
    # Tax crimes
    "tax evasion fraud prosecution",
    "offshore tax fraud money laundering",
    # Shell companies and ownership
    "shell company beneficial ownership fraud",
    "nominee director money laundering",
    # Crypto
    "crypto money laundering enforcement",
    "cryptocurrency sanctions evasion",
    # Human trafficking financial
    "human trafficking money laundering proceeds",
    # Drug trafficking financial
    "drug trafficking money laundering seizure",
    # Hawala and informal transfer
    "hawala money transfer illegal",
    # Trade-based laundering
    "trade based money laundering invoice fraud",
    # Real estate
    "real estate money laundering property",
    # Professional enablers
    "accountant lawyer money laundering enabler",
    # Terror finance
    "terror financing AML enforcement",
    # Organized crime
    "organized crime money laundering",
    # Cyber fraud financial
    "cybercrime fraud money laundering",
    # Corruption / PEP
    "politically exposed person bribery corruption",
]

TOPIC_KEYWORDS = [
    # Core AML
    "money laundering", "anti money laundering", "aml", "financial crime", "illicit finance",
    # Suspicious reporting
    "suspicious transaction report", "suspicious activity report", "sar",
    # FATF / regulators
    "fatf", "mutual evaluation", "grey list",
    # Typologies
    "smurfing", "structuring", "layering", "trade based money laundering", "tbml",
    # Shell companies
    "shell company", "beneficial ownership", "beneficial owner", "nominee director",
    # Crypto laundering
    "crypto laundering", "crypto mixer", "tornado cash", "blockchain laundering",
    # Sanctions
    "sanctions evasion", "sanctions violation", "ofac", "sdn list",
    # Terror finance
    "terrorist financing", "terror finance", "terror funding",
    # Organized crime
    "organized crime", "criminal syndicate", "cartel",
    # Drug trafficking
    "drug trafficking", "narco trafficking",
    # Human trafficking
    "human trafficking", "modern slavery finance",
    # Cybercrime
    "cybercrime", "ransomware", "darknet", "dark web",
    # Professional enablers
    "professional enabler", "trust service provider",
    # Real estate
    "property laundering", "real estate laundering",
    # Corruption
    "politically exposed person", "pep", "bribery", "corruption",
    # Enforcement
    "financial penalty", "compliance failure", "regulatory fine",
    "deferred prosecution", "conviction", "settlement",
    # Asset recovery
    "asset seizure", "asset forfeiture", "confiscation", "proceeds of crime",
    # Additional
    "hawala", "tax evasion", "tax fraud", "sanctions",
]


def is_topic_relevant(article: dict) -> bool:
    """Check if an article is relevant to AML/financial crime topics."""
    text = (
        (article.get("title") or "") + " " +
        (article.get("description") or "") + " " +
        (article.get("content") or "")
    ).lower()
    return any(kw in text for kw in TOPIC_KEYWORDS)


def _is_rate_limited(resp: requests.Response) -> bool:
    """Detect NewsAPI rate limit: HTTP 429 or error code 'rateLimited'."""
    if resp.status_code == 429:
        return True
    try:
        data = resp.json()
        return data.get("code") == "rateLimited"
    except Exception:
        return False


def _fetch_query(query: str, from_date: str, api_key: str) -> list | None:
    """
    Fetch a single query with a given key.
    Returns article list on success, None on rate limit, raises on other errors.
    """
    params = {
        "q": query,
        "from": from_date,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 10,
        "apiKey": api_key,
    }
    resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
    if _is_rate_limited(resp):
        return None  # Signal to try next key
    resp.raise_for_status()
    return resp.json().get("articles", [])


def fetch_articles() -> list[dict]:
    """
    Query NewsAPI for AML-related articles from the last 7 days.
    Automatically falls back from key 1 to key 2 if rate limited.
    Returns a deduplicated list of raw article dicts.
    """
    if not NEWSAPI_KEYS:
        raise ValueError("No NEWSAPI_KEY_1 or NEWSAPI_KEY_2 set in .env")

    from_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    seen_urls = set()
    results = []
    active_key_idx = 0

    for query in AML_QUERIES:
        articles = None
        # Try each key in order until one works
        for idx in range(active_key_idx, len(NEWSAPI_KEYS)):
            try:
                articles = _fetch_query(query, from_date, NEWSAPI_KEYS[idx])
                if articles is None:
                    print(f"[NewsAPI] Key {idx + 1} rate limited — switching to key {idx + 2}")
                    active_key_idx = idx + 1
                    continue
                active_key_idx = idx  # Stick with this key
                break
            except Exception as e:
                print(f"[NewsAPI] Key {idx + 1} error on '{query}': {e}")
                break

        if articles is None:
            print(f"[NewsAPI] All keys exhausted for query '{query}' — skipping")
            continue

        for article in articles:
            url = article.get("url", "")
            if not url or url in seen_urls:
                continue
            if not is_topic_relevant(article):
                continue
            seen_urls.add(url)
            results.append({
                "title": article.get("title", ""),
                "url": url,
                "source": article.get("source", {}).get("name", ""),
                "published_at": article.get("publishedAt", ""),
                "description": article.get("description", ""),
                "content": article.get("content", ""),
                "api_source": "newsapi",
            })

    print(f"[NewsAPI] Fetched {len(results)} relevant articles (used key {active_key_idx + 1})")
    return results


if __name__ == "__main__":
    articles = fetch_articles()
    for a in articles[:5]:
        print(f"  - {a['title']} ({a['source']})")
