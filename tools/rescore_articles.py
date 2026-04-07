"""
One-time migration: rescore all existing articles with updated thresholds.

Updates quality_score (MO fallback fix) and quality_tier (new thresholds:
Critical 90+, High 75+, Elevated 60+, Watch <60) for all articles in Supabase.
"""

import os
import sys
from collections import Counter
from dotenv import load_dotenv
from supabase import create_client

# Add parent dir so we can import from tools/
sys.path.insert(0, os.path.dirname(__file__))
from curate_articles import score_article, _assign_tier

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

BATCH_SIZE = 500  # Supabase default page limit


def fetch_all_articles(client):
    """Fetch all articles in batches."""
    all_articles = []
    offset = 0
    while True:
        resp = (
            client.table("articles")
            .select(
                "id, title, amlwire_title, summary, modus_operandi, "
                "aml_typology, publication_type, financial_amount, "
                "enforcement_authority, country, action_required, "
                "quality_score, quality_tier"
            )
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
        )
        if not resp.data:
            break
        all_articles.extend(resp.data)
        if len(resp.data) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    return all_articles


def rescore():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY not set in .env")
        return

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    print("[Rescore] Fetching all articles from Supabase...")
    articles = fetch_all_articles(client)
    print(f"[Rescore] Found {len(articles)} articles")

    if not articles:
        return

    # Track changes
    old_tiers = Counter()
    new_tiers = Counter()
    changed = 0
    updates = []

    for article in articles:
        old_score = article.get("quality_score") or 0
        old_tier = article.get("quality_tier") or "Watch"
        old_tiers[old_tier] += 1

        # Recalculate score with MO fallback fix
        new_score = score_article(article)
        new_tier = _assign_tier(new_score)
        new_tiers[new_tier] += 1

        if new_score != old_score or new_tier != old_tier:
            updates.append({
                "id": article["id"],
                "quality_score": new_score,
                "quality_tier": new_tier,
                "old_score": old_score,
                "old_tier": old_tier,
                "title": article.get("amlwire_title") or article.get("title", ""),
            })
            changed += 1

    # Print before/after comparison
    tier_order = ["Critical", "High", "Elevated", "Watch"]
    print("\n[Rescore] Tier distribution comparison:")
    print(f"  {'Tier':<10} {'Before':>8} {'After':>8} {'Change':>8}")
    print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
    for tier in tier_order:
        before = old_tiers.get(tier, 0)
        after = new_tiers.get(tier, 0)
        diff = after - before
        sign = "+" if diff > 0 else ""
        print(f"  {tier:<10} {before:>8} {after:>8} {sign}{diff:>7}")

    print(f"\n[Rescore] {changed}/{len(articles)} articles will change")

    if not updates:
        print("[Rescore] No changes needed")
        return

    # Show sample of changed articles
    print("\n[Rescore] Sample changes (up to 20):")
    for u in updates[:20]:
        print(f"  {u['old_score']:>3}→{u['quality_score']:>3}  "
              f"{u['old_tier']:<10}→{u['quality_tier']:<10}  "
              f"{u['title'][:60]}")
    if len(updates) > 20:
        print(f"  ... and {len(updates) - 20} more")

    # Apply updates in batches
    print(f"\n[Rescore] Applying {len(updates)} updates to Supabase...")
    applied = 0
    errors = 0
    for u in updates:
        try:
            client.table("articles").update({
                "quality_score": u["quality_score"],
                "quality_tier": u["quality_tier"],
            }).eq("id", u["id"]).execute()
            applied += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error updating {u['id']}: {e}")

    print(f"[Rescore] Done: {applied} updated, {errors} errors")


if __name__ == "__main__":
    rescore()
