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
    r"|online_features)(?:/|$)",
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


def _extract_date(url: str, content: str) -> str:
    """
    Fallback date extraction when Tavily doesn't return published_date.
    Priority: URL path date → date in content text → today's date.
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


def _search_regulatory(query: str, domains: list[str], days: int = 90, country_tag: str = "") -> list[dict]:
    """
    Search within specific regulatory domains using Tavily's include_domains filter.
    Returns primary-source content direct from the regulatory body's website.
    """
    if not TAVILY_API_KEY:
        return []
    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "days": days,
            "include_domains": domains,
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

            tavily_date = item.get("published_date", "")
            if tavily_date:
                try:
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(tavily_date).strftime("%Y-%m-%d")
                except Exception:
                    published = _extract_date(url, content)
            else:
                published = _extract_date(url, content)

            results.append({
                "title": title,
                "url": url,
                "source": domains[0],
                "published_at": published,
                "description": content[:500] if content else "",
                "content": content,
                "api_source": "tavily_regulatory",
                **({"country": country_tag} if country_tag else {}),
            })
        return results

    except Exception as e:
        print(f"[Tavily Regulatory] Error on '{query}' ({domains}): {e}")
        return []


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
                except Exception:
                    published = _extract_date(url, content)
            else:
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
