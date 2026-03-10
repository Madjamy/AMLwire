"""
Fetch country-specific AML news for 6 priority countries.
Returns top 5 most relevant articles per country, tagged with country name.
Uses NewsAPI only. Top 5 articles per country, last 7 days.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

NEWSAPI_KEYS = [k for k in [os.getenv("NEWSAPI_KEY_1"), os.getenv("NEWSAPI_KEY_2")] if k]
NEWSAPI_URL = "https://newsapi.org/v2/everything"

TOP_N = 5  # Articles to return per country

COUNTRY_QUERIES = {
    "Australia": [
        "Australia AUSTRAC money laundering",
        "Australian AML financial crime enforcement",
        "Australia sanctions compliance",
    ],
    "USA": [
        "US FinCEN OFAC money laundering enforcement",
        "American AML financial crime",
        "United States sanctions violation",
    ],
    "UK": [
        "UK FCA NCA money laundering enforcement",
        "Britain AML financial crime",
        "UK sanctions evasion",
    ],
    "India": [
        "India Enforcement Directorate money laundering PMLA",
        "Indian AML financial crime",
        "India ED hawala sanctions",
    ],
    "Singapore": [
        "Singapore MAS CAD money laundering enforcement",
        "Singapore AML financial crime",
        "Singapore sanctions compliance",
    ],
    "UAE": [
        "UAE CBUAE Dubai money laundering enforcement",
        "UAE AML financial crime",
        "Dubai sanctions evasion",
    ],
}

TOPIC_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "sanctions", "tax evasion",
    "tax fraud", "financial crime", "typology", "suspicious transaction", "fatf",
    "shell company", "beneficial ownership", "smurfing", "structuring", "layering",
    "trade-based money laundering", "tbml", "crypto mixing", "mule account",
    "terror finance", "terrorist financing", "human trafficking", "smuggling",
    "drug trafficking", "narco", "organized crime", "cybercrime", "cyber fraud",
    "enforcement action", "compliance failure", "suspicious activity report",
    "darknet", "sanctions evasion", "hawala", "informal value transfer",
    "real estate laundering", "professional enabler", "beneficial owner",
    "pep", "politically exposed", "bribery", "corruption", "proceeds of crime",
    "asset seizure", "confiscation", "deferred prosecution", "conviction", "fraud",
]


def _is_relevant(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in TOPIC_KEYWORDS)


def _newsapi_fetch(query: str, from_date: str, country: str) -> list[dict]:
    """Fetch from NewsAPI for a specific country query. Returns raw article dicts."""
    if not NEWSAPI_KEYS:
        return []
    for key in NEWSAPI_KEYS:
        try:
            params = {
                "q": query,
                "from": from_date,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": 10,
                "apiKey": key,
            }
            resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "rateLimited":
                continue
            articles = []
            for a in data.get("articles", []):
                url = a.get("url", "")
                text = (a.get("title", "") + " " + a.get("description", "")).lower()
                if not url or not _is_relevant(text):
                    continue
                articles.append({
                    "title": a.get("title", ""),
                    "url": url,
                    "source": a.get("source", {}).get("name", ""),
                    "published_at": a.get("publishedAt", ""),
                    "description": a.get("description", ""),
                    "content": a.get("content", ""),
                    "api_source": "newsapi",
                    "country": country,
                })
            return articles
        except Exception as e:
            print(f"[CountryFetch][NewsAPI] Error for '{country}': {e}")
    return []


def fetch_country_articles() -> list[dict]:
    """
    Fetch top 5 AML articles per priority country.
    Uses NewsAPI only. Returns combined list tagged with 'country' field.
    Returns combined list tagged with 'country' field.
    """
    from_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    all_results = []
    seen_urls = set()

    for country, queries in COUNTRY_QUERIES.items():
        country_articles = []
        country_seen = set()

        # Try each query via NewsAPI
        for query in queries:
            if len(country_articles) >= TOP_N:
                break
            fetched = _newsapi_fetch(query, from_date, country)
            for a in fetched:
                if a["url"] not in country_seen and a["url"] not in seen_urls:
                    country_seen.add(a["url"])
                    country_articles.append(a)
                if len(country_articles) >= TOP_N:
                    break

        # Take top N, mark all their URLs as globally seen
        top = country_articles[:TOP_N]
        for a in top:
            seen_urls.add(a["url"])
        all_results.extend(top)
        print(f"[CountryFetch] {country}: {len(top)} articles")

    print(f"[CountryFetch] Total: {len(all_results)} country-specific articles")
    return all_results


if __name__ == "__main__":
    articles = fetch_country_articles()
    for a in articles:
        print(f"  [{a['country']}] {a['title'][:70]}")
