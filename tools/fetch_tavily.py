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

TAVILY_API_KEYS = [k for k in [os.getenv(f"TAVILY_API_KEY{s}") for s in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"]] if k]
TAVILY_API_KEY = TAVILY_API_KEYS[0] if TAVILY_API_KEYS else None
_tavily_key_idx = 0


def _get_tavily_key() -> str | None:
    """Get current Tavily key, rotating on quota errors."""
    global _tavily_key_idx
    if not TAVILY_API_KEYS:
        return None
    return TAVILY_API_KEYS[_tavily_key_idx % len(TAVILY_API_KEYS)]


def _rotate_tavily_key() -> bool:
    """Rotate to next Tavily key. Returns False if all keys exhausted."""
    global _tavily_key_idx
    _tavily_key_idx += 1
    if _tavily_key_idx >= len(TAVILY_API_KEYS):
        return False
    print(f"[Tavily] Key {_tavily_key_idx} quota hit, switching to key {_tavily_key_idx + 1}/{len(TAVILY_API_KEYS)}")
    return True
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
    "UN Security Council sanctions designation enforcement 2026",
    "UN Panel of Experts sanctions evasion report 2026",

    # Tax crimes
    "tax evasion money laundering prosecution",
    "tax fraud offshore account concealment",

    # Terror finance
    "terror financing AML enforcement",
    "terrorist financing crypto hawala",

    # Organized crime
    "organized crime financial crime money laundering",
    "criminal network laundering proceeds",

    # Cybercrime financial — general
    "cybercrime fraud money laundering proceeds",
    "cyber fraud financial crime enforcement",

    # Cybercrime — specific high-value methods
    "ransomware payment cryptocurrency laundering enforcement",
    "BEC business email compromise money laundering proceeds",
    "investment fraud scam money laundering arrest conviction",
    "pig butchering romance scam crypto fraud laundering",
    "cyber heist cryptocurrency theft laundering enforcement",
    "online fraud money mule proceeds laundering",
    "deepfake fraud financial crime enforcement action",
    "SIM swap fraud money laundering bank enforcement",

    # FATF and regulatory
    "FATF evaluation AML deficiency",
    "financial intelligence unit AML action",
]

# ─── FIU / FATF / FSRB authority queries ────────────────────────────────────
# Targets publications, typology reports, announcements and enforcement news
# from major global financial intelligence and standard-setting bodies.
AUTHORITY_QUERIES = [
    # FATF
    "FATF report publication typology mutual evaluation 2026",
    "FATF grey list black list country AML deficiency",
    "FATF guidance paper financial crime recommendation",

    # FSRBs (FATF-Style Regional Bodies)
    "MONEYVAL mutual evaluation AML report 2026",          # Europe
    "APG Asia Pacific Group AML typology report 2026",     # Asia-Pacific
    "ESAAMLG eastern southern Africa AML report",          # East/Southern Africa
    "GABAC central Africa AML financial crime",            # Central Africa
    "GAFILAT Latin America AML report typology",           # Latin America
    "GIABA west Africa AML financial crime 2026",          # West Africa
    "MENAFATF Middle East North Africa AML report",        # MENA
    "EAG Eurasian AML financial crime report",             # Eurasia
    "CFATF Caribbean AML typology report",                 # Caribbean

    # Major FIUs
    "AUSTRAC Australia AML enforcement action report 2026",
    "FinCEN United States financial intelligence advisory alert 2026",
    "UKFIU NCA financial intelligence AML action 2026",
    "FINTRAC Canada AML enforcement penalty report 2026",
    "TRACFIN France financial intelligence AML report",
    "FIU-IND India financial intelligence AML action",
    "STR suspicious transaction report FIU enforcement",
    "financial intelligence unit advisory typology alert 2026",

    # Egmont Group
    "Egmont Group financial intelligence unit cooperation 2026",
    "FIU international cooperation money laundering",
]

# Country-specific queries for priority jurisdictions
COUNTRY_QUERIES = {
    "Australia": [
        "AUSTRAC money laundering enforcement Australia",
        "Australia financial crime AML action 2026",
        "ASIC Australia enforcement penalty fraud 2026",
        "Australian Federal Police AFP money laundering arrest 2026",
        "Scamwatch Australia fraud scam alert 2026",
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
    "Japan": [
        "Japan financial crime money laundering JAFIC enforcement",
        "Japan AML anti-money laundering enforcement action 2026",
    ],
    "Hong Kong": [
        "Hong Kong money laundering JFIU enforcement action",
        "Hong Kong financial crime AML SFC HKMA 2026",
    ],
    "Malaysia": [
        "Malaysia money laundering AML enforcement BNM",
        "Malaysia financial crime AMLA enforcement action 2026",
    ],
    "South Korea": [
        "South Korea money laundering financial crime enforcement KoFIU",
        "Korea AML financial crime enforcement action 2026",
    ],
    "China": [
        "China money laundering financial crime enforcement PBC",
        "China AML anti-money laundering enforcement action 2026",
    ],
    "Indonesia": [
        "Indonesia money laundering PPATK financial crime enforcement",
        "Indonesia AML financial crime enforcement action 2026",
    ],
    "EU": [
        "European Union AML enforcement action AMLA 2026",
        "EU money laundering financial crime directive enforcement",
    ],
    "Germany": [
        "Germany money laundering AML enforcement BaFin",
        "Germany financial crime Geldwäsche enforcement 2026",
    ],
    "Canada": [
        "FINTRAC Canada money laundering enforcement penalty",
        "Canada financial crime AML enforcement action 2026",
    ],
    "New Zealand": [
        "New Zealand money laundering AML enforcement FIU",
        "New Zealand financial crime AML action 2026",
    ],
    "South Africa": [
        "South Africa money laundering FIC enforcement action",
        "South Africa AML financial crime FATF 2026",
    ],
    "Nigeria": [
        "Nigeria money laundering EFCC enforcement action",
        "Nigeria financial crime AML enforcement 2026",
    ],
}

# ─── Regulatory direct queries ────────────────────────────────────────────────
# Uses Tavily's include_domains to search WITHIN the regulatory body's own site,
# fetching primary-source content (enforcement notices, guidance, typology reports).
# days=90 because regulatory publications are less frequent than daily news.
REGULATORY_DOMAIN_QUERIES = [
    # AUSTRAC (Australia)
    {
        "query": "enforcement action penalty notice money laundering AML",
        "domains": ["austrac.gov.au"],
        "country": "Australia",
    },
    {
        "query": "industry guidance advisory financial crime typology",
        "domains": ["austrac.gov.au"],
        "country": "Australia",
    },
    # FATF
    {
        "query": "mutual evaluation report typology guidance grey list",
        "domains": ["fatf-gafi.org"],
        "country": None,
    },
    # MAS Singapore
    {
        "query": "enforcement action AML penalty notice financial crime",
        "domains": ["mas.gov.sg"],
        "country": "Singapore",
    },
    # Egmont Group
    {
        "query": "financial intelligence unit cooperation typology",
        "domains": ["egmontgroup.org"],
        "country": None,
    },
    # APG Asia-Pacific Group
    {
        "query": "mutual evaluation typology AML report",
        "domains": ["apgml.org"],
        "country": None,
    },
    # HKMA
    {
        "query": "AML enforcement penalty guidance money laundering",
        "domains": ["hkma.gov.hk"],
        "country": "Hong Kong",
    },
    # ED India (Enforcement Directorate)
    {
        "query": "money laundering enforcement arrest attachment PMLA",
        "domains": ["enforcementdirectorate.gov.in"],
        "country": "India",
    },
    # MONEYVAL (Council of Europe)
    {
        "query": "mutual evaluation AML money laundering report",
        "domains": ["coe.int"],
        "country": None,
    },
    # FinCEN (US) — in addition to RSS
    {
        "query": "advisory alert guidance financial crime AML",
        "domains": ["fincen.gov"],
        "country": "United States",
    },
    # FINTRAC (Canada)
    {
        "query": "enforcement action penalty notice AML money laundering",
        "domains": ["fintrac-canafe.gc.ca"],
        "country": "Canada",
    },
    # BNM Malaysia
    {
        "query": "enforcement action AML penalty financial crime",
        "domains": ["bnm.gov.my"],
        "country": "Malaysia",
    },
]

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


# URL path segments that indicate a reference/resource page rather than a news article
_RESOURCE_PATH_PATTERNS = re.compile(
    r"/(?:topics|resources|guidance|about|faq|faqs|explainer|learn|knowledge"
    r"|library|education|training|support|help|whitepaper|what-is|overview"
    r"|definitions|glossary|careers|contact|index-|publications(?:/index)?"
    r"|expertise|programmes?|tools|toolkits?|datasets?|standards|frameworks?"
    r"|legislation|mandates?|our-work|what-we-do|priorities|strategy"
    r"|technical-assistance|capacity-building"
    r"|virtual-library|abstracts|techniques/T\d"  # academic libs, AMLTRIX technique pages
    r"|press_releases?|announce/detail"           # press release wires
    r"|online_features"
    r"|search[-_]page|search\?|core-guidance(?:/[^/]+){0,1}$"  # nav/search pages, AUSTRAC guidance index
    r"|latest[-_]guidance[-_]updates|news\.html$|rss(?:\.xml)?$"  # AUSTRAC nav, RSS URLs themselves
    r"|/en/countries/|/calendar/|/events/"  # FATF nav pages, event listings
    r"|/tags?/|/category/|/archives?)(?:/|$)",  # tag/category listing pages
    re.IGNORECASE,
)

# Block PDFs whose URL path clearly contains an old year (before 3 years ago) — avoids surfacing stale docs
_OLD_PDF_YEAR_PATTERN = re.compile(
    r"/(?:200\d|201[0-9]|202[0-3])-\d{2}/.*\.pdf$",
    re.IGNORECASE,
)

# Domains that consistently produce non-AML content or noise
_BLOCKED_DOMAINS = {
    "researchgate.net",       # academic papers
    "framework.amltrix.com",  # reference framework
    "ojp.gov",                # US justice academic library
    "telecomasia.net",        # telecom trade news
    "hollywoodreporter.com",  # entertainment
    "sputnikglobe.com",       # Russian state media, geopolitical only
    "lelezard.com",           # press release aggregator
    "wfxg.com",               # local TV / press releases
    "markets.ft.com",         # FT press release wire
    "maritime-executive.com", # maritime ops, not financial crime
    "globenewswire.com",      # press release wire (investor alerts etc.)
    "prnewswire.com",         # press release wire
    "businesswire.com",       # press release wire
    "accesswire.com",         # press release wire
}


# Title patterns that indicate evergreen/educational content, not a news event
# Matches at the START of the title, or after a short prefix like "PEPs: " or "AML: "
_EVERGREEN_TITLE_PATTERNS = re.compile(
    r"(?:^|^[\w\s\(\)]{1,20}:\s*)"
    r"(?:understanding\s|explaining\s|what\s+is\s|what\s+are\s|how\s+to\s|"
    r"a\s+guide\s+to|guide\s+to\s|introduction\s+to\s|an\s+introduction|"
    r"the\s+role\s+of\s|the\s+importance\s+of\s|"
    r"how\s+does\s|all\s+you\s+need\s+to\s+know|overview\s+of\s)",
    re.IGNORECASE,
)

# Separate pattern for "Why X demand/matter/require" style titles (any number of words before verb)
_EVERGREEN_WHY_PATTERN = re.compile(
    r"^why\s+.{5,60}?\s+(?:matter|demand|require|need|pose|present)\b",
    re.IGNORECASE,
)


def _is_resource_url(url: str) -> bool:
    """Return True if the URL is a reference/resource page or blocked domain."""
    if _RESOURCE_PATH_PATTERNS.search(url):
        return True
    if _OLD_PDF_YEAR_PATTERN.search(url):
        return True
    # Extract domain (strip www.)
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if m:
        domain = m.group(1).lower()
        if domain in _BLOCKED_DOMAINS:
            return True
        # Block any subdomain of a blocked domain
        if any(domain.endswith("." + bd) for bd in _BLOCKED_DOMAINS):
            return True
    return False


def _is_evergreen_title(title: str) -> bool:
    """Return True if the title looks like an educational/evergreen explainer, not a news event."""
    t = title.strip()
    return bool(_EVERGREEN_TITLE_PATTERNS.match(t)) or bool(_EVERGREEN_WHY_PATTERN.match(t))


def _is_relevant(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in TOPIC_KEYWORDS)


def _extract_date(url: str, content: str) -> tuple[str | None, str]:
    """
    Fallback date extraction when Tavily doesn't return published_date.
    Priority: URL path date → date in content text → None.
    Returns (ISO date string or None, date_confidence).
    date_confidence: "url_extracted" | "content_extracted" | "none"
    """

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
                    return dt.strftime("%Y-%m-%d"), "url_extracted"
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
                    return f"{m.group(3)}-{mo}-{int(m.group(2)):02d}", "content_extracted"
                elif i == 1:  # DD Month YYYY
                    mo = month_map[m.group(2)[:3].lower()]
                    return f"{m.group(3)}-{mo}-{int(m.group(1)):02d}", "content_extracted"
                elif i == 2:  # ISO
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "content_extracted"
            except (KeyError, ValueError):
                pass

    # 3. No date found — return None instead of today's date
    return None, "none"


def _search_regulatory(query: str, domains: list[str], days: int = 90, country_tag: str = "") -> list[dict]:
    """
    Search within specific regulatory domains using Tavily's include_domains filter.
    Returns primary-source content direct from the regulatory body's website.
    """
    key = _get_tavily_key()
    if not key:
        return []
    try:
        payload = {
            "api_key": key,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "days": days,
            "include_domains": domains,
            "include_raw_content": False,
            "include_answer": False,
        }
        resp = requests.post(TAVILY_URL, json=payload, timeout=20)
        if resp.status_code == 432:
            if _rotate_tavily_key():
                return _search_regulatory(query, domains, days, country_tag)
            print(f"[Tavily Regulatory] All keys exhausted on '{query}'")
            return []
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            url = item.get("url", "")
            title = item.get("title", "")
            content = item.get("content", "")

            if not url or not title:
                continue

            tavily_date = item.get("published_date", "")
            if tavily_date:
                try:
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(tavily_date).strftime("%Y-%m-%d")
                    date_confidence = "api"
                except Exception:
                    published, date_confidence = _extract_date(url, content)
            else:
                published, date_confidence = _extract_date(url, content)

            results.append({
                "title": title,
                "url": url,
                "source": domains[0],
                "published_at": published,
                "date_confidence": date_confidence,
                "description": content[:500] if content else "",
                "content": content,
                "api_source": "tavily_regulatory",
                **({"country": country_tag} if country_tag else {}),
            })
        return results

    except Exception as e:
        if "432" in str(e):
            if _rotate_tavily_key():
                return _search_regulatory(query, domains, days, country_tag)
        print(f"[Tavily Regulatory] Error on '{query}' ({domains}): {e}")
        return []


def _search(query: str, days: int = 7, country_tag: str = "") -> list[dict]:
    """Execute a single Tavily search and return standardised article dicts."""
    key = _get_tavily_key()
    if not key:
        return []
    try:
        payload = {
            "api_key": key,
            "query": query,
            "topic": "news",            # news only — filters out reference/educational pages
            "search_depth": "basic",
            "max_results": 8,
            "days": days,
            "include_raw_content": False,
            "include_answer": False,
        }
        resp = requests.post(TAVILY_URL, json=payload, timeout=20)
        if resp.status_code == 432:
            if _rotate_tavily_key():
                return _search(query, days, country_tag)  # retry with next key
            print(f"[Tavily] All keys exhausted on '{query}'")
            return []
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            url = item.get("url", "")
            title = item.get("title", "")
            content = item.get("content", "")

            if not url or not title:
                continue

            # Skip resource/reference/topic pages (not news articles)
            if _is_resource_url(url):
                continue

            # Skip evergreen educational/explainer articles by title pattern
            if _is_evergreen_title(title):
                continue

            text = (title + " " + content).lower()
            if not _is_relevant(text):
                continue

            # Use Tavily's published_date if present (RFC format: "Fri, 06 Mar 2026 09:30:06 GMT")
            # Fall back to URL/content extraction only if Tavily doesn't provide one
            tavily_date = item.get("published_date", "")
            if tavily_date:
                try:
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(tavily_date).strftime("%Y-%m-%d")
                    date_confidence = "api"
                except Exception:
                    published, date_confidence = _extract_date(url, content)
            else:
                published, date_confidence = _extract_date(url, content)

            results.append({
                "title": title,
                "url": url,
                "source": item.get("source", ""),
                "published_at": published,
                "date_confidence": date_confidence,
                "description": content[:500] if content else "",
                "content": content,
                "api_source": "tavily",
                **({"country": country_tag} if country_tag else {}),
            })
        return results

    except Exception as e:
        if "432" in str(e):
            if _rotate_tavily_key():
                return _search(query, days, country_tag)
        print(f"[Tavily] Error on '{query}': {e}")
        return []


def fetch_articles() -> list[dict]:
    """
    Fetch AML news via Tavily for the last 7 days.
    Covers topic gaps not well-served by NewsAPI.
    Returns deduplicated list of article dicts with full content.
    """
    if not TAVILY_API_KEYS:
        print("[Tavily] No TAVILY_API_KEY set — skipping Tavily fetch")
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

    # FIU / FATF / FSRB authority queries (top 3 per query, deduplicated)
    authority_start = len(results)
    for query in AUTHORITY_QUERIES:
        for article in _search(query, days=7):
            if article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                results.append(article)
    print(f"[Tavily] Authority fetch (FIU/FATF/FSRB): {len(results) - authority_start} articles")

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

    # Regulatory direct queries — fetches content FROM regulatory body websites
    reg_start = len(results)
    for spec in REGULATORY_DOMAIN_QUERIES:
        for article in _search_regulatory(
            query=spec["query"],
            domains=spec["domains"],
            country_tag=spec.get("country") or "",
        ):
            if article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                results.append(article)
    reg_count = len(results) - reg_start
    print(f"[Tavily] Regulatory direct: {reg_count} articles from regulatory websites")

    return results


if __name__ == "__main__":
    articles = fetch_articles()
    for a in articles[:10]:
        tag = f"[{a['country']}] " if a.get("country") else ""
        print(f"  {tag}{a['title'][:80]} ({a['source']})")
