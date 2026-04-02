# AMLWire Geographic Coverage Report — 2026-04-02

**Report date:** 2026-04-02  
**Period assessed:** March 15 – April 2, 2026  
**Trigger:** Scheduled post-deployment coverage check (fixes deployed 2026-03-29)  
**Status:** PARTIAL — live database queries blocked by egress proxy (see note below)

---

## Network / Connectivity Notice

All four Supabase REST API queries failed with `x-deny-reason: host_not_allowed` via the
environment's egress proxy. The proxy allowlist covers package registries and dev-tool hosts
only — `ykfkbuuwfqmjkogwbkjp.supabase.co` is not included.

**Queries attempted (all returned 403 Forbidden):**
- `GET /rest/v1/articles` (post-fix window: 2026-03-29 – 2026-04-02)
- `GET /rest/v1/articles` (pre-fix baseline: 2026-03-15 – 2026-03-28)
- `GET /rest/v1/articles_staging?api_source=eq.ai_discovery`
- `GET /rest/v1/pending_keywords`

The sections below use the pre-fix baseline captured on 2026-03-29 (provided externally).
Post-fix metrics are marked **UNVERIFIED — requires direct DB access**.

---

## 1. Country Distribution Comparison

| Country | Pre-fix (Mar 15–28) | Post-fix (Mar 29–Apr 2) | Change |
|---|---|---|---|
| United States | ~19% | UNVERIFIED | — |
| India | ~15% | UNVERIFIED | — |
| United Kingdom | ~8% | UNVERIFIED | — |
| Singapore | ~5% | UNVERIFIED | — |
| UAE | ~3% | UNVERIFIED | — |
| Australia | ~2% | UNVERIFIED | — |
| Canada | ~2% | UNVERIFIED | — |
| Other | ~46% | UNVERIFIED | — |

**Pre-fix notes (captured 2026-03-29):**
- Total articles: **556** over 20 days (~27.8/day)
- US + India combined: **34%** of all articles — clear anglophone/APAC bias
- Long-tail countries heavily underrepresented

---

## 2. Region Distribution Comparison

| Region | Pre-fix | Post-fix | Target |
|---|---|---|---|
| Americas (US, Canada, LatAm) | ~23% | UNVERIFIED | ~25% |
| APAC (AU, SG, India, Asia) | ~30% | UNVERIFIED | ~30% |
| Europe (UK, EU) | ~20% | UNVERIFIED | ~20% |
| MENA (UAE, GCC, N.Africa) | ~7% | UNVERIFIED | ~10% |
| Africa (Sub-Saharan) | ~5% | UNVERIFIED | ~10% |
| Other / Unknown | ~15% | UNVERIFIED | ~5% |

MENA and Sub-Saharan Africa remain the most likely underperformers based on pre-fix data.

---

## 3. Priority Country Daily Averages

| Country | Pre-fix avg/day | Pre-fix missing days | Post-fix avg/day | Post-fix missing days |
|---|---|---|---|---|
| Australia | 0.5 | 12/20 | UNVERIFIED | — |
| Canada | 0.4 | 13/20 | UNVERIFIED | — |
| UAE | 0.7 | 12/20 | UNVERIFIED | — |
| Singapore | 0.9 | 10/20 | UNVERIFIED | — |
| UK | 1.6 | 6/20 | UNVERIFIED | — |
| India | ~4.2 | 0/20 | UNVERIFIED | — |
| United States | ~5.3 | 0/20 | UNVERIFIED | — |

**Baseline concern:** Australia, Canada and UAE were missing on more than half of all days —
indicating the rebalancing fixes were specifically targeting sporadic low-volume countries.

**Fix deployed (2026-03-29 commit `d980c1b`):**
- Geographic imbalance fixes
- AI Discovery step redesign with live Tavily search
- Intent: surface underrepresented countries via MiMo story identification + Tavily verification

---

## 4. AI Discovery Effectiveness

Query to `articles_staging` was blocked. No live data available.

**Expected signal to look for:**
- `api_source = 'ai_discovery'` records appearing for AU, CA, UAE, SG
- Non-zero coverage on days when RSS/GDELT/NewsAPI sources return nothing for those countries
- Titles referencing AML/financial crime enforcement from under-covered jurisdictions

**Recommended manual check:**
```sql
SELECT country, COUNT(*) as n, MIN(published_at), MAX(published_at)
FROM articles_staging
WHERE api_source = 'ai_discovery'
  AND created_at >= '2026-03-29'
GROUP BY country
ORDER BY n DESC;
```

---

## 5. Pending Keywords

Query to `pending_keywords` was blocked. No live data available.

**Recommended manual check:**
```sql
SELECT * FROM pending_keywords ORDER BY created_at DESC LIMIT 50;
```

Look for patterns suggesting the AI Discovery step is surfacing new search terms for
under-covered geographies (e.g. country-specific regulator names, enforcement bodies).

---

## 6. Overall Verdict

### What we know
- Pre-fix coverage was **skewed ~34% toward US+India**, with sporadic-to-absent coverage of
  Australia, Canada, UAE and Singapore.
- The fix (commit `d980c1b`, 2026-03-29) directly targeted this with geographic rebalancing
  and a rebuilt AI Discovery pipeline using Tavily live search.
- The fix has been live for **4 days** as of this report.

### What we cannot confirm (blocked)
- Whether post-fix article counts for AU/CA/UAE/SG have improved
- Whether AI Discovery is contributing new country coverage
- Whether US+India share has decreased toward a healthier ~20-25%

### Recommended actions
1. **Run this report from a network environment with Supabase access** (the deployed VPS,
   or a local dev machine with `.env` populated) — see queries in sections above.
2. **Check cron logs** on the VPS for any pipeline errors since 2026-03-29.
3. If AU/CA/UAE are still missing >50% of days after 7 days post-fix, consider:
   - Adding dedicated RSS feeds for those jurisdictions (AUSTRAC, FINTRAC, CBUAE)
   - Increasing Tavily query frequency for those specific countries
   - Adding country-targeted keyword sets to `pending_keywords`

### Proxy allowlist fix
To enable scheduled coverage reports from this environment, add
`ykfkbuuwfqmjkogwbkjp.supabase.co` to the egress proxy allowlist, or run the
coverage-check script directly on the VPS where Supabase access is unrestricted.

---

*Report generated by Claude Code scheduled task. Commit: pending.*
