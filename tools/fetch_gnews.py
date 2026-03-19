"""
Fetch AML news from GNews API — Google News wrapper for AU, UK, Canada.

GNews taps into Google's news index which is different from all other APIs' source pools.
Country filter uses Google's geolocation — finds local sources other APIs miss.
Particularly strong for Australian regional media (SMH, The Age, AFR, etc.).

API: https://gnews.io/docs/v4
Quota: 100 requests/day per key.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

GNEWS_API_KEY = os.getenv("gnews_API_KEY") or os.getenv("GNEWS_API_KEY")
GNEWS_URL = "https://gnews.io/api/v4/search"

TOPIC_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "sanctions", "financial crime",
    "fraud", "enforcement", "penalty", "fine", "arrest", "convicted", "prosecution",
    "crypto", "hawala", "shell company", "typology", "proceeds", "forfeiture",
    "terrorist financing", "cybercrime", "ransomware", "scam", "compliance",
]

# Country-filtered queries: 5 per country = 15 queries/day (15% of 100 quota)
GNEWS_QUERIES = {
    "Australia": {
        "country": "au",
        "queries": [
            "AUSTRAC enforcement penalty",
            "money laundering arrest Australia",
            "ASIC penalty fraud",
            "AFP financial crime",
            "scam fraud cyber Australia",
        ],
    },
    "United Kingdom": {
        "country": "gb",
        "queries": [
            "FCA enforcement fine",
            "NCA money laundering",
            "financial crime prosecution UK",
            "SFO fraud bribery",
            "UK sanctions enforcement",
        ],
    },
    "Canada": {
        "country": "ca",
        "queries": [
            "FINTRAC enforcement",
            "money laundering Canada",
            "RCMP financial crime",
            "Canada fraud prosecution",
            "OSFI compliance",
        ],
    },
}


def _is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in TOPIC_KEYWORDS)


def fetch_gnews_articles() -> list[dict]:
    """Fetch AML articles from GNews for AU, UK, Canada."""
    if not GNEWS_API_KEY:
        print("[GNews] No API key set -- skipping")
        return []

    seen_urls: set[str] = set()
    results: list[dict] = []

    total_queries = sum(len(v["queries"]) for v in GNEWS_QUERIES.values())
    print(f"[GNews] Running {total_queries} queries across {len(GNEWS_QUERIES)} countries...")

    for country_name, spec in GNEWS_QUERIES.items():
        country_code = spec["country"]
        for query in spec["queries"]:
            try:
                params = {
                    "q": query,
                    "lang": "en",
                    "country": country_code,
                    "max": 10,
                    "apikey": GNEWS_API_KEY,
                }
                resp = requests.get(GNEWS_URL, params=params, timeout=15)
                if resp.status_code == 429:
                    print("[GNews] Rate limited -- stopping")
                    return results
                if resp.status_code == 403:
                    print("[GNews] Forbidden (key issue) -- stopping")
                    return results
                if resp.status_code != 200:
                    continue
                data = resp.json()
                articles = data.get("articles", [])
                added = 0
                for a in articles:
                    url = a.get("url", "")
                    title = a.get("title", "")
                    desc = a.get("description", "") or ""
                    content = a.get("content", "") or desc
                    if not url or not title or url in seen_urls:
                        continue
                    if not _is_relevant(title + " " + desc):
                        continue
                    seen_urls.add(url)
                    # GNews date format: "2026-03-18T10:30:00Z"
                    pub_date = (a.get("publishedAt") or "")[:10]
                    source_name = a.get("source", {}).get("name", "") if isinstance(a.get("source"), dict) else ""
                    results.append({
                        "title": title,
                        "url": url,
                        "source": source_name,
                        "published_at": pub_date,
                        "description": desc[:500],
                        "content": content[:2000],
                        "api_source": "gnews",
                        "country": country_name,
                    })
                    added += 1
                if added:
                    print(f"  [GNews] {country_name} '{query[:40]}' -> {added} articles")
            except Exception as e:
                print(f"  [GNews] Error for {country_name} '{query[:30]}': {e}")

    print(f"[GNews] Total: {len(results)} unique articles")
    return results


if __name__ == "__main__":
    articles = fetch_gnews_articles()
    for a in articles[:5]:
        print(f"  [{a['country']}] {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
