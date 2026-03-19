"""
Scrape announcements from regulator pages that don't have RSS feeds.

Uses BeautifulSoup to extract article titles, links, and dates from
regulatory body news pages. Graceful degradation — if a site blocks
scraping, we simply get no results.

Regulators publish public interest information and want distribution.
"""

import re
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
TIMEOUT = 15
LOOKBACK_DAYS = 7

AML_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "financial crime",
    "enforcement", "penalty", "fine", "sanction", "fraud", "compliance",
    "suspicious", "crypto", "typology", "proceeds", "forfeiture",
    "arrest", "convicted", "prosecution", "advisory", "guidance",
    "mutual evaluation", "risk assessment", "designation",
]

# Regulator scrape targets
REGULATORS = [
    {
        "name": "MAS Singapore",
        "url": "https://www.mas.gov.sg/news",
        "country": "Singapore",
        "selector": "a.mas-media-summary",
        "title_selector": ".mas-media-summary__title",
        "date_selector": ".mas-media-summary__date",
    },
    {
        "name": "ASIC Australia",
        "url": "https://asic.gov.au/about-asic/news-centre/find-a-media-release/",
        "country": "Australia",
        "selector": "article, .media-release-item, .news-item",
        "title_selector": "h3 a, h2 a, .title a",
        "date_selector": "time, .date, .published",
    },
    {
        "name": "FATF",
        "url": "https://www.fatf-gafi.org/en/publications.html",
        "country": "International",
        "selector": ".news-item, article, .publication-item",
        "title_selector": "h3 a, h2 a, .title a",
        "date_selector": "time, .date, .published",
    },
    {
        "name": "CBUAE",
        "url": "https://www.centralbank.ae/en/news-and-publications/",
        "country": "UAE",
        "selector": ".news-card, article, .post-item",
        "title_selector": "h3 a, h2 a, .card-title a",
        "date_selector": "time, .date, .post-date",
    },
    {
        "name": "ADGM",
        "url": "https://www.adgm.com/media/announcements",
        "country": "UAE",
        "selector": ".announcement-card, article, .news-item",
        "title_selector": "h3 a, h2 a, .title a",
        "date_selector": "time, .date",
    },
]


def _is_aml_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in AML_KEYWORDS)


def _parse_date_text(date_text: str) -> str:
    """Try to parse various date formats from scraped text."""
    date_text = date_text.strip()
    formats = [
        "%d %B %Y",       # "18 March 2026"
        "%B %d, %Y",      # "March 18, 2026"
        "%d %b %Y",       # "18 Mar 2026"
        "%b %d, %Y",      # "Mar 18, 2026"
        "%Y-%m-%d",       # "2026-03-18"
        "%d/%m/%Y",       # "18/03/2026"
        "%m/%d/%Y",       # "03/18/2026"
        "%d.%m.%Y",       # "18.03.2026"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_text, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Try extracting date with regex
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})", date_text, re.I)
    if m:
        month_map = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                     "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        try:
            day, month_str, year = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
            month = month_map.get(month_str, 0)
            if month:
                return f"{year}-{month:02d}-{day:02d}"
        except (ValueError, KeyError):
            pass

    return ""


def _scrape_generic(reg: dict, cutoff: datetime) -> list[dict]:
    """Generic scraper that tries multiple CSS selector strategies."""
    articles = []
    try:
        resp = requests.get(reg["url"], headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  [Scraper] {reg['name']}: HTTP {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, "html.parser")

        # Strategy 1: Use configured selectors
        items = soup.select(reg.get("selector", "article"))
        if not items:
            # Strategy 2: Fall back to common patterns
            items = soup.select("article, .news-item, .media-item, .card, .list-item")

        for item in items[:30]:  # Cap per page to avoid processing noise
            # Find title
            title_el = item.select_one(reg.get("title_selector", "h3 a, h2 a, a"))
            if not title_el:
                # Try the item itself if it's a link
                title_el = item if item.name == "a" else item.find("a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if not title or not link:
                continue

            # Make relative URLs absolute
            if link.startswith("/"):
                from urllib.parse import urljoin
                link = urljoin(reg["url"], link)

            # Find date
            date_el = item.select_one(reg.get("date_selector", "time, .date"))
            date_str = ""
            if date_el:
                date_str = _parse_date_text(date_el.get_text())
                if not date_str:
                    # Try datetime attribute
                    dt_attr = date_el.get("datetime", "")
                    if dt_attr:
                        date_str = dt_attr[:10]

            # AML relevance check
            if not _is_aml_relevant(title):
                continue

            # Date filter
            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                except ValueError:
                    pass

            articles.append({
                "title": title,
                "url": link,
                "source": reg["name"],
                "published_at": date_str,
                "description": "",
                "content": "",
                "api_source": "regulator_scrape",
                "country": reg["country"],
            })

    except Exception as e:
        print(f"  [Scraper] {reg['name']} failed: {e}")

    return articles


def fetch_regulator_articles() -> list[dict]:
    """Scrape all configured regulator pages for recent AML announcements."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    seen_urls: set[str] = set()
    results: list[dict] = []

    print(f"[Scraper] Scraping {len(REGULATORS)} regulator pages (last {LOOKBACK_DAYS} days)...")
    for reg in REGULATORS:
        articles = _scrape_generic(reg, cutoff)
        new = [a for a in articles if a["url"] not in seen_urls]
        seen_urls.update(a["url"] for a in new)
        if new:
            print(f"  [Scraper] {reg['name']}: {len(new)} articles")
        results.extend(new)

    print(f"[Scraper] Total: {len(results)} articles from regulator pages")
    return results


if __name__ == "__main__":
    articles = fetch_regulator_articles()
    for a in articles[:10]:
        print(f"  [{a['country']}] {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
