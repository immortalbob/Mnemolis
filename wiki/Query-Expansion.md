# Query Expansion

`web` search gets one extra trick `news` doesn't: for queries with enough words to benefit from it, Mnemolis asks the [LLM](LLM-Client) for a genuinely differently-worded version of the same question, searches SearXNG with *both* phrasings, and merges the raw result pools before scoring decides what's actually relevant. The idea is straightforward — SearXNG's own ranking depends heavily on the exact words you used, and a real, equally-valid phrasing can surface results the first wording missed entirely.

## When it actually triggers

Only for queries with **3 or more words** (`_MIN_WORDS`). Shorter queries don't have enough room for a genuinely different phrasing to exist — there's only so many ways to rephrase a two-word query before you're just repeating it with different filler.

## The alternate phrasing has to pass real sanity checks

The LLM's response isn't trusted blindly. Three checks run before an alternate phrasing is used at all:

1. **Not empty** — a blank or failed LLM response is discarded, not treated as "no alternate available" silently
2. **Not absurdly longer than the original** — if the rephrasing comes back more than twice the original's word count, it's discarded as unreliable rather than searched
3. **Not identical to the original** — if the "rephrasing" is just the same query back, there's no point searching it again

Any failure here means query expansion simply doesn't happen for that query — the primary search still runs and returns normally, expansion is a pure bonus, never a requirement.

A successfully-generated alternate phrasing is cached in the routing cache (`altquery:{query}`), the same way other LLM-backed routing decisions are — see [Caching](Caching) — so a repeated query doesn't pay the LLM cost twice within the cache's TTL.

## How the two searches get merged

```text
   Original query (3+ words)
                  │
                  ▼
   Fetch SearXNG with the ORIGINAL query
                  │
                  ▼
   Ask LLM for an alternate phrasing
                  │
          ┌───────┴───────┐
          ▼ none/invalid    ▼ valid
   Use only the          Fetch SearXNG with the
   original results      ALTERNATE phrasing too
          │                     │
          │                     ▼
          │           Merge both raw result pools,
          │           dedupe by normalized URL
          │                     │
          └──────────┬──────────┘
                      ▼
        Score EVERY result — original-search
        results AND alternate-search results
        alike — against the ORIGINAL query only
                      │
                      ▼
              Filter & rank as usual
```

The merge step deduplicates using the same [normalized URL comparison](Confidence-Aware-Fusion#deduplication-across-url-variants) used elsewhere, so a result that happens to surface in both searches doesn't get counted twice.

## Why scoring always uses the original query, never the alternate

This is the detail that makes the whole feature trustworthy rather than just noisy. Every result from *both* searches gets scored against the query you actually typed — never against the LLM's rephrasing. A result only survives into the final response because it's genuinely relevant to what was actually asked, not because it happened to match the wording of an LLM-generated alternate phrasing. The alternate phrasing's only job is to surface a wider net of *raw candidates*; it has zero influence over which of those candidates is judged relevant.

If a failed alternate fetch happens (network issue, SearXNG hiccup), it's non-fatal — the primary search's results still stand on their own and the response proceeds normally. Query expansion is additive, never a point of fragility for the base case.

## The primary fetch and alternate-phrasing chain run concurrently

When expansion fires, `search()` needs the primary SearXNG fetch, a full LLM completion call (`get_alternate_phrasing()`), and a *second* SearXNG fetch for the alternate phrasing. These run concurrently via a small thread pool rather than one after another — but they didn't always. A real latency cost from running them sequentially (roughly 4x the cost of a single fetch) was found via a live Adversarial Self-Testing flag, and fixing it surfaced a genuine, separate file-write race in how the routing cache persists to disk. See [The Latency Parallelization Investigation](The-Latency-Parallelization-Investigation#the-web-case-was-fixed-first) for the full story.

The routing cache still softens a repeat the same way as before: a cache hit skips the LLM completion call entirely, since only the alternate phrasing *text* is cached, not its search results — `search()` still makes the second SearXNG fetch every time expansion fires, cache hit or not. A repeated query within the cache's TTL still needs two real fetches, just run concurrently with the (now-skipped, on a hit) LLM call rather than after it.

## Why `news` doesn't have this

`news` searches your own RSS feeds, which are a small, fixed, already-curated set of sources — there's no equivalent to "SearXNG's ranking might miss something with different wording," because there's no external search ranking involved at all. The relevant scoring problem for `news` is "which of my existing articles is actually about this," which [Confidence-Aware Fusion](Confidence-Aware-Fusion) already handles directly; there's nothing a second, differently-worded search would surface that a single pass over your own feed wouldn't already see.
