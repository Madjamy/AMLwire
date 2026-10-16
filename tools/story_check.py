"""
Decide whether two articles report the same event, a new development of it, or
different events.

curate_articles finds likely pairs by rule (a shared bank, company or person
plus a matching amount or headline). Rules cannot tell a repeat from a follow-up:
"US fines Bank of America $1.2bn" and "Bank of America profit hit by $1.2bn fine"
are one story, while an arrest, charge and sentence in one case are three, even
though all quote the same people and amount. The model makes that call.

judge_pairs returns None when the model cannot be reached, and None for any pair
it did not answer; the caller then decides those pairs by rule.
"""

import json
import re

from openai import OpenAI

from tools.analyze_articles import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_MODEL_FALLBACK,
)

PAIRS_PER_CALL = 25
MAX_PAIRS = 300  # bound on cost; anything beyond is decided by rule
VERDICTS = {"same", "development", "different"}

SYSTEM_PROMPT = """You are the news desk editor of an anti-money-laundering news site. \
Each story must be published once. For each pair of articles, decide how B relates to A:

- "same": B reports the same underlying event as A. This includes rewrites, syndicated \
copies, explainers, analysis, reactions, market or earnings impact, and coverage that adds \
detail but reports no new event.
- "development": B reports a NEW event in the same matter that happened after A's event: \
a new enforcement action, fine, charge, arrest, indictment, plea, conviction, sentence, \
court ruling, appeal, settlement, designation, or official finding.
- "different": A and B are unrelated events that merely share a name or amount.

Reply with JSON only: a list of {"pair": <number>, "verdict": "same" | "development" | "different"}, \
one entry per pair."""


def _describe(article: dict) -> str:
    date = article.get("published_date") or str(article.get("published_at") or "")[:10]
    title = article.get("amlwire_title") or article.get("title") or ""
    entities = ", ".join(str(e) for e in (article.get("key_entities") or [])[:5])
    summary = re.sub(r"\s+", " ", article.get("summary") or "")[:350]
    amount = article.get("financial_amount") or "none"
    return f"[{date}] {title}\n   amount: {amount}; entities: {entities}\n   {summary}"


def _build_prompt(numbered: list[tuple[int, tuple[dict, dict]]]) -> str:
    blocks = []
    for n, (a, b) in numbered:
        # A is the earlier report where dates allow, so "development" reads forwards.
        first, second = (b, a) if _day(a) > _day(b) else (a, b)
        blocks.append(f"Pair {n}\nA: {_describe(first)}\nB: {_describe(second)}")
    return "\n\n".join(blocks)


def _day(article: dict) -> str:
    raw = str(article.get("published_date") or article.get("published_at") or "")
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", raw)  # analyzer's DD-MM-YYYY
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else raw[:10]


def _parse(raw: str) -> dict[int, str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return {}
    out = {}
    for item in json.loads(raw[start:end + 1]):
        try:
            verdict = str(item["verdict"]).strip().lower()
            if verdict in VERDICTS:
                out[int(item["pair"])] = verdict
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ask(client: OpenAI, prompt: str) -> dict[int, str] | None:
    """Verdicts from the first model that gives any; {} if all answered empty, None if all failed."""
    responded = False
    for model in dict.fromkeys([OPENROUTER_MODEL, OPENROUTER_MODEL_FALLBACK]):
        if not model:
            continue
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                # Reasoning models spend output tokens thinking; 4,000 left a
                # 40-pair batch with an empty answer.
                max_tokens=16000,
            )
            result = _parse(response.choices[0].message.content or "")
            responded = True
            if result:
                return result
            print(f"[StoryCheck] {model} returned no verdicts")
        except Exception as e:
            print(f"[StoryCheck] {model} failed: {e}")
    return {} if responded else None


def judge_pairs(pairs: list[tuple[dict, dict]]) -> list[str | None] | None:
    """Verdict per pair ("same" / "development" / "different" / None), or None if unavailable."""
    if not pairs:
        return []
    if not OPENROUTER_API_KEY:
        return None
    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, timeout=120)
    verdicts: dict[int, str] = {}
    answered_any = False
    numbered = list(enumerate(pairs[:MAX_PAIRS]))
    for start in range(0, len(numbered), PAIRS_PER_CALL):
        chunk = numbered[start:start + PAIRS_PER_CALL]
        for _attempt in range(2):  # second pass asks again for any pair it skipped
            result = _ask(client, _build_prompt(chunk))
            if result is None:
                break
            answered_any = True
            verdicts.update({n: v for n, v in result.items() if n in dict(chunk)})
            chunk = [(n, p) for n, p in chunk if n not in verdicts]
            if not chunk:
                break
    if not answered_any:
        return None
    counts = {v: sum(1 for x in verdicts.values() if x == v) for v in sorted(VERDICTS)}
    print(f"[StoryCheck] {len(pairs)} candidate pairs: {counts}, "
          f"{len(pairs) - len(verdicts)} left to rule")
    return [verdicts.get(k) for k in range(len(pairs))]
