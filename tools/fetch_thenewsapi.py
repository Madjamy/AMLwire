"""
Fetch AML news from TheNewsAPI — Precision entity/event searches using AND/OR/NOT syntax.

Uses a pool of ~30 precision queries, rotating 5-7 per day on a 5-day cycle.

API: https://www.thenewsapi.com/documentation
Quota: 1,000/month (~33/day) per key.
"""

import os
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

THENEWSAPI_KEY = os.getenv("thenewsapi_API_KEY") or os.getenv("THENEWSAPI_API_KEY")
THENEWSAPI_URL = "https://api.thenewsapi.com/v1/news/all"

TOPIC_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "sanctions", "financial crime",
    "fraud", "enforcement", "penalty", "fine", "arrest", "convicted", "prosecution",
    "crypto", "hawala", "shell company", "typology", "proceeds", "forfeiture",
    "terrorist financing", "cybercrime", "ransomware", "scam", "compliance",
    "fatf", "fiu", "suspicious", "confiscation",
]

# Pool of 30 precision queries — rotate 6 per day on a 5-day cycle
QUERY_POOL = [
    # Day 0
    'FATF grey list',
    'FATF mutual evaluation',
    'AUSTRAC enforcement penalty',
    'AUSTRAC Tranche 2',
    'FinCEN advisory',
    'OFAC designation',
    # Day 1
    'FCA fine enforcement',
    'Enforcement Directorate PMLA',
    'MAS AML enforcement',
    'sanctions evasion crypto',
    'money laundering conviction Australia',
    'money laundering conviction UK',
    # Day 2
    'money laundering arrest India',
    'pig butchering scam',
    'shell company beneficial ownership',
    'trade based money laundering',
    'terrorist financing enforcement',
    'compliance failure penalty',
    # Day 3
    'money mule arrest',
    'crypto sanctions enforcement',
    'hawala prosecution',
    'deceptive shipping sanctions',
    'correspondent banking enforcement',
    'real estate laundering',
    # Day 4
    'drug trafficking proceeds seizure',
    'human trafficking financial',
    'deepfake fraud',
    'FATF report 2026',
    'ransomware payment',
    'AML reform 2026',
]


def _get_todays_queries() -> list[str]:
    """Pick 6 queries for today based on day-of-year rotation."""
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    cycle_day = day_of_year % 5  # 5-day rotation cycle
    start = cycle_day * 6
    return QUERY_POOL[start:start + 6]


def _is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in TOPIC_KEYWORDS)


def fetch_thenewsapi_articles() -> list[dict]:
    """Fetch precision AML articles from TheNewsAPI with daily rotation."""
    if not THENEWSAPI_KEY:
        print("[TheNewsAPI] No API key set -- skipping")
        return []

    queries = _get_todays_queries()
    seen_urls: set[str] = set()
    results: list[dict] = []

    print(f"[TheNewsAPI] Running {len(queries)} precision queries (day rotation)...")
    for query in queries:
        try:
            params = {
                "api_token": THENEWSAPI_KEY,
                "search": query,
                "language": "en",
                "limit": 5,
            }
            resp = requests.get(THENEWSAPI_URL, params=params, timeout=15)
            if resp.status_code == 429:
                print("[TheNewsAPI] Rate limited -- stopping")
                break
            if resp.status_code == 422:
                print(f"  [TheNewsAPI] Query rejected: '{query[:40]}'")
                continue
            if resp.status_code != 200:
                continue
            data = resp.json()
            articles = data.get("data", []) or []
            added = 0
            for a in articles:
                url = a.get("url", "")
                title = a.get("title", "")
                desc = a.get("description", "") or a.get("snippet", "") or ""
                if not url or not title or url in seen_urls:
                    continue
                if not _is_relevant(title + " " + desc):
                    continue
                seen_urls.add(url)
                # TheNewsAPI date format: "2026-03-18T10:30:00.000000Z"
                pub_date = (a.get("published_at") or "")[:10]
                results.append({
                    "title": title,
                    "url": url,
                    "source": a.get("source", ""),
                    "published_at": pub_date,
                    "description": desc[:500],
                    "content": desc,
                    "api_source": "thenewsapi",
                    "country": None,  # Let AI determine
                })
                added += 1
            if added:
                print(f"  [TheNewsAPI] '{query[:40]}' -> {added} articles")
        except Exception as e:
            print(f"  [TheNewsAPI] Error for '{query[:30]}': {e}")

    print(f"[TheNewsAPI] Total: {len(results)} unique articles")
    return results


if __name__ == "__main__":
    articles = fetch_thenewsapi_articles()
    for a in articles[:5]:
        print(f"  {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
