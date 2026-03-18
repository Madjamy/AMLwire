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

# ─── Country caps ──────────────────────────────────────────────────────────────
COUNTRY_CAPS = {
    "USA":            5,
    "United States":  5,
    "UK":             5,
    "United Kingdom": 5,
    "Australia":      8,
    "Japan":          5,
    "Singapore":      5,
    "India":          5,
    "UAE":            5,
}
DEFAULT_CAP = 2

MAX_TOTAL = int(os.getenv("CURATION_MAX_TOTAL", "40"))

# Articles scoring at or above this threshold bypass the country cap
CAP_OVERRIDE_THRESHOLD = 55

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

PRIORITY_COUNTRIES = {"Australia", "United Kingdom", "UK", "India"}

# Keywords for institutional significance scoring (checked in title + summary)
SIGNIFICANCE_KEYWORDS_HIGH = {
    "fatf", "un security council", "unsc", "united nations",
    "fsrb", "mutual evaluation", "panel of experts",
}
SIGNIFICANCE_KEYWORDS_MID = {
    "austrac", "fincen", "fca", "ofac", "mas", "hkma", "sec", "finra",
    "europol", "interpol", "nca", "rbi", "ed india",
}


def _assign_tier(score: int) -> str:
    """Map quality score to display tier."""
    if score >= 80:
        return "Critical"
    elif score >= 60:
        return "High"
    elif score >= 40:
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
    score += pub_scores.get(pub_type, 0)

    # Modus operandi depth
    mo_len = len(mo)
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

    # Score and tier every article
    for article in articles:
        article["quality_score"] = score_article(article)
        article["quality_tier"] = _assign_tier(article["quality_score"])

    # Sort by score descending
    sorted_articles = sorted(articles, key=lambda a: a["quality_score"], reverse=True)

    country_counts: dict[str, int] = defaultdict(int)
    curated = []
    overflow_high_score = []  # High-scoring articles blocked by cap

    for article in sorted_articles:
        if len(curated) >= MAX_TOTAL:
            break

        country = (article.get("country") or "Unknown").strip()
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)

        if country_counts[country] < cap:
            curated.append(article)
            country_counts[country] += 1
        elif article["quality_score"] >= CAP_OVERRIDE_THRESHOLD:
            overflow_high_score.append(article)

    # Append high-scoring overflow articles (cap override)
    for article in overflow_high_score:
        if len(curated) >= MAX_TOTAL:
            break
        curated.append(article)
        country = (article.get("country") or "Unknown").strip()
        country_counts[country] += 1

    # ── Logging ────────────────────────────────────────────────────────────
    total_dropped = len(articles) - len(curated)
    breakdown = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"[Curate] {len(articles)} → {len(curated)} articles after curation (cap: {MAX_TOTAL})")
    print(f"[Curate] Country breakdown: {', '.join(f'{c}:{n}' for c, n in breakdown)}")

    if overflow_high_score:
        override_count = len([a for a in overflow_high_score if a in curated])
        print(f"[Curate] Cap override: {override_count} high-scoring articles (>={CAP_OVERRIDE_THRESHOLD}) bypassed country cap")

    # Log tier breakdown
    tier_counts = defaultdict(int)
    for a in curated:
        tier_counts[a["quality_tier"]] += 1
    tier_str = ", ".join(f"{t}:{n}" for t, n in sorted(tier_counts.items()))
    print(f"[Curate] Tier breakdown: {tier_str}")

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
