"""
Generate typology summaries from today's analyzed articles.
Groups articles by aml_typology, then uses AI to write a synthesis
paragraph per typology explaining the method, how it appeared today,
and which countries were involved.

Output goes to the typology_summaries Supabase table.
"""

import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SKIP_TYPOLOGIES = {"General AML news"}  # Too generic to synthesize meaningfully

SYSTEM_PROMPT = """You are a senior AML typology analyst writing concise briefings for financial crime compliance professionals.

For each money laundering typology you receive, write a clear, authoritative synthesis that:
1. Briefly explains what the typology IS (1 sentence)
2. Summarises how it appeared in today's reporting (2-3 sentences referencing the cases)
3. Notes which countries or regions were involved
4. Highlights any emerging patterns or risk signals

Keep each summary to 3-4 sentences total. Use precise, professional language.
Do not repeat the article titles verbatim — synthesise and interpret.

OUTPUT FORMAT
Respond with ONLY a valid JSON array. No markdown, no explanation.
Each element:
{
  "typology_name": "...",
  "summary": "3-4 sentence synthesis",
  "countries_involved": ["Country1", "Country2"],
  "article_count": <integer>
}
"""


def _group_by_typology(articles: list[dict]) -> dict[str, list[dict]]:
    """Group articles by their aml_typology field."""
    groups: dict[str, list[dict]] = {}
    for a in articles:
        typology = (a.get("aml_typology") or "General AML news").strip()
        if typology in SKIP_TYPOLOGIES:
            continue
        groups.setdefault(typology, []).append(a)
    return groups


def _build_prompt(groups: dict[str, list[dict]], current_date: str) -> str:
    lines = [f"Today's date: {current_date}\n\nGenerate typology summaries for the following groups:\n"]
    for typology, articles in groups.items():
        lines.append(f"=== TYPOLOGY: {typology} ===")
        lines.append(f"Number of articles: {len(articles)}")
        for i, a in enumerate(articles, 1):
            lines.append(f"  Article {i}:")
            lines.append(f"    Title: {a.get('title', '')}")
            lines.append(f"    Country: {a.get('country', a.get('region', 'Unknown'))}")
            lines.append(f"    Summary: {a.get('summary', a.get('description', ''))}")
        lines.append("")
    return "\n".join(lines)


def generate_typology_summaries(articles: list[dict]) -> list[dict]:
    """
    Takes today's analyzed articles, groups by typology, and generates
    a synthesis paragraph per typology using the AI.

    Returns a list of typology_summary dicts ready for Supabase upload.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set in .env")
    if not articles:
        return []

    groups = _group_by_typology(articles)
    if not groups:
        print("[Typology] No specific typologies found — skipping summary generation")
        return []

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    current_date = datetime.now(timezone.utc).strftime("%d-%m-%Y")
    prompt = _build_prompt(groups, current_date)

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        summaries = json.loads(raw)
        if not isinstance(summaries, list):
            summaries = [summaries]

        # Ensure article_count matches our actual count
        for s in summaries:
            typology = s.get("typology_name", "")
            if typology in groups:
                s["article_count"] = len(groups[typology])

        print(f"[Typology] Generated {len(summaries)} typology summaries")
        return summaries

    except json.JSONDecodeError as e:
        print(f"[Typology] JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"[Typology] OpenRouter error: {e}")
        return []


if __name__ == "__main__":
    # Quick test
    sample_articles = [
        {
            "title": "AUSTRAC targets shell company networks in Sydney",
            "aml_typology": "Shell companies",
            "country": "Australia",
            "summary": "AUSTRAC identified a network of 14 shell companies used to move $50M.",
        },
        {
            "title": "Singapore bust crypto mixing ring",
            "aml_typology": "Crypto mixing / tumblers",
            "country": "Singapore",
            "summary": "CAD Singapore arrested 3 for operating a crypto tumbler.",
        },
        {
            "title": "UK NCA cracks down on shell firm laundering",
            "aml_typology": "Shell companies",
            "country": "UK",
            "summary": "NCA seized £12M from shell company accounts tied to organized crime.",
        },
    ]
    result = generate_typology_summaries(sample_articles)
    print(json.dumps(result, indent=2))
