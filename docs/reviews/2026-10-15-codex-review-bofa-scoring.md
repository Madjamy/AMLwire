I reproduced the following issues, ranked by impact. Examples are synthetic; scoring examples use otherwise identical articles worth 72 before the new bonus.

1. **High — Preserving bank names enables false merges of separate events.**  
   [_norm_entity / _same_story](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/tools/curate_articles.py:511)

   These two articles, dated 13 and 14 October, with `key_entities=["Bank of America"]`, identical typology, and `financial_amount="USD 50 million"`:

   - “OCC fines Bank of America for sanctions screening failures”
   - “FCA fines Bank of America for transaction monitoring failures”

   produce `_same_story=True`; previously `False`. Title overlap is 0.40, exceeding the single-entity threshold. One separate action disappears. Additionally, two stories sharing **Bank of America plus JPMorgan** now satisfy the two-entity rule regardless of different amounts or unrelated headlines. The normalization fix is sensible, but it exposes these existing event-matching weaknesses.

2. **High — The bonus sizes the largest amount, not necessarily the penalty.**  
   [Bonus gate](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/tools/curate_articles.py:364)

   Title `"Bank fined for AML failures"` with:

   `financial_amount="USD 2 billion laundered; USD 5 million fine"`  
   → `_amount_usd=2,000,000,000` → **92 Critical**, although the actual fine warrants no size bonus: **72 Elevated**.

   The keyword gate also admits non-penalty language: `"Regulator orders bank to assess suspicious transfers"` plus `"USD 2 billion"` → **92 Critical**, because `assess` matches `_PENALTY_WORDS`. Restricting publication type does not establish that the amount is a penalty.

3. **Medium — Currency parsing can substantially overvalue amounts.**  
   [Currency regex](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/tools/curate_articles.py:271)

   - `"HKD $1.2 billion"` → **USD 1.2bn**, whereas `"HK$1.2 billion"` → **USD 153.6m**. The former receives +20 instead of +6: **92 Critical versus 78 High**.
   - `"AUD $1 billion"` → USD 1bn instead of USD 650m.
   - `"MX$1 billion"` → USD 1bn, despite the comment promising unsupported currencies get no bonus.

   The standalone `$` alternative matches after an unconsumed currency qualifier.

   Separately, blindly deleting commas makes `"EUR 0,1 billion"` → **USD 1.08bn**, versus USD 108m for `"EUR 0.1 billion"`—again **92 versus 78**. Ambiguous numeric formatting should not silently inflate the value.

4. **Medium — The stale exemption exposes the existing fail-open date parser.**  
   [Exemption](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/main.py:380), [date fallback](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/main.py:398)

   At 15 October 2026:

   `publication_type="enforcement_action"`, summary mentioning only a 2023 penalty, `published_date="2023-10-13T00:00:00Z"`  
   → **kept**. The equivalent `"13-10-2023"` → **dropped**.

   This requires the fetched metadata to pass the initial date gate—for example, an old page carrying a current feed date. The analyzer does not enforce its requested date format. Previously the content-year check rejected this example; now unsupported nonempty dates bypass both checks. Rejecting unparseable dates would close this bounded hole.

5. **Medium — Common amount spellings miss the intended promotion.**  
   [Suffix regex](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/tools/curate_articles.py:280)

   Both `"1.21 billion USD"` and `"1.21bn dollars"` → **0**, leaving a qualifying penalty at **72 Elevated**. Suffix currency codes are unsupported, and suffix multipliers omit abbreviations supported by the prefix parser.

6. **Low — Plural “settlements” fails the penalty-word regex.**  
   [Penalty regex](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/aml/tools/curate_articles.py:286)

   Title `"Bank resolves AML breaches"` plus `"USD 1.21 billion in settlements"` → **72 Elevated**. Singular `"settlement"` → **92 Critical**. The terminal word boundary prevents matching the plural.

What is fine: `_GENERIC_ENTITIES` is defined **before** `_norm_entity`; there is no ordering issue. `"Bank of America"` remains distinct from generic `"America"`. The regexes compile, capture-group indexing is correct, and `"USD 1.21bn"` parses correctly. Exempting historical case references is reasonable when the publication date is valid; supported old dates still get rejected.
---

## Maker response (Claude, 2026-10-15)

1. **False merges from preserving bank names (High): rejected.** The two-entity and single-entity-plus-amount rules already apply to every other bank name (HSBC, JPMorgan, Danske), which never went through the "bank of" stripping. The patch only stops "Bank of America" / "Bank of England" etc. collapsing to a generic place name. Tightening `_same_story` across the board is a separate change, out of scope for this fix.
2. **Bonus sizes largest amount, not the penalty (High): accepted.** New `_penalty_usd`: when financial_amount names a penalty, only that segment is sized; otherwise the amount is sized only if the headline says fine/penalty/settlement/forfeit. Dropped `assess*` from the penalty words. "USD 2 billion laundered; USD 5 million fine" now scores 72, and the "assess" headline also scores 72.
3. **Currency over-valuation (Medium): partly accepted.** A bare symbol after a 3-letter code uses that code ("HKD $1.2 billion" → USD 153.6m, "AUD $1 billion" → USD 650m). A symbol glued to letters ("MX$") now gets no bonus. Rejected the comma-decimal case ("EUR 0,1 billion"): the analyzer prompt asks for dot decimals, and no such value appears in the 995 amounts since 1 Sep.
4. **Stale exemption and fail-open date parser (Medium): accepted.** The AI date gate now parses `date_str[:10]`, so ISO timestamps are dated rather than waved through.
5. **Suffix spellings (Medium): accepted.** The suffix parser takes usd/eur/gbp/inr and the bn/mn/tn abbreviations.
6. **Plural "settlements" (Low): accepted.**

Verified by test cases for each example and a replay of 1,696 articles (1 Sep–15 Oct). Tier changes: 6 Critical (4 BofA, 1MDB), 6 Elevated→High (Amex ×3, Star, COVID forfeitures). BofA copies on 14 Oct collapse from 4 to 2.
