"""
Post-run watchdog for the AMLWire pipeline.

Runs ~2.5h after the pipeline starts and asks one question: did today's run
actually publish anything? Alerts on Telegram if not.

This exists because main.py's failure handler cannot cover every failure mode.
The pipeline has been SIGKILLed mid-run five times (29-30 Jul, 28+30 Aug, 1 Sep);
SIGKILL cannot be caught by Python, so no exception is raised, no alert is sent,
and the run vanishes silently. An external check is the only thing that catches it.
"""
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv("/app/.env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BOT_TOKEN = os.getenv("Telegram_API_KEY")  # same var name send_telegram_report.py uses
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LOG_PATH = "/app/logs/amlwire.log"  # on the ./logs volume, so it survives rebuilds

# A healthy run publishes tens of articles; anything below this is a failure,
# not a quiet news day.
MIN_EXPECTED_ARTICLES = 5


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def published_today() -> int:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/articles",
        params={"select": "id", "created_at": f"gte.{_today()}T00:00:00"},
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "count=exact",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return len(resp.json())


def diagnose() -> str:
    """Best-effort cause from the tail of today's log."""
    try:
        with open(LOG_PATH, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 200_000))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return "could not read the pipeline log"

    today_lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not any(_today() in ln for ln in today_lines):
        return "no log output for today — the cron job never started"

    last = today_lines[-1] if today_lines else ""
    if last.strip() == "Killed":
        return "process was SIGKILLed mid-run (out of memory)"
    for marker, cause in (
        ("402", "OpenRouter credits exhausted"),
        ("Payment Required", "OpenRouter credits exhausted"),
        ("404", "model unavailable — check OPENROUTER_MODEL"),
        ("Traceback", "unhandled exception — see the log"),
    ):
        if marker in tail:
            return cause
    return f"run did not complete; last log line: {last[:160]}"


def alert(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[Watchdog] Telegram not configured. Would have sent:\n{text}")
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )


def main() -> int:
    try:
        count = published_today()
    except Exception as e:
        alert(f"⚠️ <b>AMLWire watchdog could not reach Supabase</b>\n{e}")
        return 1

    if count >= MIN_EXPECTED_ARTICLES:
        print(f"[Watchdog] OK — {count} articles published today")
        return 0

    reason = diagnose()
    alert(
        "🚨 <b>AMLWire pipeline did not publish today</b>\n\n"
        f"Articles published: <b>{count}</b> (expected ≥{MIN_EXPECTED_ARTICLES})\n"
        f"Likely cause: {reason}\n\n"
        f"Date: {_today()} UTC"
    )
    print(f"[Watchdog] ALERT sent — {count} articles, cause: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
