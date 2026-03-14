"""
Curate analyzed articles for global diversity and quality.

Rules:
- Country cap: USA max 4, UK/Australia max 3, all others max 2
- Quality priority: articles with specific typologies rank above generic "AML News"
- Total cap: MAX_TOTAL articles published to the site
- Within each country bucket, typology-specific articles are preferred over AML News

This runs AFTER AI analysis and BEFORE upload.
"""

from collections import defaultdict

# Country caps — any country not listed uses DEFAULT_CAP
COUNTRY_CAPS = {
    "USA":       5,
    "UK":        5,
    "Australia": 5,
    "Japan":     5,
    "Singapore": 5,
    "India":     5,
    "UAE":       5,
}
DEFAULT_CAP = 2

# Absolute ceiling on articles published per pipeline run
MAX_TOTAL = 40

# Typologies that signal rich modus operandi content — ranked higher
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
    "Sanctions",
    "Offshore concealment",
    "Cash-intensive business laundering",
}

LOW_VALUE_TYPOLOGIES = {"AML News", "AML compliance failure"}


def _quality_rank(article: dict) -> int:
    """Higher = better quality. Used to sort within country buckets."""
    typology = article.get("aml_typology", "")
    mo = article.get("modus_operandi") or ""
    score = 0
    if typology in HIGH_VALUE_TYPOLOGIES:
        score += 10
    elif typology not in LOW_VALUE_TYPOLOGIES:
        score += 5
    # Reward articles where AI found enough detail for a modus operandi
    if len(mo) > 80:
        score += 3
    return score


def curate_articles(articles: list[dict]) -> list[dict]:
    """
    Apply country cap and quality ranking.
    Returns a curated list of articles ready for upload.
    """
    if not articles:
        return []

    # Sort all articles by quality (best first) so country buckets fill with best articles
    sorted_articles = sorted(articles, key=_quality_rank, reverse=True)

    country_counts: dict[str, int] = defaultdict(int)
    curated = []

    for article in sorted_articles:
        if len(curated) >= MAX_TOTAL:
            break

        country = (article.get("country") or "Unknown").strip()
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)

        if country_counts[country] < cap:
            curated.append(article)
            country_counts[country] += 1

    # Log the breakdown
    breakdown = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)
    print(f"[Curate] {len(articles)} → {len(curated)} articles after curation (cap: {MAX_TOTAL})")
    print(f"[Curate] Country breakdown: {', '.join(f'{c}:{n}' for c, n in breakdown)}")

    # Warn if any country hit its cap
    for country, count in breakdown:
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)
        if count == cap:
            print(f"[Curate] Cap hit: {country} ({cap} articles max)")

    return curated
