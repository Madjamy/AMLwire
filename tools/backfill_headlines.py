"""
One-time backfill: re-generate amlwire_title for all existing articles using the
updated headline rules. Scrapes full article text, sends to LLM, updates Supabase.
"""

import os
import re
import json
import time
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "xiaomi/mimo-v2-pro")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

HEADERS_SUPABASE = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

SCRAPE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

client = OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

HEADLINE_PROMPT = """You are rewriting headlines for AMLWire.com, an AML compliance intelligence platform.

For each article below, you have the article's ID, its current headline, and the FULL SCRAPED ARTICLE TEXT.
Your job: write a NEW headline based on the full article text.

THE HEADLINE TEST: Would a compliance professional who has never seen this story understand WHAT HAPPENED and WHY IT MATTERS from the headline alone?

Principles:
1. TELL THE STORY — the headline must convey the narrative. A reader should think "I understand what happened."
2. IDENTIFY people/entities with context — never assume the reader knows who someone is. Use a descriptor: "Fugitive scam boss Chen Zhi" not just "Chen Zhi". "Turkish state bank Halkbank" not just "Halkbank". Well-known entities (DOJ, Binance, HSBC) need no descriptor.
3. ANSWER "SO WHAT?" — frame around why it matters, not just raw facts.
4. WRITE LIKE A NEWS EDITOR — Financial Times / Reuters quality, not a database entry.
5. NO PREFIXES — never start with country code ("US:", "India:"), source name ("CBC News:"), or category label.
6. Spell out uncommon acronyms (ED → Enforcement Directorate, HC → High Court). OK without expansion: DOJ, FBI, FATF, AUSTRAC, OFAC, FCA, Europol.
7. Under 120 characters. Active voice. No hedge words ("allegedly", "reportedly").
8. Never stack more than two nouns before the verb.
9. Base the headline on the FULL ARTICLE TEXT, not on rephrasing the current headline.

Return ONLY a valid JSON array. Each element: {"id": "<article id>", "new_headline": "<your headline>"}
"""


def scrape_article(url: str) -> str:
    """Scrape full article text, return up to 8000 chars."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    try:
        resp = requests.get(url, headers={
            "User-Agent": SCRAPE_USER_AGENTS[0],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }, timeout=15)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                          "form", "button", "noscript", "iframe", "svg"]):
            tag.decompose()
        body = None
        for selector in ["article", "main", ".article-body", ".entry-content",
                         ".post-content", ".story-body", "[itemprop='articleBody']"]:
            body = soup.select_one(selector)
            if body:
                break
        if not body:
            body = soup.body or soup
        text = body.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except Exception:
        return ""


def fetch_all_articles():
    """Fetch all articles from Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/articles?select=id,amlwire_title,source_url&order=created_at.desc"
    resp = requests.get(url, headers=HEADERS_SUPABASE)
    resp.raise_for_status()
    return resp.json()


def update_headline(article_id: str, new_headline: str):
    """Update a single article's amlwire_title in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/articles?id=eq.{article_id}"
    resp = requests.patch(url, headers=HEADERS_SUPABASE, json={"amlwire_title": new_headline})
    resp.raise_for_status()


def process_batch(batch: list[dict]) -> int:
    """Send a batch of articles to LLM for headline rewrite, update Supabase. Returns count updated."""
    # Build prompt with scraped text
    article_texts = []
    valid_articles = []
    for a in batch:
        scraped = scrape_article(a["source_url"])
        if len(scraped) < 100:
            print(f"  [SKIP] Could not scrape: {a['source_url'][:60]}")
            continue
        valid_articles.append(a)
        article_texts.append(
            f"ID: {a['id']}\n"
            f"CURRENT HEADLINE: {a['amlwire_title']}\n"
            f"ARTICLE TEXT:\n{scraped}\n"
        )

    if not article_texts:
        return 0

    prompt = HEADLINE_PROMPT + "\n\nARTICLES:\n\n" + "\n---\n\n".join(article_texts)

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        results = json.loads(content)
    except Exception as e:
        print(f"  [ERROR] LLM call failed: {e}")
        return 0

    updated = 0
    for r in results:
        aid = r.get("id")
        new_hl = r.get("new_headline", "").strip()
        if not aid or not new_hl:
            continue
        # Find original to print comparison
        orig = next((a for a in valid_articles if str(a["id"]) == str(aid)), None)
        old_hl = orig["amlwire_title"] if orig else "?"
        try:
            update_headline(aid, new_hl)
            print(f"  BEFORE: {old_hl}")
            print(f"  AFTER:  {new_hl}")
            print()
            updated += 1
        except Exception as e:
            print(f"  [ERROR] Update failed for {aid}: {e}")

    return updated


def main():
    print("Fetching all articles from Supabase...")
    articles = fetch_all_articles()
    print(f"Found {len(articles)} articles.\n")

    batch_size = 5  # smaller batches = better scraping + LLM quality
    total_updated = 0
    total_skipped = 0

    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(articles) + batch_size - 1) // batch_size
        print(f"=== Batch {batch_num}/{total_batches} ({len(batch)} articles) ===")
        updated = process_batch(batch)
        total_updated += updated
        total_skipped += len(batch) - updated
        print(f"  Batch done: {updated} updated, {len(batch) - updated} skipped\n")
        # Small delay between batches to avoid rate limits
        if i + batch_size < len(articles):
            time.sleep(2)

    print(f"\n{'='*50}")
    print(f"COMPLETE: {total_updated} headlines updated, {total_skipped} skipped")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
