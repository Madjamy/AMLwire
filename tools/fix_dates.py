"""
One-off fix: scrape real publish dates for articles where published_at == fetched_at (fallback date).
"""
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def to_iso(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    # Already ISO: 2026-03-06 or 2026-03-06T...
    m = re.match(r"(20\d\d)-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # RFC: "Fri, 06 Mar 2026 09:30:06 GMT" or "06 Mar 2026"
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d\d)", raw, re.I)
    if m:
        mo = MONTH_MAP[m.group(2)[:3].lower()]
        return f"{m.group(3)}-{mo}-{int(m.group(1)):02d}"
    # "March 09, 2026" or "Mar 9, 2026"
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)", raw, re.I)
    if m:
        mo = MONTH_MAP[m.group(1)[:3].lower()]
        return f"{m.group(3)}-{mo}-{int(m.group(2)):02d}"
    return None


def get_real_date(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    for key in ("datePublished", "dateCreated"):
                        val = item.get(key, "")
                        if val and "202" in str(val):
                            result = to_iso(str(val))
                            if result:
                                return result
            except Exception:
                pass

        # 2. Meta tags
        for attr, name in [
            ("property", "article:published_time"),
            ("property", "og:article:published_time"),
            ("name", "pubdate"),
            ("name", "date"),
            ("itemprop", "datePublished"),
        ]:
            tag = soup.find("meta", {attr: name})
            if tag:
                val = tag.get("content", "")
                if val and "202" in val:
                    result = to_iso(val)
                    if result:
                        return result

        # 3. <time datetime=...>
        t = soup.find("time", attrs={"datetime": True})
        if t and "202" in t["datetime"]:
            result = to_iso(t["datetime"])
            if result:
                return result

        return None
    except Exception:
        return None


def fix_dates():
    client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

    rows = client.table("articles").select("id, title, source_url, published_at, fetched_at").execute().data or []
    suspect = [
        r for r in rows
        if (r.get("published_at") or "")[:10] == (r.get("fetched_at") or "")[:10]
    ]
    print(f"Articles with pub_date == fetch_date (to fix): {len(suspect)}")

    fixed = 0
    for i, r in enumerate(suspect):
        url = r.get("source_url", "")
        real_date = get_real_date(url)
        current = (r.get("published_at") or "")[:10]

        if real_date and real_date != current and re.match(r"20\d\d-\d\d-\d\d", real_date):
            try:
                client.table("articles").update(
                    {"published_at": real_date + "T00:00:00Z"}
                ).eq("id", r["id"]).execute()
                print(f"  Fixed: {current} -> {real_date} | {r['title'][:60]}")
                fixed += 1
            except Exception as e:
                print(f"  DB error for {url[:60]}: {e}")

        if (i + 1) % 25 == 0:
            print(f"  Progress: {i + 1}/{len(suspect)} ({fixed} fixed)...")
        time.sleep(0.25)

    print(f"\nDone. Fixed {fixed}/{len(suspect)} article dates.")


if __name__ == "__main__":
    fix_dates()
