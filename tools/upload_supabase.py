"""
Upload analyzed AML articles and typology summaries to Supabase.
Uses upsert on source_url (articles) — safe to run multiple times.
"""

import os
from datetime import datetime, date, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


def _get_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_URL or SUPABASE_SERVICE_KEY not set in .env")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _parse_published_at(date_str: str) -> str | None:
    """Convert DD-MM-YYYY or ISO string to ISO timestamptz string."""
    if not date_str:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.isoformat() + "Z"
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    return None


def upload_staging(articles: list[dict]) -> int:
    """
    Upsert raw fetched articles into articles_staging table.
    Called before dedup/AI -- records everything that was fetched.
    Skips articles with no URL or no publish date.
    Returns count saved.
    """
    if not articles:
        return 0

    client = _get_client()
    fetched_at = datetime.now(timezone.utc).isoformat()
    saved = 0

    for a in articles:
        url = (a.get("url") or "").strip()
        published_at = (a.get("published_at") or "").strip()
        if not url or not published_at:
            continue

        row = {
            "title": a.get("title", ""),
            "url": url,
            "source": a.get("source", ""),
            "published_at": published_at,
            "description": (a.get("description") or a.get("content") or "")[:1000],
            "api_source": a.get("api_source", ""),
            "country": a.get("country") or None,
            "fetched_at": fetched_at,
        }
        try:
            client.table("articles_staging").upsert(row, on_conflict="url").execute()
            saved += 1
        except Exception as e:
            print(f"[Staging] Error saving '{url}': {e}")

    print(f"[Staging] {saved}/{len(articles)} articles saved to articles_staging")
    return saved


def find_related_articles(article_id: str, article: dict, client) -> list[str]:
    """
    Query Supabase for articles related to the given article by typology and enforcement authority.
    Returns a list of up to 5 related article UUIDs, excluding the article itself.
    """
    seen: set[str] = set()
    related: list[str] = []

    typology = article.get("aml_typology")
    authority = article.get("enforcement_authority")

    # Match by typology (most recent 5, skip generic "AML News")
    if typology and typology not in ("AML News",):
        try:
            resp = (
                client.table("articles")
                .select("id")
                .eq("aml_typology", typology)
                .neq("id", article_id)
                .order("published_at", desc=True)
                .limit(5)
                .execute()
            )
            for r in (resp.data or []):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    related.append(r["id"])
        except Exception:
            pass

    # Match by enforcement authority (most recent 3, cap at 5 total)
    if authority and len(related) < 5:
        try:
            resp = (
                client.table("articles")
                .select("id")
                .eq("enforcement_authority", authority)
                .neq("id", article_id)
                .order("published_at", desc=True)
                .limit(3)
                .execute()
            )
            for r in (resp.data or []):
                if r["id"] not in seen and len(related) < 5:
                    seen.add(r["id"])
                    related.append(r["id"])
        except Exception:
            pass

    return related[:5]


def upload_articles(articles: list[dict]) -> int:
    """
    Upsert analyzed articles into Supabase articles table.
    Conflict key: source_url (unique).
    After each upsert, populates related_article_ids by querying matching typology/authority.
    Returns count of successfully uploaded articles.
    """
    if not articles:
        print("[Upload] No articles to upload")
        return 0

    client = _get_client()
    fetched_at = datetime.now(timezone.utc).isoformat()
    uploaded = 0

    for article in articles:
        source_url = (article.get("source_url") or article.get("url", "")).strip()
        if not source_url:
            continue

        row = {
            "title": article.get("title", ""),
            "amlwire_title": article.get("amlwire_title") or None,
            "summary": article.get("summary", ""),
            "modus_operandi": article.get("modus_operandi") or None,
            "raw_snippet": article.get("raw_snippet", ""),
            "source_url": source_url,
            "source_name": article.get("source_name") or article.get("source", ""),
            "category": article.get("category", "news"),
            "aml_typology": article.get("aml_typology", "General AML news"),
            "country": article.get("country"),
            "region": article.get("region", "Not clearly identified"),
            "tags": article.get("tags", []),
            "published_at": _parse_published_at(article.get("published_date", "")),
            "fetched_at": fetched_at,
            "enforcement_authority": article.get("enforcement_authority") or None,
            "financial_amount": article.get("financial_amount") or None,
            "key_entities": article.get("key_entities") or [],
            "action_required": article.get("action_required") or False,
            "publication_type": article.get("publication_type") or None,
            "related_article_ids": [],
        }

        try:
            resp = client.table("articles").upsert(row, on_conflict="source_url").execute()
            uploaded += 1

            # Fetch the article's UUID so we can query related articles and update the link
            id_resp = (
                client.table("articles")
                .select("id")
                .eq("source_url", source_url)
                .limit(1)
                .execute()
            )
            if id_resp.data:
                article_id = id_resp.data[0]["id"]
                related = find_related_articles(article_id, article, client)
                if related:
                    client.table("articles").update(
                        {"related_article_ids": related}
                    ).eq("id", article_id).execute()

            print(f"[Upload] Saved: {article.get('amlwire_title') or article.get('title', '')[:70]}")
        except Exception as e:
            print(f"[Upload] Error saving '{source_url}': {e}")

    print(f"[Upload] {uploaded}/{len(articles)} articles saved to Supabase")
    return uploaded


def upload_typology_summaries(summaries: list[dict]) -> int:
    """
    Insert typology summaries into Supabase typology_summaries table.
    Each summary is for today's digest date.
    Returns count of successfully uploaded summaries.
    """
    if not summaries:
        print("[Upload] No typology summaries to upload")
        return 0

    client = _get_client()
    digest_date = date.today().isoformat()
    uploaded = 0

    for s in summaries:
        row = {
            "typology_name": s.get("typology_name", ""),
            "summary": s.get("summary", ""),
            "countries_involved": s.get("countries_involved", []),
            "article_count": s.get("article_count", 0),
            "digest_date": digest_date,
        }
        try:
            client.table("typology_summaries").insert(row).execute()
            uploaded += 1
            print(f"[Upload] Typology saved: {s.get('typology_name', '')}")
        except Exception as e:
            print(f"[Upload] Error saving typology '{s.get('typology_name', '')}': {e}")

    print(f"[Upload] {uploaded}/{len(summaries)} typology summaries saved")
    return uploaded
