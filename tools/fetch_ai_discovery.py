"""
AI Discovery: use Grok with live web + X/Twitter search to find today's top
AML/financial crime stories, then cross-check against what the keyword-based
pipeline already found. Catches stories that static keywords miss.

Phase 1: DISCOVER — Grok :online searches live web for today's top stories
Phase 2: COMPARE  — Cross-check against existing pipeline articles
Phase 3: LEARN    — Analyse keyword gaps, suggest new keywords for approval
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
# Grok with :online suffix = live web + X/Twitter search
DISCOVERY_MODEL = "x-ai/grok-4.1-fast:online"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

TAVILY_API_KEYS = [k for k in [os.getenv(f"TAVILY_API_KEY{s}") for s in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"]] if k]
TAVILY_URL = "https://api.tavily.com/search"

# Supabase for storing keyword suggestions
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_tavily_key_idx = 0


def _get_tavily_key() -> str | None:
    global _tavily_key_idx
    if not TAVILY_API_KEYS:
        return None
    if _tavily_key_idx < len(TAVILY_API_KEYS):
        return TAVILY_API_KEYS[_tavily_key_idx]
    return None


def _rotate_tavily_key() -> bool:
    global _tavily_key_idx
    _tavily_key_idx += 1
    return _tavily_key_idx < len(TAVILY_API_KEYS)


# ─── Phase 1: DISCOVER ──────────────────────────────────────────────────────

DISCOVERY_PROMPT = """You are an AML/financial crime intelligence analyst with access to live web and X/Twitter search.

Search the web and X/Twitter RIGHT NOW for the most significant global AML, financial crime, sanctions, regulatory enforcement, and compliance stories from the last 48 hours.

You MUST search and cover ALL of these regions — at minimum 3 stories per region:
- Asia-Pacific: Australia, Singapore, Hong Kong, Japan, India, Malaysia, Indonesia, Philippines, South Korea, China, New Zealand
- Europe: UK, EU, Germany, France, Switzerland, Netherlands, Nordics
- Americas: USA, Canada, Latin America, Caribbean
- Middle East & Africa: UAE, Saudi Arabia, South Africa, Nigeria, Kenya

Include these categories:
- Enforcement actions and penalties (fines, arrests, convictions)
- Regulatory guidance and policy changes
- Regtech product launches and partnerships
- Tech company compliance issues (Meta, Google, Binance, etc.)
- Major fraud, corruption, and financial crime cases
- Sanctions designations and evasion cases
- Terrorist financing and proliferation financing

Return EXACTLY a JSON array of 20 stories. Each item MUST have:
- "headline": clear, specific headline with entity names, amounts, countries
- "url": the actual article URL you found (must be a real URL)
- "region": the primary country
- "source": the publication name

Return ONLY the JSON array, no other text."""


def _discover_stories(current_date: str) -> list[dict]:
    """Phase 1: Use Grok :online to search the live web for today's stories."""
    if not OPENROUTER_API_KEY:
        print("[AI Discovery] No OPENROUTER_API_KEY — skipping")
        return []

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)

    try:
        response = client.chat.completions.create(
            model=DISCOVERY_MODEL,
            messages=[
                {"role": "system", "content": DISCOVERY_PROMPT},
                {"role": "user", "content": f"Today is {current_date}. Search the web now and list the 20 most significant global AML/financial crime stories from the last 48 hours. Return real URLs."},
            ],
            temperature=0.2,
            max_tokens=6000,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        stories = json.loads(raw)
        if isinstance(stories, list):
            print(f"[AI Discovery] Grok found {len(stories)} stories via live search")
            return stories
        print("[AI Discovery] Grok returned non-list JSON")
        return []

    except json.JSONDecodeError as e:
        print(f"[AI Discovery] JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"[AI Discovery] Grok API error: {e}")
        return []


# ─── Phase 2: COMPARE ───────────────────────────────────────────────────────

def _title_similarity(t1: str, t2: str) -> float:
    """Simple Jaccard similarity on significant words."""
    def words(t):
        return set(w.lower() for w in re.findall(r'\b\w{3,}\b', t))
    w1, w2 = words(t1), words(t2)
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


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
        best = results[0]
        return {
            "url": best.get("url", ""),
            "title": best.get("title", ""),
            "content": best.get("content", ""),
            "published_date": best.get("published_date", ""),
        }
    except Exception as e:
        print(f"[AI Discovery] Tavily error for '{query}': {e}")
        return None


# ─── Phase 3: LEARN ─────────────────────────────────────────────────────────

def _store_keyword_suggestions(missed_stories: list[dict]):
    """Store keyword suggestions in Supabase pending_keywords table."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not missed_stories:
        return

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }

    for story in missed_stories:
        row = {
            "keyword": story.get("suggested_keyword", ""),
            "recommended_api": story.get("recommended_api", "tavily"),
            "reason": story.get("gap_reason", ""),
            "source_story_url": story.get("url", ""),
            "source_story_title": story.get("headline", ""),
            "status": "pending",
        }
        if not row["keyword"]:
            continue
        try:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/pending_keywords",
                headers=headers,
                json=row,
                timeout=10,
            )
        except Exception as e:
            print(f"[AI Discovery] Failed to store keyword suggestion: {e}")


def _analyse_gaps(missed_stories: list[dict], current_date: str) -> list[dict]:
    """Phase 3: For each missed story, analyse WHY it was missed and suggest keywords."""
    if not missed_stories or not OPENROUTER_API_KEY:
        return []

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
    # Use the standard model for gap analysis (cheaper, doesn't need live search)
    analysis_model = os.getenv("OPENROUTER_MODEL", "xiaomi/mimo-v2-pro")

    stories_text = "\n".join(
        f"- {s.get('headline', '')} ({s.get('region', '')})" for s in missed_stories[:10]
    )

    try:
        response = client.chat.completions.create(
            model=analysis_model,
            messages=[
                {"role": "system", "content": """You are an AML news pipeline analyst. For each missed story, suggest:
1. A search keyword (2-5 words) that would have caught this story
2. Which API it should be added to: "newsapi" (broad news), "tavily" (deep search), "gdelt" (regional), "country_news" (country-specific), "thenewsapi" (precision queries)
3. Why the current keywords missed it

Return JSON array: [{"headline": "...", "suggested_keyword": "...", "recommended_api": "...", "gap_reason": "..."}]
Return ONLY the JSON array."""},
                {"role": "user", "content": f"These AML/financial crime stories from {current_date} were missed by our keyword-based pipeline:\n{stories_text}"},
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        suggestions = json.loads(raw)
        if isinstance(suggestions, list):
            print(f"[AI Discovery] Gap analysis: {len(suggestions)} keyword suggestions")
            return suggestions
        return []
    except Exception as e:
        print(f"[AI Discovery] Gap analysis error: {e}")
        return []


# ─── Main Entry Point ────────────────────────────────────────────────────────

def fetch_ai_discovery(existing_urls: set[str] = None) -> list[dict]:
    """
    3-phase AI discovery:
    Phase 1: Grok :online searches live web for today's top stories
    Phase 2: Compare against existing pipeline articles, fetch missed ones
    Phase 3: Analyse gaps and suggest new keywords
    """
    if existing_urls is None:
        existing_urls = set()

    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Phase 1: DISCOVER ─────────────────────────────────────────────────
    stories = _discover_stories(current_date)
    if not stories:
        return []

    # Build title index from existing articles for similarity matching
    # (existing_urls only has URLs, but we also need title-based dedup)
    existing_titles = set()  # populated from pipeline context if available

    # ── Phase 2: COMPARE ──────────────────────────────────────────────────
    new_articles = []
    already_found = 0
    missed_stories = []

    for story in stories:
        url = story.get("url", "")
        headline = story.get("headline", "")

        # Check URL match
        if url and url in existing_urls:
            already_found += 1
            continue

        # If Grok provided a URL, use it directly; otherwise search Tavily
        if url and url.startswith("http"):
            article_data = {"url": url, "title": headline, "content": "", "published_date": ""}
        else:
            # Search Tavily for the story
            search_query = headline[:100]  # use headline as search query
            article_data = _search_tavily(search_query)
            if not article_data or not article_data["url"]:
                continue
            url = article_data["url"]
            if url in existing_urls:
                already_found += 1
                continue

        # Extract date
        pub_date = (article_data.get("published_date") or "")[:10] or current_date

        article = {
            "title": article_data.get("title") or headline,
            "url": url,
            "source": story.get("source", ""),
            "published_at": pub_date,
            "description": (article_data.get("content") or "")[:2000],
            "content": article_data.get("content") or "",
            "api_source": "ai_discovery",
            "country": story.get("region", ""),
        }

        existing_urls.add(url)
        new_articles.append(article)
        missed_stories.append(story)

    print(f"[AI Discovery] {len(new_articles)} new articles, {already_found} already in pipeline")

    # ── Phase 3: LEARN ────────────────────────────────────────────────────
    if missed_stories:
        suggestions = _analyse_gaps(missed_stories, current_date)
        if suggestions:
            # Merge URLs into suggestions for storage
            for i, s in enumerate(suggestions):
                if i < len(missed_stories):
                    s["url"] = missed_stories[i].get("url", "")
            _store_keyword_suggestions(suggestions)
            print(f"[AI Discovery] {len(suggestions)} keyword suggestions stored for approval")

    return new_articles
