"""
Curate analyzed articles for global diversity and quality.

Scoring model (0-100):
  Tier 1 — Content Quality (0-40): publication_type, modus_operandi depth, financial_amount
  Tier 2 — Typology & Predicate Crime (0-30): typology value, cybercrime/trafficking/sanctions bonus
  Tier 3 — Authority & Significance (0-20): enforcement_authority, UN/FATF significance
  Tier 4 — Strategic Priority (0-10): priority country (AU/UK/IN), action_required flag

Curation rules:
- Each story is published once: copies from other outlets are folded into the
  best version as other_sources, including copies of stories already on the site
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
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

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

# Hard ceiling per country, applied even to protected (>=PROTECTED_SCORE) articles.
# Protection with no ceiling produced 21 US articles in a single run, because every
# US story cleared the bar. Within a country the highest-scoring ones survive.
#
# This does discard some articles scoring >=65, which the unlimited version did
# not. That is deliberate: one jurisdiction dominating the feed costs more than
# the marginal story does.
HARD_COUNTRY_CEILING = {
    "United States": 8,
    "United Kingdom": 8,
    "Australia": 8,
    "India": 6,
}
DEFAULT_HARD_CEILING = 5

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


# Rough USD value of one unit, only precise enough to place an amount in a size
# band. Unknown currencies are ignored rather than guessed, so they get no bonus.
_USD_PER_UNIT = {
    "usd": 1.0, "us$": 1.0, "$": 1.0, "dollars": 1.0,
    "cad": 0.72, "c$": 0.72, "aud": 0.65, "a$": 0.65, "sgd": 0.77, "s$": 0.77,
    "hkd": 0.128, "hk$": 0.128, "nzd": 0.59, "nz$": 0.59, "brl": 0.18, "r$": 0.18,
    "eur": 1.08, "€": 1.08, "euros": 1.08, "euro": 1.08,
    "gbp": 1.27, "£": 1.27, "pounds": 1.27,
    "inr": 0.012, "₹": 0.012, "rs": 0.012, "rs.": 0.012, "rupees": 0.012,
    "ngn": 0.00065, "₦": 0.00065, "n": 0.00065, "naira": 0.00065,
    "zar": 0.055, "r": 0.055, "rand": 0.055,
    "chf": 1.13, "jpy": 0.0067, "¥": 0.0067, "yen": 0.0067,
    "cny": 0.14, "rmb": 0.14, "yuan": 0.14,
    "aed": 0.27, "dirhams": 0.27, "myr": 0.22, "rm": 0.22, "ringgit": 0.22,
    "php": 0.017, "kes": 0.0077, "ksh": 0.0077, "pkr": 0.0036,
    "bdt": 0.0082, "tk": 0.0082, "taka": 0.0082, "idr": 0.000062,
    "krw": 0.00072, "₩": 0.00072, "won": 0.00072, "thb": 0.029, "baht": 0.029,
}

_MULTIPLIERS = {
    "trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "mln": 1e6, "m": 1e6, "crore": 1e7, "cr": 1e7,
    "lakh": 1e5, "thousand": 1e3, "k": 1e3,
}

# Symbols and codes written before the number ("USD 1.2 billion", "₹25 crore",
# "N2 billion"). Longer prefixes first so "hk$" wins over "$". Single-letter codes
# (N for naira, R for rand) must touch the digits and not end a word, and a symbol
# glued to letters ("MX$1 billion") is an unknown currency, not US dollars.
_PREFIX_CUR = (
    r"(?:(?<![a-z])(us\$|hk\$|nz\$|c\$|a\$|s\$|r\$|usd|cad|aud|sgd|hkd|nzd|brl|eur|gbp|inr|"
    r"rs\.?|ngn|zar|chf|jpy|cny|rmb|aed|myr|rm|php|kes|ksh|pkr|bdt|tk|idr|krw|thb|"
    r"n(?=\d)|r(?=\d))|(?<![a-z])([$€£₹₦¥₩]))"
)
_NUMBER = r"\s*(\d[\d,]*(?:\.\d+)?)\s*(trillion|tn|billion|bn|million|mln|mn|crore|cr|lakh|thousand|b|m|k)?\b"
_PREFIX_AMOUNT = re.compile(_PREFIX_CUR + _NUMBER)
# Currency written after the number ("5 billion naira", "1.21bn USD").
_SUFFIX_AMOUNT = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(trillion|tn|billion|bn|million|mln|mn|crore|lakh|thousand)?\s*"
    r"(dollars|euros?|pounds|rupees|naira|rand|yuan|yen|dirhams|ringgit|taka|won|baht|usd|eur|gbp|inr)\b"
)


_PENALTY_WORDS = re.compile(r"\b(fine[ds]?|penalt(y|ies)|settle(s|d|ments?)?|forfeit\w*|civil money)\b")


def _penalty_usd(fin_amount: str, title: str) -> float:
    """USD size of the penalty in an enforcement article, 0 if it isn't one.

    financial_amount can list several figures ("USD 2 billion laundered; USD 5
    million fine"); when it names the penalty, only that part is sized.
    """
    text = (fin_amount or "").lower()
    parts = [p for p in re.split(r";|,\s|\band\b", text) if _PENALTY_WORDS.search(p)]
    if parts:
        return max(_amount_usd(p) for p in parts)
    return _amount_usd(text) if _PENALTY_WORDS.search(title) else 0.0


def _amount_usd(text: str) -> float:
    """Largest amount in a financial_amount string, in approximate USD (0 if none)."""
    t = (text or "").lower().replace("\u202f", " ").replace("\xa0", " ")
    best = 0.0
    for m in _PREFIX_AMOUNT.finditer(t):
        cur = m.group(1) or m.group(2)
        if m.group(2):
            # "HKD $1.2 billion": the code before a bare symbol says which dollar.
            before = t[:m.start()].split()[-1:]
            if before and before[0] in _USD_PER_UNIT and len(before[0]) == 3:
                cur = before[0]
        num, mult = m.group(3), m.group(4)
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        best = max(best, value * _MULTIPLIERS.get(mult or "", 1) * _USD_PER_UNIT.get(cur, 0))
    for m in _SUFFIX_AMOUNT.finditer(t):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        best = max(best, value * _MULTIPLIERS.get(m.group(2) or "", 1) * _USD_PER_UNIT.get(m.group(3), 0))
    return best


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

    # Size of the penalty. The flat +10 above scored the record USD 1.2bn Bank of
    # America penalty (13 Oct 2026) like a USD 20,000 fine; it landed at 72
    # "Elevated", below the High/Critical tiers the ticker, hero and alerts read.
    # Penalties only: most other amounts are volumes ("network moved USD 17bn",
    # "Rs 15,000 crore case") that already score High on typology and would
    # flood Critical if sized the same way (122 promotions in a 6-week replay).
    if pub_score == 20:
        usd = _penalty_usd(fin_amount, title)
        if usd >= 1e9:
            score += 20
        elif usd >= 250e6:
            score += 12
        elif usd >= 50e6:
            score += 6

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

# Authority-shaped names can't be enumerated — every country has its own police
# forces, courts and departments. Any entity containing one of these words is
# an actor that recurs across unrelated cases, not an identifier of one case.
# Without this, "Dubai Police" linked the Daniel Kinahan arrest to an unrelated
# Scottish crime-clan story, and "Mossos d'Esquadra" linked two Catalan drug cases.
_AUTHORITY_MARKERS = re.compile(
    r"\b(police|policia|polizei|garda|gardai|mossos|carabinieri|gendarmerie|"
    r"prosecutor|prosecution|court|tribunal|judge|ministry|department|"
    r"office of|authority|commission|agency|bureau|directorate|regulator|"
    r"central bank|reserve bank|monetary authority|treasury|customs|"
    r"financial intelligence unit|fiu|task force|interior)\b"
)

_TITLE_STOP = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "and", "or", "but",
    "is", "are", "was", "were", "with", "by", "from", "as", "its", "it", "be",
    "has", "had", "have", "that", "this", "which", "who", "how", "says", "said",
    "over", "after", "amid", "into", "about", "up", "us", "new", "more",
}


def _norm_entity(value: str) -> str:
    """Lowercase, strip punctuation and legal suffixes for entity comparison."""
    e = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    e = re.sub(r"\b(inc|ltd|llc|plc|pty|gmbh|sa|nv|bv|co|corp|corporation|limited|company|group|holdings)\b", " ", e)
    e = re.sub(r"\s+", " ", e).strip()
    # "Bank of X" and "X" usually name the same bank, except when X is a place:
    # stripping turned "Bank of America" into "america", a generic jurisdiction,
    # so the 13 Oct 2026 USD 1.2bn BofA penalty never matched itself across
    # outlets and four copies were published.
    short = re.sub(r"\s+", " ", re.sub(r"\bbank of\b", " ", e)).strip()
    return e if short in _GENERIC_ENTITIES else short


def _distinctive_entities(article: dict) -> set[str]:
    """Named entities that actually identify this story."""
    out = set()
    for raw in (article.get("key_entities") or []):
        # Test for authority words BEFORE suffix-stripping: _norm_entity removes
        # "bank of", which turned "Central Bank of the UAE" into "central the uae"
        # and let it slip past the "central bank" marker.
        plain = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(raw).lower())).strip()
        if _AUTHORITY_MARKERS.search(plain):
            continue
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


def _amounts_match(a: dict, b: dict) -> bool:
    """Same reported figure, allowing for outlets rounding it differently.

    Comparing digits alone kept "USD 1.2 billion" and "$1.21 billion" apart, so
    13 copies of the 13 Oct 2026 Bank of America penalty were published as
    separate stories.
    """
    ua = _amount_usd(a.get("financial_amount") or "")
    ub = _amount_usd(b.get("financial_amount") or "")
    if ua and ub:
        return abs(ua - ub) <= 0.05 * max(ua, ub)
    na = _norm_amount(a)
    return bool(na) and na == _norm_amount(b)


# Link strengths between two articles, decided by _story_link.
SAME, CANDIDATE, NONE = "same", "candidate", "none"


def _story_link(a: dict, b: dict) -> str:
    """How strongly the evidence says two articles report the same event.

    SAME needs no further check. CANDIDATE means they share a distinctive entity
    plus a corroborating signal, but could still be a follow-up rather than a
    repeat: an arrest, charge and sentence in one case quote the same people and
    often the same amount. Those go to story_check, which can tell the two apart.
    """
    ta, tb = _title_words(a), _title_words(b)
    overlap = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    if len(ta | tb) >= 4:
        # Near-identical headlines: the same report, syndicated or lightly rewritten.
        if overlap >= 0.9:
            return SAME
        # Close but not identical can be the next step: "X charged in $20m
        # scheme" and "X convicted in $20m scheme" overlap 0.75.
        if overlap >= 0.75:
            return CANDIDATE
    shared = _distinctive_entities(a) & _distinctive_entities(b)
    if shared and (len(shared) >= 2 or _amounts_match(a, b) or overlap >= 0.35):
        return CANDIDATE
    return NONE


def _fallback_same(a: dict, b: dict) -> bool:
    """Decide a CANDIDATE pair without the model (story_check unavailable).

    Biased towards precision, as before story_check existed: merging two stories
    hides one behind a source link, which is worse than letting a repeat through.
    """
    ta, tb = _title_words(a), _title_words(b)
    overlap = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    if len(ta | tb) >= 4 and overlap >= 0.75:
        return True  # the headline rule that predates story_check
    shared = _distinctive_entities(a) & _distinctive_entities(b)
    if len(shared) >= 2 and a.get("aml_typology") == b.get("aml_typology"):
        return True
    if _amounts_match(a, b):
        if overlap >= 0.35:
            return True
        # A matching figure of real size with unrelated headlines is the same
        # event only when reported close together; days later it may be the
        # case's next step. Small amounts coincide (two Catalan drug cases once
        # shared EUR 70,000), so those always need the headline link above.
        day_a, day_b = _article_day(a), _article_day(b)
        return (
            _amount_usd(a.get("financial_amount") or "") >= 1e6
            and day_a is not None and day_b is not None
            and abs(day_a - day_b) <= timedelta(days=2)
        )
    return False


def _resolve(pairs: list[tuple[dict, dict]]) -> list[bool]:
    """True for each CANDIDATE pair that reports the same event."""
    if not pairs:
        return []
    from tools.story_check import judge_pairs  # deferred: imports the OpenAI client

    verdicts = judge_pairs(pairs)
    if verdicts is None:
        print(f"[Curate] story_check unavailable — deciding {len(pairs)} candidate pairs by rule")
        return [_fallback_same(a, b) for a, b in pairs]
    return [
        v == "same" if v else _fallback_same(a, b)
        for (a, b), v in zip(pairs, verdicts)
    ]


# Two articles are only the same story if they were also published close together.
# The same people and entities recur across weeks of a case (arrest, charge, bail,
# trial); without a window those separate developments collapse into one.
SAME_STORY_WINDOW_DAYS = 4

# Rows are fetched by first publication, but the window runs from a story's
# latest source, and big stories keep getting coverage for weeks: a story first
# published 30 days ago can still be within the window of today's candidates.
_PUBLISHED_LOOKBACK_DAYS = 30


def _parse_day(value) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _article_day(article: dict) -> datetime | None:
    """Publication date from either the AI's DD-MM-YYYY field or the stored timestamp."""
    return _parse_day(article.get("published_date") or article.get("published_at"))


def _last_activity(article: dict) -> datetime | None:
    """Latest date any source reported this story.

    Coverage of a big story runs for a week or more. Measuring the window from
    the first report alone let the fifth day's copies through as new stories.
    """
    days = [_article_day(article)] + [_parse_day(s.get("published_at")) for s in _other_sources(article)]
    days = [d for d in days if d]
    return max(days) if days else None


def _within_window(a: dict, b: dict) -> bool:
    day_a, day_b = _last_activity(a), _last_activity(b)
    if day_a is None or day_b is None:
        return False  # unknown date: don't risk merging separate developments
    return abs(day_a - day_b) <= timedelta(days=SAME_STORY_WINDOW_DAYS)


# ─── Sources ─────────────────────────────────────────────────────────────────
# One story is published once. Every other outlet that reported it is kept as a
# link on that article (articles.other_sources), official and major outlets first.

_OFFICIAL_SUFFIXES = (
    ".gov", ".mil", ".gov.uk", ".gov.au", ".gov.sg", ".gov.in", ".nic.in", ".gc.ca",
    ".gov.hk", ".gov.my", ".gov.ph", ".gov.za", ".gov.ng", ".gov.ae", ".govt.nz",
    ".europa.eu", ".admin.ch", ".gouv.fr", ".bund.de", ".gob.mx", ".gov.br",
)
_OFFICIAL_DOMAINS = {
    "fca.org.uk", "fatf-gafi.org", "mas.gov.sg", "hkma.gov.hk", "sfc.hk", "bis.org",
    "interpol.int", "un.org", "egmontgroup.org", "rbi.org.in", "sebi.gov.in",
    "finra.org", "finma.ch", "bafin.de", "afm.nl", "dnb.nl", "centralbank.ie",
    "bankofengland.co.uk", "ecb.europa.eu", "imf.org", "worldbank.org",
    "nationalcrimeagency.gov.uk", "sfo.gov.uk", "austrac.gov.au", "asic.gov.au",
    "fintrac-canafe.canada.ca", "osc.ca", "cbuae.gov.ae", "sca.gov.ae",
}
_MAJOR_OUTLETS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com",
    "washingtonpost.com", "economist.com", "bbc.com", "bbc.co.uk", "theguardian.com",
    "cnbc.com", "cnn.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "npr.org",
    "pbs.org", "axios.com", "politico.com", "politico.eu", "aljazeera.com",
    "americanbanker.com", "icij.org", "occrp.org", "scmp.com", "straitstimes.com",
    "afr.com", "abc.net.au", "smh.com.au", "theaustralian.com.au", "economictimes.indiatimes.com",
    "thehindu.com", "livemint.com", "hindustantimes.com", "indianexpress.com",
    "nikkei.com", "asia.nikkei.com", "lemonde.fr", "dw.com", "france24.com",
    "globeandmail.com", "theglobeandmail.com", "cbc.ca", "thenationalnews.com",
    "amlintelligence.com", "moneylaundering.com", "finextra.com", "fintechfutures.com",
}
# A URL whose path ends here is a listing page, not the story itself: FinCEN's
# entry for the BofA penalty pointed at /news/press-releases.
_LISTING_SEGMENTS = {
    "", "news", "press-releases", "news-releases", "press", "media", "newsroom",
    "latest", "media-releases", "press-room", "news-and-events", "updates", "index",
}


def _domain(url: str) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_listing_page(url: str) -> bool:
    try:
        path = urlparse(url or "").path
    except ValueError:
        return True
    last = path.rstrip("/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(html?|aspx?|php)$", "", last) in _LISTING_SEGMENTS


def _source_rank(url: str) -> int:
    """3 official, 2 major outlet, 1 other, 0 listing page or no URL."""
    if not url or _is_listing_page(url):
        return 0
    d = _domain(url)
    if d in _OFFICIAL_DOMAINS or d.endswith(_OFFICIAL_SUFFIXES):
        return 3
    if d in _MAJOR_OUTLETS or any(d.endswith("." + m) for m in _MAJOR_OUTLETS):
        return 2
    return 1


def _source_url(article: dict) -> str:
    return (article.get("source_url") or article.get("url") or "").strip()


_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|cmpid|ocid|taid|smid|sr_share|guccounter|syn-[0-9a-f]+)$")


def _url_key(url: str) -> str:
    """Compare URLs without scheme, www, tracking parameters or trailing slash.

    Other query parameters stay: on some sites they are the article ("?id=1").
    """
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    query = "&".join(sorted(
        q for q in p.query.split("&") if q and not _TRACKING_PARAMS.match(q.split("=", 1)[0].lower())
    ))
    return host + p.path.rstrip("/") + ("?" + query if query else "")


def _other_sources(article: dict) -> list[dict]:
    value = article.get("other_sources") or []
    return [s for s in value if isinstance(s, dict) and s.get("url")]


def _source_entry(article: dict) -> dict:
    url = _source_url(article)
    published = _article_day(article)
    return {
        "name": (article.get("source_name") or article.get("source") or _domain(url) or "Source").strip(),
        "url": url,
        "published_at": published.date().isoformat() if published else None,
    }


MAX_OTHER_SOURCES = 20


def _merge_sources(primary: dict, others: list[dict]) -> list[dict]:
    """primary's other_sources plus every source of `others`, best first, deduplicated."""
    entries = list(_other_sources(primary))
    for o in others:
        entries.append(_source_entry(o))
        entries.extend(_other_sources(o))
    seen = {_url_key(_source_url(primary))}
    merged = []
    for e in entries:
        key = _url_key(e["url"])
        if not e["url"] or key in seen:
            continue
        seen.add(key)
        merged.append(e)
    # Stable sort: within a rank, the order sources were found is kept.
    merged.sort(key=lambda e: -_source_rank(e["url"]))
    kept = merged[:MAX_OTHER_SOURCES]
    # The newest report dates the story for the same-story window, so it must
    # survive the cap, or later coverage would read as a new story.
    dated = [e for e in merged if e.get("published_at")]
    if dated and len(merged) > MAX_OTHER_SOURCES:
        newest = max(dated, key=lambda e: e["published_at"])
        if newest not in kept:
            kept[-1] = newest
    return kept


def fold_report(kept: dict, dropped: dict) -> None:
    """Keep a report dropped before AI analysis as a copy of `kept` as one of its sources."""
    entries = kept.setdefault("other_sources", [])
    entries.append(_source_entry(dropped))
    entries.extend(_other_sources(dropped))


def _choose_primary(group: list[dict]) -> dict:
    """The version to publish: the most authoritative of the strongest versions.

    Scores of copies of one event differ mostly by how much each outlet's text
    gave the model, so any copy within 10 points of the best is eligible, and
    among those an official release or major outlet beats a syndicator.
    """
    best = max(a.get("quality_score", 0) for a in group)
    eligible = [a for a in group if a.get("quality_score", 0) >= best - 10]
    return max(
        eligible,
        key=lambda a: (_source_rank(_source_url(a)), a.get("quality_score", 0), len(a.get("key_entities") or [])),
    )


def _merge_group(group: list[dict]) -> dict:
    """Fold a group of copies into its primary, which carries the others as sources."""
    primary = _choose_primary(group)
    others = [a for a in group if a is not primary]
    primary["other_sources"] = _merge_sources(primary, others)
    # The story is as significant as its best-scored telling.
    primary["quality_score"] = max(a.get("quality_score", 0) for a in group)
    primary["quality_tier"] = _assign_tier(primary["quality_score"])
    return primary


def group_stories(articles: list[dict]) -> list[list[dict]]:
    """Partition articles into stories. Each group lists its anchor first.

    Articles are compared against group anchors only, never other members, so
    matches cannot chain: A~B and B~C must not pull an unrelated C into A's group.
    """
    ranked = sorted(
        articles,
        key=lambda a: (a.get("quality_score", 0), len(a.get("key_entities") or [])),
        reverse=True,
    )
    # Candidate pairs are judged in one batch up front. Each is (later, earlier)
    # in ranked order, so `earlier` is always a possible anchor for `later`.
    links: dict[tuple[int, int], str] = {}
    for i, a in enumerate(ranked):
        for j in range(i):
            if _within_window(a, ranked[j]):
                link = _story_link(a, ranked[j])
                if link != NONE:
                    links[(i, j)] = link
    candidates = [k for k, v in links.items() if v == CANDIDATE]
    decided = dict(zip(candidates, _resolve([(ranked[i], ranked[j]) for i, j in candidates])))

    groups: list[list[int]] = []
    for i in range(len(ranked)):
        home = next(
            (g for g in groups if links.get((i, g[0])) == SAME or decided.get((i, g[0]))),
            None,
        )
        if home is None:
            groups.append([i])
        else:
            home.append(i)
    return [[ranked[i] for i in g] for g in groups]


def _collapse_duplicates(articles: list[dict]) -> tuple[list[dict], int]:
    """Publish each story once, as its best version. Returns (kept, removed_count)."""
    kept: list[dict] = []
    removed = 0
    for group in group_stories(articles):
        primary = _merge_group(group) if len(group) > 1 else group[0]
        kept.append(primary)
        for a in group:
            if a is primary:
                continue
            removed += 1
            print(
                f"  [Curate] Duplicate: [{a.get('country')}] {(a.get('title') or '')[:52]} "
                f"-> source of [{primary.get('country')}] {(primary.get('title') or '')[:52]}"
            )
    return kept, removed


def _supabase_client():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    from supabase import create_client

    return create_client(url, key)


def _load_recently_published(client) -> list[dict]:
    """Articles already on the site that a candidate could duplicate."""
    since = (datetime.now(timezone.utc) - timedelta(days=_PUBLISHED_LOOKBACK_DAYS)).isoformat()
    rows: list[dict] = []
    start = 0
    while True:  # paged explicitly: PostgREST silently caps unbounded selects at 1,000
        page = (
            client.table("articles")
            .select("id, title, amlwire_title, key_entities, aml_typology, financial_amount, "
                    "published_at, source_url, source_name, other_sources, quality_score")
            .gte("published_at", since)
            .is_("merged_into", "null")  # attach to the published story, never a merged copy
            .order("published_at", desc=True)
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        start += 1000


def _attach_to_published(articles: list[dict]) -> tuple[list[dict], int]:
    """Fold candidates that repeat a story already on the site into that article.

    Step 14 only rejects a URL it has seen, so the same event from a different
    outlet the next day was published again (347 such rows on 16 Sep 2026; 13
    Bank of America penalty articles by 16 Oct). The repeat now becomes a source
    link on the published article instead of being published or discarded.
    A genuine development of the story is still published on its own.
    """
    try:
        client = _supabase_client()
        existing = _load_recently_published(client) if client else []
    except Exception as e:  # never let this check break a run
        print(f"[Curate] Already-published check skipped, could not load recent articles: {e}")
        return articles, 0

    # (candidate index, existing index) pairs, strongest evidence first.
    pairs: list[tuple[int, int, str]] = []
    for i, a in enumerate(articles):
        for j, e in enumerate(existing):
            if _within_window(a, e):
                link = _story_link(a, e)
                if link != NONE:
                    pairs.append((i, j, link))
    candidates = [(i, j) for i, j, link in pairs if link == CANDIDATE]
    decided = dict(zip(candidates, _resolve([(articles[i], existing[j]) for i, j in candidates])))

    match_of: dict[int, int] = {}
    for i, j, link in pairs:
        if i not in match_of and (link == SAME or decided.get((i, j))):
            match_of[i] = j

    # Matched reports are never published, even if recording them below fails:
    # a failed write only loses the link until the report is fetched again
    # (its URL is not stored, so the next run re-finds and re-attaches it).
    kept = [a for i, a in enumerate(articles) if i not in match_of]
    attach: dict[int, list[dict]] = defaultdict(list)
    for i, j in match_of.items():
        attach[j].append(articles[i])
        print(
            f"  [Curate] Already published: {(articles[i].get('title') or '')[:52]} "
            f"-> source of {(existing[j].get('amlwire_title') or existing[j].get('title') or '')[:52]}"
        )

    attached = 0
    for j, repeats in attach.items():
        row = existing[j]
        update = {"other_sources": _merge_sources(row, repeats)}
        best = max(a.get("quality_score", 0) for a in repeats)
        if best > (row.get("quality_score") or 0):
            # A better telling of the story raises it; nothing ever lowers it.
            update["quality_score"] = best
            update["quality_tier"] = _assign_tier(best)
        try:
            client.table("articles").update(update).eq("id", row["id"]).execute()
            attached += len(repeats)
        except Exception as e:
            print(f"  [Curate] ERROR could not add {len(repeats)} sources to {row['id']}: {e}")
    return kept, attached


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
        if article.get("other_sources"):  # carried from the pre-AI title dedup
            article["other_sources"] = _merge_sources(article, [])

    # Collapse repeats of the same story before any slot is allocated, so one
    # event reported by three outlets cannot consume three slots.
    articles, duplicates_removed = _collapse_duplicates(articles)
    if duplicates_removed:
        print(f"[Curate] Collapsed {duplicates_removed} duplicate stories into their strongest version")

    # ...and fold repeats of stories already on the site into those articles.
    articles, already_published = _attach_to_published(articles)
    if already_published:
        print(f"[Curate] Added {already_published} repeat reports as sources of stories already on the site")

    # Sort by score descending
    sorted_articles = sorted(articles, key=lambda a: a["quality_score"], reverse=True)

    country_counts: dict[str, int] = defaultdict(int)
    curated = []
    curated_urls = set()

    # ── Pass 1: protected ──────────────────────────────────────────────────
    # Articles at or above PROTECTED_SCORE are admitted before anything else, so
    # a significant story can never lose its place to lower-tier filler. They
    # ignore the soft country cap but still respect a hard per-country ceiling —
    # without one, a jurisdiction where every story clears the bar takes over the
    # feed (21 US articles in the 10 Sep run).
    protected = [a for a in sorted_articles if a["quality_score"] >= PROTECTED_SCORE]
    ceiling_dropped: list[dict] = []
    for article in protected:
        country = article.get("country", "Unknown")
        ceiling = HARD_COUNTRY_CEILING.get(country, DEFAULT_HARD_CEILING)
        if country_counts[country] >= ceiling:
            # sorted_articles is score-descending, so the ones surviving to here
            # are always that country's highest-rated.
            ceiling_dropped.append(article)
            continue
        curated.append(article)
        curated_urls.add(article.get("source_url") or article.get("url", ""))
        country_counts[country] += 1

    if ceiling_dropped:
        by_country: dict[str, int] = defaultdict(int)
        for a in ceiling_dropped:
            by_country[a.get("country", "Unknown")] += 1
        summary = ", ".join(f"{c}:{n}" for c, n in sorted(by_country.items(), key=lambda kv: -kv[1]))
        print(f"[Curate] Hard ceiling dropped {len(ceiling_dropped)} protected articles ({summary})")

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
