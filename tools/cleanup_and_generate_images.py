"""
One-off maintenance script:
1. Fetch all articles from Supabase
2. Detect and delete duplicate articles (same story, different titles/URLs)
   - Exact title duplicates (case-insensitive)
   - Near-duplicate titles sharing 4+ significant keywords
   - Keeps the earliest fetched_at
"""

import os
import re
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Words that don't help distinguish articles
STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "is", "are", "was", "were", "be", "been", "has", "have",
    "had", "over", "from", "into", "its", "via", "new", "as", "it",
    "that", "this", "after", "amid", "against", "about",
}


def _title_keywords(title: str) -> set[str]:
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return {w for w in words if w not in STOP_WORDS and len(w) > 2}


def fetch_all_articles(client) -> list[dict]:
    resp = (
        client.table("articles")
        .select("id, title, source_url, fetched_at, published_at, summary, region, aml_typology")
        .order("fetched_at", desc=False)
        .execute()
    )
    return resp.data or []


def _better(a: dict, b: dict) -> dict:
    """Return the 'better' of two articles (keep earlier fetched)."""
    return a  # a is already the earlier one (list is sorted asc)


def find_duplicates(articles: list[dict]) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Returns:
      to_delete: list of IDs to delete
      pairs: list of (kept_title, deleted_title) for logging
    """
    # Group by normalized title (exact match)
    groups: dict[str, list[dict]] = {}
    for a in articles:
        key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", a.get("title", "").lower())).strip()
        groups.setdefault(key, []).append(a)

    to_delete: list[str] = []
    pairs: list[tuple[str, str]] = []

    # Exact duplicates
    for key, group in groups.items():
        if len(group) > 1:
            best = group[0]
            for dup in group[1:]:
                to_delete.append(dup["id"])
                pairs.append((best.get("title", ""), dup.get("title", "")))

    # Near-duplicates: keyword overlap >= 4 significant words
    remaining = [a for a in articles if a["id"] not in to_delete]
    keyword_cache = {a["id"]: _title_keywords(a.get("title", "")) for a in remaining}

    processed = set()
    for i, a in enumerate(remaining):
        if a["id"] in processed:
            continue
        kw_a = keyword_cache[a["id"]]
        if len(kw_a) < 3:
            continue
        for b in remaining[i + 1:]:
            if b["id"] in processed or b["id"] in to_delete:
                continue
            kw_b = keyword_cache[b["id"]]
            overlap = kw_a & kw_b
            # Require overlap of at least 4 words OR >=60% of the shorter title's keywords
            shorter_len = min(len(kw_a), len(kw_b))
            if len(overlap) >= 4 or (shorter_len >= 3 and len(overlap) / shorter_len >= 0.6):
                better = _better(a, b)
                worse = b if better["id"] == a["id"] else a
                to_delete.append(worse["id"])
                pairs.append((better.get("title", ""), worse.get("title", "")))
                processed.add(worse["id"])

    return to_delete, pairs


def delete_articles(client, ids: list[str]) -> int:
    deleted = 0
    for article_id in ids:
        try:
            client.table("articles").delete().eq("id", article_id).execute()
            deleted += 1
        except Exception as e:
            print(f"[Cleanup] Error deleting {article_id}: {e}")
    return deleted


def safe_print(s: str):
    print(s.encode("ascii", errors="replace").decode("ascii"))


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_URL or SUPABASE_SERVICE_KEY not set")

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Step 1: Fetch all articles
    print("[Cleanup] Fetching all articles from Supabase...")
    articles = fetch_all_articles(client)
    print(f"[Cleanup] Total articles: {len(articles)}")

    # Print all titles
    print("\n--- Current articles ---")
    for a in articles:
        safe_print(f"  {a.get('title', '')[:85]}")
    print()

    # Step 2: Find duplicates
    to_delete, pairs = find_duplicates(articles)
    if to_delete:
        print(f"[Cleanup] Found {len(to_delete)} duplicate articles:")
        for kept, removed in pairs:
            safe_print(f"  KEEP:   {kept[:75]}")
            safe_print(f"  DELETE: {removed[:75]}")
            print()

        deleted = delete_articles(client, to_delete)
        print(f"[Cleanup] Deleted {deleted} duplicate articles")
    else:
        print("[Cleanup] No duplicates found")

    # Step 3: Re-fetch after deletions
    articles = fetch_all_articles(client)
    print(f"[Cleanup] {len(articles)} articles remaining after dedup")

    print("
Done.")




if __name__ == "__main__":
    main()
