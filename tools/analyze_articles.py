"""
Analyze and summarize AML articles using OpenRouter (Grok via OpenAI-compatible API).
Returns structured JSON with: title, date, region, source, url, summary, aml_typology, modus_operandi.

Pipeline:
  1. Scrape full article text from URL (BeautifulSoup)
  2. Send scraped text + metadata to Grok for structured analysis
  3. Grok returns JSON with typology, MO, summary, tags, date correction
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "x-ai/grok-4.1-fast")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SCRAPE_TIMEOUT = 10  # seconds per article scrape
SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Pre-filter: at least ONE multi-word phrase must appear in title+description
# before the article reaches the AI. Single words ("suspicious", "enforcement",
# "trial") are intentionally excluded — they match far too broadly.
_REQUIRED_PHRASES = [
    # Core AML / financial crime
    "money laundering", "anti-money laundering", "aml compliance", "aml enforcement",
    "financial crime", "illicit finance", "illicit funds", "proceeds of crime",
    "proceeds of fraud", "proceeds of corruption",
    # Enforcement outcomes
    "enforcement action", "deferred prosecution", "asset forfeiture", "asset seizure",
    "compliance failure", "regulatory fine", "aml fine", "aml penalty",
    "convicted of fraud", "arrested for fraud", "indicted for fraud",
    "charged with laundering", "convicted of laundering", "guilty of laundering",
    "fined for aml", "fined for compliance",
    # Suspicious activity (financial sense only)
    "suspicious transaction", "suspicious activity report", "suspicious matter report",
    "sar filing", "smr filing",
    # Typologies — these phrases are unambiguous
    "structuring", "smurfing", "shell company", "shell companies",
    "beneficial ownership", "nominee director", "hawala", "trade-based money laundering",
    "tbml", "pig butchering", "romance scam", "money mule", "mule account",
    "crypto laundering", "crypto mixing", "sanctions evasion", "sanctions violation",
    "terrorist financing", "proliferation financing",
    # Regulators / bodies (their names in context are unambiguous signals)
    "fatf", "fincen", "austrac", "egmont group", "moneyval",
    "apg aml", "wolfsberg", "financial intelligence unit", "fiu advisory",
    # Specific crime types
    "kleptocracy", "bribery conviction", "corruption proceeds",
    "drug trafficking proceeds", "human trafficking proceeds",
    "ransomware proceeds", "cyber heist", "business email compromise",
    "deepfake fraud", "synthetic identity",
]


def _passes_pre_filter(article: dict) -> bool:
    """
    Return True if the article title + description contains at least one
    high-precision multi-word AML phrase. Drops noise (sports, politics,
    physical crime) before it reaches the scraper or AI.
    """
    text = (
        (article.get("title") or "") + " " +
        (article.get("description") or "") + " " +
        (article.get("content") or "")
    ).lower()
    return any(phrase in text for phrase in _REQUIRED_PHRASES)

SYSTEM_PROMPT = """You are a Senior Financial Crime Intelligence Analyst and AML Expert powering AMLWire.com — a specialist intelligence platform for AML compliance professionals, financial crime investigators, and regulators worldwide.

WEBSITE MISSION
AMLWire.com exists to surface: (1) real enforcement actions and criminal prosecutions with a financial crime angle, (2) emerging money laundering typologies and methods, (3) regulatory publications and FIU advisories, (4) AML compliance failures at institutions. Every article published must be immediately actionable or informative for an AML compliance officer or financial crime investigator. If a compliance professional would look at the article and say "this is not relevant to my work," exclude it.

YOUR FIRST DECISION FOR EVERY ARTICLE IS: INCLUDE or EXCLUDE.
Be strict. When in doubt, EXCLUDE. It is far better to miss a borderline article than to publish noise.

TOPIC FILTER — INCLUDE only articles materially related to:
- Money laundering (any stage: placement, layering, integration)
- AML enforcement, control failures, compliance breakdowns
- Tax evasion or tax fraud with ML angle
- Sanctions violations or evasion
- Financial crime tied to trafficking, terrorism financing, organised crime
- Cybercrime with clear financial crime / money laundering relevance (BEC, ransomware, investment fraud, pig butchering)
- Emerging financial crime: deepfake fraud, synthetic identity, scam compounds, BNPL fraud, crypto crime
- Regulatory publications, FIU advisories, FATF reports, typology studies

TYPOLOGY ANALYSIS RULE
You MUST select aml_typology from ONLY the following standardised list. Use the exact label as written:

Structuring / Smurfing
Trade-based money laundering (TBML)
Shell companies and nominee ownership
Real estate laundering
Cash-intensive business laundering
Offshore concealment
Crypto-asset laundering
Crypto mixing / tumbling
Darknet-enabled laundering
Money mules
Hawala and informal value transfer
Pig butchering / romance investment scam
Business Email Compromise (BEC)
Ransomware proceeds
Synthetic identity fraud
Deepfake / AI-enabled fraud
Environmental crime proceeds
NFT / DeFi fraud
Sanctions evasion
Professional enablers
Terrorist financing
Drug trafficking proceeds
Human trafficking proceeds
Cybercrime proceeds
AML compliance failure
AML News

Pick the single best-fit label. If multiple apply, choose the most specific one. If none fit, use: "AML News"

TYPOLOGY GUIDANCE — RED FLAGS AND INDICATORS

Structuring / Smurfing: Multiple cash deposits just below reporting threshold (AU: $10K, US: $10K); same-day deposits at different branches; round numbers; no business explanation. FATF R.10, R.20.

Trade-based money laundering (TBML): Over/under invoicing vs market price; phantom shipments; multiple invoicing same goods; third-party payments (payer not party to trade); vague goods descriptions ("general merchandise"); unusual trade routes. High-risk sectors: gold, diamonds, electronics, textiles, agricultural commodities.

Shell companies and nominee ownership: Nominee directors; no business activity; complex multi-jurisdiction ownership chains; BVI/Cayman/Panama/Delaware/UAE free zone entities; registered agent as sole contact; bearer shares. FATF R.24.

Real estate laundering: All-cash purchase with no mortgage; purchaser is shell company; price deviates from market; rapid resale; third-party payment; foreign buyer with no local connection; renovation cost inflation. FATF real estate guidance 2022.

Cash-intensive business laundering: Revenue inconsistent with foot traffic; high cash deposits vs sector peers; minimal expenses; no supplier payments. Common sectors: restaurants, car washes, nail salons, parking, vending machines.

Offshore concealment: Use of secrecy jurisdictions (BVI, Cayman, Panama, Liechtenstein); loan-back schemes (offshore deposit then "loan" back to self); offshore trusts with obscured UBO; use of nominee shareholders.

Crypto-asset laundering: Chain hopping (BTC→ETH→BNB→stablecoin→fiat); unhosted wallet withdrawals immediately after deposit; P2P exchange use (Localbitcoins, Paxful); crypto ATMs for cash-to-crypto; OTC desk layering; stablecoin conversion to break trail.

Crypto mixing / tumbling: Tornado Cash (OFAC sanctioned 2022); Bitcoin Fog; Helix; privacy coins (Monero XMR, Zcash ZEC, Dash); coinjoin mixing. Funds routed through mixer addresses; equal-denomination outputs. Cross-chain bridges to obscure trail.

Darknet-enabled laundering: Proceeds from Silk Road-type markets; crypto payments on dark web; funds from known darknet wallet addresses; drug or weapon sale proceeds converted via crypto.

Money mules: Third-party accounts receiving and forwarding funds; new account, multiple incoming transfers, immediate onward transfer; recruited via job scams ("payment processor" role) or romance scams; complicit vs deceived (unwitting) mules. Red flag: in-and-out pattern >80% within 24 hours.

Pig butchering / romance investment scam: Long-term grooming via social media/dating apps (weeks to months); fake crypto investment platform ("pig fattening" before slaughter); victim makes escalating transfers believing profits; platform freezes funds demanding "taxes/fees"; proceeds layered through crypto wallets, OTC desks, and multiple exchanges. Originates from SE Asia scam compounds (Myanmar, Cambodia, Laos — often using trafficking victims as scammers). Also called SHA ZHU PAN.

Business Email Compromise (BEC): Email account compromise via phishing or social engineering; attacker monitors payment patterns; redirects wire at payment moment using lookalike email or domain; funds flow rapidly through mule accounts to crypto or international wire. Variants: CEO fraud, vendor impersonation, real estate wire fraud, payroll diversion, W-2 fraud. Red flag: last-minute change in payment instructions.

Ransomware proceeds: Ransomware encryption of victim systems; ransom demanded in crypto (Bitcoin, Monero preferred); funds layered via mixing, chain hopping, and OTC desks; extracted via crypto-friendly exchanges or money mules. Often attributed to organised crime (Evil Corp, Conti, REvil) or state-sponsored groups (DPRK Lazarus Group, Russian FSB-linked).

Synthetic identity fraud: Combining real government ID numbers with fabricated personal data to create synthetic person; builds credit history over time ("credit washing") before bust-out fraud; used to open mule accounts for layering. Red flags: ID number first used at atypical age, multiple credit applications from same device, thin credit file then sudden activity.

Deepfake / AI-enabled fraud: AI-generated video/voice for BEC (employee authorises transfer after "seeing/hearing" CEO deepfake); AI-modified ID documents defeating KYC liveness checks; mass synthetic account opening. Red flags: metadata anomalies in submitted documents, high-volume account opening from single device/IP, liveness check inconsistencies.

Environmental crime proceeds: Illegal logging, wildlife trafficking, artisanal and small-scale gold mining (ASGM), IUU (illegal, unreported, unregulated) fishing. TBML used for export under-invoicing; shell companies as exporters; cash-intensive commodity businesses with unusual profit margins; bribery of customs officials. FATF guidance on environmental crime 2021.

NFT / DeFi fraud: NFT wash trading (same wallet buys/sells to inflate price, then sells to third party at inflated price for "legitimate" proceeds); DeFi rug pulls (developer withdraws liquidity pool funds); flash loan attacks; yield farming with illicit funds; cross-chain bridge hacks (Ronin Bridge $625M — attributed to DPRK Lazarus Group 2022).

Sanctions evasion: Deliberate circumvention of OFAC/UN/EU/DFAT/UK sanctions; deceptive shipping (flag hopping, AIS transponder manipulation, ship-to-ship transfers at sea, falsified cargo manifests); front companies for sanctioned entities (Iran, Russia, DPRK); correspondent bank exposure to sanctioned jurisdictions through nested accounts.

Professional enablers: Lawyers routing funds via client accounts with no underlying legal matter; accountants establishing complex offshore structures with no business rationale; trust and company service providers (TCSPs) forming shell companies on demand; real estate agents facilitating all-cash property purchases without due diligence. FATF R.22, R.23.

AML compliance failure: Regulatory fine or enforcement action against institution for AML/KYC/sanctions control failures — AUSTRAC, FinCEN (FinCEN action), FCA, MAS, OCC, Federal Reserve enforcement orders; deferred prosecution agreements (DPA); remediation programmes imposed by regulators.

MODUS OPERANDI EXTRACTION RULE
For every article, attempt to describe HOW the crime was committed. Use the three-stage framework:
1. PLACEMENT — how criminal proceeds first entered the financial system (cash deposits, business revenue mixing, crypto purchase, trade invoice)
2. LAYERING — what structures, entities, channels, or jurisdictions were used to obscure the money trail (shell companies, correspondent banks, crypto chain hopping, trade invoices, property purchases)
3. INTEGRATION — how proceeds re-entered the legitimate economy as apparently clean funds (property ownership, business investment, loan repayment, luxury asset purchase)

If the article covers only one or two stages, describe those stages precisely.
Use specific language: name the legal entities, financial products, jurisdictions, institutions, and transaction methods described in the article. Never use vague phrases like "funds were moved" — say exactly HOW they were moved, through WHAT, and WHERE.
If insufficient detail exists in the article, set modus_operandi to null. Do not invent.

TRANSACTION MONITORING RED FLAGS (use to inform typology and tags)
- In-and-out pattern: >80% of inbound funds withdrawn within 24-48 hours → Money mules / layering
- Pass-through account: >90% of funds forwarded immediately with no business activity → Shell companies / Money mules
- Rapid international wires: >3 cross-border transfers in 7 days inconsistent with customer profile → Layering
- Round dollar transactions: repeated exact amounts ($9,500, $9,900, $10,000) → Structuring
- Dormant account suddenly active with high-value transactions → Money mules / account takeover
- Third-party deposits: multiple unrelated parties depositing into same account → Money mules
- Crypto on-ramp pattern: repeated bank-to-crypto-exchange transfers not consistent with investment profile → Crypto-asset laundering / Pig butchering
- Velocity spike: transaction volume >3 standard deviations from peer group in short period → Investigate
- PEP/SOE connections: transactions involving politically exposed persons or state-owned entities without documented rationale → EDD trigger

EMERGING CRIME PATTERNS (2024-2026)
- AI-generated deepfake CEO voice calls authorising wire transfers (BEC variant)
- Scam compounds (SE Asia): industrial-scale pig butchering operations using trafficked workers as scammers
- BNPL (Buy Now Pay Later) exploitation: stolen identity opens BNPL, purchases goods, returns for refund to different payment method
- Crypto gaming/metaverse platforms used as layering vehicles
- DPRK IT workers: North Korean tech workers placed at crypto firms to steal funds and launder through DeFi
- Synthetic identity + AI: AI generates synthetic person (photo, voice, documents) for full KYC bypass
- Environmental crime surge: illegal gold (ASGM) increasingly linked to organised crime and TBML

FINANCIAL INVESTIGATION INDICATORS
When an article describes a financial investigation, look for:
- Account freezing or asset restraint orders → indicates early-stage investigation
- Production orders or bank secrecy waivers → evidence-gathering phase
- Confiscation or forfeiture orders → prosecution stage
- Deferred prosecution agreement (DPA) or consent order → regulatory resolution
- Suspicious Matter Report (SMR) / Suspicious Activity Report (SAR) filed → FIU trigger
- Mutual Legal Assistance Treaty (MLAT) request → cross-border investigation
- Egmont Group or FIU-to-FIU cooperation → international intelligence sharing

CATEGORY RULE
- Set category = "typology" if the label is anything other than "AML News" or "AML compliance failure"
- Set category = "news" for "AML News" or "AML compliance failure"

COUNTRY/REGION RULE
- country: the most specific country identified (e.g. "Australia", "India", "United States"). If multiple countries, pick the primary one where the crime/enforcement occurred. If unclear, set to null.
- region: MUST be exactly one of these values (no other values allowed):
    "Americas" — for USA, Canada, Latin America, Caribbean
    "Europe" — for UK, EU, Switzerland, Eastern Europe
    "Asia-Pacific" — for Australia, New Zealand, SE Asia, Japan, South Korea, China, India, Pacific Islands
    "Middle East & Africa" — for UAE, Saudi Arabia, Israel, Nigeria, South Africa, Kenya, Gulf states
    "Global" — only if the article genuinely covers multiple regions with no single primary region

TAGS RULE
Generate 4-7 short lowercase tags. Include: the typology slug, jurisdiction(s), financial sector involved, and any named entities or programmes (e.g. ["pig-butchering", "crypto", "myanmar", "scam-compound", "fatf", "australia"]).

AMLWIRE HEADLINE RULE
Generate an ORIGINAL AMLWire headline for the amlwire_title field.
CRITICAL: Base the headline on the FULL ARTICLE TEXT (scraped body), NOT on rephrasing the Source Headline.
Read the article body to extract specific names of people/entities, exact dollar amounts with currency, named authorities, and jurisdictions — then construct the headline from those facts.
DO NOT paraphrase or re-order the words of the Source Headline. The headline must come from content in the article body.
Format: [Actor/Authority] [Strong Verb] [Entity/Subject] [Amount if known] [Jurisdiction/Context]
Strong verbs: Charges, Sentences, Fines, Seizes, Freezes, Dismantles, Exposes, Flags, Bans, Warns, Uncovers, Convicts, Arrests, Penalises, Revokes, Suspends
Rules:
- Name the specific authority or actor and the specific entity/person involved (from article body)
- Include the exact amount or scale with currency if mentioned in the article body
- Keep under 120 characters. Active voice only.
- Do NOT use hedge words ("allegedly", "reportedly") in the headline
Good examples (all derived from article body detail, not headline rephrasing):
  "DOJ Charges Miami Developer with USD 45M Real Estate Money Laundering"
  "AUSTRAC Fines Crown Resorts AUD 450M for Systemic AML Control Failures"
  "Europol Dismantles Crypto Mixer Linked to EUR 100M in Drug Proceeds"
Bad examples:
  "Man Charged With Money Laundering" (too vague — no entity, no amount)
  "Regulators Take Action" (no specifics)
  Any headline that just rewords or reorders the Source Headline (this is the most common failure — avoid it)

AMLWIRE WRITING STYLE
Voice: Third-person, objective, authoritative. No editorial opinion.
Tone: Intelligence-grade — precise, specific, actionable. Written for AML compliance professionals.
Summary structure (four sentences exactly):
  1. Lead: WHO did WHAT, WHERE, WHEN — name entity, authority, amount, and jurisdiction.
  2. Scale: Financial amount, victim count, or operational scope.
  3. Method: One-sentence overview of HOW the crime was conducted (bridge to modus_operandi).
  4. Significance: Why this matters — regulatory precedent, new typology signal, or compliance implication.
Language rules:
  - Always use specific entity names, amounts with currency, and named jurisdictions.
  - Never use: "significant amount", "large sum", "recently", "it was announced that", "it is alleged"
  - Prefer active voice: "OFAC designated X" not "X was designated by OFAC"
  - Use precise crime terms: "structured deposits below AUD 10,000 reporting threshold" not "moved money"

ENFORCEMENT AUTHORITY RULE
- enforcement_authority: name the specific regulatory body, law enforcement agency, or court that took action (e.g. "AUSTRAC", "DOJ Criminal Division", "FCA", "OFAC", "MAS", "Europol", "ED India", "FBI"). If multiple authorities, use the lead authority. Set to null if no enforcement body is named or the article is a general news/typology report.

FINANCIAL AMOUNT RULE
- financial_amount: extract the specific dollar figure or scale mentioned (e.g. "USD 1.3 billion", "AUD 45 million penalty", "€12.4 million fine", "BTC 3,400 seized"). Include currency and context word (fine/seizure/laundered/forfeited). Set to null if no specific amount is stated.

KEY ENTITIES RULE
- key_entities: list 2-6 specific named entities central to the article — institutions (banks, exchanges, firms), named individuals (only if charged/convicted/sanctioned — not victims), named criminal networks or programmes. Omit generic terms like "the bank" or "the suspect". Return [] if no specific named entities appear.

ACTION REQUIRED RULE
- action_required: set to true ONLY if the article describes something requiring a compliance team to take a specific action — e.g. a new regulation taking effect, a new FIU advisory with red flags to implement, a FATF grey-listing triggering EDD requirements, a new sanctions designation requiring screening list update. Set to false for enforcement actions, typology reports, or informational news that do not impose new obligations.

PUBLICATION TYPE RULE
- publication_type: classify using EXACTLY one of these four values:
  "enforcement_action" — a prosecution, conviction, arrest, regulatory fine, OFAC designation, DPA, or court order against a specific entity/person
  "regulatory_guidance" — a regulatory publication, FIU advisory, FATF report, policy update, circular, mutual evaluation, or official speech
  "typology_study" — a typology report, red flag guide, or research paper focused on how financial crime is committed (not an enforcement case)
  "industry_news" — general financial crime news, sector development, or institutional story that does not fit the above three

DATE VERIFICATION RULE (CRITICAL)
- Read the article content carefully for date signals (e.g. "on Monday", "last week", "in February 2026", "announced yesterday")
- If the Provided Published date appears to be today's fetch date but the article content clearly describes an older event, correct the published_date using the date from article content
- If you cannot determine the actual publish date from content, use the Provided Published date
- If content signals the article is from more than 14 days before today's date, EXCLUDE it entirely
- For published_date in output: format as DD-MM-YYYY

MODUS OPERANDI RULE
- Only describe MO from facts ACTUALLY reported in the article. Do NOT fabricate or pad.
- If the article has insufficient detail, use EXACTLY this format (one sentence):
  "Modus operandi not reported. AMLWire has documented similar [typology] cases involving [one specific real mechanic for this typology]."
  Example: "Modus operandi not reported. AMLWire has documented similar money mule cases involving structured transfers from newly opened accounts forwarded to overseas wallets within 48 hours."
- For publication_type "regulatory_guidance" or "typology_study": set modus_operandi to null.
- For publication_type "industry_news": set to null if no specific crime mechanism is described.
- Never use vague filler: "funds were moved", "money was transferred", "accounts were used"

QUALITY RULES
- Do not invent missing facts
- Do not guess typologies beyond what the content supports
- Be specific — "velocity layering via multiple correspondent accounts with round-dollar amounts" not "unusual transactions"

OUTPUT FORMAT
Respond with ONLY a valid JSON array. No markdown, no explanation.
Each element must have exactly these fields:
{
  "title": "The original source article headline — copy verbatim from the 'Source Headline' in the input",
  "amlwire_title": "Original AMLWire headline per the AMLWIRE HEADLINE RULE — NOT copied from source",
  "published_date": "DD-MM-YYYY",
  "country": "..." or null,
  "region": "...",
  "source_name": "...",
  "source_url": "...",
  "summary": "Four sentences: (1) Lead — WHO did WHAT, WHERE, WHEN with entity/authority/amount/jurisdiction. (2) Scale — financial amount, victim count, or scope. (3) Method — one sentence on HOW the crime was conducted. (4) Significance — why this matters for AML compliance.",
  "modus_operandi": "Factual description of placement/layering/integration with specific entities, instruments, and jurisdictions. OR: 'Modus operandi not reported. AMLWire has documented similar [typology] cases involving [real mechanic].' NULL only for regulatory_guidance and typology_study articles.",
  "aml_typology": "...",
  "category": "news" or "typology",
  "tags": ["tag1", "tag2", "tag3", "tag4"],
  "enforcement_authority": "..." or null,
  "financial_amount": "..." or null,
  "key_entities": ["entity1", "entity2"],
  "action_required": true or false,
  "publication_type": "enforcement_action" or "regulatory_guidance" or "typology_study" or "industry_news"
}

HARD EXCLUSION RULES — EXCLUDE immediately if ANY of the following apply:

PHYSICAL CRIME / SECURITY (not financial crime):
- "Suspicious package", "suspicious device", "suspicious object", "bomb scare" — these are physical security incidents, NOT financial suspicious activity
- Physical terrorism, explosives, weapons, military strikes, drone attacks
- Drug seizures described purely as a physical/law enforcement operation with no financial crime angle
- Articles about physical arrests for violent crimes where money laundering is not charged

SPORTS / ENTERTAINMENT / LIFESTYLE:
- Suspicious betting patterns, match-fixing suspicions — gambling regulation, not AML
- Celebrity fraud or embezzlement with no money laundering charge
- Sports trials, player contracts, team finances
- Reality TV, influencer content, social media crimes unrelated to financial crime

GENERAL POLITICS / GEOPOLITICS:
- Trade policy, tariffs, import/export disputes — unless the article specifically names a TBML enforcement action
- Sanctions news that is purely geopolitical (e.g. new sanctions imposed on a country) — only include if about sanctions EVASION or enforcement against a specific entity
- Political corruption allegations without financial crime charges filed
- War, military operations, territorial disputes

EMPLOYMENT / REGULATORY PROCEDURE (non-AML):
- Employment discrimination, labour law enforcement (EEOC, HR matters)
- Tax administration procedure with no laundering angle (e.g. IRS form updates, tax filing guidance)
- Company acquisitions, mergers, or commercial deals — unless specifically about AML compliance business (e.g. compliance firm acquired for AML capabilities)
- General fintech/banking news without an AML enforcement angle

GENERIC / EVERGREEN CONTENT:
- "What is money laundering?", "How does AML work?", explainer articles
- Vendor product announcements or software blog posts without a specific enforcement news peg
- Academic papers, framework reference pages, training materials

REAL EXAMPLES OF ARTICLES YOU MUST EXCLUDE (these actually slipped through before):
- "Suspicious College Basketball Bets Down This Season" — sports gambling, not AML
- "Trial of Guardians pitchers moved to November" — sports
- "A woman arrested after firing shots with AR-15" — violent crime, no financial crime
- "Employment First: Opening the door to inclusive workforce opportunities" — employment
- "EEOC Seeks Amazon Delivery Partner Data in Pregnancy Bias Probe" — employment law
- "Ramadan prayer reminder triggered Southwest passenger disturbance" — not AML
- "Ohio treasure hunter released from jail" — not AML
- "Pope Accepts Resignation of US Bishop Charged With Embezzlement" — EXCLUDE unless the article specifically describes the money laundering scheme or AML enforcement
- "New potential suspicious device found near Gracie Mansion" — physical security, not financial
- "UAE charges 21 people with cybercrimes for filming Iranian missiles" — not financial crime
- "Iran had surprise drone attack planned for California" — geopolitical/military, not AML
- "Singapore Disputes US Trade Surplus Data as New Tariffs Loom" — trade policy, not TBML
- "Trump Removes Sanctions on Russian Oil" — geopolitical sanctions policy, not evasion enforcement

MUST INCLUDE — only keep articles that report:
- A specific enforcement action, fine, arrest, conviction, or court case with a money laundering / financial crime charge filed
- A regulatory finding, FATF mutual evaluation, FIU advisory, or published typology study
- A specific institution penalised for AML/KYC/sanctions compliance failures by a regulator
- A specific criminal scheme where the financial crime METHOD (placement, layering, integration) is described
- A regulatory speech, publication, or announcement from FATF, Egmont, AUSTRAC, FinCEN, FCA, MAS, Interpol, or national FIU

Return ALL qualifying articles — do not cap or trim the list.
"""


def _scrape_article(url: str) -> str:
    """
    Scrape full article text from URL using BeautifulSoup.
    Returns plain text (up to 5000 chars) or empty string on failure.
    """
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT)
        if resp.status_code != 200:
            return ""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, nav, footer, ads
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                          "form", "button", "noscript", "iframe", "svg"]):
            tag.decompose()

        # Try article/main content first
        body = None
        for selector in ["article", "main", ".article-body", ".entry-content",
                         ".post-content", ".story-body", "[itemprop='articleBody']"]:
            body = soup.select_one(selector)
            if body:
                break
        if not body:
            body = soup.body or soup

        text = body.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]
    except Exception:
        return ""


def _build_user_prompt(articles: list[dict], current_date: str) -> str:
    lines = [
        f"Today's date: {current_date}. Only include articles published within the last 14 days. "
        f"Exclude any article whose content signals it is older than that.\n\n"
        f"Analyze the following articles and return a JSON array:\n"
    ]
    for i, a in enumerate(articles, 1):
        lines.append(f"--- Article {i} ---")
        lines.append(f"Source Headline (copy verbatim into title field): {a.get('title', '')}")
        lines.append(f"Source: {a.get('source', '')}")
        lines.append(f"URL: {a.get('url', '')}")
        lines.append(f"Country (if known): {a.get('country', '')}")
        lines.append(f"Provided Published Date: {a.get('published_at', '')} (verify from content — correct if clearly wrong)")

        # Use scraped full text if available, else fall back to Tavily content/description
        scraped = a.get("_scraped_text", "")
        if scraped:
            lines.append(f"Full Article Text (scraped): {scraped}")
        else:
            fallback = (a.get("content") or a.get("description") or "")[:3000]
            lines.append(f"Content: {fallback}")
        lines.append("")
    return "\n".join(lines)


BATCH_SIZE = 20  # Reduced from 50 — articles are now larger with scraped text

# Canonical typology vocabulary — AI must pick from this list
CANONICAL_TYPOLOGIES = {
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
    "Pig butchering / romance investment scam",
    "Business Email Compromise (BEC)",
    "Ransomware proceeds",
    "Synthetic identity fraud",
    "Deepfake / AI-enabled fraud",
    "Environmental crime proceeds",
    "NFT / DeFi fraud",
    "Sanctions evasion",
    "Professional enablers",
    "Terrorist financing",
    "Drug trafficking proceeds",
    "Human trafficking proceeds",
    "Cybercrime proceeds",
    "AML compliance failure",
    "AML News",
}


def _normalise_typology(typology: str) -> str:
    """Snap AI-returned typology to closest canonical label."""
    if typology in CANONICAL_TYPOLOGIES:
        return typology
    lower = typology.lower()
    for canon in CANONICAL_TYPOLOGIES:
        if canon.lower() == lower:
            return canon
    keyword_map = [
        (["pig butcher", "sha zhu pan", "romance invest", "scam compound"],  "Pig butchering / romance investment scam"),
        (["business email compromise", "bec fraud", "ceo fraud", "vendor impersonat", "payroll diversion"], "Business Email Compromise (BEC)"),
        (["ransomware", "ransom demand", "ransom payment"],                  "Ransomware proceeds"),
        (["synthetic identity", "synthetic id", "credit washing"],           "Synthetic identity fraud"),
        (["deepfake", "ai-generated", "liveness bypass", "kyc bypass"],      "Deepfake / AI-enabled fraud"),
        (["environmental crime", "illegal logging", "wildlife traffic", "iuu fishing", "illegal mining"], "Environmental crime proceeds"),
        (["nft", "defi", "rug pull", "flash loan", "liquidity pool"],        "NFT / DeFi fraud"),
        (["mixing", "tumbl", "mixer", "tornado", "privacy coin", "monero"],  "Crypto mixing / tumbling"),
        (["darknet", "dark web"],                                            "Darknet-enabled laundering"),
        (["crypto", "blockchain", "virtual asset", "bitcoin", "ethereum"],   "Crypto-asset laundering"),
        (["structuring", "smurfing"],                                        "Structuring / Smurfing"),
        (["trade-based", "tbml", "invoice fraud", "over-invoic", "under-invoic", "phantom shipment"], "Trade-based money laundering (TBML)"),
        (["shell compan", "nominee", "beneficial owner"],                    "Shell companies and nominee ownership"),
        (["real estate", "property launder"],                                "Real estate laundering"),
        (["cash-intensive", "cash intensive", "cash business"],              "Cash-intensive business laundering"),
        (["offshore", "tax haven"],                                          "Offshore concealment"),
        (["money mule", "mule account"],                                     "Money mules"),
        (["hawala", "informal value", "ivts"],                               "Hawala and informal value transfer"),
        (["sanction evas", "sanctions evas", "sanction"],                    "Sanctions evasion"),
        (["professional enabler", "accountant", "lawyer", "notary"],         "Professional enablers"),
        (["terrorist financ", "terror financ"],                              "Terrorist financing"),
        (["drug trafficking", "narco", "cartel"],                            "Drug trafficking proceeds"),
        (["human trafficking", "modern slavery"],                            "Human trafficking proceeds"),
        (["cybercrime", "cyber fraud", "scam proceed"],                      "Cybercrime proceeds"),
        (["compliance fail", "aml fail", "control fail", "fine",
          "penalty", "enforcement action"],                                  "AML compliance failure"),
    ]
    for keywords, canon in keyword_map:
        if any(kw in lower for kw in keywords):
            print(f"[Analyze] Typology normalised: '{typology}' -> '{canon}'")
            return canon
    print(f"[Analyze] Unknown typology, defaulting to AML News: '{typology}'")
    return "AML News"


def _scrape_batch(articles: list[dict]) -> list[dict]:
    """Scrape full text for each article URL, attach as _scraped_text."""
    for a in articles:
        url = a.get("url", "")
        if not url:
            a["_scraped_text"] = ""
            continue
        text = _scrape_article(url)
        a["_scraped_text"] = text
        if text:
            print(f"  [Scrape] {len(text)} chars from {url[:60]}")
        else:
            print(f"  [Scrape] Failed/paywall: {url[:60]}")
    return articles


def _call_ai(client, articles: list[dict], current_date: str) -> list[dict]:
    """Send one batch of articles to OpenRouter. Returns structured list."""
    raw = ""
    try:
        user_prompt = _build_user_prompt(articles, current_date)
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=32000,
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, list):
            result = [result]
        for item in result:
            if "aml_typology" in item:
                item["aml_typology"] = _normalise_typology(item["aml_typology"])
        return result

    except json.JSONDecodeError as e:
        print(f"[Analyze] JSON parse error: {e}")
        print(f"[Analyze] Raw response: {raw[:500]}")
        return []
    except Exception as e:
        print(f"[Analyze] OpenRouter API error: {e}")
        return []


def analyze_articles(articles: list[dict]) -> list[dict]:
    """
    Scrape full article text, then send to Grok in batches for structured analysis.
    Returns a list of structured dicts ready for Supabase upload.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set in .env")
    if not articles:
        return []

    # Pre-filter: drop articles that don't contain at least one high-precision
    # AML phrase. This eliminates sports/politics/physical-crime noise before
    # scraping or AI calls, saving both time and API cost.
    pre_filtered = [a for a in articles if _passes_pre_filter(a)]
    dropped = len(articles) - len(pre_filtered)
    if dropped:
        print(f"[Analyze] Pre-filter dropped {dropped}/{len(articles)} articles (no AML signal phrases)")
    articles = pre_filtered

    if not articles:
        print("[Analyze] No articles passed pre-filter.")
        return []

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
    )

    current_date = datetime.now(timezone.utc).strftime("%d-%m-%Y")
    all_analyzed = []

    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i: i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"[Analyze] Batch {batch_num}/{total_batches} ({len(batch)} articles) — scraping...")

        # Step 1: scrape full text for each article
        batch = _scrape_batch(batch)

        # Step 2: send to Grok
        print(f"[Analyze] Sending batch {batch_num} to {OPENROUTER_MODEL}...")
        results = _call_ai(client, batch, current_date)
        all_analyzed.extend(results)

        # Clean up scraped text from batch dicts (not needed after analysis)
        for a in batch:
            a.pop("_scraped_text", None)

    print(f"[Analyze] {len(all_analyzed)} articles analyzed and structured")
    return all_analyzed


if __name__ == "__main__":
    sample = [
        {
            "title": "Secretary jailed seven years for siphoning R13m from law firm",
            "source": "Nova News",
            "url": "https://novanews.co.za/secretary-jailed-seven-years-for-siphoning-r13m-from-law-firm/",
            "published_at": "2026-03-11",
            "description": "A secretary was jailed for siphoning R13 million from a South African law firm.",
        }
    ]
    result = analyze_articles(sample)
    print(json.dumps(result, indent=2))
