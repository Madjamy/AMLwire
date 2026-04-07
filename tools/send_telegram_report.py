"""
Send AMLWire daily pipeline report via Telegram Bot API.

Two functions:
  send_pipeline_report(report_data)  — full daily stats after successful run
  send_pipeline_failure(error, report_data) — short alert on pipeline failure

Env vars: Telegram_API_KEY (bot token), TELEGRAM_CHAT_ID (recipient)
"""

import os
import requests
from datetime import datetime, timezone
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("Telegram_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _send_message(text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return False

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    # Truncate if too long
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 40] + "\n\n... (message truncated)"

    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            print("[Telegram] Report sent successfully")
            return True
        else:
            print(f"[Telegram] API error: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[Telegram] Failed to send: {e}")
        return False


def send_pipeline_report(report_data: dict) -> bool:
    """
    Build and send the full daily report.

    report_data keys:
        source_counts: dict   — per-source raw counts (e.g. {"NewsAPI": 27, ...})
        total_fetched: int    — raw combined total before any filtering
        after_date_filter: int
        after_title_dedup: int
        after_supabase_dedup: int
        ai_processed: int     — articles sent to AI
        ai_selected: int      — articles returned by AI
        after_curation: int
        published: int
        articles: list[dict]  — final curated articles (with quality_score, quality_tier, country, amlwire_title/title)
        alerts: list[str]     — source failures, rate limits, etc.
    """
    now = datetime.now(timezone.utc).strftime("%d %b %Y")
    lines = [f"<b>AMLWire Daily Report — {now}</b>"]

    # ── Pipeline Funnel ──
    lines.append("")
    lines.append("<b>PIPELINE FUNNEL</b>")
    lines.append(f"  Sources fetched:      {report_data.get('total_fetched', 0)}")
    lines.append(f"  After date filter:    {report_data.get('after_date_filter', 0)}")
    lines.append(f"  After title dedup:    {report_data.get('after_title_dedup', 0)}")
    lines.append(f"  After Supabase dedup: {report_data.get('after_supabase_dedup', 0)}")
    lines.append(f"  AI processed:         {report_data.get('ai_processed', 0)}")
    lines.append(f"  AI selected:          {report_data.get('ai_selected', 0)}")
    lines.append(f"  After curation:       {report_data.get('after_curation', 0)}")
    lines.append(f"  Published:            {report_data.get('published', 0)}")

    # ── Source Breakdown ──
    sc = report_data.get("source_counts", {})
    if sc:
        lines.append("")
        lines.append("<b>SOURCE BREAKDOWN</b>")
        row1 = []
        row2 = []
        row3 = []
        order = ["NewsAPI", "Tavily", "Country", "RSS", "GDELT",
                 "Scrapers", "NewsData", "GNews", "TheNewsAPI"]
        for i, name in enumerate(order):
            val = sc.get(name, 0)
            entry = f"{name}: {val}"
            if i < 3:
                row1.append(entry)
            elif i < 6:
                row2.append(entry)
            else:
                row3.append(entry)
        if row1:
            lines.append("  " + " | ".join(row1))
        if row2:
            lines.append("  " + " | ".join(row2))
        if row3:
            lines.append("  " + " | ".join(row3))

    articles = report_data.get("articles", [])

    # ── Countries ──
    if articles:
        country_counts = Counter(
            (a.get("country") or "Unknown") for a in articles
        )
        lines.append("")
        lines.append("<b>COUNTRIES</b>")
        country_parts = [f"{c}: {n}" for c, n in country_counts.most_common()]
        # Wrap into rows of ~3
        for i in range(0, len(country_parts), 3):
            lines.append("  " + " | ".join(country_parts[i:i+3]))

    # ── Typology Mix ──
    if articles:
        typo_counts = Counter(
            (a.get("aml_typology") or "Unknown") for a in articles
        )
        lines.append("")
        lines.append("<b>TYPOLOGY MIX</b>")
        for typo, count in typo_counts.most_common():
            lines.append(f"  {typo}: {count}")

    # ── Tier Distribution ──
    if articles:
        tier_counts = Counter(
            (a.get("quality_tier") or "Unscored") for a in articles
        )
        lines.append("")
        lines.append("<b>TIER DISTRIBUTION</b>")
        tier_parts = []
        for tier in ["Critical", "High", "Elevated", "Watch"]:
            if tier in tier_counts:
                tier_parts.append(f"{tier}: {tier_counts[tier]}")
        lines.append("  " + " | ".join(tier_parts))

    # ── Top Articles (score >= 60) ──
    if articles:
        top = [a for a in articles if (a.get("quality_score") or 0) >= 75]
        top.sort(key=lambda a: a.get("quality_score", 0), reverse=True)
        if top:
            lines.append("")
            lines.append(f"<b>TOP ARTICLES (score >= 75)</b>")
            shown = 0
            for a in top:
                if shown >= 15:
                    remaining = len(top) - shown
                    lines.append(f"  ...and {remaining} more")
                    break
                score = a.get("quality_score", 0)
                country = (a.get("country") or "?")[:15]
                title = (a.get("amlwire_title") or a.get("title") or "?")[:70]
                icon = "\U0001f534" if score >= 90 else "\U0001f7e0"  # red / orange circle
                lines.append(f"  {icon} {score} [{country}] {title}")
                shown += 1

    # ── Alerts ──
    alerts = report_data.get("alerts", [])
    if alerts:
        lines.append("")
        lines.append("<b>ALERTS</b>")
        for alert in alerts:
            lines.append(f"  \u26a0\ufe0f {alert}")

    text = "\n".join(lines)
    return _send_message(text)


def send_pipeline_failure(error: str, report_data: dict) -> bool:
    """Send a short failure alert."""
    now = datetime.now(timezone.utc).strftime("%d %b %Y")
    fetched = report_data.get("total_fetched", 0)
    published = report_data.get("published", 0)

    text = (
        f"\U0001f6a8 <b>AMLWire Pipeline FAILED — {now}</b>\n"
        f"\n"
        f"<b>Error:</b> {str(error)[:500]}\n"
        f"Articles fetched before failure: {fetched}\n"
        f"Articles published before failure: {published}"
    )

    alerts = report_data.get("alerts", [])
    if alerts:
        text += "\n\n<b>Source alerts:</b>\n"
        for alert in alerts[:5]:
            text += f"  \u26a0\ufe0f {alert}\n"

    return _send_message(text)
