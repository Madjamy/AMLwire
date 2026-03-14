"""
One-off cleanup: find and remove near-duplicate articles from Supabase.

Groups articles by normalised title (Jaccard similarity ≥ 60%).
Within each duplicate group, keeps the best article (has modus_operandi,
longest summary, earliest fetched_at) and deletes the rest.

Usage:
    python tools/cleanup_duplicates.py
    python tools/cleanup_duplicates.py --dry-run   # preview only, no deletes
"""

import os
import re
import sys
import argparse
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "and", "or",
    "but", "is", "are", "was", "were", "with", "by", "from", "as", "its",
    "it", "be", "has", "had", "have", "that", "this", "which", "who", "how",
    "says", "said", "over", "after", "amid", "into", "about", "up", "us",
}

SIMILARITY_THRESHOLD = 0.50   # Lowered from 0.60
PREFIX_MATCH_WORDS = 4         # If first N significant words match → duplicate


def _norm(title: str) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return frozenset(w for w in words if w not in _STOP_WORDS and len(w) > 2)


def _word_list(title: str) -> list[str]:
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def _similar(a: frozenset, b: frozenset,
             a_words: list[str] | None = None,
             b_words: list[str] | None = None) -> bool:
    if not a or not b:
        return False
    # Prefix match
    if a_words and b_words and len(a_words) >= PREFIX_MATCH_WORDS and len(b_words) >= PREFIX_MATCH_WORDS:
        if a_words[:PREFIX_MATCH_WORDS] == b_words[:PREFIX_MATCH_WORDS]:
            return True
    return (len(a & b) / len(a | b)) >= SIMILARITY_THRESHOLD


def _quality_score(article: dict) -> int:
    """Higher = better. Used to pick the keeper within a duplicate group."""
    score = 0
    if article.get("modus_operandi"):
        score += 10
    score += min(len(article.get("summary") or ""), 500) // 50  # up to 10 pts
    # Prefer articles with a non-null country
    if article.get("country"):
        score += 2
    return score


def cleanup_duplicates(dry_run: bool = False):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        sys.exit(1)

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    print("[Cleanup] Fetching all articles from Supabase...")
    resp = client.table("articles").select(
        "id, title, summary, modus_operandi, country, fetched_at, source_url, source_name"
    ).execute()
    articles = resp.data or []
    print(f"[Cleanup] {len(articles)} articles fetched")

    # Build groups of near-duplicate articles
    groups: list[list[dict]] = []
    assigned: set[str] = set()  # article IDs already placed in a group

    for article in articles:
        if article["id"] in assigned:
            continue
        norm = _norm(article.get("title", ""))
        words = _word_list(article.get("title", ""))
        group = [article]
        assigned.add(article["id"])

        for other in articles:
            if other["id"] in assigned:
                continue
            other_norm = _norm(other.get("title", ""))
            other_words = _word_list(other.get("title", ""))
            if _similar(norm, other_norm, words, other_words):
                group.append(other)
                assigned.add(other["id"])

        if len(group) > 1:
            groups.append(group)

    print(f"[Cleanup] Found {len(groups)} duplicate groups ({sum(len(g) for g in groups)} articles total)")

    if not groups:
        print("[Cleanup] No duplicates to remove.")
        return

    total_deleted = 0

    for group in groups:
        # Sort by quality descending — keep the first (best)
        group_sorted = sorted(group, key=_quality_score, reverse=True)
        keeper = group_sorted[0]
        to_delete = group_sorted[1:]

        print(f"\n  GROUP ({len(group)} articles):")
        print(f"    KEEP  [{keeper['id'][:8]}] {keeper['title'][:70]}")
        for d in to_delete:
            print(f"    DELETE[{d['id'][:8]}] {d['title'][:70]}")

        if not dry_run:
            for d in to_delete:
                try:
                    client.table("articles").delete().eq("id", d["id"]).execute()
                    total_deleted += 1
                except Exception as e:
                    print(f"    ERROR deleting {d['id']}: {e}")

    if dry_run:
        print(f"\n[Cleanup] DRY RUN — would delete {sum(len(g) - 1 for g in groups)} articles")
    else:
        print(f"\n[Cleanup] Done. Deleted {total_deleted} duplicate articles.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no deletes")
    args = parser.parse_args()
    cleanup_duplicates(dry_run=args.dry_run)
