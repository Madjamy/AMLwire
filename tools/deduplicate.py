"""
Deduplicate a merged list of raw articles.
- Removes URL duplicates within the batch
- Removes articles whose URLs already exist in Supabase
- Removes articles older than 7 days
- Removes near-duplicate titles (same story from different sources)
"""

import os
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
CUTOFF_DAYS = 7

# Stop words to strip when normalising titles for fuzzy comparison
_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "and", "or",
    "but", "is", "are", "was", "were", "with", "by", "from", "as", "its",
    "it", "be", "has", "had", "have", "that", "this", "which", "who", "how",
    "says", "said", "over", "after", "amid", "into", "about", "up", "us",
}

# Minimum word overlap ratio to treat two titles as the same story
_TITLE_SIMILARITY_THRESHOLD = 0.60  # Raised back: 0.50 caused false-positive dedup across different countries

# If first N significant words match exactly → always treat as duplicate
_PREFIX_MATCH_WORDS = 4


def _normalise_title(title: str) -> frozenset[str]:
    """Lowercase, strip punctuation, remove stop words → frozenset of words."""
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return frozenset(w for w in words if w not in _STOP_WORDS and len(w) > 2)


def _title_word_list(title: str) -> list[str]:
    """Return ordered significant words (no stop words, len > 2) for prefix matching."""
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def _titles_are_similar(a: frozenset, b: frozenset,
                        a_words: list[str] | None = None,
                        b_words: list[str] | None = None,
                        a_country: str = "",
                        b_country: str = "") -> bool:
    """
    Return True if titles are near-duplicates via:
    1. First-N-words prefix match (catches same story with different endings)
    2. Jaccard similarity >= threshold (catches paraphrased same-story titles)
    If articles have different known countries, require higher overlap (0.75).
    """
    if not a or not b:
        return False
    # Prefix match: if first 4 significant words are identical → duplicate
    if a_words and b_words and len(a_words) >= _PREFIX_MATCH_WORDS and len(b_words) >= _PREFIX_MATCH_WORDS:
        if a_words[:_PREFIX_MATCH_WORDS] == b_words[:_PREFIX_MATCH_WORDS]:
            return True
    # Jaccard similarity — higher threshold if different countries
    intersection = len(a & b)
    union = len(a | b)
    ratio = intersection / union
    # If both have known but different countries, require 0.75 overlap
    if a_country and b_country and a_country.lower() != b_country.lower():
        return ratio >= 0.75
    return ratio >= _TITLE_SIMILARITY_THRESHOLD


def _parse_date(date_str: str) -> datetime | None:
    """Try to parse an ISO-style datetime string."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(date_str[:26], fmt[:len(fmt)])
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_within_cutoff(article: dict) -> bool:
    """Return True if the article was published within the last 7 days."""
    dt = _parse_date(article.get("published_at", ""))
    if dt is None:
        return True  # can't determine date — let AI analysis decide
    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def _get_existing_data() -> tuple[set[str], list[frozenset], list[list[str]]]:
    """Fetch all URLs and normalised titles already stored in Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[Dedup] Supabase credentials not set — skipping Supabase dedup check")
        return set(), [], []
    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        response = client.table("articles").select("source_url, title").execute()
        urls = {row["source_url"] for row in response.data}
        title_sets = [_normalise_title(row.get("title", "")) for row in response.data]
        title_words = [_title_word_list(row.get("title", "")) for row in response.data]
        return urls, title_sets, title_words
    except Exception as e:
        print(f"[Dedup] Could not fetch existing data from Supabase: {e}")
        return set(), [], []


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Takes a merged list from NewsAPI + Tavily + country fetchers.
    Returns a clean, deduplicated list ready for AI analysis.
    Deduplication logic:
      1. Exact URL match (within batch and vs Supabase)
      2. Near-duplicate title match (Jaccard similarity ≥ 60%, within batch and vs Supabase)
      3. Articles older than 7 days are dropped
    """
    existing_urls, existing_title_sets, existing_title_words = _get_existing_data()

    seen_urls: set[str] = set()
    seen_title_sets: list[frozenset] = list(existing_title_sets)
    seen_title_words: list[list[str]] = list(existing_title_words)
    seen_countries: list[str] = [""] * len(existing_title_sets)  # No country info for existing
    clean = []
    skipped_title_dedup = 0

    for article in articles:
        url = (article.get("url") or "").strip()

        # Skip empty URLs
        if not url:
            continue

        # Skip already in Supabase (exact URL)
        if url in existing_urls:
            continue

        # Skip duplicates within this batch (exact URL)
        if url in seen_urls:
            continue

        # Skip articles outside 7-day window
        if not _is_within_cutoff(article):
            continue

        # Near-duplicate title check (Jaccard + prefix + country awareness)
        title = article.get("title", "")
        country = (article.get("country") or "").strip()
        norm = _normalise_title(title)
        words = _title_word_list(title)
        if norm:
            is_dup = any(
                _titles_are_similar(norm, existing_set, words, existing_words,
                                    country, existing_country)
                for existing_set, existing_words, existing_country
                in zip(seen_title_sets, seen_title_words, seen_countries)
            )
            if is_dup:
                skipped_title_dedup += 1
                continue
            seen_title_sets.append(norm)
            seen_title_words.append(words)
            seen_countries.append(country)

        seen_urls.add(url)
        clean.append(article)

    print(f"[Dedup] {len(clean)} unique new articles after deduplication (from {len(articles)} total)")
    if skipped_title_dedup:
        print(f"[Dedup]   {skipped_title_dedup} dropped as near-duplicate stories (same title, different source)")
    return clean


if __name__ == "__main__":
    # Quick test with dummy data
    test_articles = [
        {"url": "https://example.com/1", "title": "Test 1", "published_at": "2025-03-08T10:00:00Z"},
        {"url": "https://example.com/1", "title": "Duplicate", "published_at": "2025-03-08T10:00:00Z"},
        {"url": "https://example.com/2", "title": "Test 2", "published_at": "2025-02-01T10:00:00Z"},  # old
    ]
    result = deduplicate(test_articles)
    print(f"Result: {len(result)} articles")
    for a in result:
        print(f"  - {a['title']}")
