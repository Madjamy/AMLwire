"""
Curate analyzed articles for global diversity and quality.

Scoring model (0-100):
  Tier 1 — Content Quality (0-40): publication_type, modus_operandi depth, financial_amount
  Tier 2 — Typology & Predicate Crime (0-30): typology value, cybercrime/trafficking/sanctions bonus
  Tier 3 — Authority & Significance (0-20): enforcement_authority, UN/FATF significance
  Tier 4 — Strategic Priority (0-10): priority country (AU/UK/IN), action_required flag

Curation rules:
- Near-duplicate stories are collapsed first, keeping the strongest version
- Articles scoring >= PROTECTED_SCORE are ALL published, ignoring country caps
- Remaining slots are filled by score, respecting country caps
- Total published = max(BASE_MAX_TOTAL, number of protected articles)
- Region floors then guarantee minimum coverage per region
- quality_score is stored on each article dict for Supabase persistence

This runs AFTER AI analysis and BEFORE upload.
"""

import os
import re
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

# Baseline number of articles published per run. The effective total is
# max(BASE_MAX_TOTAL, number of protected articles) — so a heavy news day expands
# the feed rather than discarding significant stories.
BASE_MAX_TOTAL = int(os.getenv("CURATION_MAX_TOTAL", "55"))

# Articles scoring at or above this are PROTECTED: every one is published,
# regardless of country cap or the baseline total. Nothing significant is dropped
# to make room for lower-tier filler.
#
# This threshold previously existed as a country-cap override but never once
# fired in production ("Cap override: 0" on every logged run). The old loop filled
# the total to its ceiling before the override list was appended, so overflow
# articles never had a slot left. Selection below is now ordered so protection
# applies first.
# Tunable without a redeploy: yesterday's scraper fix feeds the model full article
# bodies, which should lift scores (modus operandi depth and a named financial
# amount are worth 10 points each). If that pushes daily volume higher than the
# site wants, raise this rather than reintroducing a hard ceiling — it keeps the
# guarantee that nothing above the bar is dropped.
PROTECTED_SCORE = int(os.getenv("CURATION_PROTECTED_SCORE", "65"))
CAP_OVERRIDE_THRESHOLD = PROTECTED_SCORE  # backwards-compatible alias

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


# ─── Near-duplicate collapse ─────────────────────────────────────────────────
# Step 14 already de-duplicates, but it runs BEFORE AI analysis and can only
# compare titles and URLs. It also deliberately relaxes its threshold when two
# articles carry different countries, so a US sanctions action on a Turkish bank
# reported by a UK outlet arrives three times under three country labels and
# consumes three slots.
#
# By this point the AI has extracted key_entities, typology and financial amount,
# which identify a story far more reliably than its headline. The three variants
# below share almost no title words but the same entities:
#   "Treasury Severs Iranian Regime's Financial Lifelines in Türkiye"  [Turkey]
#   "US sanctions Turkish bank network Israel wanted closed over ..."  [US]
#   "US sanctions Turkish bank as Iran financial crackdown widens"     [UK]

# Entities that appear across unrelated stories and so carry no identifying
# power. Matching on these alone would merge every OFAC action into one.
#
# Jurisdictions matter as much as regulators here: key_entities routinely lists
# countries, and two unrelated Iran-sanctions stories share "Iran" and "FinCEN"
# without being the same event. Testing on real data, leaving countries in merged
# a Russia-Iran network story and a Swiss bank story into an Egyptian bank story.
_GENERIC_AUTHORITIES = {
    "doj", "department of justice", "us treasury", "u s treasury", "treasury",
    "ofac", "fincen", "fca", "austrac", "mas", "hkma", "europol", "interpol",
    "fatf", "fbi", "sec", "irs", "nca", "rbi", "ed", "ed india", "fintrac",
    "occ", "federal reserve", "finra", "finma", "bafin", "apra", "asic", "sfo",
    "afp", "efcc", "police", "government", "the government", "unknown", "n a",
    "central bank", "ministry of finance", "public prosecutor",
}

_GENERIC_JURISDICTIONS = {
    "united states", "america", "united kingdom", "britain", "england",
    "european union", "europe", "uae", "united arab emirates", "dubai",
    "abu dhabi", "iran", "russia", "china", "north korea", "dprk", "india",
    "australia", "canada", "singapore", "japan", "hong kong", "switzerland",
    "germany", "france", "spain", "italy", "netherlands", "belgium", "turkey",
    "israel", "egypt", "nigeria", "south africa", "kenya", "pakistan",
    "bangladesh", "ukraine", "poland", "romania", "malaysia", "philippines",
    "indonesia", "thailand", "vietnam", "cambodia", "myanmar", "mexico",
    "brazil", "colombia", "venezuela", "saudi arabia", "qatar", "iraq",
    "lebanon", "syria", "afghanistan", "ireland", "monaco", "cyprus", "malta",
    "iranian", "russian", "chinese", "american", "british",
}

_GENERIC_ENTITIES = _GENERIC_AUTHORITIES | _GENERIC_JURISDICTIONS

_TITLE_STOP = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "and", "or", "but",
    "is", "are", "was", "were", "with", "by", "from", "as", "its", "it", "be",
    "has", "had", "have", "that", "this", "which", "who", "how", "says", "said",
    "over", "after", "amid", "into", "about", "up", "us", "new", "more",
}


def _norm_entity(value: str) -> str:
    """Lowercase, strip punctuation and legal suffixes for entity comparison."""
    e = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    e = re.sub(r"\b(inc|ltd|llc|plc|pty|gmbh|sa|nv|bv|co|corp|corporation|limited|company|group|holdings|bank of)\b", " ", e)
    return re.sub(r"\s+", " ", e).strip()


def _distinctive_entities(article: dict) -> set[str]:
    """Named entities that actually identify this story."""
    out = set()
    for raw in (article.get("key_entities") or []):
        e = _norm_entity(raw)
        if len(e) >= 4 and e not in _GENERIC_ENTITIES:
            out.add(e)
    return out


def _title_words(article: dict) -> frozenset[str]:
    title = article.get("amlwire_title") or article.get("title") or ""
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return frozenset(w for w in words if w not in _TITLE_STOP and len(w) > 2)


def _norm_amount(article: dict) -> str:
    """Digits of the financial amount — currency wording varies between outlets."""
    return re.sub(r"[^0-9]", "", (article.get("financial_amount") or ""))[:6]


def _same_story(a: dict, b: dict) -> bool:
    """True if two analyzed articles report the same underlying event.

    Biased towards precision: wrongly merging two stories silently deletes one,
    which is worse than letting a duplicate through.
    """
    ea, eb = _distinctive_entities(a), _distinctive_entities(b)
    ta, tb = _title_words(a), _title_words(b)
    overlap = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    same_typology = a.get("aml_typology") == b.get("aml_typology")

    if ea and eb and same_typology:
        shared = ea & eb
        # Two distinctive entities in common is strong evidence by itself, and
        # is what catches the same story told from different national angles.
        if len(shared) >= 2:
            return True
        # One shared entity is weak evidence: a regional police force or a single
        # bank recurs across unrelated cases. Two Catalan drug cases sharing only
        # "Mossos d'Esquadra" and a coincidental EUR 70,000 were merged by an
        # earlier version of this rule. Require the same amount AND a real
        # headline relationship before collapsing on a single entity.
        if shared and _norm_amount(a) and _norm_amount(a) == _norm_amount(b) and overlap >= 0.35:
            return True

    # Headline evidence — deliberately strict. Step 14 already caught the easy
    # cases at 0.60; this only needs to catch what its country exemption let
    # through, so a high bar avoids merging genuinely parallel actions.
    return len(ta | tb) >= 4 and overlap >= 0.75


def _collapse_duplicates(articles: list[dict]) -> tuple[list[dict], int]:
    """Keep the strongest version of each story. Returns (kept, removed_count)."""
    # Strongest first, so the survivor of each group is the best version.
    ranked = sorted(
        articles,
        key=lambda a: (a.get("quality_score", 0), len(a.get("key_entities") or [])),
        reverse=True,
    )
    kept: list[dict] = []
    removed = 0
    for article in ranked:
        duplicate_of = next((k for k in kept if _same_story(article, k)), None)
        if duplicate_of is None:
            kept.append(article)
            continue
        removed += 1
        print(
            f"  [Curate] Duplicate: [{article.get('country')}] "
            f"{(article.get('title') or '')[:52]} -> merged into "
            f"[{duplicate_of.get('country')}] {(duplicate_of.get('title') or '')[:52]}"
        )
    return kept, removed


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

    # Collapse repeats of the same story before any slot is allocated, so one
    # event reported by three outlets cannot consume three slots.
    articles, duplicates_removed = _collapse_duplicates(articles)
    if duplicates_removed:
        print(f"[Curate] Collapsed {duplicates_removed} duplicate stories into their strongest version")

    # Sort by score descending
    sorted_articles = sorted(articles, key=lambda a: a["quality_score"], reverse=True)

    country_counts: dict[str, int] = defaultdict(int)
    curated = []
    curated_urls = set()

    # ── Pass 1: protected ──────────────────────────────────────────────────
    # Every article at or above PROTECTED_SCORE is published, ignoring country
    # caps. This runs BEFORE any filling, so a significant story can never lose
    # its place to lower-tier filler — the failure the old ordering guaranteed.
    protected = [a for a in sorted_articles if a["quality_score"] >= PROTECTED_SCORE]
    for article in protected:
        curated.append(article)
        curated_urls.add(article.get("source_url") or article.get("url", ""))
        country_counts[article.get("country", "Unknown")] += 1

    # A heavy news day expands the feed rather than discarding protected stories.
    effective_max = max(BASE_MAX_TOTAL, len(curated))

    # ── Pass 2: fill remaining slots ───────────────────────────────────────
    # Below the protection bar, country caps still apply so no single
    # jurisdiction floods the feed with routine coverage.
    for article in sorted_articles:
        if len(curated) >= effective_max:
            break
        if article["quality_score"] >= PROTECTED_SCORE:
            continue  # already admitted above
        country = article.get("country", "Unknown")
        cap = COUNTRY_CAPS.get(country, DEFAULT_CAP)
        if country_counts[country] < cap:
            curated.append(article)
            curated_urls.add(article.get("source_url") or article.get("url", ""))
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

    print(f"[Curate] {len(articles)} → {len(curated)} articles after curation "
          f"(baseline {BASE_MAX_TOTAL}, effective {effective_max})")
    print(f"[Curate] Country breakdown: {', '.join(f'{c}:{n}' for c, n in breakdown)}")

    # How many protected articles were admitted past a country cap they would
    # otherwise have failed. This is the number the old code always reported as 0.
    over_cap = 0
    seen: dict[str, int] = defaultdict(int)
    for a in curated:
        c = a.get("country", "Unknown")
        seen[c] += 1
        if seen[c] > COUNTRY_CAPS.get(c, DEFAULT_CAP):
            over_cap += 1
    if over_cap:
        print(f"[Curate] Protected: {over_cap} articles (>={PROTECTED_SCORE}) published past their country cap")
    if len(curated) > BASE_MAX_TOTAL:
        print(f"[Curate] Feed expanded to {len(curated)} (baseline {BASE_MAX_TOTAL}) to keep all protected stories")

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
