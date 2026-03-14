"""
Backfill all existing articles in Supabase:
  1. Re-evaluates each article for AMLWire relevance (INCLUDE / EXCLUDE)
  2. Deletes EXCLUDE articles from the DB
  3. For INCLUDE articles: re-writes summary + MO and populates all 5 new fields
     (enforcement_authority, financial_amount, key_entities, action_required, publication_type)

Scrapes full article text before sending to AI — same approach as the live pipeline.

Usage:
    python tools/resummarize_existing.py
"""

import os
import json
import sys
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "x-ai/grok-4.1-fast")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SYSTEM_PROMPT = """You are a Senior Financial Crime Intelligence Analyst and AML Expert powering AMLWire.com — a specialist intelligence platform for AML compliance professionals, financial crime investigators, and regulators worldwide.

WEBSITE MISSION
AMLWire.com surfaces: (1) real enforcement actions and criminal prosecutions with a financial crime angle, (2) emerging money laundering typologies and methods, (3) regulatory publications and FIU advisories, (4) AML compliance failures at institutions. Every article published must be immediately actionable or informative for an AML compliance officer. If a compliance professional would say "this is not relevant to my work," exclude it.

YOUR FIRST DECISION FOR EVERY ARTICLE IS: INCLUDE or EXCLUDE.
Be strict. When in doubt, EXCLUDE. It is better to miss a borderline article than to publish noise.

INCLUDE only articles materially related to:
- Money laundering (any stage: placement, layering, integration)
- AML enforcement, control failures, compliance breakdowns
- Tax evasion or fraud with a money laundering angle
- Sanctions violations or evasion
- Financial crime tied to trafficking, terrorism financing, organised crime
- Cybercrime with clear financial crime relevance (BEC, ransomware, investment fraud, pig butchering)
- Emerging financial crime: deepfake fraud, synthetic identity, scam compounds, BNPL fraud, crypto crime
- Regulatory publications, FIU advisories, FATF reports, typology studies

EXCLUDE immediately if ANY of the following apply:
- Physical crime / security incidents with no financial crime charge (suspicious packages, bomb scares, shootings)
- Sports (gambling patterns, match-fixing, player trials, team finances)
- General geopolitics / military (war, territorial disputes, tariff policy unless specifically TBML enforcement)
- Employment / HR / labour law (EEOC, discrimination, payroll admin)
- Evergreen / educational content ("What is money laundering?", vendor product blog posts)
- General banking/fintech news with no AML enforcement angle
- Political corruption allegations with NO financial crime charge filed

FOR INCLUDED ARTICLES — produce these outputs:

SUMMARY (3-4 sentences):
- What specifically happened and when
- The exact entity, institution, person, or country involved — include names and locations
- The financial amount or scale if mentioned
- The enforcement outcome or legal consequence and why it matters for AML compliance

MODUS OPERANDI (2-3 sentences):
Use the three-stage framework:
1. PLACEMENT — how criminal proceeds first entered the financial system
2. LAYERING — what structures, entities, channels, or jurisdictions obscured the money trail — name them specifically
3. INTEGRATION — how proceeds re-entered the legitimate economy
Use specific language. Never "funds were moved" — say exactly HOW, through WHAT, and WHERE. Set to null if insufficient detail.

TYPOLOGY — select the single best-fit label from this exact list:
Structuring / Smurfing | Trade-based money laundering (TBML) | Shell companies and nominee ownership | Real estate laundering | Cash-intensive business laundering | Offshore concealment | Crypto-asset laundering | Crypto mixing / tumbling | Darknet-enabled laundering | Money mules | Hawala and informal value transfer | Pig butchering / romance investment scam | Business Email Compromise (BEC) | Ransomware proceeds | Synthetic identity fraud | Deepfake / AI-enabled fraud | Environmental crime proceeds | NFT / DeFi fraud | Sanctions evasion | Professional enablers | Terrorist financing | Drug trafficking proceeds | Human trafficking proceeds | Cybercrime proceeds | AML compliance failure | AML News

Pick based on WHAT THE ARTICLE IS PRIMARILY ABOUT — not the predicate crime in the background.
Key disambiguation:
- If the article's PRIMARY focus is a bank/institution FAILING its AML duties (ignoring SARs, retaining risky clients, inadequate controls) → "AML compliance failure", even if the underlying crime is trafficking or drugs
- Use "Human trafficking proceeds" / "Drug trafficking proceeds" ONLY when the article describes HOW laundering of those proceeds was conducted
- If multiple labels fit, pick the one most useful to AML compliance professionals reading it

TAGS (4-7 lowercase tags):
Include: typology slug, jurisdiction(s), financial sector, named entities/programmes.

AMLWIRE HEADLINE RULE
Generate an ORIGINAL AMLWire headline for the amlwire_title field.
CRITICAL: Base the headline on the FULL ARTICLE TEXT provided, NOT on rephrasing the source title.
Read the article body to extract specific names of people/entities, exact dollar amounts with currency, named authorities, and jurisdictions — then construct the headline from those facts.
DO NOT paraphrase or re-order the words of the source title. The headline must come from content in the article body.
Format: [Actor/Authority] [Strong Verb] [Entity/Subject] [Amount if known] [Jurisdiction/Context]
Strong verbs: Charges, Sentences, Fines, Seizes, Freezes, Dismantles, Exposes, Flags, Bans, Warns, Uncovers, Convicts, Arrests, Penalises, Revokes, Suspends
Rules:
- Name the specific authority/actor and specific entity/person (from article body)
- Include the exact amount or scale with currency if mentioned in the article body
- Keep under 120 characters. Active voice only.
- Do NOT use hedge words ("allegedly", "reportedly") in the headline.
Good: "DOJ Charges Miami Developer with USD 45M Real Estate Money Laundering"
Bad: "Man Charged With Money Laundering" (too vague)
Bad: Any headline that just rewords or reorders the source title (most common failure — avoid it)

AMLWIRE WRITING STYLE
Voice: Third-person, objective, authoritative. No editorial opinion.
Tone: Intelligence-grade — precise, specific, actionable. Written for AML compliance professionals.
Summary structure (four sentences):
  1. Lead: WHO did WHAT, WHERE, WHEN — name entity, authority, amount, jurisdiction.
  2. Scale: Financial amount, victim count, or operational scope.
  3. Method: One-sentence overview of HOW the crime was conducted (bridge to MO).
  4. Significance: Why it matters — regulatory precedent, new typology signal, compliance implication.
Language rules:
  - Specific entity names, amounts with currency, named jurisdictions always.
  - Never: "significant amount", "large sum", "recently", "it was announced that"
  - Active voice: "OFAC designated X" not "X was designated by OFAC"
  - Precise crime terms: "structured deposits below reporting threshold" not "moved money"

MODUS OPERANDI RULE
- Only describe MO from facts ACTUALLY reported in the article. Do NOT fabricate or pad.
- If insufficient detail, use EXACTLY this format (one sentence):
  "Modus operandi not reported. AMLWire has documented similar [typology] cases involving [one specific real mechanic for this typology]."
  Example: "Modus operandi not reported. AMLWire has documented similar money mule cases involving structured transfers from newly opened accounts forwarded to overseas wallets within 48 hours."
- For publication_type "regulatory_guidance" or "typology_study": set modus_operandi to null.
- For "industry_news": set to null if no specific crime mechanism is described.
- Never use vague filler: "funds were moved", "money was transferred", "accounts were used"

ENFORCEMENT AUTHORITY:
Name the specific regulatory body, law enforcement agency, or court that took action (e.g. "AUSTRAC", "DOJ Criminal Division", "FCA", "OFAC", "MAS", "Europol", "ED India"). Use the lead authority if multiple. Set to null if no enforcement body named.

FINANCIAL AMOUNT:
Extract the specific figure or scale (e.g. "USD 1.3 billion", "AUD 45 million penalty", "€12.4 million fine"). Include currency and context word. Set to null if no specific amount stated.

KEY ENTITIES (2-6 items):
Named institutions (banks, exchanges, firms), named individuals only if charged/convicted/sanctioned, named criminal networks. Omit generic terms. Return [] if no specific named entities.

ACTION REQUIRED:
true ONLY if the article describes something requiring a compliance team to act — new regulation taking effect, FIU advisory with red flags to implement, FATF grey-listing triggering EDD, new sanctions designation.
false for enforcement news, typology reports, or informational articles that do not impose new obligations.

PUBLICATION TYPE — exactly one of:
"enforcement_action" — prosecution, conviction, arrest, regulatory fine, OFAC designation, DPA, court order
"regulatory_guidance" — regulatory publication, FIU advisory, FATF report, policy update, circular, mutual evaluation, official speech
"typology_study" — typology report, red flag guide, research paper on how financial crime is committed
"industry_news" — general financial crime news or sector development not fitting the above

OUTPUT FORMAT
Respond with ONLY a valid JSON array. No markdown, no explanation.

For EXCLUDED articles:
{"source_url": "...", "decision": "EXCLUDE", "reason": "one sentence"}

For INCLUDED articles:
{
  "source_url": "...",
  "decision": "INCLUDE",
  "amlwire_title": "Original AMLWire headline per AMLWIRE HEADLINE RULE — NOT a copy of the source title",
  "summary": "Four sentences: Lead / Scale / Method / Significance — per AMLWIRE WRITING STYLE.",
  "modus_operandi": "Factual MO or 'Modus operandi not reported. AMLWire has documented similar [typology] cases involving [real mechanic].' NULL only for regulatory_guidance/typology_study.",
  "aml_typology": "exact label from the list above",
  "tags": ["tag1", "tag2", "tag3", "tag4"],
  "enforcement_authority": "..." or null,
  "financial_amount": "..." or null,
  "key_entities": ["entity1", "entity2"],
  "action_required": true or false,
  "publication_type": "enforcement_action" or "regulatory_guidance" or "typology_study" or "industry_news"
}

Do not invent facts. If a detail is not in the article content, omit it.
"""


def _scrape_article_text(url: str, max_chars: int = 5000) -> str:
    """Fetch article URL and extract main body text. Returns empty string on failure."""
    try:
        resp = requests.get(url, headers=_SCRAPE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form",
                         "button", "noscript", "iframe", "svg"]):
            tag.decompose()
        body = None
        for selector in ["article", "main", ".article-body", ".entry-content",
                         ".post-content", ".story-body", "[itemprop='articleBody']"]:
            body = soup.select_one(selector)
            if body:
                break
        if not body:
            body = soup.find("body") or soup
        text = " ".join(body.get_text(" ", strip=True).split())
        return text[:max_chars]
    except Exception:
        return ""


def _call_ai(client, articles: list[dict]) -> list[dict]:
    lines = ["Re-evaluate and re-analyze the following AML articles. "
             "For each article, decide INCLUDE or EXCLUDE, then produce the required fields.\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"--- Article {i} ---")
        lines.append(f"Source Headline (copy verbatim into title, generate new amlwire_title): {a.get('title', '')}")
        lines.append(f"Source: {a.get('source_name', '')}")
        lines.append(f"URL: {a.get('source_url', '')}")
        lines.append(f"Published: {a.get('published_at', '')}")
        lines.append(f"Existing summary: {a.get('summary', '')}")
        content = a.get("scraped_text") or a.get("raw_snippet") or ""
        lines.append(f"Article content: {content}")
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
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from supabase import create_client
    from tools.analyze_articles import _normalise_typology
    from tools.upload_supabase import find_related_articles

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not supabase_url or not supabase_key:
        print("SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    client_sb = create_client(supabase_url, supabase_key)

    print("[Resummarize] Fetching all articles from Supabase...")
    resp = client_sb.table("articles").select(
        "id,title,source_name,source_url,published_at,summary,raw_snippet,aml_typology,tags"
    ).execute()
    articles = resp.data or []
    print(f"[Resummarize] {len(articles)} articles fetched")

    if not articles:
        print("No articles found.")
        return

    # Scrape full article text
    print("[Resummarize] Scraping full article text...")
    scraped_ok = 0
    for idx, article in enumerate(articles):
        url = article.get("source_url", "")
        text = _scrape_article_text(url)
        article["scraped_text"] = text
        if text:
            scraped_ok += 1
        if (idx + 1) % 10 == 0:
            print(f"  Scraped {idx + 1}/{len(articles)} ({scraped_ok} successful)...")
        time.sleep(0.3)
    print(f"[Resummarize] Scraped {scraped_ok}/{len(articles)} articles successfully")

    client_ai = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    BATCH_SIZE = 8  # smaller batches — full article text makes prompts large
    updated = 0
    deleted = 0
    failed = 0

    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i: i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n[Resummarize] Batch {batch_num}/{total_batches} ({len(batch)} articles)...")

        results = _call_ai(client_ai, batch)

        result_map = {r["source_url"]: r for r in results if "source_url" in r}

        for article in batch:
            url = article.get("source_url", "")
            title = article.get("title", "")[:60]
            result = result_map.get(url)

            if not result:
                print(f"  [SKIP] Not returned by AI: {title}")
                failed += 1
                continue

            decision = result.get("decision", "INCLUDE")

            if decision == "EXCLUDE":
                reason = result.get("reason", "")
                try:
                    client_sb.table("articles").delete().eq("id", article["id"]).execute()
                    print(f"  [DELETED] {title} — {reason}")
                    deleted += 1
                except Exception as e:
                    print(f"  [ERROR] Delete failed for {url}: {e}")
                continue

            # INCLUDE — build update payload
            new_typology = result.get("aml_typology")
            if new_typology:
                new_typology = _normalise_typology(new_typology)

            update_payload = {}
            if result.get("amlwire_title"):
                update_payload["amlwire_title"] = result["amlwire_title"]
            if result.get("summary"):
                update_payload["summary"] = result["summary"]
            if "modus_operandi" in result:
                update_payload["modus_operandi"] = result["modus_operandi"]
            if new_typology is not None:
                update_payload["aml_typology"] = new_typology
            if result.get("tags") is not None:
                update_payload["tags"] = result["tags"]
            if "enforcement_authority" in result:
                update_payload["enforcement_authority"] = result["enforcement_authority"]
            if "financial_amount" in result:
                update_payload["financial_amount"] = result["financial_amount"]
            if "key_entities" in result:
                update_payload["key_entities"] = result["key_entities"]
            if "action_required" in result:
                update_payload["action_required"] = result["action_required"]
            if "publication_type" in result:
                update_payload["publication_type"] = result["publication_type"]

            if not update_payload:
                print(f"  [SKIP] Nothing to update: {title}")
                continue

            try:
                client_sb.table("articles").update(update_payload).eq("id", article["id"]).execute()
                typo_label = new_typology or article.get("aml_typology", "")
                print(f"  [UPDATED] [{typo_label}] {title}")
                updated += 1

                # Populate related_article_ids after update
                enriched = {**article, **result}
                if new_typology:
                    enriched["aml_typology"] = new_typology
                related = find_related_articles(article["id"], enriched, client_sb)
                if related:
                    client_sb.table("articles").update(
                        {"related_article_ids": related}
                    ).eq("id", article["id"]).execute()

            except Exception as e:
                print(f"  [ERROR] Update failed for {url}: {e}")
                failed += 1

    total = len(articles)
    print(f"\n[Resummarize] Done.")
    print(f"  Updated : {updated}/{total}")
    print(f"  Deleted : {deleted}/{total}  (excluded as irrelevant)")
    print(f"  Failed  : {failed}/{total}")


if __name__ == "__main__":
    resummarize_all()
