"""
One-time backfill: Fetch Australian AML articles from March 1-18 2026.
Uses:
  1. Saved SerpAPI raw data (filtered to 2026 only)
  2. Fresh NewsAPI queries (multi-key rotation)
  3. Fresh Tavily queries (multi-key rotation)
Then dedup -> AI analysis -> upload.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path so 'from tools.X import Y' works
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

# Multi-key loading
NEWSAPI_KEYS = [k for k in [os.getenv(f"NEWSAPI_KEY_{i}") for i in range(1, 10)] if k]
NEWSAPI_URL = "https://newsapi.org/v2/everything"

TAVILY_API_KEYS = [k for k in [os.getenv(f"TAVILY_API_KEY{s}") for s in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"]] if k]
TAVILY_URL = "https://api.tavily.com/search"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Australia-focused queries
AU_QUERIES_NEWSAPI = [
    "Australia AUSTRAC money laundering",
    "Australian AML financial crime enforcement",
    "Australia sanctions compliance",
    "ASIC Australia financial crime fraud",
    "Australian Federal Police money laundering",
    "APRA Australia banking compliance",
    "Australia court convicted laundering fraud",
    "Australia scam fraud cyber crime",
    "AUSTRAC enforcement action penalty",
    "Australian financial crime prosecution",
]

AU_QUERIES_TAVILY = [
    "AUSTRAC money laundering enforcement Australia 2026",
    "Australia financial crime AML action 2026",
    "ASIC Australia enforcement penalty fraud 2026",
    "Australian Federal Police AFP money laundering 2026",
    "Scamwatch Australia fraud scam alert 2026",
    "Australia sanctions evasion compliance enforcement 2026",
    "AUSTRAC civil penalty compliance failure 2026",
    "Australian court money laundering conviction 2026",
]

FROM_DATE = "2026-03-01"
TO_DATE = "2026-03-18"
SERP_RAW_PATH = PROJECT_ROOT / ".tmp" / "au_backfill_raw.json"


def load_serp_filtered():
    """Load saved SerpAPI data, filtered to 2026 articles only."""
    if not SERP_RAW_PATH.exists():
        print("  [SerpAPI] No saved raw data found, skipping")
        return []

    with open(SERP_RAW_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    filtered = []
    for a in raw:
        pub = a.get("publishedAt", "")
        try:
            dt = datetime.strptime(pub.split(", +")[0], "%m/%d/%Y, %I:%M %p")
            if dt.year == 2026:
                filtered.append({
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "url": a.get("url", ""),
                    "publishedAt": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source_name": a.get("source_name", ""),
                    "country": "Australia",
                })
        except (ValueError, IndexError):
            continue

    print(f"  [SerpAPI] {len(raw)} total -> {len(filtered)} from 2026")
    return filtered


def fetch_newsapi_au():
    """Fetch Australian articles from NewsAPI with multi-key rotation."""
    articles = []
    key_idx = 0

    for query in AU_QUERIES_NEWSAPI:
        if key_idx >= len(NEWSAPI_KEYS):
            print("  [NewsAPI] All keys exhausted")
            break
        params = {
            "q": query,
            "from": FROM_DATE,
            "to": TO_DATE,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 20,
            "apiKey": NEWSAPI_KEYS[key_idx],
        }
        try:
            resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
            if resp.status_code == 429 or (resp.ok and resp.json().get("code") == "rateLimited"):
                key_idx += 1
                print(f"  [NewsAPI] Key {key_idx} rate-limited, switching to key {key_idx + 1}")
                continue
            resp.raise_for_status()
            results = resp.json().get("articles", [])
            for r in results:
                articles.append({
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                    "url": r.get("url", ""),
                    "publishedAt": r.get("publishedAt", ""),
                    "source_name": r.get("source", {}).get("name", ""),
                    "country": "Australia",
                })
            print(f"  [NewsAPI] '{query[:50]}' -> {len(results)} results")
        except Exception as e:
            print(f"  [NewsAPI] Error for '{query[:40]}': {e}")
        time.sleep(0.5)

    print(f"  [NewsAPI] Total: {len(articles)} raw articles")
    return articles


def fetch_tavily_au():
    """Fetch Australian articles from Tavily with multi-key rotation."""
    if not TAVILY_API_KEYS:
        print("  [Tavily] No API keys, skipping")
        return []

    articles = []
    key_idx = 0

    for query in AU_QUERIES_TAVILY:
        if key_idx >= len(TAVILY_API_KEYS):
            print("  [Tavily] All keys exhausted")
            break
        try:
            payload = {
                "api_key": TAVILY_API_KEYS[key_idx],
                "query": query,
                "search_depth": "advanced",
                "max_results": 10,
                "include_answer": False,
                "days": 18,
            }
            resp = requests.post(TAVILY_URL, json=payload, timeout=20)
            if resp.status_code == 432 or resp.status_code == 429:
                key_idx += 1
                print(f"  [Tavily] Key {key_idx} quota hit, switching to key {key_idx + 1}")
                continue
            resp.raise_for_status()
            results = resp.json().get("results", [])
            for r in results:
                articles.append({
                    "title": r.get("title", ""),
                    "description": r.get("content", "")[:500],
                    "url": r.get("url", ""),
                    "publishedAt": r.get("published_date", ""),
                    "source_name": r.get("url", "").split("/")[2] if r.get("url") else "",
                    "country": "Australia",
                })
            print(f"  [Tavily] '{query[:50]}' -> {len(results)} results")
        except Exception as e:
            print(f"  [Tavily] Error for '{query[:40]}': {e}")
        time.sleep(1)

    print(f"  [Tavily] Total: {len(articles)} raw articles")
    return articles


def dedup_against_supabase(articles):
    """Remove articles already in Supabase (by source_url)."""
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/articles?select=source_url",
        headers=headers,
    )
    resp.raise_for_status()
    existing_urls = {a["source_url"] for a in resp.json()}

    seen = set()
    deduped = []
    for a in articles:
        url = a.get("url", "")
        if url and url not in existing_urls and url not in seen:
            seen.add(url)
            deduped.append(a)

    print(f"  [Dedup] {len(articles)} -> {len(deduped)} after removing {len(articles) - len(deduped)} duplicates")
    return deduped


def main():
    print(f"{'='*60}")
    print(f"AUSTRALIA BACKFILL (v2): {FROM_DATE} to {TO_DATE}")
    print(f"Keys: {len(NEWSAPI_KEYS)} NewsAPI, {len(TAVILY_API_KEYS)} Tavily")
    print(f"{'='*60}\n")

    # Step 1: Load filtered SerpAPI data
    print("Step 1: Loading saved SerpAPI data (2026 only)...")
    serp = load_serp_filtered()
    print()

    # Step 2: Fresh NewsAPI
    print("Step 2: Fetching from NewsAPI...")
    newsapi = fetch_newsapi_au()
    print()

    # Step 3: Fresh Tavily
    print("Step 3: Fetching from Tavily...")
    tavily = fetch_tavily_au()
    print()

    all_articles = serp + newsapi + tavily
    print(f"Total raw: {len(all_articles)} articles (SerpAPI: {len(serp)}, NewsAPI: {len(newsapi)}, Tavily: {len(tavily)})\n")

    if not all_articles:
        print("No articles found. Exiting.")
        return

    # Step 4: Dedup
    print("Step 4: Deduplicating against Supabase...")
    deduped = dedup_against_supabase(all_articles)
    print()

    if not deduped:
        print("All articles already in database. Exiting.")
        return

    # Step 5: AI Analysis (smaller batches to avoid context overflow)
    print(f"Step 5: AI analysis on {len(deduped)} articles...")
    try:
        import tools.analyze_articles as aa
        original_batch_size = aa.BATCH_SIZE
        aa.BATCH_SIZE = 3  # Small batches for backfill reliability
        analyzed = aa.analyze_articles(deduped, backfill_mode=True)
        aa.BATCH_SIZE = original_batch_size
        print(f"  AI returned {len(analyzed)} articles after analysis\n")
    except Exception as e:
        print(f"  AI analysis failed: {e}")
        return

    if not analyzed:
        print("No articles passed AI analysis. Exiting.")
        return

    # Ensure country is set to Australia
    for a in analyzed:
        if not a.get("country"):
            a["country"] = "Australia"

    # Step 6: Upload
    print(f"Step 6: Uploading {len(analyzed)} articles to Supabase...")
    try:
        from tools.upload_supabase import upload_articles
        uploaded = upload_articles(analyzed)
        print(f"  {uploaded}/{len(analyzed)} articles uploaded\n")
    except Exception as e:
        print(f"  Upload failed: {e}")
        return

    print(f"{'='*60}")
    print(f"COMPLETE: {len(analyzed)} Australian articles backfilled")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
