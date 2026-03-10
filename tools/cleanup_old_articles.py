"""
Cleanup script: verify and correct publish dates for articles in Supabase.

1. Delete articles confirmed older than 7 days (stored published_at).
2. For articles with today's date (fallback), scrape the article URL for
   real publish date from HTML meta tags / JSON-LD.
3. If real date found and it's old (>7 days) → delete.
4. If real date found and it differs from stored → update published_at.
5. Print a summary of all actions taken.

Run: py tools/cleanup_old_articles.py
"""

import os
import re
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False
    print("[WARN] beautifulsoup4 not installed — meta-tag scraping disabled")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
CUTOFF_DAYS = 7
TODAY = datetime.now(timezone.utc).date().isoformat()  # e.g. "2026-03-11"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ──────────────────────────────────────────────────────────────────────────────
# Date parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_old(dt: datetime) -> bool:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    return dt < cutoff


def _date_from_meta(url: str) -> str | None:
    """
    Scrape the article URL for a publish date in HTML meta tags or JSON-LD.
    Returns ISO date string YYYY-MM-DD, or None on failure.
    """
    if not BS4_OK:
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. JSON-LD (most reliable)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                # Handle list or dict
                items = data if isinstance(data, list) else [data]
                for item in items:
                    for key in ("datePublished", "dateCreated", "dateModified"):
                        val = item.get(key, "")
                        if val:
                            dt = _parse_iso(val)
                            if dt:
                                return dt.strftime("%Y-%m-%d")
            except (json.JSONDecodeError, AttributeError):
                pass

        # 2. OpenGraph / meta tags
        meta_names = [
            ("property", "article:published_time"),
            ("property", "article:modified_time"),
            ("name",     "pubdate"),
            ("name",     "date"),
            ("name",     "publish_date"),
            ("name",     "DC.date"),
            ("itemprop", "datePublished"),
        ]
        for attr, value in meta_names:
            tag = soup.find("meta", {attr: value})
            if tag and tag.get("content"):
                dt = _parse_iso(tag["content"])
                if dt:
                    return dt.strftime("%Y-%m-%d")

        # 3. time element with datetime attribute
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            dt = _parse_iso(time_tag["datetime"])
            if dt:
                return dt.strftime("%Y-%m-%d")

        return None

    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    rows = (
        client.table("articles")
        .select("id, title, published_at, source_url")
        .execute()
        .data
    ) or []
    print(f"Fetched {len(rows)} articles from Supabase")

    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    print(f"Cutoff date: {cutoff_str}  (articles before this will be deleted)\n")

    to_delete: list[tuple[str, str, str]] = []   # (id, title, reason)
    to_update: list[tuple[str, str, str, str]] = []  # (id, title, old_date, new_date)
    needs_scrape: list[dict] = []

    # ── Step 1: partition into confirmed-old vs needs-scraping ────────────────
    for row in rows:
        stored_raw = (row.get("published_at") or "").strip()
        stored_date = stored_raw[:10] if stored_raw else ""
        title = row.get("title", "")
        url = row.get("source_url", "")

        # Parse stored date
        stored_dt = _parse_iso(stored_raw) if stored_raw else None

        if stored_dt and _is_old(stored_dt):
            # Confirmed old by stored date — queue for deletion
            to_delete.append((row["id"], title, f"stored date {stored_date} is old"))
        elif stored_date == TODAY or not stored_date:
            # Today's fallback date — need to scrape
            needs_scrape.append(row)
        # else: stored date is recent and not today — trust it

    print(f"Confirmed old (stored date):  {len(to_delete)}")
    print(f"Needs URL scraping:           {len(needs_scrape)}")
    print(f"Trusted (recent stored date): {len(rows) - len(to_delete) - len(needs_scrape)}\n")

    # ── Step 2: scrape URLs for real publish dates ────────────────────────────
    print(f"Scraping {len(needs_scrape)} article URLs for real publish dates...")
    for i, row in enumerate(needs_scrape, 1):
        url = row.get("source_url", "")
        title = row.get("title", "")
        safe_title = title[:60].encode("ascii", errors="replace").decode("ascii")

        if not url:
            continue

        print(f"  [{i}/{len(needs_scrape)}] {safe_title}", end=" ... ", flush=True)
        real_date = _date_from_meta(url)

        if real_date:
            real_dt = _parse_iso(real_date)
            if real_dt and _is_old(real_dt):
                print(f"OLD ({real_date}) -> DELETE")
                to_delete.append((row["id"], title, f"real date {real_date} is old"))
            elif real_date != TODAY:
                print(f"updated to {real_date}")
                to_update.append((row["id"], title, TODAY, real_date))
            else:
                print("date confirmed as today")
        else:
            print("no date found — keeping")

        time.sleep(0.3)  # polite crawl delay

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"TO DELETE: {len(to_delete)} articles")
    for _, title, reason in to_delete:
        safe = title[:65].encode("ascii", errors="replace").decode("ascii")
        print(f"  DEL  [{reason}]  {safe}")

    print(f"\nTO UPDATE (date correction): {len(to_update)} articles")
    for _, title, old, new in to_update:
        safe = title[:55].encode("ascii", errors="replace").decode("ascii")
        print(f"  UPD  {old} -> {new}  {safe}")

    print(f"\n{'='*60}")

    # ── Step 4: Apply deletions ───────────────────────────────────────────────
    if to_delete:
        print(f"\nDeleting {len(to_delete)} old articles...")
        deleted = 0
        for aid, title, _ in to_delete:
            try:
                client.table("articles").delete().eq("id", aid).execute()
                deleted += 1
            except Exception as e:
                print(f"  Error deleting {aid}: {e}")
        print(f"Deleted {deleted}/{len(to_delete)}")

    # ── Step 5: Apply date updates ────────────────────────────────────────────
    if to_update:
        print(f"\nUpdating {len(to_update)} article dates...")
        updated = 0
        for aid, _, _, new_date in to_update:
            try:
                client.table("articles").update(
                    {"published_at": new_date + "T00:00:00Z"}
                ).eq("id", aid).execute()
                updated += 1
            except Exception as e:
                print(f"  Error updating {aid}: {e}")
        print(f"Updated {updated}/{len(to_update)}")

    remaining = len(rows) - len(to_delete)
    print(f"\nDone. {remaining} articles remain in Supabase.")


if __name__ == "__main__":
    main()
