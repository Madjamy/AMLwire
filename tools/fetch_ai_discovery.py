"""
AI Discovery: use MiMo to identify today's top AML/financial crime stories
globally, then verify each via Tavily search. Catches stories that keyword-based
sources miss (e.g. tech company penalties, regtech launches, non-English regions).
"""

import os
import json
import re
import requests
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "xiaomi/mimo-v2-pro")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

TAVILY_API_KEYS = [k for k in [os.getenv(f"TAVILY_API_KEY{s}") for s in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"]] if k]
TAVILY_URL = "https://api.tavily.com/search"

_tavily_key_idx = 0


def _get_tavily_key() -> str | None:
    global _tavily_key_idx
    if not TAVILY_API_KEYS:
        return None
    while _tavily_key_idx < len(TAVILY_API_KEYS):
        return TAVILY_API_KEYS[_tavily_key_idx]
    return None


def _rotate_tavily_key() -> bool:
    global _tavily_key_idx
    _tavily_key_idx += 1
    if _tavily_key_idx >= len(TAVILY_API_KEYS):
        return False
    return True


DISCOVERY_PROMPT = """You are an AML/financial crime intelligence analyst. Your job is to identify the most significant global stories from the last 48 hours.

List exactly 20 stories. You MUST cover ALL of these regions — at minimum 2 stories per region:
- Asia-Pacific (Australia, Singapore, Hong Kong, Japan, India, Malaysia, Indonesia, Philippines, South Korea, China, New Zealand)
- Europe (UK, EU, Germany, France, Switzerland, Netherlands, Nordics)
- Americas (USA, Canada, Latin America, Caribbean)
- Middle East & Africa (UAE, Saudi Arabia, South Africa, Nigeria, Kenya)

Include these categories:
- Enforcement actions and penalties (fines, arrests, convictions, sanctions)
- Regulatory guidance and policy changes
- Regtech product launches and partnerships (Hawk AI, Napier, ComplyAdvantage, etc.)
- Tech company compliance issues (Meta, Google, Apple, Binance, etc.)
- Major fraud, corruption, and financial crime cases
- Sanctions designations and evasion cases
- Whistleblower cases and compliance failures
- Terrorist financing and proliferation financing

For each story, return a JSON array. Each item must have:
- "headline": a clear, specific headline (include entity names, amounts, countries)
- "region": the primary region/country
- "search_query": a precise search query (under 15 words) to find the actual article — use entity names, amounts, and specific details

Return ONLY the JSON array, no other text. Example:
[
  {"headline": "Meta to Pay $375M in Child Exploitation Case", "region": "United States", "search_query": "Meta 375 million child exploitation settlement 2026"},
  {"headline": "Hawk AI Launches New AML Screening Product", "region": "Germany", "search_query": "Hawk AI AML screening product launch 2026"}
]"""


def _ask_mimo_for_stories(current_date: str) -> list[dict]:
    """Call MiMo to identify today's top AML/financial crime stories."""
    if not OPENROUTER_API_KEY:
        print("[AI Discovery] No OPENROUTER_API_KEY — skipping")
        return []

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": DISCOVERY_PROMPT},
                {"role": "user", "content": f"Today is {current_date}. List the 20 most significant global AML/financial crime stories from the last 48 hours."},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        stories = json.loads(raw)
        if isinstance(stories, list):
            print(f"[AI Discovery] MiMo identified {len(stories)} stories")
            return stories
        print("[AI Discovery] MiMo returned non-list JSON")
        return []

    except json.JSONDecodeError as e:
        print(f"[AI Discovery] JSON parse error from MiMo: {e}")
        return []
    except Exception as e:
        print(f"[AI Discovery] MiMo API error: {e}")
        return []


def _search_tavily(query: str) -> dict | None:
    """Search Tavily for a single story. Returns best matching article or None."""
    key = _get_tavily_key()
    if not key:
        return None

    try:
        payload = {
            "api_key": key,
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": 3,
            "days": 3,
            "include_raw_content": False,
            "include_answer": False,
        }
        resp = requests.post(TAVILY_URL, json=payload, timeout=20)
        if resp.status_code == 432:
            if _rotate_tavily_key():
                return _search_tavily(query)
            print("[AI Discovery] All Tavily keys exhausted")
            return None
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        # Return the top result
        best = results[0]
        return {
            "url": best.get("url", ""),
            "title": best.get("title", ""),
            "content": best.get("content", ""),
            "published_date": best.get("published_date", ""),
        }

    except Exception as e:
        print(f"[AI Discovery] Tavily search error for '{query}': {e}")
        return None


def fetch_ai_discovery(existing_urls: set[str] = None) -> list[dict]:
    """
    Main entry point. Ask MiMo for today's top stories, verify via Tavily,
    return articles not already in the pipeline.
    """
    if existing_urls is None:
        existing_urls = set()

    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stories = _ask_mimo_for_stories(current_date)

    if not stories:
        return []

    articles = []
    dupes = 0

    for story in stories:
        query = story.get("search_query", "")
        if not query:
            continue

        result = _search_tavily(query)
        if not result or not result["url"]:
            continue

        url = result["url"]
        if url in existing_urls:
            dupes += 1
            continue

        # Extract date — use Tavily's published_date or fall back to today
        pub_date = result.get("published_date", "")
        if pub_date:
            # Tavily returns ISO dates; normalise to YYYY-MM-DD
            pub_date = pub_date[:10]
        else:
            pub_date = current_date

        article = {
            "title": result["title"],
            "url": url,
            "source": "",
            "published_at": pub_date,
            "description": result.get("content", "")[:2000],
            "content": result.get("content", ""),
            "api_source": "ai_discovery",
            "country": story.get("region", ""),
        }

        existing_urls.add(url)
        articles.append(article)

    print(f"[AI Discovery] {len(articles)} new articles found, {dupes} already in pipeline")
    return articles
