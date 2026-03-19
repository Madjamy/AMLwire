"""
Fetch AML/financial crime news from regulatory and authoritative RSS feeds.
Covers: FATF, AUSTRAC, FinCEN, FCA, Interpol, major law enforcement,
EIN Presswire topic/region feeds, and specialist AML publications.
Returns list of raw article dicts compatible with the main pipeline.
Free — no API key required.
"""

import re
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# How far back to look — regulatory publications are less frequent than news
LOOKBACK_DAYS = 30

# ─── Feed Registry ────────────────────────────────────────────────────────────
# Format: (name, url, country, region)
# URLs verified working. All feeds are free with no commercial use restrictions.
RSS_FEEDS = [

    # ── Direct RSS feeds — primary source content from the regulatory body itself ──
    ("FCA",
     "https://www.fca.org.uk/news/rss.xml",
     "United Kingdom", "Europe"),

    ("DOJ",
     "https://www.justice.gov/news/rss",
     "United States", "Americas"),

    ("OFAC",
     "https://ofac.treasury.gov/rss.xml",
     "United States", "Americas"),

    ("FinCEN",
     "https://www.fincen.gov/news/rss.xml",
     "United States", "Americas"),

    ("SEC Press",
     "https://www.sec.gov/news/pressreleases.rss",
     "United States", "Americas"),

    ("Europol",
     "https://www.europol.europa.eu/newsroom/rss",
     "International", "Europe"),

    ("Interpol",
     "https://www.interpol.int/en/News-and-Events/News/rss",
     "International", "Global"),

    ("GFI",
     "https://gfintegrity.org/feed/",
     "International", "Global"),

    ("UNODC",
     "https://www.unodc.org/unodc/en/frontpage/rss.xml",
     "International", "Global"),

    # ── NCA (UK) — direct news feed ───────────────────────────────────────────
    ("NCA UK",
     "https://www.nationalcrimeagency.gov.uk/news?format=feed&type=rss",
     "United Kingdom", "Europe"),

    # ── Specialist AML / financial crime publications ─────────────────────────
    ("ACAMS AMLwire",
     "https://www.acams.org/en/media/library/articles/rss",
     "International", "Global"),

    ("FATF News",
     "https://www.fatf-gafi.org/en/publications/fatfrecommendations/rss.xml",
     "International", "Global"),

    # ── Additional specialist publications (verified working) ────────────────
    ("MoneyLaunderingNews",
     "https://www.moneylaunderingnews.com/feed/",
     "International", "Global"),

    ("Financial Crime Academy",
     "https://financialcrimeacademy.org/feed/",
     "International", "Global"),

    # ── Australian news ────────────────────────────────────────────────────
    ("ABC News Crime AU",
     "https://www.abc.net.au/news/feed/2942460/rss.xml",
     "Australia", "Asia-Pacific"),

    ("AUSTRAC",
     "http://www.austrac.gov.au/rss/news.xml",
     "Australia", "Asia-Pacific"),

    ("CDPP Australia",
     "https://www.cdpp.gov.au/rss-feeds",
     "Australia", "Asia-Pacific"),

    ("Guardian AU",
     "https://www.theguardian.com/australia-news/rss",
     "Australia", "Asia-Pacific"),

    # ── UK additional ─────────────────────────────────────────────────────
    ("Guardian UK",
     "https://www.theguardian.com/uk-news/rss",
     "United Kingdom", "Europe"),

    # ── India ─────────────────────────────────────────────────────────────
    ("RBI India",
     "https://www.rbi.org.in/Scripts/rss.aspx",
     "India", "Asia-Pacific"),

    ("NDTV India",
     "https://feeds.feedburner.com/NDTV-LatestNews",
     "India", "Asia-Pacific"),

    # ── Canada ────────────────────────────────────────────────────────────
    ("CBC News",
     "https://www.cbc.ca/webfeed/rss/rss-topstories",
     "Canada", "Americas"),

    ("Financial Post",
     "https://business.financialpost.com/feed/",
     "Canada", "Americas"),

    # ── UAE ───────────────────────────────────────────────────────────────
    ("The National UAE",
     "https://www.thenationalnews.com/rss",
     "UAE", "Middle East"),

    # ── Japan ─────────────────────────────────────────────────────────────
    ("NHK World",
     "https://www3.nhk.or.jp/rss/news/cat0.xml",
     "Japan", "Asia-Pacific"),

    # ── Singapore ─────────────────────────────────────────────────────────
    ("Channel NewsAsia",
     "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
     "Singapore", "Asia-Pacific"),

    ("Straits Times",
     "https://www.straitstimes.com/news/singapore/rss.xml",
     "Singapore", "Asia-Pacific"),

    # ── Specialist ────────────────────────────────────────────────────────
    ("EIN Presswire AML",
     "https://moneylaundering.einnews.com/all_rss",
     "International", "Global"),
]

# AML-relevant keywords — article must contain at least one to be included.
# Kept broad for RSS because these feeds are pre-scoped to AML regulators/bodies —
# false positives here are minimal and the AI does final relevance filtering.
AML_KEYWORDS = [
    "money laundering", "aml", "anti-money laundering", "financial crime",
    "suspicious", "enforcement", "penalty", "fine", "sanction", "fraud",
    "crypto", "virtual asset", "typology", "proceeds", "forfeiture",
    "arrest", "convicted", "indicted", "charged", "prosecution",
    "beneficial owner", "shell company", "hawala", "structuring",
    "terrorist financing", "proliferation", "illicit", "confiscation",
    "fatf", "fiu", "smr", "sar", "wire transfer", "trade-based",
    "cybercrime", "ransomware", "pig butchering", "scam", "deepfake",
    "compliance failure", "deferred prosecution", "remediation",
    # Regulatory publication language (used in formal regulatory titles)
    "advisory", "guidance", "circular", "mutual evaluation", "report",
    "red flag", "typologies", "alert", "notice", "designation",
    "sectoral risk", "national risk assessment", "nra",
    "confiscation order", "restraining order", "civil recovery",
]

# Stricter keyword list for broad general-news feeds (Guardian, NDTV, ABC Crime, CBC, etc.)
# These feeds produce thousands of articles — single words like "fraud" or "arrest" match
# too many irrelevant articles (speeding fines, sports, condo boards, celebrity trials).
# Require multi-word AML phrases to reduce noise.
_STRICT_AML_KEYWORDS = [
    "money laundering", "anti-money laundering", "aml", "financial crime",
    "proceeds of crime", "illicit funds", "illicit finance",
    "money laundering case", "money laundering charge", "money laundering arrest",
    "money laundering conviction", "money laundering probe",
    "laundering proceeds", "laundered funds",
    "enforcement directorate", "pmla", "efcc", "fintrac",
    "austrac", "fincen", "fatf", "ofac", "sanctions evasion",
    "terrorist financing", "hawala", "shell company", "beneficial owner",
    "asset forfeiture", "asset seizure", "confiscation order",
    "wire fraud", "bank fraud", "investment fraud",
    "cyber fraud", "ransomware", "pig butchering", "romance scam",
    "deepfake fraud", "crypto laundering",
    "compliance failure", "regulatory fine", "aml penalty",
    "suspicious transaction", "suspicious activity report",
    "financial fraud scheme", "fraud ring", "fraud syndicate",
    "embezzlement", "siphon", "misappropriat",
    "extradition", "criminal proceeds",
]

# Feeds that need the stricter filter (broad general-news outlets)
_STRICT_FILTER_FEEDS = {
    "ABC News Crime AU", "Guardian AU", "Guardian UK", "NDTV India",
    "CBC News", "Financial Post", "The National UAE", "NHK World",
    "Channel NewsAsia", "Straits Times",
}


def _parse_date(entry) -> str | None:
    """Extract and normalise publish date from feed entry. Returns YYYY-MM-DD or None."""
    # feedparser sets published_parsed (time.struct_time) when it can parse the date
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    # Fallback: raw published string
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def _is_recent(date_str: str | None, cutoff: datetime) -> bool:
    if not date_str:
        return False  # Reject dateless articles — prevents stale content slipping through
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return False  # Unparseable dates are also rejected


def _is_aml_relevant(title: str, summary: str, feed_name: str = "") -> bool:
    text = (title + " " + summary).lower()
    if feed_name in _STRICT_FILTER_FEEDS:
        return any(kw in text for kw in _STRICT_AML_KEYWORDS)
    return any(kw in text for kw in AML_KEYWORDS)


def _fetch_feed(name: str, url: str, country: str, region: str, cutoff: datetime) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    articles = []
    try:
        # Use requests with timeout for ALL feeds to prevent hanging on unresponsive servers
        try:
            resp = requests.get(url, headers={"User-Agent": "AMLWire/1.0 (+https://amlwire.com)"}, timeout=12, allow_redirects=True)
            if resp.status_code != 200:
                return []
            feed = feedparser.parse(resp.text)
        except requests.exceptions.Timeout:
            print(f"[RSS] Timeout fetching {name} ({url[:50]})")
            return []
        except requests.exceptions.ConnectionError:
            print(f"[RSS] Connection failed for {name} ({url[:50]})")
            return []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            # Strip HTML tags from summary
            summary = re.sub(r"<[^>]+>", " ", summary).strip()
            summary = re.sub(r"\s+", " ", summary)[:1000]

            date_str = _parse_date(entry)

            if not title or not link:
                continue
            if not _is_recent(date_str, cutoff):
                continue
            if not _is_aml_relevant(title, summary, feed_name=name):
                continue

            articles.append({
                "title":        title,
                "url":          link,
                "source":       name,
                "country":      country,
                "region":       region,
                "published_at": date_str or "",
                "description":  summary,
                "content":      summary,
                "fetch_source": "rss",
            })
    except Exception as e:
        print(f"[RSS] Failed to fetch {name} ({url[:50]}): {e}")
    return articles


def fetch_rss_articles() -> list[dict]:
    """
    Fetch articles from all regulatory RSS feeds.
    Returns list of raw article dicts for the pipeline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    all_articles = []
    seen_urls = set()

    print(f"[RSS] Fetching {len(RSS_FEEDS)} regulatory feeds (last {LOOKBACK_DAYS} days)...")
    for name, url, country, region in RSS_FEEDS:
        articles = _fetch_feed(name, url, country, region, cutoff)
        new = [a for a in articles if a["url"] not in seen_urls]
        seen_urls.update(a["url"] for a in new)
        if new:
            print(f"  [RSS] {name}: {len(new)} articles")
        all_articles.extend(new)

    print(f"[RSS] Total: {len(all_articles)} articles from regulatory feeds")
    return all_articles


if __name__ == "__main__":
    articles = fetch_rss_articles()
    for a in articles[:5]:
        print(f"  [{a['source']}] {a['title'][:80]} ({a['published_at']})")
    print(f"\nTotal: {len(articles)} articles")
