# -*- coding: utf-8 -*-
"""
One-off cleanup script:
1. Normalise region values in existing articles
2. Remove duplicate articles (same story, different source/title variant)
"""

import os
import re
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# ── Region normalisation map ──────────────────────────────────────────────────
REGION_MAP = {
    # Americas
    "united states": "Americas",
    "usa": "Americas",
    "us": "Americas",
    "north america": "Americas",
    "latin america": "Americas",
    "south america": "Americas",
    "caribbean": "Americas",
    "canada": "Americas",
    "mexico": "Americas",
    "brazil": "Americas",
    # Europe
    "united kingdom": "Europe",
    "uk": "Europe",
    "western europe": "Europe",
    "eastern europe": "Europe",
    "eu": "Europe",
    "european union": "Europe",
    "germany": "Europe",
    "france": "Europe",
    "netherlands": "Europe",
    "switzerland": "Europe",
    "scandinavia": "Europe",
    "nordic": "Europe",
    # Asia-Pacific
    "asia": "Asia-Pacific",
    "southeast asia": "Asia-Pacific",
    "south asia": "Asia-Pacific",
    "east asia": "Asia-Pacific",
    "australia": "Asia-Pacific",
    "new zealand": "Asia-Pacific",
    "india": "Asia-Pacific",
    "china": "Asia-Pacific",
    "japan": "Asia-Pacific",
    "singapore": "Asia-Pacific",
    "hong kong": "Asia-Pacific",
    "south korea": "Asia-Pacific",
    "myanmar": "Asia-Pacific",
    "cambodia": "Asia-Pacific",
    "thailand": "Asia-Pacific",
    "philippines": "Asia-Pacific",
    "indonesia": "Asia-Pacific",
    "malaysia": "Asia-Pacific",
    "pacific": "Asia-Pacific",
    "apac": "Asia-Pacific",
    # Middle East & Africa
    "middle east": "Middle East & Africa",
    "africa": "Middle East & Africa",
    "uae": "Middle East & Africa",
    "dubai": "Middle East & Africa",
    "gulf": "Middle East & Africa",
    "saudi arabia": "Middle East & Africa",
    "israel": "Middle East & Africa",
    "nigeria": "Middle East & Africa",
    "south africa": "Middle East & Africa",
    "kenya": "Middle East & Africa",
    "iran": "Middle East & Africa",
}

VALID_REGIONS = {"Americas", "Europe", "Asia-Pacific", "Middle East & Africa", "Global"}


def normalise_region(region: str) -> str | None:
    if not region:
        return None
    if region in VALID_REGIONS:
        return region  # already correct
    lower = region.lower().strip()
    # Direct lookup
    if lower in REGION_MAP:
        return REGION_MAP[lower]
    # Substring match
    for key, mapped in REGION_MAP.items():
        if key in lower:
            return mapped
    return None  # can't map — leave for manual review


# ── Title dedup helpers ───────────────────────────────────────────────────────
_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "and", "or",
    "but", "is", "are", "was", "were", "with", "by", "from", "as", "its",
    "it", "be", "has", "had", "have", "that", "this", "which", "who", "how",
    "says", "said", "over", "after", "amid", "into", "about", "up", "us",
}
_SIMILARITY_THRESHOLD = 0.65


def _normalise_title(title: str) -> frozenset:
    words = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
    return frozenset(w for w in words if w not in _STOP_WORDS and len(w) > 2)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _quality_score(article: dict) -> int:
    """Higher = better. Keep this one when deduping."""
    score = 0
    if article.get("modus_operandi"):
        score += 3
    if article.get("tags"):
        score += 2
    if article.get("summary") and len(article.get("summary", "")) > 100:
        score += 1
    return score


def fix_regions_and_dupes():
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    print("[Cleanup] Fetching all articles...")
    resp = client.table("articles").select(
        "id,title,region,source_url,summary,modus_operandi,tags,fetched_at"
    ).execute()
    articles = resp.data or []
    print(f"[Cleanup] {len(articles)} articles fetched")

    # ── Step 1: Fix regions ───────────────────────────────────────────────────
    print("\n[Cleanup] Step 1: Normalising regions...")
    region_fixed = 0
    region_unknown = []

    for article in articles:
        current = article.get("region", "")
        if current in VALID_REGIONS:
            continue  # already valid

        new_region = normalise_region(current or "")
        if new_region:
            try:
                client.table("articles").update({"region": new_region}).eq("id", article["id"]).execute()
                print(f"  Region: '{current}' -> '{new_region}' | {article.get('title', '')[:50]}")
                article["region"] = new_region  # update in-memory for dedup step
                region_fixed += 1
            except Exception as e:
                print(f"  Failed: {e}")
        else:
            region_unknown.append((article["id"], current, article.get("title", "")[:60]))

    print(f"\n[Cleanup] Regions fixed: {region_fixed}")
    if region_unknown:
        print(f"[Cleanup] Could not map {len(region_unknown)} regions:")
        for id_, reg, title in region_unknown:
            print(f"  [{id_[:8]}] '{reg}' — {title}")

    # ── Step 2: Remove duplicates ─────────────────────────────────────────────
    print("\n[Cleanup] Step 2: Finding duplicate articles...")

    # Sort by quality score descending — best articles first
    articles_sorted = sorted(articles, key=_quality_score, reverse=True)

    kept_titles: list[tuple[str, frozenset]] = []  # (id, norm_title)
    to_delete: list[str] = []

    for article in articles_sorted:
        norm = _normalise_title(article.get("title", ""))
        if not norm:
            continue

        # Check against already-kept articles
        is_dup = False
        for kept_id, kept_norm in kept_titles:
            if _similarity(norm, kept_norm) >= _SIMILARITY_THRESHOLD:
                is_dup = True
                print(f"  DUP [{article['id'][:8]}] '{article.get('title','')[:55]}'")
                print(f"    kept by kept [{kept_id[:8]}]")
                break

        if is_dup:
            to_delete.append(article["id"])
        else:
            kept_titles.append((article["id"], norm))

    print(f"\n[Cleanup] {len(to_delete)} duplicate articles to delete, {len(kept_titles)} to keep")

    if to_delete:
        print("[Cleanup] Deleting duplicates...")
        deleted = 0
        for art_id in to_delete:
            try:
                client.table("articles").delete().eq("id", art_id).execute()
                deleted += 1
            except Exception as e:
                print(f"  Failed to delete {art_id}: {e}")
        print(f"[Cleanup] Deleted {deleted} duplicate articles")

    print(f"\n[Cleanup] Done. Regions fixed: {region_fixed} | Duplicates removed: {len(to_delete)}")


if __name__ == "__main__":
    fix_regions_and_dupes()
