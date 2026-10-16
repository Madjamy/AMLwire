// get-articles — public read API for amlwire.com.
//
// Runs inside the AMLWire Supabase project (ykfkbuuwfqmjkogwbkjp), the project
// that stores the articles. It previously ran in the Lovable-managed frontend
// project and proxied here through EXTERNAL_SUPABASE_* secrets. Reads use the
// anon key, so the "Public read articles" RLS policy is the access boundary.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.99.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const JSON_HEADERS = {
  ...corsHeaders,
  "Content-Type": "application/json",
  "Cache-Control": "no-cache, no-store, must-revalidate",
};

const PAGE = 1000;       // PostgREST returns at most 1,000 rows per request
const MAX_LIMIT = 100;   // largest page any screen requests is 22
const DAY_MS = 86_400_000;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

function intParam(value: string | null, fallback: number, min: number, max: number): number {
  const n = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(n, min), max);
}

// The pipeline never normalised `region`, so a few rows hold variants the site
// would otherwise list as separate regions: "Asia‑Pacific" spelled with a
// non-breaking hyphen (U+2011), "Africa", and one "Australia". Canonicalise on
// read so filters and cards stay clean regardless of what is stored.
const REGION_ALIASES: Record<string, string> = {
  "africa": "Middle East & Africa",
  "australia": "Asia-Pacific",
};

function canonicalRegion(raw: string | null): string | null {
  if (raw == null) return raw;
  const plain = raw.replace(/[\u2010-\u2015\u2212]/g, "-").replace(/\s+/g, " ").trim();
  return REGION_ALIASES[plain.toLowerCase()] ?? plain;
}

// Every stored spelling that canonicalises to `region`, for filtering.
function regionVariants(region: string): string[] {
  const canonical = canonicalRegion(region) ?? region;
  const variants = new Set([canonical, canonical.replace(/-/g, "\u2011")]);
  for (const [alias, target] of Object.entries(REGION_ALIASES)) {
    if (target === canonical) variants.add(alias);
  }
  return [...variants];
}

const utcDay = (iso: string) => iso.slice(0, 10);

// ── Diversity for the homepage showcase (hero, ticker, critical section) ─────
// One event often arrives as many articles the pipeline cannot safely merge:
// on 15 Sep 2026 four of the five hero slots were the same OFAC action against
// Xinbi Guarantee, each copy carrying a different typology and figure. Merging
// such copies in the pipeline would delete data on a ~1-in-5 false-match rate,
// so the showcase instead skips an article that names the same organisation or
// person as one already chosen. A false match here only means a slot shows a
// different story — nothing is removed.
const SHARED_WORDS = new Set((
  "bank banks exchange group network company technology marketplace market guarantee " +
  "holdings international global capital limited trust fund services crypto digital " +
  "asset assets finance financial national federal united police court office operation " +
  "cartel department treasury ministry agency authority commission bureau directorate " +
  "prosecutor justice government central reserve customs securities investment state " +
  "states kingdom republic union european america american chinese russian iranian " +
  "fincen austrac ofac europol interpol secret service"
).split(" "));

function identityTokens(entities: unknown): Set<string> {
  const out = new Set<string>();
  if (!Array.isArray(entities)) return out;
  for (const e of entities) {
    const name = String(e).toLowerCase();
    for (const t of name.match(/[a-z0-9]{4,}/g) ?? []) {
      if (!SHARED_WORDS.has(t) && !/^\d+$/.test(t)) out.add(t);
    }
    // "Bank of America" is made only of shared words, so it produced no token
    // and five copies of its Oct 2026 penalty all passed as different stories.
    // Keep only the place, so "Bank of America Merrill Lynch" matches too.
    const bankOf = name.match(/\bbank of (?:the )?(new [a-z]+|[a-z]+)/);
    // Central banks are regulators that recur across unrelated stories.
    if (bankOf && !/\b(central|reserve) bank of/.test(name)) out.add(`bank of ${bankOf[1]}`);
  }
  return out;
}

function pickDiverse<T extends { key_entities?: unknown }>(candidates: T[], limit: number): T[] {
  const chosen: T[] = [];
  const skipped: T[] = [];
  const used = new Set<string>();
  for (const c of candidates) {
    if (chosen.length >= limit) break;
    const tokens = identityTokens(c.key_entities);
    if ([...tokens].some((t) => used.has(t))) {
      skipped.push(c);
      continue;
    }
    chosen.push(c);
    tokens.forEach((t) => used.add(t));
  }
  // Never return fewer rows than asked for just to stay diverse.
  for (const s of skipped) {
    if (chosen.length >= limit) break;
    chosen.push(s);
  }
  return chosen;
}

// Search terms are interpolated into a PostgREST or() filter, where commas,
// parentheses and wildcards are syntax. Left unescaped, a query such as
// "Binance, OFAC" produced a 500 instead of results.
function safeSearch(term: string): string {
  return term.replace(/[,()%*\\]/g, " ").replace(/\s+/g, " ").trim();
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const url = Deno.env.get("SUPABASE_URL");
    const key = Deno.env.get("SUPABASE_ANON_KEY") ?? Deno.env.get("SUPABASE_PUBLISHABLE_KEY");
    if (!url || !key) throw new Error("Supabase environment not available");
    const db = createClient(url, key);

    const params = new URL(req.url).searchParams;
    const id = params.get("id");
    const category = params.get("category");
    const region = params.get("region");
    const country = params.get("country");
    const search = params.get("search");
    const typology = params.get("typology");
    const tag = params.get("tag");
    const tier = params.get("tier");
    // published_at | quality_score | created_at | surfaced
    const sort = params.get("sort") || "published_at";
    const limit = intParam(params.get("limit"), 50, 1, MAX_LIMIT);
    const offset = intParam(params.get("offset"), 0, 0, 1_000_000);
    const distinct = params.get("distinct"); // regions | typologies
    // Publication-date range, inclusive, as YYYY-MM-DD.
    const from = params.get("from");
    const to = params.get("to");
    // Restrict to articles AMLwire added in the last N days. Used by the homepage
    // hero, ticker and critical section, which ranked by score across the whole
    // archive and so showed the same June–August articles indefinitely.
    const recentDays = params.get("recent_days")
      ? intParam(params.get("recent_days"), 0, 1, 365)
      : 0;
    const diverse = params.get("diverse") === "1";

    if (id) {
      let { data, error } = await db.from("articles").select("*").eq("id", id).maybeSingle();
      if (error) throw error;
      // A copy folded into another article as one of its sources: links to it,
      // already shared, open the article it was merged into.
      if (data?.merged_into) {
        ({ data, error } = await db.from("articles").select("*").eq("id", data.merged_into).maybeSingle());
        if (error) throw error;
      }
      if (!data) return json({ error: "Article not found" }, 404);
      return json({ ...data, region: canonicalRegion(data.region) });
    }

    // Filter options. Both previously read every row in one request, which
    // PostgREST caps at 1,000 — so region and typology counts on the site were
    // computed from 1,000 of ~3,600 articles. Page through all rows instead.
    if (distinct === "regions" || distinct === "typologies") {
      const columns = distinct === "regions" ? "region, country" : "aml_typology";
      const notNull = distinct === "regions" ? "region" : "aml_typology";
      const rows: Record<string, string | null>[] = [];
      for (let start = 0; ; start += PAGE) {
        const { data, error } = await db
          .from("articles")
          .select(columns)
          .not(notNull, "is", null)
          .is("merged_into", null)
          .order("id")
          .range(start, start + PAGE - 1);
        if (error) throw error;
        rows.push(...((data ?? []) as Record<string, string | null>[]));
        if (!data || data.length < PAGE) break;
      }

      if (distinct === "regions") {
        const countries: Record<string, Set<string>> = {};
        const counts: Record<string, number> = {};
        for (const row of rows) {
          const r = canonicalRegion(row.region) as string;
          (countries[r] ??= new Set()).add(row.country ?? "");
          counts[r] = (counts[r] ?? 0) + 1;
        }
        return json(
          Object.entries(countries).map(([region, set]) => ({
            region,
            countries: Array.from(set).filter(Boolean).sort(),
            count: counts[region],
          })),
        );
      }

      const counts: Record<string, number> = {};
      for (const row of rows) {
        const t = row.aml_typology as string;
        counts[t] = (counts[t] ?? 0) + 1;
      }
      return json(
        Object.entries(counts)
          .map(([name, count]) => ({ name, count }))
          .sort((a, b) => b.count - a.count),
      );
    }

    // Build a filtered, ordered list query. `windowed` controls whether the
    // recent_days restriction applies, so the same filters can be re-run wider.
    const buildQuery = (windowed: boolean) => {
      // Merged copies live on only as other_sources of the article they joined.
      let q = db.from("articles").select("*").is("merged_into", null);

      if (sort === "quality_score") {
        q = q.order("quality_score", { ascending: false, nullsFirst: false })
          .order("published_at", { ascending: false });
      } else if (sort === "created_at" || sort === "surfaced") {
        q = q.order("created_at", { ascending: false })
          .order("quality_score", { ascending: false, nullsFirst: false });
      } else {
        q = q.order("published_at", { ascending: false })
          .order("quality_score", { ascending: false, nullsFirst: false });
      }

      if (category && category !== "all") q = q.eq("category", category);
      if (region) {
        const clauses = regionVariants(region.trim()).map((v) => `region.ilike."${v}"`);
        q = q.or(clauses.join(","));
      }
      if (country) q = q.ilike("country", country.trim());
      if (typology) q = q.ilike("aml_typology", typology.trim());
      if (tag) q = q.contains("tags", [tag]);
      if (tier) q = q.eq("quality_tier", tier);
      if (search) {
        const term = safeSearch(search);
        if (term) {
          q = q.or(`title.ilike.%${term}%,amlwire_title.ilike.%${term}%,summary.ilike.%${term}%`);
        }
      }
      if (from && ISO_DAY.test(from)) q = q.gte("published_at", `${from}T00:00:00Z`);
      if (to && ISO_DAY.test(to)) {
        const end = new Date(`${to}T00:00:00Z`).getTime() + DAY_MS; // inclusive of that day
        q = q.lt("published_at", new Date(end).toISOString());
      }
      if (windowed && recentDays > 0) {
        q = q.gte("created_at", new Date(Date.now() - recentDays * DAY_MS).toISOString());
      }
      return q;
    };

    type Row = {
      id: string;
      created_at: string;
      quality_score: number | null;
      region: string | null;
      key_entities?: unknown;
    };
    const withRegions = (list: Row[]) => list.map((r) => ({ ...r, region: canonicalRegion(r.region) }));

    // "surfaced": newest pipeline run first, highest score first within a run.
    // created_at alone is wrong for this: the pipeline inserts each run in
    // descending score order, so the most recently inserted row of a run is its
    // LOWEST scorer and the feed would open on Watch-tier articles. PostgREST
    // can't order by an expression, so order by created_at, fetch through the
    // end of the day that contains the requested page, then re-rank. Reading to
    // the day boundary keeps pages consistent: no article repeats or goes missing
    // between page N and page N+1.
    if (sort === "surfaced") {
      const needed = offset + limit;
      const CHUNK = 200;
      const collected: Row[] = [];
      for (let start = 0; ; start += CHUNK) {
        const { data, error } = await buildQuery(true).range(start, start + CHUNK - 1);
        if (error) throw error;
        const batch = (data ?? []) as Row[];
        collected.push(...batch);
        const exhausted = batch.length < CHUNK;
        if (exhausted) break;
        if (collected.length >= needed) {
          const boundary = utcDay(collected[needed - 1].created_at);
          if (utcDay(collected[collected.length - 1].created_at) !== boundary) break;
        }
      }
      collected.sort((a, b) =>
        utcDay(b.created_at).localeCompare(utcDay(a.created_at)) ||
        (b.quality_score ?? -1) - (a.quality_score ?? -1) ||
        a.created_at.localeCompare(b.created_at)
      );

      // Within each day, move repeat coverage of a story below that day's other
      // stories. Ranking purely by score put five near-identical Xinbi Guarantee
      // articles in the top nine cards. Nothing is dropped, so every article
      // stays reachable, and each day is re-ranked only from its complete set of
      // rows, so pagination stays consistent.
      const ranked: Row[] = [];
      for (let i = 0; i < collected.length; ) {
        const day = utcDay(collected[i].created_at);
        let j = i;
        while (j < collected.length && utcDay(collected[j].created_at) === day) j++;
        const firsts: Row[] = [];
        const repeats: Row[] = [];
        const used = new Set<string>();
        for (const row of collected.slice(i, j)) {
          const tokens = identityTokens(row.key_entities);
          if ([...tokens].some((t) => used.has(t))) {
            repeats.push(row);
          } else {
            firsts.push(row);
            tokens.forEach((t) => used.add(t));
          }
        }
        ranked.push(...firsts, ...repeats);
        i = j;
      }
      return json(withRegions(ranked.slice(offset, needed)));
    }

    // Diverse selection over-fetches so there is room to skip repeats.
    const fetchLimit = diverse && offset === 0 ? Math.min(limit * 5, MAX_LIMIT * 2) : limit;
    const { data, error } = await buildQuery(true).range(offset, offset + fetchLimit - 1);
    if (error) throw error;
    let rows = (data ?? []) as Row[];

    // If the recent window is thin (e.g. the pipeline missed a few nights), top
    // up from the full archive in the same order, rather than showing an empty
    // hero or ticker.
    if (recentDays > 0 && offset === 0 && rows.length < fetchLimit) {
      const seen = new Set(rows.map((r) => r.id));
      const { data: wider, error: widerError } = await buildQuery(false).range(0, fetchLimit + rows.length);
      if (widerError) throw widerError;
      for (const r of (wider ?? []) as Row[]) {
        if (rows.length >= fetchLimit) break;
        if (!seen.has(r.id)) rows = [...rows, r];
      }
    }

    if (diverse && offset === 0) {
      rows = pickDiverse(rows, limit);
    }

    return json(withRegions(rows.slice(0, limit)));
  } catch (error: unknown) {
    const msg = error instanceof Error
      ? error.message
      : (error as { message?: string })?.message ?? "Unknown error";
    console.error("get-articles error:", msg);
    return json({ error: msg }, 500);
  }
});
