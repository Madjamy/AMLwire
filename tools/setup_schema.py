"""
Add new columns to the Supabase articles table.
Tries the RPC/Management API approach, falls back to printing SQL for manual execution.

Usage:
    python tools/setup_schema.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

NEW_COLUMNS = {
    "enforcement_authority": "TEXT",
    "financial_amount":      "TEXT",
    "key_entities":          "TEXT[] DEFAULT '{}'",
    "action_required":       "BOOLEAN DEFAULT FALSE",
    "publication_type":      "TEXT",
    "amlwire_title":         "TEXT",
    "related_article_ids":   "UUID[] DEFAULT '{}'",
}

HEADERS = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "apikey": SUPABASE_SERVICE_KEY,
    "Content-Type": "application/json",
}


def check_existing_columns() -> set[str]:
    """Read the OpenAPI spec to see which columns already exist in the articles table."""
    existing = set()
    try:
        resp = requests.get(f"{SUPABASE_URL}/rest/v1/", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return existing
        spec = resp.json()
        articles_def = spec.get("definitions", {}).get("articles", {})
        existing = set(articles_def.get("properties", {}).keys())
    except Exception as e:
        print(f"  [warn] Could not read schema spec: {e}")
    return existing


def try_add_column_via_rpc(col: str, col_type: str) -> bool:
    """Attempt to add a column via a exec_sql RPC (only works if that function exists)."""
    sql = f"ALTER TABLE articles ADD COLUMN IF NOT EXISTS {col} {col_type};"
    payload = {"sql": sql}
    for endpoint in [f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
                     f"{SUPABASE_URL}/rest/v1/rpc/run_sql",
                     f"{SUPABASE_URL}/rest/v1/rpc/execute_sql"]:
        try:
            r = requests.post(endpoint, json=payload, headers=HEADERS, timeout=10)
            if r.status_code in (200, 204):
                return True
        except Exception:
            pass
    return False


def print_manual_sql(missing_cols: dict):
    """Print the SQL the user needs to run manually."""
    print("\n" + "=" * 65)
    print("ACTION REQUIRED — Run this in Supabase → SQL Editor:")
    print("=" * 65)
    print()
    print("ALTER TABLE articles")
    lines = []
    for col, col_type in missing_cols.items():
        lines.append(f"  ADD COLUMN IF NOT EXISTS {col} {col_type}")
    print(",\n".join(lines) + ";")
    print()
    print("URL: https://supabase.com/dashboard/project/ykfkbuuwfqmjkogwbkjp/sql/new")
    print("=" * 65)


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[Setup] SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        sys.exit(1)

    print("[Setup] Checking existing columns via OpenAPI spec...")
    existing = check_existing_columns()
    if existing:
        print(f"[Setup] Found {len(existing)} existing columns in articles table")
    else:
        print("[Setup] Could not read existing columns (will attempt to add all)")

    missing = {
        col: col_type
        for col, col_type in NEW_COLUMNS.items()
        if col not in existing
    }

    if not missing:
        print("[Setup] All 5 new columns already exist. Nothing to do.")
        return

    print(f"[Setup] Missing columns: {list(missing.keys())}")
    print("[Setup] Attempting to add via RPC...")

    added_via_rpc = []
    for col, col_type in missing.items():
        if try_add_column_via_rpc(col, col_type):
            print(f"  Added via RPC: {col}")
            added_via_rpc.append(col)
        else:
            print(f"  RPC not available for: {col}")

    still_missing = {c: t for c, t in missing.items() if c not in added_via_rpc}

    if still_missing:
        print_manual_sql(still_missing)
        sys.exit(1)  # signal that manual action is needed
    else:
        print("[Setup] All columns added successfully via RPC.")


if __name__ == "__main__":
    main()
