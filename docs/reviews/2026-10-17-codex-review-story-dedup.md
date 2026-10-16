The pair bookkeeping looks sound. The main risks are hiding genuine developments and losing source links.

1. **P1 — Fallback can silently merge genuine developments.**  
   [_fallback_same](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:601) accepts a matching amount ≥USD 1m without requiring headline similarity. I reproduced “fresh fraud charges against John Smith” and “John Smith receives prison sentence,” both quoting USD 20m, becoming one story. This applies during outages, unanswered pairs, and beyond the 300-pair limit. The two-shared-entities branch also accepts developments. Unresolved candidates need a more conservative fallback.

2. **P1 — High headline overlap bypasses development detection entirely.**  
   [_story_link](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:590) classifies these as `SAME`, without consulting the LLM:
   
   - “John Smith **charged** in $20 million money laundering scheme”
   - “John Smith **convicted** in $20 million money laundering scheme”
   
   Their token overlap is exactly 0.75; I reproduced one resulting group. **This threshold is inherited**, but it defeats the new follow-up protection. Earlier title dedup can also discard such developments before curation.

3. **P2 — The published-row query contradicts the activity-based window.**  
   [_load_recently_published](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:892) still filters by original `published_at`. Example: primary published October 1, another source dated October 14, new report October 16. `_within_window` correctly accepts this pair, but the primary is excluded from the October 17 query. The repeat can therefore publish separately. Retrieval must account for recent source activity.

4. **P2 — The 20-source cap can itself cause republication.**  
   [_merge_sources](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:785) retains existing entries first, sorts by authority, then truncates. I reproduced:
   
   - Twenty retained sources end on October 10.
   - October 14 coverage matches, but its URL is discarded by the cap.
   - Stored last activity remains October 10.
   - October 15 coverage falls outside the four-day window and can publish again.
   
   A display limit must not discard the activity information used for deduplication.

5. **P2 — Earlier filters discard reports instead of preserving their source links.**  
   [main.py](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/main.py:293) discards matching title prefixes; [deduplicate.py](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/deduplicate.py:190) discards similar titles, including against stored articles. Two outlets carrying the same headline therefore produce one article **without the second source link**. These existing filters need to carry rejected reports forward as sources for the new feature to satisfy the requirement.

6. **P2 — Applying a stale cleanup plan overwrites newer sources and can lower scores.**  
   [apply](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/merge_duplicate_stories.py:89) checks existence and `merged_into`, but writes the planned source list and score verbatim. I reproduced: plan records score 80; daily curation adds source C and raises the primary to 90; apply removes C and restores 80. Reject changed rows or preserve their current sources and maximum score. Also, descendants repointed at line 113 are absent from the backup, so it does not cover every changed row.

7. **P2 — Failed attachment writes still discard candidates and report success.**  
   [_attach_to_published](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:947) removes matches before writing. An update exception is logged, but those reports remain removed and count as attached. I reproduced two reports returning `kept=[]`, attached count `2`, despite the write failing. Their source links are lost unless rediscovered later. Preserve a retry path and count only successful attachments.

8. **P2 — URL normalization removes article identity parameters.**  
   [_url_key](/private/tmp/claude-501/-Users-madhursharma/bc9be326-d449-4d02-b440-52450856ec97/scratchpad/dedup/aml/tools/curate_articles.py:755) treats `article?id=1` and `article?id=2` as identical. Likewise, all YouTube `watch?v=…` links collide. If two such reports belong to one story, one source disappears; if the primary shares that path, the alternate disappears immediately. Preserve identity parameters when removing tracking parameters.

**Deployment gap:** No migration defining `other_sources` or `merged_into` appears in the supplied patch or trees. Unless applied separately, this is **P1**: list queries fail on `merged_into`, and uploads fail on `other_sources`. I could not verify the stated migration itself.

The following parts look fine:

- **Grouping and attachment indices:** candidate/verdict alignment is correct; anchor-only grouping prevents chaining; multiple incoming matches for one stored row produce one combined update.
- **LLM batching:** global pair numbering, partial-answer retries, fenced JSON parsing, model fallback, and output padding are consistent. Mocked checks passed across 305 pairs. The unsafe rule fallback above is the issue.
- **Primary selection:** the ten-point eligibility rule, authority ranking, listing-page demotion, and score/tier promotion work as described. The focused archive replay folded **12 dated BofA penalty reports into one article with 11 source links**, selecting Al Jazeera over FinCEN’s listing page.
- **Supabase syntax:** Python [`.is_(…, "null")`](https://supabase.com/docs/reference/python/is) and [`.in_(…, values)`](https://supabase.com/docs/reference/python/in), and JavaScript [`.is(…, null)`](https://supabase.com/docs/reference/javascript/is), are correct.
- **Edge function:** merged-row filtering is correctly placed; one-hop ID resolution matches the cleanup tool’s chain-flattening behavior. The new identity token affects diversity ordering, not permanent visibility.
- **React and logging:** no apparent new TypeScript error; imports and source types are consistent. Cron and watchdog paths match the mounted logs volume.

Python 3.11 syntax checks passed for all six changed Python modules. TypeScript compilation was unavailable because Node/npm and frontend dependencies are absent; no live database or paid model calls were made.
---

## Maker response (Claude, 2026-10-17)

1. **Fallback merges developments (P1): partly accepted.** In `_fallback_same`, a matching amount with unrelated headlines now merges only when the two reports are ≤2 days apart. The two-shared-entities rule predates this change and stays, since it only applies when the model is unavailable. The "fresh charges" / "prison sentence" example, 3 days apart, now stays separate. The BofA copies, 1 day apart, still merge.
2. **≥0.75 headline overlap bypasses the model (P1): accepted.** Only overlap ≥0.9 is SAME without a check. 0.75–0.9 is a CANDIDATE judged by story_check, with the old 0.75 rule as its fallback. The charged/convicted example now goes to the model.
3. **Lookup by first publication vs activity window (P2): accepted.** The lookback is now 30 days.
4. **Source cap drops the newest report (P2): accepted.** `_merge_sources` always keeps the newest-dated source when it truncates.
5. **Earlier filters drop same-headline copies without links (P2): partly accepted.** Copies dropped in the same run are now carried forward as sources (`fold_report` from main.py step 12 and deduplicate's in-batch title check). Rejected for copies whose headline matches an article stored on an earlier day: those are mostly syndicated reposts, and different-headline reports from other outlets are already attached after analysis.
6. **Stale plan overwrites newer sources or lowers scores; backup misses repointed rows (P2): accepted.** Apply re-merges the planned sources with the row as it is now and keeps the higher score. Rows repointed from a copy are backed up too.
7. **Failed attach write discards reports and reports success (P2): partly accepted.** Only successful writes are counted, and failures are logged as ERROR. Matched reports are still not published, because publishing them is the duplicate this change exists to stop. The source URL isn't stored on failure, so the next run re-finds the report and attaches it.
8. **URL key drops identity query parameters (P2): accepted.** Only tracking parameters (utm_*, fbclid, gclid, syn-*, …) are stripped.
- **Migration not in the patch: accepted.** Added as `supabase/migrations/20261017000000_story_sources.sql`.

Re-verified with: unit checks for each example above; end-to-end curation of the 15 and 16 Oct batches against a fake DB (0 BofA articles published, 5 and 3 reports attached as sources); a full 3-week replay with the model (12 BofA reports → 1 article + 11 sources); site `tsc --noEmit` clean and `vite build` passes.
