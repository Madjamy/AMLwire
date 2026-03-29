"""
Fetch AML news from NewsData.io — Non-Western country coverage using crime category filter.

NewsData.io is the ONLY API with an explicit category=crime filter,
which produces different results from keyword-only APIs.
Focuses on countries underserved by NewsAPI (India, SE Asia, ME, Africa).

API: https://newsdata.io/documentation
Quota: 200 credits/day (each request = 1 credit, returns up to 10 articles).
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

NEWSDATA_API_KEY = os.getenv("newsdata_API_KEY") or os.getenv("NEWSDATA_API_KEY")
NEWSDATA_URL = "https://newsdata.io/api/1/latest"

TOPIC_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "sanctions", "financial crime",
    "fraud", "enforcement", "penalty", "fine", "arrest", "convicted", "prosecution",
    "crypto", "hawala", "shell company", "typology", "proceeds", "forfeiture",
    "terrorist financing", "cybercrime", "ransomware", "scam", "compliance",
]

# Country code -> country name mapping
COUNTRY_MAP = {
    "in": "India",
    "sg": "Singapore",
    "ae": "UAE",
    "jp": "Japan",
    "my": "Malaysia",
    "id": "Indonesia",
    "ng": "Nigeria",
    "za": "South Africa",
    "au": "Australia",
    "pk": "Pakistan",
    "ph": "Philippines",
    "gb": "United Kingdom",
    "ca": "Canada",
    "de": "Germany",
    "fr": "France",
}

# Max articles per country from NewsData (prevents India domination)
COUNTRY_MAX = {
    "in": 5,   # India: was returning 100+ uncapped
}
DEFAULT_COUNTRY_MAX = 10

# Query plan: ~22 credits/day (of 200 quota)
NEWSDATA_QUERIES = [
    # India — tightened queries, AML-specific only
    {"country": "in", "q": "money laundering OR enforcement directorate PMLA"},
    {"country": "in", "q": "hawala OR terror financing OR ED arrest"},
    # Singapore
    {"country": "sg", "q": "money laundering OR MAS enforcement"},
    {"country": "sg", "q": "financial crime OR fraud arrest"},
    # UAE
    {"country": "ae", "q": "money laundering OR sanctions OR CBUAE"},
    {"country": "ae", "q": "financial crime OR fraud Dubai"},
    # Japan
    {"country": "jp", "q": "money laundering OR financial crime"},
    {"country": "jp", "q": "fraud OR JAFIC enforcement"},
    # Malaysia
    {"country": "my", "q": "money laundering OR BNM"},
    # Indonesia
    {"country": "id", "q": "money laundering OR PPATK"},
    # Nigeria
    {"country": "ng", "q": "money laundering OR EFCC"},
    # South Africa
    {"country": "za", "q": "money laundering OR FIC"},
    # Australia
    {"country": "au", "q": "money laundering OR AUSTRAC"},
    {"country": "au", "q": "fraud OR financial crime Australia"},
    # Pakistan
    {"country": "pk", "q": "money laundering OR financial crime"},
    # Philippines
    {"country": "ph", "q": "money laundering OR AMLC"},
    # UK
    {"country": "gb", "q": "money laundering OR FCA enforcement"},
    # Canada
    {"country": "ca", "q": "money laundering OR FINTRAC enforcement"},
    # Germany
    {"country": "de", "q": "money laundering OR BaFin enforcement"},
    # France
    {"country": "fr", "q": "money laundering OR Tracfin enforcement"},
]


def _is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in TOPIC_KEYWORDS)


def fetch_newsdata_articles() -> list[dict]:
    """Fetch crime-category articles from NewsData.io for underserved countries."""
    if not NEWSDATA_API_KEY:
        print("[NewsData] No API key set -- skipping")
        return []

    seen_urls: set[str] = set()
    results: list[dict] = []
    country_article_counts: dict[str, int] = {}

    print(f"[NewsData] Running {len(NEWSDATA_QUERIES)} queries with crime category filter...")
    for spec in NEWSDATA_QUERIES:
        country_code = spec["country"]
        country_name = COUNTRY_MAP.get(country_code, country_code.upper())
        max_for_country = COUNTRY_MAX.get(country_code, DEFAULT_COUNTRY_MAX)
        if country_article_counts.get(country_code, 0) >= max_for_country:
            print(f"  [NewsData] {country_name} hit cap ({max_for_country}), skipping remaining queries")
            continue
        try:
            params = {
                "apikey": NEWSDATA_API_KEY,
                "q": spec["q"],
                "country": country_code,
                "category": "crime",
                "language": "en",
            }
            resp = requests.get(NEWSDATA_URL, params=params, timeout=15)
            if resp.status_code == 429:
                print("[NewsData] Rate limited -- stopping")
                break
            if resp.status_code != 200:
                print(f"  [NewsData] {country_code} HTTP {resp.status_code}")
                continue
            data = resp.json()
            if data.get("status") != "success":
                print(f"  [NewsData] {country_code} API error: {data.get('results', {}).get('message', 'unknown')}")
                continue
            articles = data.get("results", []) or []
            added = 0
            for a in articles:
                url = a.get("link", "")
                title = a.get("title", "")
                desc = a.get("description", "") or ""
                content = a.get("content", "") or desc
                if not url or not title or url in seen_urls:
                    continue
                if not _is_relevant(title + " " + desc):
                    continue
                if country_article_counts.get(country_code, 0) >= max_for_country:
                    break
                seen_urls.add(url)
                country_article_counts[country_code] = country_article_counts.get(country_code, 0) + 1
                # Parse date: NewsData returns "YYYY-MM-DD HH:MM:SS" format
                pub_date = (a.get("pubDate") or "")[:10]
                results.append({
                    "title": title,
                    "url": url,
                    "source": a.get("source_name", "") or a.get("source_id", ""),
                    "published_at": pub_date,
                    "description": desc[:500],
                    "content": content[:2000],
                    "api_source": "newsdata",
                    "country": country_name,
                })
                added += 1
            if added:
                print(f"  [NewsData] {country_name} '{spec['q'][:40]}' -> {added} articles")
        except Exception as e:
            print(f"  [NewsData] Error for {country_code}: {e}")

    print(f"[NewsData] Total: {len(results)} unique articles")
    return results


if __name__ == "__main__":
    articles = fetch_newsdata_articles()
    for a in articles[:5]:
        print(f"  [{a['country']}] {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
