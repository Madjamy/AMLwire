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
    "Structuring / Smurfing",
    "Trade-based money laundering (TBML)",
    "Shell companies and nominee ownership",
    "Real estate laundering",
    "Cash-intensive business laundering",
    "Offshore concealment",
    "Crypto-asset laundering",
    "Crypto mixing / tumbling",
    "Darknet-enabled laundering",
    "Money mules",
    "Hawala and informal value transfer",
    "Sanctions",
    "Professional enablers",
    "Terrorist financing",
    "Drug trafficking proceeds",
    "Human trafficking proceeds",
    "Cybercrime proceeds",
    "AML compliance failure",
    "AML News",
}

# ─── Manual mapping: old value → new canonical label ────────────────────────
MAPPING = {
    # Previous canonical → new canonical
    "Layering and placement":                               "AML News",
    "Structuring / smurfing":                               "Structuring / Smurfing",
    "Trade-based money laundering":                         "Trade-based money laundering (TBML)",
    "Shell companies and beneficial ownership concealment": "Shell companies and nominee ownership",
    "Crypto mixing and tumbling":                           "Crypto mixing / tumbling",
    "Cryptocurrency-based laundering":                      "Crypto-asset laundering",
    "Sanctions evasion":                                    "Sanctions",
    "Mule accounts":                                        "Money mules",
    "Terror financing":                                     "Terrorist financing",
    "Cyber-enabled fraud laundering":                       "Cybercrime proceeds",
    "Drug trafficking proceeds laundering":                 "Drug trafficking proceeds",
    "Human trafficking proceeds laundering":                "Human trafficking proceeds",
    "General AML news":                                     "AML News",

    # Legacy free-text variants
    "Crypto mixing / tumblers":                             "Crypto mixing / tumbling",
    "Crypto mixing/tumblers":                               "Crypto mixing / tumbling",
    "Crypto mixing / sanctions evasion networks":           "Crypto mixing / tumbling",
    "Crypto mixing / sanctions evasion":                    "Crypto mixing / tumbling",
    "Crypto mixing and darknet-enabled laundering":         "Darknet-enabled laundering",
    "Crypto mixing, darknet-enabled laundering":            "Darknet-enabled laundering",
    "Sanctions evasion through crypto exchanges":           "Sanctions",
    "Sanctions evasion networks":                           "Sanctions",
    "Crypto-based sanctions evasion":                       "Sanctions",
    "Crypto exchange sanctions compliance failures":        "Sanctions",
    "Sanctions case":                                       "Sanctions",
    "Hawala / informal value transfer":                     "Hawala and informal value transfer",
    "Hawala and informal value transfer systems":           "Hawala and informal value transfer",
    "Shell companies | Beneficial ownership concealment":   "Shell companies and nominee ownership",
    "Layering | Shell companies | Cash-intensive business laundering":
                                                            "Shell companies and nominee ownership",
    "Trade-based money laundering | Money mules | Real estate laundering":
                                                            "Trade-based money laundering (TBML)",
    "Human trafficking financial flows":                    "Human trafficking proceeds",
    "Corporate money mule accounts":                        "Money mules",
    "Organized crime money laundering":                     "AML News",
    "AML compliance failures":                              "AML compliance failure",
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
        print(f"     {title.encode('ascii', errors='replace').decode('ascii')}")
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
