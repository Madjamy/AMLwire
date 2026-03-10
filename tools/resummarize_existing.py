"""
Re-analyze existing articles in Supabase with the improved prompt and new model.
Fetches all articles, re-runs AI analysis, and updates the `summary` field.

Usage:
    python tools/resummarize_existing.py
"""

import os
import json
import sys
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = "x-ai/grok-4.1-fast"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are a financial crime intelligence analyst. For each article provided, write a detailed factual summary.

The summary MUST include ALL of the following where available:
1. What specifically happened (the event, action, or finding) and when
2. The exact entity, institution, person, or country involved — include names and locations
3. The financial amount, scale, or timeframe if mentioned
4. The AML/financial crime angle — the specific method or typology used (e.g. shell companies, crypto mixing, smurfing, trade-based laundering)
5. The enforcement outcome, regulatory action, or legal consequence (arrest, fine, conviction, designation, etc.)
6. Why it is significant for AML compliance, financial crime prevention, or the broader regulatory landscape

OUTPUT FORMAT
Respond with ONLY a valid JSON array. No markdown, no explanation.
Each element must have exactly these two fields:
{
  "source_url": "...",
  "summary": "5-6 sentence factual summary covering: what happened and when, who was involved and where, the financial scale, the specific AML typology or method, the enforcement outcome, and the broader significance."
}

Do not invent facts. If a detail is not in the title or description, omit it — do not guess.
"""


def _call_ai_for_summaries(client, articles: list[dict]) -> list[dict]:
    lines = ["Re-summarize the following AML news articles:\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"--- Article {i} ---")
        lines.append(f"Title: {a.get('title', '')}")
        lines.append(f"Source: {a.get('source_name', a.get('source', ''))}")
        lines.append(f"URL: {a.get('source_url', a.get('url', ''))}")
        lines.append(f"Published: {a.get('published_at', '')}")
        lines.append(f"Existing summary: {a.get('summary', '')}")
        lines.append(f"Raw snippet: {a.get('raw_snippet', '')}")
        lines.append("")

    user_prompt = "\n".join(lines)
    raw = ""
    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=16000,
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, list):
            result = [result]
        return result

    except json.JSONDecodeError as e:
        print(f"[Resummarize] JSON parse error: {e}")
        print(f"[Resummarize] Raw: {raw[:500]}")
        return []
    except Exception as e:
        print(f"[Resummarize] API error: {e}")
        return []


def resummarize_all():
    from supabase import create_client

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not supabase_url or not supabase_key:
        print("SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    client_sb = create_client(supabase_url, supabase_key)

    print("[Resummarize] Fetching all articles from Supabase...")
    resp = client_sb.table("articles").select("id,title,source_name,source_url,published_at,summary,raw_snippet").execute()
    articles = resp.data or []
    print(f"[Resummarize] {len(articles)} articles fetched")

    if not articles:
        print("No articles found.")
        return

    client_ai = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    BATCH_SIZE = 15  # smaller batches for quality
    updated = 0

    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i: i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"[Resummarize] Batch {batch_num}/{total_batches} ({len(batch)} articles)...")

        summaries = _call_ai_for_summaries(client_ai, batch)

        # Build a lookup by source_url
        summary_map = {s["source_url"]: s["summary"] for s in summaries if "source_url" in s and "summary" in s}

        for article in batch:
            url = article.get("source_url", "")
            new_summary = summary_map.get(url)
            if new_summary and new_summary != article.get("summary"):
                try:
                    client_sb.table("articles").update({"summary": new_summary}).eq("id", article["id"]).execute()
                    print(f"  Updated: {article.get('title', '')[:60]}")
                    updated += 1
                except Exception as e:
                    print(f"  Failed to update {url}: {e}")
            else:
                print(f"  No change or not returned: {article.get('title', '')[:60]}")

    print(f"\n[Resummarize] Done. {updated}/{len(articles)} articles updated.")


if __name__ == "__main__":
    resummarize_all()
