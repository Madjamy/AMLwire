# AMLWire — Functional Specification

**Platform**: [amlwire.com](https://amlwire.com)
**Purpose**: Financial crime intelligence platform for AML compliance professionals, investigators, and regulators
**Last Updated**: 2026-03-15

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Pipeline Architecture](#2-pipeline-architecture)
3. [Data Sources](#3-data-sources)
4. [Pre-Processing & Filtering](#4-pre-processing--filtering)
5. [AI Analysis Engine](#5-ai-analysis-engine)
6. [Typology System](#6-typology-system)
7. [Curation & Publishing](#7-curation--publishing)
8. [Database Schema](#8-database-schema)
9. [Audit & Observability](#9-audit--observability)
10. [Environment Configuration](#10-environment-configuration)
11. [File Structure](#11-file-structure)
12. [Changelog](#12-changelog)

---

## 1. System Overview

AMLWire is an automated intelligence pipeline that:

1. **Fetches** AML/financial crime news from 5 sources (NewsAPI, Tavily, Country-specific, RSS, GDELT)
2. **Deduplicates** against existing articles and within the batch
3. **Analyzes** each article with AI (Grok 4.1 Fast via OpenRouter) to classify, summarize, and extract structured data
4. **Curates** for global diversity (country caps, quality ranking)
5. **Publishes** to Supabase for the frontend

The pipeline runs daily via `python main.py`.

---

## 2. Pipeline Architecture

### 12-Step Flow

| Step | Action | File | Description |
|------|--------|------|-------------|
| 1 | Global NewsAPI | `fetch_newsapi.py` | 25+ AML queries, 10 results each, 7-day lookback |
| 2 | Tavily Deep Search | `fetch_tavily.py` | 41 global + 29 authority + 18 country + 11 regulatory queries |
| 3 | Country-Specific | `fetch_country_news.py` | 17 jurisdictions, top 5 per country via NewsAPI |
| 4 | Regulatory RSS | `fetch_rss_feeds.py` | 12 regulatory body feeds, 30-day lookback |
| 5 | GDELT Global | `fetch_gdelt.py` | 13 queries, 50 results each, 3-day lookback |
| 6 | Date Filter | `main.py` | Drop articles with no discoverable publish date |
| 7 | Staging | `upload_supabase.py` | Save all candidates to `articles_staging` (audit trail) |
| 8 | Deduplication | `deduplicate.py` | URL + title similarity, within batch + vs Supabase |
| 9 | AI Analysis | `analyze_articles.py` | Pre-filter → scrape → AI classify/summarize (Grok 4.1 Fast) |
| 10 | Curation | `curate_articles.py` | Country caps, quality ranking, max 40 articles |
| 11 | Upload | `upload_supabase.py` | Upsert to `articles` table, link related articles |
| 12 | Typology Summaries | `generate_typology_summary.py` | AI synthesis per typology group |

### Data Flow Diagram

```
NewsAPI (25+ queries)
Tavily  (99 queries)     ──→  Combined Pool  ──→  Date Filter  ──→  Staging
Country (17 × 2-3 queries)        (300+)         (drop no-date)     (audit)
RSS     (12 feeds)                                     │
GDELT   (13 queries)                                   ▼
                                                  Deduplication
                                                  (URL + title)
                                                       │
                                                       ▼
                                              Two-Tier Pre-Filter
                                          (hard pass / soft pass / drop)
                                                       │
                                                       ▼
                                              Parallel Scraping
                                             (5 workers, 12K chars)
                                                       │
                                                       ▼
                                              AI Analysis (Grok 4.1)
                                            (batches of 10 articles)
                                                       │
                                                       ▼
                                              Date Gate (7-day max)
                                                       │
                                                       ▼
                                                  Curation
                                           (country caps + quality)
                                                       │
                                                       ▼
                                              Upload to Supabase
                                             (articles + typology
                                                  summaries)
```

---

## 3. Data Sources

### 3.1 NewsAPI (`fetch_newsapi.py`)

- **API**: `https://newsapi.org/v2/everything`
- **Authentication**: Dual-key fallback (NEWSAPI_KEY_1 primary, NEWSAPI_KEY_2 on 429/rate limit)
- **Rate Limit**: 100 requests/day per key
- **Lookback**: 7 days
- **Results per query**: 10 (pageSize)

**25+ Query Topics**:

| Category | Queries |
|----------|---------|
| Core AML | "money laundering", "AML enforcement", "anti-money laundering compliance" |
| Typologies | "financial crime typology", "shell company money laundering", "trade based money laundering TBML" |
| Regulatory | "FATF compliance evaluation", "suspicious transaction report" |
| Sanctions | "sanctions violation evasion", "OFAC SDN sanctions enforcement" |
| Tax | "tax evasion fraud prosecution", "offshore tax fraud money laundering" |
| Crypto | "crypto laundering enforcement", "crypto mixer tornado cash" |
| Cybercrime | "ransomware proceeds laundering", "BEC business email compromise", "pig butchering romance scam" |
| Trafficking | "human trafficking money laundering", "drug trafficking laundering" |
| Other | "hawala underground banking", "real estate money laundering", "PEP corruption", "organized crime laundering" |

**Relevance Filter**: 50+ TOPIC_KEYWORDS must appear in title/description (money laundering, aml, financial crime, sanctions, fatf, etc.)

---

### 3.2 Tavily (`fetch_tavily.py`)

- **API**: `https://api.tavily.com/search`
- **Search Depth**: "basic"
- **Results per query**: 8 (global/authority/country), 5 (regulatory domains)

**Four Query Categories (99 total queries)**:

#### Global Queries (41 queries, 7-day lookback)

Core AML, human trafficking financial angle, drug trafficking, hawala/informal, TBML, real estate, crypto/DeFi, shell companies, professional enablers, PEP/bribery, sanctions, tax crimes, terror finance, organized crime, cybercrime, high-value cybercrime (ransomware, BEC, investment fraud, pig butchering, cyber heist, deepfake, SIM swap), FATF/regulatory.

#### Authority Queries (29 queries, 7-day lookback)

| Authority Type | Bodies Covered |
|---------------|----------------|
| FATF | Reports, grey/black list, guidance papers |
| FSRBs (9) | MONEYVAL, APG, ESAAMLG, GABAC, GAFILAT, GIABA, MENAFATF, EAG, CFATF |
| Major FIUs (6) | AUSTRAC, FinCEN, UKFIU/NCA, FINTRAC, TRACFIN, FIU-IND |
| International | Egmont Group, generic FIU cooperation |

#### Country Queries (18 jurisdictions, 7-day lookback)

Australia, USA, UK, India, Singapore, UAE, Japan, Hong Kong, Malaysia, South Korea, China, Indonesia, EU, Germany, Canada, New Zealand, South Africa, Nigeria — 2 queries each with country-specific authority names (ED, MAS, CAD, CBUAE, JAFIC, JFIU, BNM, KoFIU, PBC, PPATK, BaFin, FIC, EFCC).

#### Regulatory Domain Queries (11 direct-source, 90-day lookback)

Searches within specific regulatory websites using Tavily's `include_domains`:

| Regulator | Domain |
|-----------|--------|
| AUSTRAC | austrac.gov.au |
| FATF | fatf-gafi.org |
| MAS Singapore | mas.gov.sg |
| Egmont Group | egmontgroup.org |
| APG | apgml.org |
| HKMA | hkma.gov.hk |
| ED India | enforcementdirectorate.gov.in |
| MONEYVAL | coe.int |
| FinCEN | fincen.gov |
| FINTRAC | fintrac-canafe.gc.ca |
| BNM Malaysia | bnm.gov.my |

**Filtering Layers**:

1. **Blocked Domains**: researchgate.net, framework.amltrix.com, ojp.gov, telecomasia.net, hollywoodreporter.com, sputnikglobe.com, lelezard.com, wfxg.com, markets.ft.com, maritime-executive.com, globenewswire.com, prnewswire.com, businesswire.com, accesswire.com
2. **Resource URL Patterns** (regex): Blocks /topics, /resources, /guidance, /faq, /learn, /library, /publications, /whitepaper, /what-is, /definitions, /glossary, /careers, /contact, /search, /en/countries/, /calendar/, /events/, /tags/, /category/, /archives/
3. **Old PDF Filter**: Blocks PDFs with pre-2024 year in URL path
4. **Evergreen Title Filter**: Blocks "Understanding X", "What is X", "How to X", "A guide to X", etc.
5. **Topic Keyword Filter**: 80+ AML-related keywords must appear

**Date Extraction** (`_extract_date`):

| Priority | Method | Confidence |
|----------|--------|------------|
| 1 | Tavily API `published_date` field | `"api"` |
| 2 | URL path date patterns (`/2026/03/10`, `/20260310-`, `/2026/03/`) | `"url_extracted"` |
| 3 | Content text date patterns (Month DD, YYYY / DD Month YYYY / ISO) | `"content_extracted"` |
| 4 | No date found | Returns `None` + `"none"` (article dropped at Step 6) |

---

### 3.3 Country-Specific News (`fetch_country_news.py`)

- **API**: NewsAPI
- **17 Countries**: Australia, USA, UK, India, Singapore, UAE, Japan, Hong Kong, Malaysia, South Korea, China, Indonesia, EU, Germany, Canada, South Africa, Nigeria
- **Queries per country**: 2-3 country-specific (e.g., "India Enforcement Directorate money laundering PMLA")
- **Top N per country**: 5 articles
- **Lookback**: 7 days

---

### 3.4 Regulatory RSS Feeds (`fetch_rss_feeds.py`)

- **No API key required** (free, direct RSS)
- **Lookback**: 30 days (regulatory publications are less frequent)
- **Date Handling**: Dateless articles are rejected (not assumed recent)

**14 Feeds** (12 regulatory/law enforcement + 2 specialist publications):

| Source | Feed URL | Country | Region |
|--------|----------|---------|--------|
| FCA | fca.org.uk/news/rss.xml | UK | Europe |
| DOJ | justice.gov/news/rss | USA | Americas |
| OFAC | ofac.treasury.gov/rss.xml | USA | Americas |
| FinCEN | fincen.gov/news/rss.xml | USA | Americas |
| SEC Press | sec.gov/news/pressreleases.rss | USA | Americas |
| Europol | europol.europa.eu/newsroom/rss | International | Europe |
| Interpol | interpol.int/en/News-and-Events/News/rss | International | Global |
| GFI | gfintegrity.org/feed/ | International | Global |
| UNODC | unodc.org/unodc/en/frontpage/rss.xml | International | Global |
| NCA UK | nationalcrimeagency.gov.uk/.../rss | UK | Europe |
| ACAMS | acams.org/.../rss | International | Global |
| FATF News | fatf-gafi.org/.../rss.xml | International | Global |
| MoneyLaunderingNews | moneylaunderingnews.com/feed/ | International | Global |
| Financial Crime Academy | financialcrimeacademy.org/feed/ | International | Global |

**Note**: EIN Presswire feeds (moneylaundering.einnews.com) were evaluated but blocked by anti-bot verification. HKMA, US Treasury, FINRA, and Cifas RSS URLs returned 404/503 — these regulators don't maintain public RSS feeds.

**AML Keyword Filter**: 55+ keywords must appear in title/summary (money laundering, aml, sanctions, fraud, crypto, fatf, enforcement, typology, etc.)

---

### 3.5 GDELT (`fetch_gdelt.py`)

- **API**: `https://api.gdeltproject.org/api/v2/doc/doc` (Doc 2.0 API)
- **Rate Limit**: 1 request per 5 seconds
- **Lookback**: 3 days (GDELT is near-real-time)
- **Results per query**: 50 max
- **Language**: English only (`sourcelang:eng` API parameter)

**13 Queries**:

| Category | Query |
|----------|-------|
| Core enforcement | `"money laundering" arrest`, `"money laundering" convicted`, `"money laundering" enforcement fine`, `"anti-money laundering" penalty investigation` |
| Crypto | `"crypto" "money laundering" enforcement`, `"pig butchering" fraud arrest` |
| South Asia / SE Asia | `"money laundering" India arrest`, `"money laundering" Singapore Malaysia enforcement` |
| Africa / Middle East | `"money laundering" Nigeria enforcement`, `"money laundering" UAE enforcement` |
| Regulatory | `FATF "grey list" AML`, `AUSTRAC enforcement penalty` |
| Sanctions | `"sanctions evasion" enforcement`, `"OFAC" sanctions designation` |

**Purpose**: Catches regional outlets (uniindia.com, novanews.co.za) missed by NewsAPI/Tavily. GDELT indexes news from 65+ languages and thousands of global sources.

---

## 4. Pre-Processing & Filtering

### 4.1 Date Filter (Step 6)

Articles with no `published_at` are dropped and logged (titles of first 10 shown in console). This catches:
- Old regulatory documents with no discoverable date
- Navigation/index pages
- Stale content that would otherwise get today's date

### 4.2 Deduplication (`deduplicate.py`)

**Three-layer dedup**:

1. **Exact URL Match** — within batch + against Supabase `articles.source_url`
2. **Near-Duplicate Title Match**:
   - Stop words removed (44 words: a, an, the, in, on, at, to, of, for, and, or, but, is, are, was, were, with, by, from, as, its, it, be, has, had, have, that, this, which, who, how, says, said, over, after, amid, into, about, up, us)
   - **Prefix match**: If first 4 significant words match exactly → duplicate
   - **Jaccard similarity**: Default threshold 0.60 (60% word overlap)
   - **Country-aware**: If two articles have different known countries, threshold rises to 0.75 (prevents "AUSTRAC enforcement" vs "ED India enforcement" false merging)
3. **Date cutoff**: Articles older than 7 days dropped

### 4.3 Two-Tier Pre-Filter (`analyze_articles.py`)

**Tier 1 — Hard Pass** (at least one multi-word AML phrase in title+description+content):

| Category | Phrases |
|----------|---------|
| Core AML | money laundering, anti-money laundering, aml compliance, aml enforcement, financial crime, illicit finance, illicit funds, proceeds of crime, proceeds of fraud, proceeds of corruption, illicit proceeds, criminal proceeds |
| Enforcement | enforcement action, deferred prosecution, asset forfeiture, asset seizure, compliance failure, regulatory fine, aml fine, aml penalty, wire fraud, bank fraud, investment fraud, confiscation order, cash seizure, currency seizure |
| Convictions | convicted of fraud, arrested for fraud, indicted for fraud, charged with laundering, convicted of laundering, guilty of laundering, fined for aml, fined for compliance |
| Suspicious Activity | suspicious transaction, suspicious activity report, suspicious matter report, sar filing, smr filing, suspicious matter |
| Typologies | structuring, smurfing, shell company, shell companies, beneficial ownership, nominee director, hawala, trade-based money laundering, tbml, pig butchering, romance scam, money mule, mule account, crypto laundering, crypto mixing, sanctions evasion, sanctions violation, terrorist financing, proliferation financing, unexplained wealth, predicate offence, predicate offense |
| Compliance | know your customer, kyc, customer due diligence, transaction monitoring, correspondent banking, de-risking |
| Regulators | fatf, fincen, austrac, egmont group, moneyval, apg aml, wolfsberg, financial intelligence unit, fiu advisory, mutual evaluation, national risk assessment, pmla |
| Crime Types | kleptocracy, bribery conviction, corruption proceeds, drug trafficking proceeds, human trafficking proceeds, ransomware proceeds, cyber heist, business email compromise, deepfake fraud, synthetic identity |

**Tier 2 — Soft Pass** (broad keywords; if found, scrape article first, then re-check against Tier 1 phrases):

fraud, seized, convicted, prosecution, indicted, arrested, sentenced, forfeiture, confiscated, laundered, embezzlement, bribery, corruption, trafficking, sanctions, compliance, regulatory, enforcement, penalty, fine, bank secrecy

**Drop** — articles matching neither tier are logged and excluded.

---

## 5. AI Analysis Engine

### Model & Configuration

| Setting | Value |
|---------|-------|
| Model | `x-ai/grok-4.1-fast` (via OpenRouter) |
| Temperature | 0.1 |
| Max Tokens | 32,000 |
| Batch Size | 10 articles per API call |
| API Base | `https://openrouter.ai/api/v1` |

### Scraping

Before AI analysis, each article's full text is scraped:

| Setting | Value |
|---------|-------|
| Max text length | 12,000 characters |
| Fallback content limit | 5,000 characters (from Tavily snippet) |
| Timeout | 15 seconds (20s on retry) |
| Max retries | 3 with exponential backoff (2s, 4s) |
| User-Agent rotation | 4 browser UAs (Chrome, Safari, Chrome Linux, Firefox) |
| Parallelism | ThreadPoolExecutor, 5 workers |
| Content selectors | article, main, .article-body, .entry-content, .post-content, .story-body, [itemprop='articleBody'] |
| Tags removed | script, style, nav, footer, header, aside, form, button, noscript, iframe, svg |

### AI Output Fields (per article)

```json
{
  "title": "Original source headline (verbatim)",
  "amlwire_title": "Original headline derived from article body text",
  "published_date": "DD-MM-YYYY",
  "country": "Primary jurisdiction or null",
  "region": "Americas | Europe | Asia-Pacific | Middle East & Africa | Global",
  "source_name": "Publication name",
  "source_url": "Article URL",
  "summary": "Exactly 4 sentences",
  "modus_operandi": "Detailed placement/layering/integration description",
  "aml_typology": "One of 26 canonical types",
  "category": "news | typology",
  "tags": ["tag1", "tag2", "tag3", "tag4"],
  "enforcement_authority": "Specific regulator/agency or null",
  "financial_amount": "USD X million fine or null",
  "key_entities": ["entity1", "entity2"],
  "action_required": true | false,
  "publication_type": "enforcement_action | regulatory_guidance | typology_study | industry_news"
}
```

### Key AI Rules

**Summary Format**: Exactly 4 sentences:
1. Lead — WHO/WHAT/WHERE/WHEN
2. Scale — amounts, victims, scope
3. Method — HOW the scheme worked
4. Significance — why it matters for compliance

**AMLWire Headline Rule**: Derived from full article body text (NOT source headline). Format: `[Authority] [Strong Verb] [Entity] [Amount] [Jurisdiction]`. Under 120 chars. Active voice. No hedge words.

**Typology Disambiguation**: Pick based on what the article is PRIMARILY about. Bank failing AML duties → "AML compliance failure" even if underlying crime is trafficking. Use trafficking/drug typology ONLY when article describes HOW laundering was conducted.

**Modus Operandi Honesty**: If article lacks MO detail, use template: *"Modus operandi not reported. AMLWire has documented similar [typology] cases involving [real mechanic]."*

**Date Verification**:
- If `date_confidence` is `"none"`: WARNING flag, AI must find date in content or EXCLUDE
- If `date_confidence` is `"content_extracted"`: NOTE to verify
- Regulatory documents: 30-day window (vs 14 days for news)
- Articles older than 14 days from today: EXCLUDE

### Resilience

- **Batch retry**: If AI returns empty → wait 5s → retry → if still fails and batch > 5, split in half
- **JSON recovery**: On parse failure, attempts trailing comma fix, wrapping in array, and per-object regex extraction
- **Typology normalization**: Word-boundary regex matching to snap AI-returned labels to canonical list

---

## 6. Typology System

### 26 Canonical Typologies

| # | Typology | Category |
|---|----------|----------|
| 1 | Structuring / Smurfing | Placement |
| 2 | Trade-based money laundering (TBML) | Layering |
| 3 | Shell companies and nominee ownership | Layering |
| 4 | Real estate laundering | Integration |
| 5 | Cash-intensive business laundering | Placement |
| 6 | Offshore concealment | Layering |
| 7 | Crypto-asset laundering | All stages |
| 8 | Crypto mixing / tumbling | Layering |
| 9 | Darknet-enabled laundering | Placement/Layering |
| 10 | Money mules | Placement/Layering |
| 11 | Hawala and informal value transfer | All stages |
| 12 | Pig butchering / romance investment scam | Predicate |
| 13 | Business Email Compromise (BEC) | Predicate |
| 14 | Ransomware proceeds | Predicate |
| 15 | Synthetic identity fraud | Predicate |
| 16 | Deepfake / AI-enabled fraud | Predicate |
| 17 | Environmental crime proceeds | Predicate |
| 18 | NFT / DeFi fraud | Predicate |
| 19 | Sanctions evasion | Regulatory |
| 20 | Professional enablers | Facilitation |
| 21 | Terrorist financing | TF |
| 22 | Drug trafficking proceeds | Predicate |
| 23 | Human trafficking proceeds | Predicate |
| 24 | Cybercrime proceeds | Predicate |
| 25 | AML compliance failure | Regulatory |
| 26 | AML News | General |

### Typology Normalization

AI output is normalized to canonical labels using keyword matching with word boundaries:

| Keywords (any match) | Canonical Label |
|---------------------|-----------------|
| pig butcher, sha zhu pan, romance invest, scam compound | Pig butchering / romance investment scam |
| business email compromise, bec fraud, ceo fraud, vendor impersonat, payroll diversion | Business Email Compromise (BEC) |
| ransomware, ransom demand, ransom payment | Ransomware proceeds |
| synthetic identity, synthetic id, credit washing | Synthetic identity fraud |
| deepfake, ai-generated, liveness bypass, kyc bypass | Deepfake / AI-enabled fraud |
| environmental crime, illegal logging, wildlife traffic, iuu fishing, illegal mining | Environmental crime proceeds |
| nft, defi, rug pull, flash loan, liquidity pool | NFT / DeFi fraud |
| mixing, tumbl, mixer, tornado, privacy coin, monero | Crypto mixing / tumbling |
| darknet, dark web | Darknet-enabled laundering |
| crypto, blockchain, virtual asset, bitcoin, ethereum | Crypto-asset laundering |
| structuring, smurfing | Structuring / Smurfing |
| trade-based, tbml, invoice fraud, over-invoic, under-invoic, phantom shipment | Trade-based money laundering (TBML) |
| shell compan, nominee, beneficial owner | Shell companies and nominee ownership |
| real estate, property launder | Real estate laundering |
| cash-intensive, cash intensive, cash business | Cash-intensive business laundering |
| offshore, tax haven | Offshore concealment |
| money mule, mule account | Money mules |
| hawala, informal value, ivts | Hawala and informal value transfer |
| sanction evas, sanctions evas, sanction | Sanctions evasion |
| professional enabler, accountant, lawyer, notary | Professional enablers |
| terrorist financ, terror financ | Terrorist financing |
| drug trafficking, narco, cartel | Drug trafficking proceeds |
| human trafficking, modern slavery | Human trafficking proceeds |
| cybercrime, cyber fraud, scam proceed | Cybercrime proceeds |
| compliance fail, aml fail, control fail, fine, penalty, enforcement action | AML compliance failure |

Unrecognized typologies default to "AML News".

### Typology Summaries

After curation, articles are grouped by typology. For each group (excluding "AML News" and "AML compliance failure"), the AI generates a 3-4 sentence synthesis:
1. What the typology IS
2. How it appeared in today's reporting
3. Countries/regions involved
4. Emerging patterns or risk signals

Output saved to `typology_summaries` table.

---

## 7. Curation & Publishing

### Country Caps

| Countries | Max Articles |
|-----------|-------------|
| USA, UK, Australia, Japan, Singapore, India, UAE | 5 each |
| All other countries | 2 each |

### Quality Scoring Model (0-100)

Each article is scored using 4 tiers. The `quality_score` is stored in Supabase for frontend feed ranking.

**Tier 1 — Content Quality (0-40)**:

| Signal | Points |
|--------|--------|
| publication_type: enforcement_action (regulatory) | +20 |
| publication_type: enforcement_action (routine arrest/sentencing) | +5 |
| publication_type: regulatory_guidance | +15 |
| publication_type: typology_study | +10 |
| publication_type: industry_news | +0 |
| Modus operandi > 200 chars | +10 |
| Modus operandi > 100 chars | +7 |
| Modus operandi > 50 chars | +3 |
| MO fallback template ("Modus operandi not reported...") | +0 (excluded from length check) |
| Financial amount present | +10 |

**Tier 2 — Typology & Predicate Crime (0-30)**:

| Signal | Points |
|--------|--------|
| HIGH_VALUE typology (18 types) | +15 |
| Mid-tier typology (not HIGH, not LOW) | +8 |
| LOW_VALUE typology (AML News, compliance failure) | +0 |
| Cybercrime predicate (Ransomware, BEC, Deepfake, Synthetic identity, Cybercrime proceeds, Darknet, NFT/DeFi) | +15 |
| Trafficking predicate (Human/Drug trafficking proceeds) | +12 |
| Sanctions evasion | +12 |
| Other specific predicate | +5 |

**Tier 3 — Authority & Significance (0-20)**:

| Signal | Points |
|--------|--------|
| Major authority named (AUSTRAC, DOJ, FCA, FinCEN, OFAC, MAS, HKMA, SEC, Europol, Interpol, ED India, NCA, RBI, etc.) | +10 |
| Other named authority | +5 |
| UN/FATF/FSRB/mutual evaluation in title/summary | +10 |
| National regulator keyword in title/summary | +5 |

**Tier 4 — Strategic Priority (0-10)**:

| Signal | Points |
|--------|--------|
| Priority country (Australia, UK, India) | +5 |
| Other named country | +2 |
| action_required = true | +5 |

### Quality Tiers

Each article is assigned a tier label based on its score, stored as `quality_tier` in Supabase:

| Tier | Score | Meaning |
|------|-------|---------|
| **Critical** | 90-100 | Major enforcement actions with institutional significance (FATF/UN), large fines from major authorities |
| **High** | 75-89 | Significant enforcement, high-value typologies with detail from major authorities |
| **Elevated** | 60-74 | Specific typology articles with moderate detail, regulatory guidance |
| **Watch** | 0-59 | Generic news, thin content, informational |

### Individual Criminal Justice Exclusion

Articles classified as `enforcement_action` that describe **individual arrests, sentencings, or convictions** with no systemic compliance value are excluded during curation. Detection: title contains arrest/sentencing signals (arrested, jailed, sentenced, convicted, pleads guilty, indicted, prison, custody, bail denied/rejected) AND does NOT contain systemic signals (OFAC, FinCEN, AUSTRAC, sanctions, dismantle, network, operation, ring, syndicate, compliance, fine, penalty).

Borderline articles (arrest + systemic element like "police dismantle network" or "OFAC designates") pass through.

### Cap Override

Articles scoring **65+** bypass the country cap (still subject to MAX_TOTAL). This ensures genuinely significant articles (major enforcement actions, large fines, UN/FATF decisions) are never silently dropped.

### Total Cap

**MAX_TOTAL = 40** articles per pipeline run (configurable via `CURATION_MAX_TOTAL` env var). Overflow articles are logged with titles, countries, and scores.

### Post-AI Date Gate

After AI analysis, an additional date gate drops articles:
- Articles with AI-determined dates older than 7 days
- Articles where AI returned no date

### Related Articles

On upload, each article is linked to up to 5 related articles:
- Up to 5 by matching `aml_typology` (most recent, excluding "AML News")
- Up to 3 by matching `enforcement_authority`
- Capped at 5 total related IDs

---

## 8. Database Schema

### `articles` Table

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | Primary key, auto-generated |
| title | TEXT | Original source headline (verbatim) |
| amlwire_title | TEXT | AI-generated original headline |
| summary | TEXT | 4-sentence AI summary |
| modus_operandi | TEXT | ML method description or honesty template |
| raw_snippet | TEXT | Original fetch snippet |
| source_url | TEXT | UNIQUE — upsert key |
| source_name | TEXT | Publication name |
| category | TEXT | "news" or "typology" |
| aml_typology | TEXT | One of 26 canonical labels |
| country | TEXT | Primary jurisdiction (nullable) |
| region | TEXT | Americas / Europe / Asia-Pacific / Middle East & Africa / Global |
| tags | TEXT[] | Array of 4-7 tags |
| published_at | TIMESTAMPTZ | Article publication date |
| fetched_at | TIMESTAMPTZ | Pipeline run timestamp |
| enforcement_authority | TEXT | Regulator/agency name (nullable) |
| financial_amount | TEXT | e.g., "USD 50M fine" (nullable) |
| key_entities | TEXT[] | Array of entity names |
| action_required | BOOLEAN | Default FALSE |
| publication_type | TEXT | enforcement_action / regulatory_guidance / typology_study / industry_news |
| quality_score | INTEGER | 0-100 quality score from scoring model (default 0) |
| quality_tier | TEXT | Critical / High / Elevated / Watch (derived from score) |
| related_article_ids | UUID[] | Up to 5 linked article IDs |
| created_at | TIMESTAMPTZ | Auto-generated |

### `articles_staging` Table

Audit trail — all fetched articles saved before dedup/AI:

| Column | Type |
|--------|------|
| title | TEXT |
| url | TEXT (UNIQUE — upsert key) |
| source | TEXT |
| published_at | TEXT |
| description | TEXT |
| api_source | TEXT |
| country | TEXT |
| fetched_at | TIMESTAMPTZ |

### `typology_summaries` Table

| Column | Type |
|--------|------|
| id | UUID |
| typology_name | TEXT |
| summary | TEXT |
| countries_involved | TEXT[] |
| article_count | INTEGER |
| digest_date | DATE |
| created_at | TIMESTAMPTZ |

---

## 9. Audit & Observability

### JSONL Log Files (`logs/` directory)

| File | Contents | Fields |
|------|----------|--------|
| `prefilter_drops_YYYY-MM-DD.jsonl` | Articles dropped by pre-filter | timestamp, title, url, source, reason |
| `scrape_failures_YYYY-MM-DD.jsonl` | Failed article scrapes | timestamp, url, error |
| `ai_exclusions_YYYY-MM-DD.jsonl` | AI-excluded articles | timestamp, title, url, reason |

### Console Logging

- Per-source article counts at each fetch step
- Dropped no-date articles (titles of first 10)
- Pre-filter drop count and sample titles
- Soft-pass promotions (title of each promoted article)
- Scrape success/failure per URL with character counts
- Batch retry and split events
- Dedup counts (URL dupes, title dupes)
- Curation country breakdown and cap hits
- Upload success per article

### Pipeline Stats

Logged to `pipeline_run_stats` table (if exists):
- Per-source counts (newsapi, tavily, country, rss, gdelt)
- Total fetched, after dedup, published

---

## 10. Environment Configuration

### Required `.env` Variables

| Variable | Purpose |
|----------|---------|
| `NEWSAPI_KEY_1` | Primary NewsAPI key |
| `NEWSAPI_KEY_2` | Fallback NewsAPI key (on rate limit) |
| `TAVILY_API_KEY` | Tavily search API key |
| `OPENROUTER_API_KEY` | OpenRouter API key (for Grok model) |
| `OPENROUTER_MODEL` | AI model ID (default: `x-ai/grok-4.1-fast`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |

### Optional `.env` Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `CURATION_MAX_TOTAL` | Max articles per pipeline run | 40 |
| `SERPAPI_KEY_1` | SerpAPI key (legacy, not actively used) | — |
| `SERPAPI_KEY_2` | SerpAPI fallback key | — |

---

## 11. File Structure

```
New Use cases/
├── main.py                              # Pipeline orchestrator (12 steps)
├── CLAUDE.md                            # WAT framework instructions
├── AMLWIRE_FUNCTIONAL_SPEC.md           # This document
├── .env                                 # API keys and config
├── logs/                                # JSONL audit logs (auto-created)
│   ├── prefilter_drops_YYYY-MM-DD.jsonl
│   ├── scrape_failures_YYYY-MM-DD.jsonl
│   └── ai_exclusions_YYYY-MM-DD.jsonl
└── tools/
    ├── fetch_newsapi.py                 # Source 1: NewsAPI
    ├── fetch_tavily.py                  # Source 2: Tavily (global + authority + country + regulatory)
    ├── fetch_country_news.py            # Source 3: Country-specific (17 jurisdictions)
    ├── fetch_rss_feeds.py               # Source 4: Regulatory RSS (12 feeds)
    ├── fetch_gdelt.py                   # Source 5: GDELT global news
    ├── deduplicate.py                   # URL + title dedup (Jaccard + prefix + country-aware)
    ├── analyze_articles.py              # Pre-filter, scrape, AI analysis (Grok 4.1 Fast)
    ├── curate_articles.py               # Country caps, quality ranking, total cap
    ├── upload_supabase.py               # Upsert articles + staging + typology summaries
    ├── generate_typology_summary.py     # AI typology synthesis
    ├── audit_logger.py                  # JSONL audit logging
    ├── setup_schema.py                  # DB column management
    ├── log_pipeline_stats.py            # Pipeline run stats
    ├── resummarize_existing.py          # Backfill: re-process all existing articles
    ├── regenerate_headlines.py          # Backfill: regenerate amlwire_title only
    ├── cleanup_duplicates.py            # One-off: remove duplicate articles
    ├── cleanup_old_articles.py          # One-off: remove stale articles
    ├── fix_dates.py                     # One-off: fix date formatting
    ├── fix_regions_and_dupes.py         # One-off: fix region labels
    └── standardise_typologies.py        # One-off: normalize typology labels
```

---

## 12. Changelog

### 2026-03-15 — 5-Phase Quality Overhaul

**Phase 1: Date Integrity**
- Tavily `_extract_date()` returns None instead of today's date (eliminates stale article root cause)
- Added `date_confidence` field to all Tavily articles
- RSS rejects dateless articles
- GDELT logs date parse failures
- AI date WARNING based on confidence level, not just date match
- Regulatory window expanded 14 → 30 days

**Phase 2: Scraping Reliability**
- Scrape limit 5,000 → 12,000 chars; fallback 3,000 → 5,000
- Retry with backoff (3 attempts) + User-Agent rotation (4 UAs)
- Parallel scraping with 5 workers (ThreadPoolExecutor)

**Phase 3: Pre-filter & Audit Trail**
- Created `audit_logger.py` for JSONL logging
- Expanded `_REQUIRED_PHRASES` with 20+ new AML terms
- Two-tier pre-filter: soft-pass articles scraped then re-checked
- All drops logged with titles and reasons

**Phase 4: Resilient JSON Processing**
- Batch size 20 → 10
- Per-article JSON recovery from malformed responses
- Batch retry: wait 5s → retry → split in half

**Phase 5: Source Quality Polish**
- URL filters for FATF nav, calendar, events, tags, categories
- GDELT `sourcelang:eng` for English-only
- Dedup threshold 0.50 → 0.60 with country awareness (0.75 cross-country)
- Curation MAX_TOTAL configurable via env var; overflow logged
- Typology normalization uses word-boundary regex

### 2026-03-14 — Foundation Fixes

- Added 12 regulatory RSS feeds
- Created `amlwire_title` with copyright-safe headline generation
- Added 7 new DB fields (enforcement_authority, financial_amount, key_entities, etc.)
- MO honesty rule (no fabrication)
- Fixed GDELT JSON crash + simplified queries
- Fixed headline generation to use full article body
- Fixed typology disambiguation (primary focus, not predicate crime)
- URL filters for search/nav pages and old PDFs
- Date WARNING for fetch-date articles
- Backfilled all existing articles (3 full runs)
