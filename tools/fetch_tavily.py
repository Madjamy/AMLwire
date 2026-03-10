"""
Fetch AML-related news articles from Tavily Search API.
Returns full article content (not just snippets) for the last 7 days.
Covers topics that NewsAPI misses: human trafficking, hawala, TBML, drug trafficking, etc.
"""

import os
import re
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"

# Queries targeting AML topics NOT well-covered by NewsAPI keyword searches
TAVILY_QUERIES = [
    # Core AML
    "money laundering enforcement action 2026",
    "anti-money laundering compliance failure fine",
    "AML typology financial crime new method",

    # Human trafficking financial angle
    "human trafficking financial crime money laundering",
    "human smuggling proceeds laundering enforcement",

    # Drug trafficking financial flows
    "drug trafficking money laundering seizure arrest",
    "narco finance cartel money laundering",

    # Hawala and informal value transfer
    "hawala money transfer illegal enforcement",
    "informal value transfer AML investigation",

    # Trade-based money laundering
    "trade based money laundering TBML invoice fraud",
    "over-invoicing under-invoicing trade fraud AML",

    # Real estate laundering
    "real estate money laundering property purchase",
    "luxury property money laundering enforcement",

    # Crypto and DeFi
    "crypto money laundering DeFi enforcement",
    "cryptocurrency sanctions evasion blockchain",

    # Beneficial ownership and shell companies
    "beneficial ownership concealment shell company fraud",
    "nominee director shell company money laundering",

    # Professional enablers
    "accountant lawyer money laundering enabler conviction",
    "professional enabler AML financial crime",

    # PEP, bribery, corruption
    "politically exposed person bribery corruption financial crime",
    "PEP corruption money laundering",

    # Sanctions
    "sanctions evasion enforcement OFAC SDN",
    "sanctions violation fine penalty 2026",

    # Tax crimes
    "tax evasion money laundering prosecution",
    "tax fraud offshore account concealment",

    # Terror finance
    "terror financing AML enforcement",
    "terrorist financing crypto hawala",

    # Organized crime
    "organized crime financial crime money laundering",
    "criminal network laundering proceeds",

    # Cybercrime financial
    "cybercrime fraud money laundering proceeds",
    "cyber fraud financial crime enforcement",

    # FATF and regulatory
    "FATF evaluation AML deficiency",
    "financial intelligence unit AML action",
]

# Country-specific queries for priority jurisdictions
COUNTRY_QUERIES = {
    "Australia": [
        "AUSTRAC money laundering enforcement Australia",
        "Australia financial crime AML action 2026",
    ],
    "USA": [
        "FinCEN OFAC enforcement action United States money laundering",
        "US Department of Justice financial crime AML 2026",
    ],
    "UK": [
        "FCA NCA money laundering enforcement United Kingdom",
        "UK financial crime AML action 2026",
    ],
    "India": [
        "Enforcement Directorate ED money laundering PMLA India",
        "India financial crime AML hawala ED arrest",
    ],
    "Singapore": [
        "MAS Singapore money laundering AML enforcement",
        "Singapore financial crime CAD AML 2026",
    ],
    "UAE": [
        "UAE Dubai money laundering AML enforcement CBUAE",
        "UAE financial crime sanctions evasion 2026",
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
    "asset seizure", "confiscation", "deferred prosecution", "conviction",
    "arrest", "fine", "penalty", "enforcement", "investigation",
]


def _is_relevant(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in TOPIC_KEYWORDS)


def _extract_date(url: str, content: str) -> str:
    """
    Extract a publish date for Tavily articles (which don't return published_date).
    Priority: URL path date → date in content text → today's date.
    Tavily's days=7 param already guarantees recency, so today is a safe fallback.
    Returns ISO date string YYYY-MM-DD.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Try URL path for embedded dates
    url_patterns = [
        # /2026/03/10 or /2026-03-10 (year/month/day)
        (r"/(20\d\d)[/-](\d{2})[/-](\d{2})", "ymd"),
        # /20260310- (compact)
        (r"/(20\d\d)(\d{2})(\d{2})[/-]", "ymd"),
        # /2026/03/ or /2026/02/ (year/month only — default day to 01)
        (r"/(20\d\d)[/-](\d{2})/", "ym"),
    ]
    for pat, fmt in url_patterns:
        m = re.search(pat, url)
        if m:
            try:
                if fmt == "ymd":
                    y, mo, d = m.group(1), m.group(2), m.group(3)
                else:
                    y, mo, d = m.group(1), m.group(2), "01"
                dt = datetime.strptime(f"{y}-{mo}-{d}", "%Y-%m-%d")
                if datetime(2020, 1, 1) <= dt <= datetime.now():
                    return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2. Try content text for month-name dates
    # Matches: "March 10, 2026" / "10 March 2026" / "Mar 10 2026" / "March 10th, 2026"
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    content_patterns = [
        # "March 10, 2026" or "March 10th, 2026"
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b",
        # "10 March 2026"
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(20\d\d)\b",
        # ISO in text: "2026-03-10"
        r"\b(20\d\d)-(\d{2})-(\d{2})\b",
    ]
    for i, pat in enumerate(content_patterns):
        m = re.search(pat, content[:2000], re.IGNORECASE)
        if m:
            try:
                if i == 0:   # Month DD, YYYY
                    mo = month_map[m.group(1)[:3].lower()]
                    return f"{m.group(3)}-{mo}-{int(m.group(2)):02d}"
                elif i == 1:  # DD Month YYYY
                    mo = month_map[m.group(2)[:3].lower()]
                    return f"{m.group(3)}-{mo}-{int(m.group(1)):02d}"
                elif i == 2:  # ISO
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            except (KeyError, ValueError):
                pass

    # 3. Fall back to today (safe: Tavily days=7 guarantees recency)
    return today


def _search(query: str, days: int = 7, country_tag: str = "") -> list[dict]:
    """Execute a single Tavily search and return standardised article dicts."""
    if not TAVILY_API_KEY:
        return []
    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "topic": "news",            # news only — filters out reference/educational pages
            "search_depth": "basic",
            "max_results": 8,
            "days": days,
            "include_raw_content": False,
            "include_answer": False,
        }
        resp = requests.post(TAVILY_URL, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            url = item.get("url", "")
            title = item.get("title", "")
            content = item.get("content", "")

            if not url or not title:
                continue

            text = (title + " " + content).lower()
            if not _is_relevant(text):
                continue

            # Tavily doesn't return published_date — extract from URL/content or use today
            published = _extract_date(url, content)

            results.append({
                "title": title,
                "url": url,
                "source": item.get("source", ""),
                "published_at": published,
                "description": content[:500] if content else "",
                "content": content,
                "api_source": "tavily",
                **({"country": country_tag} if country_tag else {}),
            })
        return results

    except Exception as e:
        print(f"[Tavily] Error on '{query}': {e}")
        return []


def fetch_articles() -> list[dict]:
    """
    Fetch AML news via Tavily for the last 7 days.
    Covers topic gaps not well-served by NewsAPI.
    Returns deduplicated list of article dicts with full content.
    """
    if not TAVILY_API_KEY:
        print("[Tavily] TAVILY_API_KEY not set — skipping Tavily fetch")
        return []

    seen_urls = set()
    results = []

    # Global topic queries
    for query in TAVILY_QUERIES:
        for article in _search(query, days=7):
            if article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                results.append(article)

    print(f"[Tavily] Global fetch: {len(results)} articles")

    # Country-specific queries (top 5 per country)
    country_total = 0
    for country, queries in COUNTRY_QUERIES.items():
        country_articles = []
        for query in queries:
            for article in _search(query, days=7, country_tag=country):
                if article["url"] not in seen_urls and len(country_articles) < 5:
                    seen_urls.add(article["url"])
                    country_articles.append(article)
            if len(country_articles) >= 5:
                break
        results.extend(country_articles)
        country_total += len(country_articles)
        print(f"[Tavily] {country}: {len(country_articles)} articles")

    print(f"[Tavily] Total fetched: {len(results)} articles ({country_total} country-specific)")
    return results


if __name__ == "__main__":
    articles = fetch_articles()
    for a in articles[:10]:
        tag = f"[{a['country']}] " if a.get("country") else ""
        print(f"  {tag}{a['title'][:80]} ({a['source']})")
