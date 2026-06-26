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

Fusion's cache key is built from the sorted, comma-joined list of sources actually involved (`fusion[sorted,sources]:query`), not the source name "fusion" alone — two different fusion combinations for the same query text get independent cache entries. This applies identically whether the fusion happens at the top level or inside one clause of a [decomposed](Query-Decomposition) compound query — a repeated compound query whose individual clause resolves to multiple sources internally is cached the same way a top-level fusion query is, not re-run on every request.

On a cache hit, none of the underlying sources get touched at all — no network call, no LLM call, nothing. This is why [Benchmarks](Benchmarks) consistently shows warm-cache latency in the tens of milliseconds even for queries whose cold-cache cost was several seconds.

### Why `uptime`'s connection is persistent even though its cache TTL stays short

`uptime`'s 1-minute TTL is intentionally the closest-to-real-time of any source besides `ha` — a stale "all services up" is actively misleading in a way a stale weather forecast isn't, so this TTL is a deliberate design choice, not an oversight to fix. But a short result-cache TTL and an expensive connection are two genuinely separate costs, and conflating them led to the wrong fix being considered for a real time: raising the TTL would have hidden a real, unrelated cost (a fresh Socket.IO connect+login cycle on every single cache miss, including every 2-minute background snapshot tick) rather than actually fixing it.

The real fix was at the connection level, not the cache level: the Socket.IO connection to Uptime Kuma is now persistent, established once and reused across every call — see [Sources](Sources#uptime--service-monitoring) for the mechanism. `CACHE_TTL_UPTIME_SECONDS` itself is completely unchanged by this; a cache miss still happens exactly as often as the TTL dictates, it's just cheap now, since it no longer pays for a fresh connection and login on top of the real backend query.

**Disk persistence is batched** — writes accumulate in memory and only get flushed to `cache.json` every 5 writes (`_CACHE_SAVE_INTERVAL`), not on every single cache write. This matters for anyone reading the code expecting every cache update to be immediately durable; a handful of very recent entries could theoretically be lost if the process died between batched saves, which is an acceptable tradeoff for a cache (the source data it's caching is still right there, ready to be re-fetched) but worth knowing.

**Bounded at `CACHE_MAX_SIZE`** (default 500). When a *new* key would push the cache over this limit, the single oldest entry (by write timestamp) gets evicted first. Updating an already-cached key never counts as "new" for this purpose, so re-caching something you already have doesn't trigger an eviction.

## Routing cache

Keyed the same way (lowercased, stripped query — though the routing cache key doesn't include a source prefix, since the whole point is recording *which* source was chosen). A single flat TTL applies to every routing decision regardless of source (`ROUTING_CACHE_TTL_SECONDS`, default 1 hour) — a query's correct source assignment doesn't really depend on the kind of freshness concerns the result cache has to account for per-source.

This is a genuinely larger key space than it might first appear. Every unique conditional query, every discourse-framing phrase, every Kiwix disambiguation candidate set gets its own routing cache entry on top of plain single-source routing decisions — which is exactly why this cache has its own, larger bound (`ROUTING_CACHE_MAX_SIZE`, default 1000) than the result cache's `CACHE_MAX_SIZE`, using the same bounded-eviction pattern.

**Disk persistence is immediate** — unlike the result cache's batched saves, the routing cache writes to `routing_cache.json` on every single call to `_set_routing()`. This asymmetry is real and intentional (routing decisions are written far less often than results are read/written during heavy query traffic), but it's also why testing the routing cache's eviction logic specifically mocks out the disk-write step — looping it to its default max size of 1000 in a test would otherwise mean 1000 real disk writes to test logic that has nothing to do with disk I/O at all.

A defensive cap also applies when loading the routing cache back from disk at startup — a file saved before the size limit existed could theoretically still be over it, so loading trims to the most recently-written entries if so, rather than silently allowing an over-limit cache to persist indefinitely across restarts.

**Only genuine successes get cached, never a fallback default.** If routing or Kiwix disambiguation picks an obviously-wrong generic fallback for a query, that fallback result is never written to the cache — so a transient hiccup doesn't lock the same query into the same wrong answer for the rest of the cache's TTL, the way a genuine success result otherwise would. The Kiwix case needs one extra distinction to get this right: the same fallback *result* can happen for two different *reasons* — the LLM call failing outright (worth retrying) versus the LLM genuinely answering with something that just didn't pass a sanity check (not worth retrying, since the same prompt would likely fail the same way again) — and only the first case skips the cache.

## What's visible without digging through code

[Health & Observability](Health-and-Observability) covers this in full, but briefly: `/health` reports both caches' current entry counts alongside their configured max sizes, so growth toward either bound is visible at a glance. `GET /cache` and `GET /cache/routing` show every individual entry with age and remaining TTL; the corresponding `/clear` endpoints wipe a cache from both memory and disk.

## Why two caches instead of one

They answer genuinely different questions. The result cache says "I already know the answer to this." The routing cache says "I already know *where to look* for the answer to this" — useful even when the actual answer has expired or could have changed, since re-deciding the source via the LLM is itself real, avoidable cost. A query whose result just expired can still skip straight back to the right source without paying for a fresh routing decision.

---

## Development Notes

- **The routing cache's flat TTL used to be hardcoded**, the same way the per-source result cache TTLs were — found and made configurable during the same audit that found those.
- **The routing cache had no size limit at all for most of this project's life.** Its key space is genuinely larger than it might first appear (every unique conditional query, discourse-framing phrase, and disambiguation candidate set gets its own entry), which made an unbounded cache a real, if slow-building, risk. Found during a deliberate operational-maturity review, not a reported failure, and fixed with the same bounded-eviction pattern the result cache already had.
- **A bad LLM response used to get "stuck."** Three separate functions (fusion source selection, single-source routing, Kiwix disambiguation candidates) each cached their own bare-fallback result under the same key a real success would use, so a transient LLM hiccup could permanently lock a query into the wrong fallback for the full routing cache TTL. Fixed by skipping the cache write for a genuine call failure, while still caching a response that failed a sanity check (since the same prompt would likely fail the same way again).
- **Synthetic traffic was leaking into both real caches.** `route_with_source()` writes to both caches as an unconditional side effect several calls deep inside the routing logic, so Adversarial Self-Testing's synthetic queries were silently polluting real cache state, contradicting the feature's own documented claim that it never touches cache files. Fixed with `router.suppress_cache_writes()`, a `ContextVar`-based context manager — which itself had a sharp edge once `ThreadPoolExecutor` entered the picture later. See [The Caching Concurrency Investigation](The-Caching-Concurrency-Investigation#chapter-1-synthetic-traffic-was-leaking-into-both-real-caches) for the full story.
- **A file-write race underneath both caches.** Both caches' disk-persistence functions used to do a bare `open(path, "w")` + `json.dump()`, vulnerable to real corruption from two genuinely concurrent writers — confirmed directly with a stress test that produced 79,609 corruption errors in two seconds against the old pattern, zero against the fix. Fixed with a shared atomic write-then-replace helper. See [The Caching Concurrency Investigation](The-Caching-Concurrency-Investigation#chapter-2-the-file-write-race-underneath-both-caches) for the full story.
- **`uptime`'s warm-cache tail looked like a caching problem for three releases before it was confirmed to actually be a connection-lifecycle problem.** `CACHE_TTL_UPTIME_SECONDS` being the only source TTL shorter than a typical benchmark run window correctly explained why a cache miss happened during every run — it never explained why that miss cost 1.5-1.9 real seconds. Reading `app/sources/uptime_kuma.py` directly (rather than continuing to reason from benchmark timing alone) found the real cause: a fresh Socket.IO connect+login cycle on every single call, cache miss or not. Fixed by making the connection persistent instead of touching the TTL at all — see the section above.
