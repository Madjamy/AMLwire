"""
Regenerate amlwire_title for all existing articles using their stored summary.

No web scraping needed — uses summary + enforcement_authority + financial_amount
+ key_entities already stored in Supabase to generate information-rich headlines.

Usage:
    python tools/regenerate_headlines.py
"""

import os
import sys
import json
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "xiaomi/mimo-v2-pro")

BATCH_SIZE = 30  # No scraping — can send more per batch


SYSTEM_PROMPT = """\
You are a headline writer for AMLWire, a financial crime intelligence platform for AML compliance professionals.

Given each article's SUMMARY, SOURCE TITLE, and metadata, generate an original AMLWire headline.

CRITICAL RULES:
- Base the headline on the SUMMARY content — extract specific names, amounts, authorities, and jurisdictions from it
- DO NOT rephrase or reorder the Source Title — the headline must be derived from the summary body
- Format: [Actor/Authority] [Strong Verb] [Entity/Subject] [Amount if known] [Jurisdiction/Context]
- Strong verbs: Charges, Sentences, Fines, Seizes, Freezes, Dismantles, Exposes, Flags, Bans, Warns, Uncovers, Convicts, Arrests, Penalises, Revokes, Suspends, Issues, Publishes
- Name the specific authority/actor and specific entity/person from the summary
- Include the exact amount with currency if mentioned in the summary
- Under 120 characters. Active voice. No hedge words ("allegedly", "reportedly").
- For regulatory guidance/typology articles: use "FATF Issues...", "AUSTRAC Publishes...", "FinCEN Issues Advisory on..." etc.

Good: "DOJ Charges Miami Developer with USD 45M Real Estate Money Laundering"
Good: "FATF Publishes Guidance on Virtual Assets and VASP Regulation"
Good: "AUSTRAC Fines Crown Resorts AUD 450M for Systemic AML Control Failures"
Bad: Any headline that just rewords the Source Title

Respond with ONLY a valid JSON array. Each element:
{"id": "<article_id>", "amlwire_title": "<generated headline>"}
"""


def _build_prompt(articles: list[dict]) -> str:
    lines = ["Generate an AMLWire headline for each article below:\n"]
    for a in articles:
        lines.append(f"--- Article ID: {a['id']} ---")
        lines.append(f"Source Title: {a.get('title', '')}")
        lines.append(f"Summary: {a.get('summary', '')}")
        if a.get("enforcement_authority"):
            lines.append(f"Enforcement Authority: {a['enforcement_authority']}")
        if a.get("financial_amount"):
            lines.append(f"Financial Amount: {a['financial_amount']}")
        if a.get("key_entities"):
            lines.append(f"Key Entities: {', '.join(a['key_entities'] or [])}")
        if a.get("publication_type"):
            lines.append(f"Publication Type: {a['publication_type']}")
        lines.append("")
    return "\n".join(lines)


def regenerate_headlines():
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    # Fetch all articles with their summary
    print("[Headlines] Fetching articles from Supabase...")
    resp = sb.table("articles").select(
        "id, title, summary, enforcement_authority, financial_amount, key_entities, publication_type"
    ).not_.is_("summary", "null").execute()

    articles = resp.data or []
    print(f"[Headlines] {len(articles)} articles with summaries found")

    if not articles:
        print("[Headlines] Nothing to update.")
        return

    updated = 0
    failed = 0

    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"[Headlines] Batch {batch_num}/{total_batches} ({len(batch)} articles)...")

        try:
            prompt = _build_prompt(batch)
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            results = json.loads(raw)
        except Exception as e:
            print(f"[Headlines] Batch {batch_num} failed: {e}")
            failed += len(batch)
            continue

        for item in results:
            article_id = item.get("id")
            headline = (item.get("amlwire_title") or "").strip()
            if not article_id or not headline:
                continue
            try:
                sb.table("articles").update({"amlwire_title": headline}).eq("id", article_id).execute()
                print(f"  ✓ {headline[:90]}")
                updated += 1
            except Exception as e:
                print(f"  ✗ Update failed for {article_id}: {e}")
                failed += 1

    print(f"\n[Headlines] Done — {updated} updated, {failed} failed")


if __name__ == "__main__":
    regenerate_headlines()
