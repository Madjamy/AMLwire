"""
Analyze and summarize AML articles using OpenRouter (Claude via OpenAI-compatible API).
Returns structured JSON with: title, date, region, source, url, summary, aml_typology.
"""

import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "x-ai/grok-4.1-fast")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are a financial crime intelligence analyst specializing in:
- Money laundering
- AML failures and typologies
- Tax evasion and tax fraud
- Sanctions violations and sanctions evasion
- Financial crime linked to human trafficking, drug trafficking, terror financing, organized crime
- Cybercrime with financial crime relevance

TOPIC FILTER
Only include items materially related to one or more of:
- Money laundering
- AML enforcement, AML control failures, compliance breakdowns, suspicious transaction failures
- Tax evasion or tax fraud
- Sanctions violations or sanctions evasion
- Financial crime tied to trafficking, terrorism financing, organized crime
- Cybercrime with clear financial crime relevance

TYPOLOGY ANALYSIS RULE
You MUST select aml_typology from ONLY the following standardised list. Use the exact label as written:

Layering and placement
Structuring / smurfing
Trade-based money laundering
Shell companies and beneficial ownership concealment
Crypto mixing and tumbling
Cryptocurrency-based laundering
Sanctions evasion
Mule accounts
Hawala and informal value transfer
Professional enablers
Darknet-enabled laundering
Cash-intensive business laundering
Real estate laundering
Offshore concealment
Terror financing
Cyber-enabled fraud laundering
Drug trafficking proceeds laundering
Human trafficking proceeds laundering
AML compliance failure
General AML news

Pick the single best-fit label. If multiple apply, choose the most specific one.
If none fit, use: "General AML news"

CATEGORY RULE
- Set category = "typology" if the label is anything other than "General AML news" or "AML compliance failure"
- Set category = "news" for "General AML news" or "AML compliance failure"

COUNTRY/REGION RULE
- country: the most specific country identified (e.g. "Australia", "India")
- region: broader region (e.g. "Asia-Pacific", "Europe", "Middle East", "Americas")
- If country not clearly identified, set country = null

TAGS RULE
Generate 3-6 short lowercase tags relevant to the article (e.g. ["sanctions", "shell-company", "crypto", "fatf", "australia"])

DATE VERIFICATION RULE (CRITICAL)
- Read the article title and description carefully for any date signals (e.g. "March 2026", "last week", "this year", "2025", "2024")
- If the content clearly indicates the article is from more than 7 days before today's date, EXCLUDE it entirely — do not include it in the output
- If the provided Published date looks like a placeholder (e.g. today's date on clearly old content), use the date from the article text instead
- Only include articles that appear to be genuinely recent (within the last 7 days of today's date)
- For published_date in output: use the date found in the article content if it differs from the provided date; format as DD-MM-YYYY

QUALITY RULES
- Do not invent missing facts
- Do not guess typologies beyond what the title/description supports

OUTPUT FORMAT
Respond with ONLY a valid JSON array. No markdown, no explanation.
Each element must have exactly these fields:
{
  "title": "...",
  "published_date": "DD-MM-YYYY",
  "country": "..." or null,
  "region": "...",
  "source_name": "...",
  "source_url": "...",
  "summary": "5-6 sentence factual summary. Must include: (1) what specifically happened and when, (2) the exact entity, institution, or person involved and their country/city, (3) the financial amount or scale if mentioned, (4) the AML/financial crime angle — the specific method used or typology involved, (5) the enforcement outcome, regulatory action, or legal consequence, (6) why it is significant for AML compliance or financial crime prevention.",
  "aml_typology": "...",
  "category": "news" or "typology",
  "tags": ["tag1", "tag2", "tag3"]
}

If an article is clearly not relevant to AML/financial crime topics, exclude it.
All articles provided have already passed a keyword relevance filter, so most should be included.
Return ALL relevant articles — do not cap or trim the list.
"""


def _build_user_prompt(articles: list[dict], current_date: str) -> str:
    lines = [f"Today's date: {current_date}. Only include articles published within the last 7 days (on or after the date 7 days ago). Exclude any article whose content signals it is older than that.\n\nAnalyze the following articles and return a JSON array:\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"--- Article {i} ---")
        lines.append(f"Title: {a.get('title', '')}")
        lines.append(f"Source: {a.get('source', '')}")
        lines.append(f"URL: {a.get('url', '')}")
        lines.append(f"Country (if known): {a.get('country', '')}")
        lines.append(f"Published: {a.get('published_at', '')}")
        lines.append(f"Description: {a.get('description', '')}")
        lines.append("")
    return "\n".join(lines)


BATCH_SIZE = 50  # articles per AI call

# Canonical typology vocabulary — AI must pick from this list
CANONICAL_TYPOLOGIES = {
    "Layering and placement",
    "Structuring / smurfing",
    "Trade-based money laundering",
    "Shell companies and beneficial ownership concealment",
    "Crypto mixing and tumbling",
    "Cryptocurrency-based laundering",
    "Sanctions evasion",
    "Mule accounts",
    "Hawala and informal value transfer",
    "Professional enablers",
    "Darknet-enabled laundering",
    "Cash-intensive business laundering",
    "Real estate laundering",
    "Offshore concealment",
    "Terror financing",
    "Cyber-enabled fraud laundering",
    "Drug trafficking proceeds laundering",
    "Human trafficking proceeds laundering",
    "AML compliance failure",
    "General AML news",
}


def _normalise_typology(typology: str) -> str:
    """
    Snap an AI-returned typology to the closest canonical label.
    If it's already canonical, return as-is.
    Otherwise log a warning and fall back to 'General AML news'.
    """
    if typology in CANONICAL_TYPOLOGIES:
        return typology
    # Case-insensitive exact match
    lower = typology.lower()
    for canon in CANONICAL_TYPOLOGIES:
        if canon.lower() == lower:
            return canon
    # Keyword-based fuzzy match
    keyword_map = [
        (["layering", "placement"],                         "Layering and placement"),
        (["structuring", "smurfing"],                       "Structuring / smurfing"),
        (["trade-based", "tbml", "invoice fraud"],          "Trade-based money laundering"),
        (["shell compan", "beneficial owner"],              "Shell companies and beneficial ownership concealment"),
        (["mixing", "tumbl", "mixer"],                      "Crypto mixing and tumbling"),
        (["crypto", "blockchain", "defi", "virtual asset"], "Cryptocurrency-based laundering"),
        (["sanction"],                                      "Sanctions evasion"),
        (["mule"],                                          "Mule accounts"),
        (["hawala", "informal value"],                      "Hawala and informal value transfer"),
        (["professional enabler", "accountant", "lawyer"],  "Professional enablers"),
        (["darknet", "dark web"],                           "Darknet-enabled laundering"),
        (["cash-intensive", "cash intensive"],              "Cash-intensive business laundering"),
        (["real estate", "property"],                       "Real estate laundering"),
        (["offshore"],                                      "Offshore concealment"),
        (["terror"],                                        "Terror financing"),
        (["cyber", "fraud laundering"],                     "Cyber-enabled fraud laundering"),
        (["drug", "narco"],                                 "Drug trafficking proceeds laundering"),
        (["human trafficking", "trafficking"],              "Human trafficking proceeds laundering"),
        (["compliance fail", "aml fail", "control fail"],   "AML compliance failure"),
    ]
    for keywords, canon in keyword_map:
        if any(kw in lower for kw in keywords):
            print(f"[Analyze] Typology normalised: '{typology}' -> '{canon}'")
            return canon
    print(f"[Analyze] Unknown typology, defaulting to General AML news: '{typology}'")
    return "General AML news"


def _call_ai(client, articles: list[dict], current_date: str) -> list[dict]:
    """Send one batch of articles to OpenRouter. Returns structured list."""
    raw = ""
    try:
        user_prompt = _build_user_prompt(articles, current_date)
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=32000,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, list):
            result = [result]
        # Snap any non-canonical typology to the closest standard label
        for item in result:
            if "aml_typology" in item:
                item["aml_typology"] = _normalise_typology(item["aml_typology"])
        return result

    except json.JSONDecodeError as e:
        print(f"[Analyze] JSON parse error: {e}")
        print(f"[Analyze] Raw response: {raw[:500]}")
        return []
    except Exception as e:
        print(f"[Analyze] OpenRouter API error: {e}")
        return []


def analyze_articles(articles: list[dict]) -> list[dict]:
    """
    Send articles to OpenRouter for analysis in batches of 50.
    Returns a list of structured dicts ready for Supabase upload.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set in .env")
    if not articles:
        return []

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
    )

    current_date = datetime.now(timezone.utc).strftime("%d-%m-%Y")
    all_analyzed = []

    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i: i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"[Analyze] Batch {batch_num}/{total_batches} ({len(batch)} articles)...")
        results = _call_ai(client, batch, current_date)
        all_analyzed.extend(results)

    print(f"[Analyze] {len(all_analyzed)} articles analyzed and structured")
    return all_analyzed


if __name__ == "__main__":
    # Quick test with sample data
    sample = [
        {
            "title": "AUSTRAC fines Westpac $1.3 billion for AML failures",
            "source": "Reuters",
            "url": "https://example.com/westpac-aml",
            "published_at": "2025-03-08T10:00:00Z",
            "description": "Australia's financial intelligence agency fined Westpac for failing to report suspicious transactions linked to child exploitation.",
        }
    ]
    result = analyze_articles(sample)
    print(json.dumps(result, indent=2))
