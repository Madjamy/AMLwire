"""
Deduplicate a merged list of raw articles.
- Removes URL duplicates within the batch
- Removes articles whose URLs already exist in Supabase
- Removes articles older than 7 days
"""

import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
CUTOFF_DAYS = 7


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


def _get_existing_urls() -> set[str]:
    """Fetch all URLs already stored in Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[Dedup] Supabase credentials not set — skipping Supabase dedup check")
        return set()
    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        response = client.table("articles").select("source_url").execute()
        return {row["source_url"] for row in response.data}
    except Exception as e:
        print(f"[Dedup] Could not fetch existing URLs from Supabase: {e}")
        return set()


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Takes a merged list from NewsAPI + SerpAPI.
    Returns a clean, deduplicated list ready for AI analysis.
    """
    existing_urls = _get_existing_urls()

    seen_urls = set()
    clean = []

    for article in articles:
        url = article.get("url", "").strip()

        # Skip empty URLs
        if not url:
            continue

        # Skip already in Supabase
        if url in existing_urls:
            continue

        # Skip duplicates within this batch
        if url in seen_urls:
            continue

        # Skip articles outside 7-day window
        if not _is_within_cutoff(article):
            continue

        seen_urls.add(url)
        clean.append(article)

    print(f"[Dedup] {len(clean)} unique new articles after deduplication (from {len(articles)} total)")
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
