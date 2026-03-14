"""
Centralized JSONL audit logging for the AMLWire pipeline.
Logs pre-filter drops, AI exclusions, and scrape failures to daily files
in the logs/ directory for post-run analysis.

Usage:
    from tools.audit_logger import log_prefilter_drop, log_ai_exclusion, log_scrape_failure
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_jsonl(filename: str, record: dict):
    filepath = LOGS_DIR / filename
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_prefilter_drop(article: dict, reason: str):
    _append_jsonl(f"prefilter_drops_{_today_str()}.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": article.get("title", "")[:200],
        "url": article.get("url", ""),
        "source": article.get("source", ""),
        "reason": reason,
    })


def log_ai_exclusion(title: str, url: str, reason: str):
    _append_jsonl(f"ai_exclusions_{_today_str()}.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title[:200],
        "url": url,
        "reason": reason,
    })


def log_scrape_failure(url: str, error: str):
    _append_jsonl(f"scrape_failures_{_today_str()}.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "error": error,
    })


def get_run_summary() -> dict:
    """Return counts from today's log files."""
    today = _today_str()
    counts = {}
    for prefix in ("prefilter_drops", "ai_exclusions", "scrape_failures"):
        filepath = LOGS_DIR / f"{prefix}_{today}.jsonl"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                counts[prefix] = sum(1 for _ in f)
        else:
            counts[prefix] = 0
    return counts
