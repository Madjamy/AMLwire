"""
One-off migration: map existing free-text aml_typology values in Supabase
to the canonical standardised vocabulary.
Safe to re-run — only updates articles whose typology is non-canonical.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# ─── Canonical vocabulary (must stay in sync with analyze_articles.py) ──────
CANONICAL = {
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

# ─── Manual mapping: old value → canonical label ────────────────────────────
MAPPING = {
    # Crypto mixing variants
    "Crypto mixing / tumblers":                     "Crypto mixing and tumbling",
    "Crypto mixing/tumblers":                       "Crypto mixing and tumbling",
    "Crypto mixing / sanctions evasion networks":   "Crypto mixing and tumbling",
    "Crypto mixing / sanctions evasion":            "Crypto mixing and tumbling",
    "Crypto mixing and darknet-enabled laundering": "Darknet-enabled laundering",
    "Crypto mixing, darknet-enabled laundering":    "Darknet-enabled laundering",

    # Sanctions evasion variants
    "Sanctions evasion through crypto exchanges":   "Sanctions evasion",
    "Sanctions evasion networks":                   "Sanctions evasion",
    "Crypto-based sanctions evasion":               "Sanctions evasion",
    "Crypto exchange sanctions compliance failures":"Sanctions evasion",
    "Sanctions case":                               "Sanctions evasion",

    # Hawala variants
    "Hawala / informal value transfer":             "Hawala and informal value transfer",
    "Hawala and informal value transfer systems":   "Hawala and informal value transfer",

    # Shell companies / multi-typology
    "Shell companies | Beneficial ownership concealment":
        "Shell companies and beneficial ownership concealment",
    "Layering | Shell companies | Cash-intensive business laundering":
        "Shell companies and beneficial ownership concealment",
    "Trade-based money laundering | Money mules | Real estate laundering":
        "Trade-based money laundering",

    # Human trafficking
    "Human trafficking financial flows":            "Human trafficking proceeds laundering",

    # Mule accounts
    "Corporate money mule accounts":                "Mule accounts",

    # Organised crime (generic → General AML news)
    "Organized crime money laundering":             "General AML news",

    # AML compliance
    "AML compliance failures":                      "AML compliance failure",

    # Already canonical but check anyway
    "Cryptocurrency-based laundering":              "Cryptocurrency-based laundering",
    "Trade-based money laundering":                 "Trade-based money laundering",
    "Professional enablers":                        "Professional enablers",
    "General AML news":                             "General AML news",
}


def normalise(typology: str) -> str:
    """Return the canonical label for a given typology string."""
    if typology in CANONICAL:
        return typology
    mapped = MAPPING.get(typology)
    if mapped:
        return mapped
    # Fuzzy fallback: return General AML news for anything unrecognised
    print(f"  [WARN] Unknown typology not in mapping: '{typology}' → General AML news")
    return "General AML news"


def main():
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    rows = client.table("articles").select("id, title, aml_typology").execute().data
    print(f"Fetched {len(rows)} articles")

    updates = []
    for row in rows:
        current = row.get("aml_typology") or "General AML news"
        canonical = normalise(current)
        if canonical != current:
            updates.append((row["id"], row["title"][:60], current, canonical))

    if not updates:
        print("All typologies are already canonical. Nothing to update.")
        return

    print(f"\n{len(updates)} articles to update:\n")
    for aid, title, old, new in updates:
        print(f"  '{old}'")
        print(f"  -> '{new}'")
        print(f"     {title}")
        print()

    print(f"Applying {len(updates)} updates...")
    ok = 0
    for aid, title, old, new in updates:
        try:
            client.table("articles").update({"aml_typology": new}).eq("id", aid).execute()
            ok += 1
        except Exception as e:
            print(f"  Error updating {aid}: {e}")

    print(f"\nDone. {ok}/{len(updates)} articles updated.")


if __name__ == "__main__":
    main()
