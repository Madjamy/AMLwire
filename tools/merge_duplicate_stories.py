"""
Fold duplicate stories already on the site into one article each.

Groups recent articles with the same logic curation uses (group_stories), keeps
the best version of each story, and turns every other copy into a source link on
it (other_sources). Copies are not deleted: merged_into points them at the kept
article, so the site stops listing them and old links open the kept story.

Two steps, so what is applied is exactly what was reviewed (the story check is
a model call and need not answer the same way twice):

  python tools/merge_duplicate_stories.py --days 21          # writes logs/merge_plan_<time>.json
  python tools/merge_duplicate_stories.py --apply logs/merge_plan_<time>.json

--apply backs up every affected row to logs/ before writing, and keeps any
source or higher score a pipeline run added between plan and apply.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from tools.curate_articles import (  # noqa: E402
    _assign_tier,
    _merge_group,
    _merge_sources,
    _supabase_client,
    group_stories,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _fetch_recent(client, days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            client.table("articles")
            .select("*")
            .gte("created_at", since)
            .is_("merged_into", "null")
            .order("created_at")
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        start += 1000


def plan(client, days: int) -> None:
    rows = _fetch_recent(client, days)
    groups = [g for g in group_stories(rows) if len(g) > 1]
    print(f"[Merge] {len(rows)} articles from the last {days} days, {len(groups)} stories with copies")

    steps = []
    for group in groups:
        primary = _merge_group(group)
        copies = [a for a in group if a is not primary]
        steps.append({
            "primary_id": primary["id"],
            "copy_ids": [c["id"] for c in copies],
            "other_sources": primary["other_sources"],
            "quality_score": primary["quality_score"],
            "quality_tier": primary["quality_tier"],
        })
        print(f"\n  KEEP {primary['quality_score']:3d} {primary.get('source_name') or ''} | "
              f"{(primary.get('amlwire_title') or primary['title'])[:90]}")
        for c in copies:
            print(f"   +src {c.get('quality_score', 0):3d} {c.get('source_name') or ''} | "
                  f"{(c.get('amlwire_title') or c['title'])[:90]}")

    os.makedirs("logs", exist_ok=True)
    path = f"logs/merge_plan_{_stamp()}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(steps, f, ensure_ascii=False, indent=1)
    copies = sum(len(s["copy_ids"]) for s in steps)
    print(f"\n[Merge] {copies} copies would become sources of {len(steps)} stories")
    print(f"[Merge] Plan written to {path}. Nothing changed. Apply with: --apply {path}")


def apply(client, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        steps = json.load(f)
    ids = [i for s in steps for i in [s["primary_id"], *s["copy_ids"]]]
    rows = []
    for start in range(0, len(ids), 100):
        rows += client.table("articles").select("*").in_("id", ids[start:start + 100]).execute().data or []
    if len(rows) != len(ids):
        sys.exit(f"[Merge] Plan names {len(ids)} articles but {len(rows)} exist; re-plan instead.")
    if any(r.get("merged_into") for r in rows):
        sys.exit("[Merge] Some planned articles are already merged; re-plan instead.")

    copy_ids = [i for s in steps for i in s["copy_ids"]]
    earlier = []  # rows merged into a copy before; repointed below, so backed up too
    for start in range(0, len(copy_ids), 100):
        earlier += client.table("articles").select("*").in_(
            "merged_into", copy_ids[start:start + 100]).execute().data or []

    backup = f"logs/merge_backup_{_stamp()}.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(rows + earlier, f, ensure_ascii=False, indent=1)
    print(f"[Merge] Backed up {len(rows) + len(earlier)} rows to {backup}")

    current = {r["id"]: r for r in rows}
    for s in steps:
        primary = current[s["primary_id"]]
        # Re-merge with the row as it is now, not as planned.
        combined = dict(primary, other_sources=(primary.get("other_sources") or []) + s["other_sources"])
        score = max(primary.get("quality_score") or 0, s["quality_score"])
        client.table("articles").update({
            "other_sources": _merge_sources(combined, []),
            "quality_score": score,
            "quality_tier": _assign_tier(score),
        }).eq("id", s["primary_id"]).execute()
        # Repoint anything merged into a copy earlier, so no chain is ever two deep.
        client.table("articles").update({"merged_into": s["primary_id"]}).in_("merged_into", s["copy_ids"]).execute()
        client.table("articles").update({"merged_into": s["primary_id"]}).in_("id", s["copy_ids"]).execute()
    copies = sum(len(s["copy_ids"]) for s in steps)
    print(f"[Merge] Done: {copies} copies merged into {len(steps)} stories")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=21, help="plan over articles created in the last N days")
    parser.add_argument("--apply", metavar="PLAN", help="apply a plan file written by a previous run")
    args = parser.parse_args()

    client = _supabase_client()
    if client is None:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    if args.apply:
        apply(client, args.apply)
    else:
        plan(client, args.days)


if __name__ == "__main__":
    main()
