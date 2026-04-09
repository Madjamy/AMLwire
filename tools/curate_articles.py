"""
Curate analyzed articles for global diversity and quality.

Scoring model (0-100):
  Tier 1 — Content Quality (0-40): publication_type, modus_operandi depth, financial_amount
  Tier 2 — Typology & Predicate Crime (0-30): typology value, cybercrime/trafficking/sanctions bonus
  Tier 3 — Authority & Significance (0-20): enforcement_authority, UN/FATF significance
  Tier 4 — Strategic Priority (0-10): priority country (AU/UK/IN), action_required flag

Curation rules:
- Country caps limit articles per country (configurable per country)
- Articles scoring >= CAP_OVERRIDE_THRESHOLD bypass the country cap
- Total cap: MAX_TOTAL articles per pipeline run
- quality_score is stored on each article dict for Supabase persistence

This runs AFTER AI analysis and BEFORE upload.
"""

import os
from collections import defaultdict

# ─── Country normalization (must match analyze_articles.py) ───────────────────
COUNTRY_NORMALIZE = {
    "USA": "United States", "US": "United States", "U.S.": "United States",
    "U.S.A.": "United States", "America": "United States",
    "UK": "United Kingdom", "U.K.": "United Kingdom",
    "Britain": "United Kingdom", "England": "United Kingdom",
    "United Arab Emirates": "UAE", "Dubai": "UAE", "Abu Dhabi": "UAE",
    "Hong Kong SAR": "Hong Kong",
    "Republic of Korea": "South Korea", "Korea": "South Korea",
    "PRC": "China", "Mainland China": "China",
}


def _normalise_country(country: str | None) -> str:
    if not country:
        return "Unknown"
    c = country.strip()
    return COUNTRY_NORMALIZE.get(c, c)


# ─── Country caps ──────────────────────────────────────────────────────────────
COUNTRY_CAPS = {
    "United States":  5,
    "United Kingdom": 5,
    "Australia":      8,
    "Japan":          5,
    "Singapore":      5,
    "India":          5,
    "UAE":            5,
    "Canada":         5,
}
DEFAULT_CAP = 2

MAX_TOTAL = int(os.getenv("CURATION_MAX_TOTAL", "45"))  # raised from 40 to accommodate region floors

# Articles scoring at or above this threshold bypass the country cap
CAP_OVERRIDE_THRESHOLD = 65

# ─── Region floor guarantees ─────────────────────────────────────────────────
# Minimum articles per region per run. If below floor after curation,
# pull in highest-scoring articles from that region.
REGION_MAP = {
    "Australia": {"Australia"},
    "APAC": {"Singapore", "India", "Japan", "Hong Kong", "Malaysia", "South Korea",
             "China", "Indonesia", "Philippines", "New Zealand", "Thailand", "Taiwan",
             "Vietnam", "Bangladesh", "Pakistan", "Sri Lanka", "Myanmar", "Cambodia", "Nepal"},
    "Europe": {"United Kingdom", "Germany", "France", "Netherlands", "Switzerland",
               "Sweden", "Denmark", "Italy", "Spain", "Ireland", "Belgium", "Austria",
               "Luxembourg", "Norway", "Finland", "Estonia", "Latvia", "Lithuania",
               "European Union", "EU", "Greece", "Portugal", "Poland", "Romania", "Cyprus", "Malta"},
    "Americas": {"United States", "Canada"},
    "MENA": {"UAE", "Saudi Arabia", "Qatar", "Bahrain", "Kuwait", "Oman",
             "Israel", "Lebanon", "Jordan", "Egypt", "Turkey", "Iran", "Iraq"},
    "Africa": {"South Africa", "Nigeria", "Kenya", "Ghana", "Tanzania", "Uganda",
               "Zimbabwe", "Mozambique", "Zambia", "Namibia", "Botswana", "Mauritius"},
}

REGION_FLOORS = {
    "Australia": 2,
    "APAC": 3,
    "Europe": 3,
    "Americas": 3,
    "MENA": 2,
    "Africa": 1,
}


def _get_region(country: str) -> str | None:
    """Return region name for a country, or None."""
    for region, countries in REGION_MAP.items():
        if country in countries:
            return region
    return None

# ─── Typology classification sets ──────────────────────────────────────────────

HIGH_VALUE_TYPOLOGIES = {
    "Ransomware proceeds",
    "Cybercrime proceeds",
    "Crypto-asset laundering",
    "Crypto mixing / tumbling",
    "Darknet-enabled laundering",
    "Trade-based money laundering (TBML)",
    "Hawala and informal value transfer",
    "Human trafficking proceeds",
    "Drug trafficking proceeds",
    "Shell companies and nominee ownership",
    "Structuring / Smurfing",
    "Money mules",
    "Real estate laundering",
    "Professional enablers",
    "Terrorist financing",
    "Sanctions evasion",
    "Offshore concealment",
    "Cash-intensive business laundering",
}

LOW_VALUE_TYPOLOGIES = {"AML News", "AML compliance failure"}

# ─── Scoring constants ────────────────────────────────────────────────────────

CYBERCRIME_TYPOLOGIES = {
    "Ransomware proceeds",
    "Business Email Compromise (BEC)",
    "Deepfake / AI-enabled fraud",
    "Synthetic identity fraud",
    "Cybercrime proceeds",
    "Darknet-enabled laundering",
    "NFT / DeFi fraud",
}

TRAFFICKING_TYPOLOGIES = {
    "Human trafficking proceeds",
    "Drug trafficking proceeds",
}

MAJOR_AUTHORITIES = {
    "austrac", "doj", "fca", "fincen", "ofac", "mas", "hkma", "sec",
    "europol", "interpol", "ed india", "nca", "rbi", "fintrac", "occ",
    "federal reserve", "fatf", "un", "finra", "finma", "bafin", "apra",
    "asic", "sfo", "serious fraud office", "afp", "fbi",
}

PRIORITY_COUNTRIES = {"Australia", "United Kingdom", "India", "Singapore", "UAE", "Canada"}

# Routine enforcement signals — arrest/sentencing news with limited compliance value
# When these appear in the title of an enforcement_action article, the base score
# is reduced from +20 to +5 (criminal justice outcome, not regulatory enforcement)
ROUTINE_ENFORCEMENT_SIGNALS = {
    "arrested", "jailed", "sentenced", "convicted", "pleads guilty",
    "found guilty", "indicted", "prison", "custody", "bail denied",
    "bail rejected", "denies bail", "rejects bail",
}

# Systemic signals — if present alongside arrest keywords, the article has
# compliance/typology value and should NOT be excluded
SYSTEMIC_SIGNALS = {
    "ofac", "fincen", "austrac", "fca", "fatf", "mas", "hkma",
    "sanctions", "designat", "operation ", "dismantl", "network",
    "compliance", "fine ", "fined", "penalty", "civil action",
    "ring ", "syndicate", "cartel", "scheme",
}

# Keywords for institutional significance scoring (checked in title + summary)
SIGNIFICANCE_KEYWORDS_HIGH = {
    "fatf", "un security council", "unsc", "united nations",
    "fsrb", "mutual evaluation", "panel of experts",
}
SIGNIFICANCE_KEYWORDS_MID = {
    "austrac", "fincen", "fca", "ofac", "mas", "hkma", "sec", "finra",
    "europol", "interpol", "nca", "rbi", "ed india",
}


def _is_individual_criminal_justice(article: dict) -> bool:
    """
    Detect individual arrest/sentencing news with no systemic compliance value.
    These are crime blotter articles about individuals being arrested, convicted,
    or sentenced — not regulatory enforcement against institutions.
    Returns True if the article should be excluded from the feed.
    """
    pub_type = article.get("publication_type", "")
    if pub_type != "enforcement_action":
        return False

    title = (article.get("title") or article.get("amlwire_title") or "").lower()
    has_routine = any(s in title for s in ROUTINE_ENFORCEMENT_SIGNALS)
    if not has_routine:
        return False

    # Check for systemic signals that indicate compliance/typology value
    has_systemic = any(s in title for s in SYSTEMIC_SIGNALS)
    return not has_systemic


def _assign_tier(score: int) -> str:
    """Map quality score to display tier."""
    if score >= 90:
        return "Critical"
    elif score >= 75:
        return "High"
    elif score >= 60:
        return "Elevated"
    return "Watch"


def score_article(article: dict) -> int:
    """
    Compute quality score (0-100) from AI-analyzed article fields.
    Higher = more significant for AML professionals.
    """
    score = 0
    typology = article.get("aml_typology", "")
    mo = article.get("modus_operandi") or ""
    pub_type = article.get("publication_type", "")
    fin_amount = article.get("financial_amount") or ""
    authority = article.get("enforcement_authority") or ""
    country = (article.get("country") or "").strip()
    action_req = article.get("action_required", False)
    title = (article.get("title") or article.get("amlwire_title") or "").lower()
    summary = (article.get("summary") or "").lower()
    text = title + " " + summary

    # ── Tier 1: Content Quality (0-40) ─────────────────────────────────────
    # Publication type
    pub_scores = {
        "enforcement_action": 20,
        "regulatory_guidance": 15,
        "typology_study": 10,
        "industry_news": 0,
    }
    pub_score = pub_scores.get(pub_type, 0)

    # Routine arrest/sentencing news — lower compliance value than regulatory enforcement
    if pub_type == "enforcement_action" and any(s in title for s in ROUTINE_ENFORCEMENT_SIGNALS):
        pub_score = 5

    score += pub_score

    # Modus operandi depth — ignore fallback template
    mo_len = len(mo)
    if mo.startswith("Modus operandi not reported"):
        mo_len = 0
    if mo_len > 200:
        score += 10
    elif mo_len > 100:
        score += 7
    elif mo_len > 50:
        score += 3

    # Financial amount present
    if fin_amount.strip():
        score += 10

    # ── Tier 2: Typology & Predicate Crime (0-30) ──────────────────────────
    # Determine if this LOW_VALUE typology is actually significant
    # (e.g. AML compliance failure WITH enforcement action + financial amount = real fine)
    is_significant_compliance = (
        typology in LOW_VALUE_TYPOLOGIES
        and pub_type == "enforcement_action"
        and fin_amount.strip()
    )

    # Typology value
    if typology in HIGH_VALUE_TYPOLOGIES:
        score += 15
    elif is_significant_compliance:
        score += 12  # Real regulatory fine — not generic news
    elif typology not in LOW_VALUE_TYPOLOGIES and typology:
        score += 8

    # Predicate crime bonus
    if typology in CYBERCRIME_TYPOLOGIES:
        score += 15
    elif typology in TRAFFICKING_TYPOLOGIES:
        score += 12
    elif typology == "Sanctions evasion":
        score += 12
    elif typology in HIGH_VALUE_TYPOLOGIES:
        score += 5
    elif is_significant_compliance:
        score += 8  # Enforcement fines are a form of predicate outcome

    # ── Tier 3: Authority & Significance (0-20) ───────────────────────────
    # Enforcement authority
    auth_lower = authority.lower().strip()
    if auth_lower and any(ma in auth_lower for ma in MAJOR_AUTHORITIES):
        score += 10
    elif auth_lower:
        score += 5

    # Institutional significance (UN/FATF mentions in title/summary)
    # High-significance events (FATF grey list, UN sanctions) get a larger boost
    # to compensate when typology is LOW_VALUE (these events ARE important)
    has_high_significance = any(kw in text for kw in SIGNIFICANCE_KEYWORDS_HIGH)
    has_mid_significance = any(kw in text for kw in SIGNIFICANCE_KEYWORDS_MID)

    if has_high_significance:
        score += 15 if typology in LOW_VALUE_TYPOLOGIES else 10
    elif has_mid_significance:
        score += 5

    # ── Tier 4: Strategic Priority (0-10) ──────────────────────────────────
    # Priority country
    if country in PRIORITY_COUNTRIES:
        score += 5
    elif country and country != "Unknown":
        score += 2

    # Action required
    if action_req:
        score += 5

    return min(score, 100)


def curate_articles(articles: list[dict]) -> list[dict]:
    """
    Score articles, apply country caps with override for high-scoring articles,
    and return a curated list ready for upload.
    Each article dict gets a 'quality_score' field attached.
    """
    if not articles:
        return []

    # Normalise country names before scoring
    for article in articles:
        article["country"] = _normalise_country(article.get("country"))

    # Exclude individual criminal justice articles (arrests/sentencings with no
    # systemic compliance value). These are crime blotter noise.
    before_filter = len(articles)
    articles = [a for a in articles if not _is_individual_criminal_justice(a)]
    excluded = before_filter - len(articles)
    if excluded:
        print(f"[Curate] Excluded {excluded} individual arrest/sentencing articles (no compliance value)")

    # Score and tier every article
    for article in articles:
        article["quality_score"] = score_article(article)
        article["quality_tier"] = _assign_tier(article["quality_score"])

    # Sort by score descending
    sorted_articles = sorted(articles, key=lambda a: a["quality_score"], reverse=True)

    country_counts: dict[str, int] = defaultdict(int)
    curated = []
    curated_urls = set()
    overflow_high_score = []  # High-scoring articles blocked by cap

    for article in sorted_articles:
        if len(curated) >= MAX_TOTAL:
            break

        country = article.get("country", "Unknown")
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)

        if country_counts[country] < cap:
            curated.append(article)
            curated_urls.add(article.get("source_url") or article.get("url", ""))
            country_counts[country] += 1
        elif article["quality_score"] >= CAP_OVERRIDE_THRESHOLD:
            overflow_high_score.append(article)

    # Append high-scoring overflow articles (cap override)
    for article in overflow_high_score:
        if len(curated) >= MAX_TOTAL:
            break
        curated.append(article)
        curated_urls.add(article.get("source_url") or article.get("url", ""))
        country = article.get("country", "Unknown")
        country_counts[country] += 1

    # ── Region floor enforcement ──────────────────────────────────────────────
    # Check if any region is below its minimum floor; if so, pull from sorted_articles
    region_counts: dict[str, int] = defaultdict(int)
    for a in curated:
        region = _get_region(a.get("country", ""))
        if region:
            region_counts[region] += 1

    floor_additions = 0
    for region, floor in REGION_FLOORS.items():
        current = region_counts.get(region, 0)
        if current >= floor:
            continue
        needed = floor - current
        # Find articles from this region not yet in curated
        candidates = [
            a for a in sorted_articles
            if _get_region(a.get("country", "")) == region
            and (a.get("source_url") or a.get("url", "")) not in curated_urls
        ]
        for a in candidates[:needed]:
            curated.append(a)
            curated_urls.add(a.get("source_url") or a.get("url", ""))
            country_counts[a.get("country", "Unknown")] = country_counts.get(a.get("country", "Unknown"), 0) + 1
            region_counts[region] = region_counts.get(region, 0) + 1
            floor_additions += 1

    if floor_additions:
        print(f"[Curate] Region floors: added {floor_additions} articles to meet minimum regional coverage")
        for region, floor in REGION_FLOORS.items():
            actual = region_counts.get(region, 0)
            if actual < floor:
                print(f"[Curate] WARNING: {region} has {actual}/{floor} articles (not enough candidates)")

    # ── Logging ────────────────────────────────────────────────────────────
    total_dropped = len(articles) - len(curated)
    breakdown = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"[Curate] {len(articles)} → {len(curated)} articles after curation (cap: {MAX_TOTAL})")
    print(f"[Curate] Country breakdown: {', '.join(f'{c}:{n}' for c, n in breakdown)}")

    if overflow_high_score:
        override_count = len([a for a in overflow_high_score if a in curated])
        print(f"[Curate] Cap override: {override_count} high-scoring articles (>={CAP_OVERRIDE_THRESHOLD}) bypassed country cap")

    # Log tier breakdown (all articles + curated)
    tier_counts_all = defaultdict(int)
    for a in articles:
        tier_counts_all[_assign_tier(a.get("quality_score", 0))] += 1
    tier_counts = defaultdict(int)
    for a in curated:
        tier_counts[a["quality_tier"]] += 1

    tier_order = ["Critical", "High", "Elevated", "Watch"]
    all_str = " | ".join(f"{t}: {tier_counts_all.get(t, 0)}" for t in tier_order)
    cur_str = " | ".join(f"{t}: {tier_counts.get(t, 0)}" for t in tier_order)
    print(f"[Curate] Tier distribution (all scored):  {all_str}")
    print(f"[Curate] Tier distribution (curated):     {cur_str}")

    # Log published articles with scores and tiers
    for a in curated:
        c = (a.get("country") or "?")
        score = a["quality_score"]
        tier = a["quality_tier"]
        print(f"  [Curate] {score:3d} {tier:<8s} | [{c}] {a.get('title', '')[:60]}")

    # Warn if any country hit its cap
    for country, count in breakdown:
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)
        if count >= cap:
            over = count - cap
            suffix = f" (+{over} override)" if over > 0 else ""
            print(f"[Curate] Cap hit: {country} ({cap} base{suffix})")

    # Log dropped articles with scores
    if total_dropped > 0:
        curated_set = set(id(a) for a in curated)
        dropped = [a for a in sorted_articles if id(a) not in curated_set]
        print(f"[Curate] {total_dropped} articles dropped:")
        for a in dropped[:10]:
            c = (a.get("country") or "?")
            score = a.get("quality_score", 0)
            tier = a.get("quality_tier", "?")
            print(f"  - {score:3d} {tier:<8s} | [{c}] {a.get('title', '')[:60]}")
        if len(dropped) > 10:
            print(f"  ... and {len(dropped) - 10} more")

    return curated
