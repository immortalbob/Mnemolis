# Caching

Mnemolis has two genuinely different caches, and conflating them is a common source of confusion. The **result cache** stores actual search results so a repeated query skips the real backend call entirely. The **routing cache** stores the *decision* about which source(s) a query should go to, separately from the result itself — so a repeated query still skips the LLM call even if the underlying result has since expired or changed.

## Result cache

Keyed on `source:query` (lowercased, stripped). Every source has its own TTL, because "how stale is acceptable" is genuinely different per source — each is independently configurable (`CACHE_TTL_KIWIX_SECONDS`, `CACHE_TTL_FORECAST_SECONDS`, and so on, one per source; see [Configuration Reference](Configuration-Reference#caching)), but the defaults reflect deliberate, real reasoning about each source's own freshness needs:

| Source | TTL | Why |
|--------|-----|-----|
| `kiwix` | 24 hours | Offline encyclopedic content essentially never changes within a day |
| `forecast` | 30 minutes | A 3-day outlook doesn't need to be fresher than this |
| `news` | 15 minutes | Your RSS feeds update on their own schedule; this is just an upper bound |
| `web` | 1 hour | Live search results are reasonably stable short-term |
| `uptime` | 1 minute | Service status is the kind of thing you want close to real-time |
| `ha` | 30 seconds | Lights and locks change state constantly — this is intentionally the shortest TTL of any source |
| `changes` | 2 minutes | "What changed" needs to stay close to real-time too |
| `fusion` | 30 minutes | A reasonable middle ground for merged multi-source results |

On a cache hit, none of the underlying sources get touched at all — no network call, no LLM call, nothing. This is why [Benchmarks](Benchmarks) consistently shows warm-cache latency in the tens of milliseconds even for queries whose cold-cache cost was several seconds.

**Disk persistence is batched** — writes accumulate in memory and only get flushed to `cache.json` every 5 writes (`_CACHE_SAVE_INTERVAL`), not on every single cache write. This matters for anyone reading the code expecting every cache update to be immediately durable; a handful of very recent entries could theoretically be lost if the process died between batched saves, which is an acceptable tradeoff for a cache (the source data it's caching is still right there, ready to be re-fetched) but worth knowing.

**Bounded at `CACHE_MAX_SIZE`** (default 500). When a *new* key would push the cache over this limit, the single oldest entry (by write timestamp) gets evicted first. Updating an already-cached key never counts as "new" for this purpose, so re-caching something you already have doesn't trigger an eviction.

## Routing cache

Keyed the same way (lowercased, stripped query — though the routing cache key doesn't include a source prefix, since the whole point is recording *which* source was chosen). A single flat TTL applies to every routing decision regardless of source (`ROUTING_CACHE_TTL_SECONDS`, default 1 hour) — a query's correct source assignment doesn't really depend on the kind of freshness concerns the result cache has to account for per-source. This was hardcoded for most of this project's life, the same way the per-source result cache TTLs above were — found and made configurable during the same audit that found those.

This is a genuinely larger key space than it might first appear. Every unique conditional query, every discourse-framing phrase, every Kiwix disambiguation candidate set gets its own routing cache entry on top of plain single-source routing decisions — which is exactly why this cache, unlike the result cache, had **no size limit at all** for most of this project's life. That gap was found during a deliberate operational-maturity review, not a reported failure, and fixed by adding the exact same bounded-eviction pattern the result cache already had (`ROUTING_CACHE_MAX_SIZE`, default 1000).

**Disk persistence is immediate** — unlike the result cache's batched saves, the routing cache writes to `routing_cache.json` on every single call to `_set_routing()`. This asymmetry is real and intentional (routing decisions are written far less often than results are read/written during heavy query traffic), but it's also why testing the routing cache's eviction logic specifically mocks out the disk-write step — looping it to its default max size of 1000 in a test would otherwise mean 1000 real disk writes to test logic that has nothing to do with disk I/O at all.

A defensive cap also applies when loading the routing cache back from disk at startup — a file saved before the size limit existed could theoretically still be over it, so loading trims to the most recently-written entries if so, rather than silently allowing an over-limit cache to persist indefinitely across restarts.

**A bad LLM response used to get "stuck."** If routing or Kiwix disambiguation ever picked an obviously-wrong generic fallback for a query, repeating the exact same query used to keep giving you that same wrong fallback for up to an hour, even though the LLM would likely have gotten it right on a retry. That's fixed now — only genuine successes get cached, never a fallback default, so a bad answer doesn't outlive its own cause.

For anyone curious why this happened: three separate functions (fusion source selection, single-source routing, and Kiwix disambiguation candidates) each cached their own bare-fallback result under the same key a real success would use. A transient LLM hiccup would permanently lock that specific query into the fallback for the full routing cache TTL. The Kiwix case needed one extra distinction to fix correctly — the same fallback *result* can happen for two different *reasons*: the LLM call failing outright (worth retrying) versus the LLM genuinely answering with something that just didn't pass a sanity check (not worth retrying, since the same prompt would likely fail the same way again). The fix only skips caching for the first case.

## Synthetic traffic can't pollute either cache, even though both are unconditionally written to on success

[Adversarial Self-Testing](Adversarial-Self-Testing) runs synthetic, generated queries through the genuinely real `route_with_source()` pipeline — that's the entire point of the feature, proving real routing/fallback/fusion behavior actually works, not a simulation of it. But `route_with_source()` writes to both caches as an unconditional side effect of any successful query, synthetic or real, several calls deep inside the routing logic (`_resolve_single_source()`'s `_set_cached()` call, `_llm_detect()`/`_llm_pick_fusion_sources()`'s `_set_routing()` calls) — there's no way for a caller several frames up to opt out after the fact.

Found via a deliberate cross-check while researching an unrelated design doc: Adversarial Self-Testing's own code claimed it "never touches cache.json, routing_cache.json... or any real user-facing state." That claim was wrong, confirmed directly with an unmocked call — a single synthetic adversarial query really did land in the real, in-memory cache dict, and would have persisted to disk on the next batched save. The existing test guarding this claim only ever mocked `route_with_source()` out entirely, so it could never have caught this; it was only ever proving "if `route_with_source` doesn't run, nothing else here touches the cache files either," not the actual claim it was named for.

Fixed with `router.suppress_cache_writes()`, a context manager Adversarial Self-Testing now wraps its real routing call in. Deliberately built on `contextvars.ContextVar`, not a plain module-level boolean — a plain flag would have been a genuine, real race condition: `BackgroundScheduler` runs Adversarial Self-Testing on its own thread pool, fully concurrent with FastAPI's request-handling threads, and a real live request's legitimate cache write landing in the same window a plain global flag was set would have been silently dropped too — a strictly worse bug than the one this fixes. `ContextVar` is thread-local (and task-local under asyncio) by design, confirmed directly: a real concurrent request on one thread is completely unaffected by suppression active on another.

## A second, separate concurrency bug found while researching whether to parallelize web query expansion

Both caches' own disk-persistence functions (`_save_routing_cache()`, `_save_cache()`) used to do a bare `open(path, "w")` followed by `json.dump()` — found via researching whether [Query Expansion](Query-Expansion)'s two sequential SearXNG fetches could safely run concurrently, which meant auditing every writer of the routing cache `get_alternate_phrasing()` touches. This wasn't a risk the parallelization work would have introduced — FastAPI's `/search` endpoint is a synchronous route, so Starlette already runs genuinely concurrent real requests on its own thread pool today, meaning two requests landing close enough together to both trigger a save is a real, already-live scenario, not a hypothetical one.

The actual risk was never really the in-memory dict (a single dict mutation is already safe under the GIL) — it was the file itself. If one thread's `open(path, "w")` truncates the file while another thread's `open(path, "w")` also truncates it before the first thread's `json.dump()` finishes, the file can end up malformed. Confirmed directly, not just reasoned about: a deliberate stress test with 8 concurrent writer threads and 8 concurrent reader threads against the old pattern produced **79,609 JSON corruption errors** in two seconds. The real-world blast radius was bounded — `load_routing_cache()`'s own existing `except json.JSONDecodeError` fallback already catches a corrupted file and starts fresh rather than crashing — but silently losing the *entire* on-disk cache on next restart is still a real, avoidable cost.

Fixed with the standard pattern for exactly this problem: a shared `_atomic_write_json()` helper writes to a temporary file in the same directory, then `os.replace()`s it onto the real target. `os.replace()` is atomic on POSIX (what this project actually runs on), so the target file is always either the complete old version or the complete new one — never a partial write from either side, no matter how two concurrent calls interleave. The identical 8-writer/8-reader stress test against the fix: zero errors. Both caches' save functions now use this one shared helper.

## What's visible without digging through code

[Health & Observability](Health-and-Observability) covers this in full, but briefly: `/health` reports both caches' current entry counts alongside their configured max sizes, so growth toward either bound is visible at a glance. `GET /cache` and `GET /cache/routing` show every individual entry with age and remaining TTL; the corresponding `/clear` endpoints wipe a cache from both memory and disk.

## Why two caches instead of one

They answer genuinely different questions. The result cache says "I already know the answer to this." The routing cache says "I already know *where to look* for the answer to this" — useful even when the actual answer has expired or could have changed, since re-deciding the source via the LLM is itself real, avoidable cost. A query whose result just expired can still skip straight back to the right source without paying for a fresh routing decision.
