# Fusion

Fusion is what happens when [Routing](Routing) decides a question genuinely needs more than one [Source](Sources). It queries every chosen source concurrently, waits for them with a real timeout, filters out anything that failed or came back empty, deduplicates overlapping content, and merges what's left into one coherent response.

## How a fusion query actually runs

```text
   Routing decides: ["kiwix", "web", "news"]
                      │
                      ▼
        Validate & deduplicate source names
        (cap at FUSION_MAX_SOURCES, default 4)
                      │
                      ▼
        Query ALL sources CONCURRENTLY
        (ThreadPoolExecutor, one worker per source)
        Each call bounded by FUSION_TIMEOUT_SECONDS
                      │
                      ▼
        Filter: keep only successful,
        non-empty results
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
      Zero survived      One survived       Two+ survived
            │                   │                   │
            ▼                   ▼                   ▼
  "No results returned   Return it directly,   Deduplicate overlapping
   from any source in    no header — a single  content (60%+ sentence
   fusion query."        source's own answer   overlap = redundant)
                          doesn't need fusion          │
                          framing                      ▼
                                              Truncate each to
                                              FUSION_MAX_CHARS_PER_SOURCE
                                                        │
                                                        ▼
                                              Join with "[SOURCE — LABEL]"
                                              headers and --- separators
                                              (no same-source merge needed
                                              here — see note below)
```

**There's no same-source merge step inside `fusion.search()` itself, even though it might look like there should be one.** `valid` (the source list this function works from) is deduplicated by its own `seen` set before any results are even gathered, so two entries for the same source can never reach this point — there's nothing to merge. The real same-source merge — for the genuinely different case of two *separately decomposed* sub-queries both happening to resolve to the same source — lives in `router.py`'s `_merge_decomposed_parts()`, a different function entirely, covered in detail in the next section.

Concurrency matters here for a real, practical reason: querying `kiwix`, `web`, and `news` one after another would mean waiting for the slowest one three separate times. Running them in a thread pool means the total wait is roughly as long as the *single slowest* source, not the sum of all of them — meaningful when `kiwix` disambiguation or `web`'s query expansion can each independently take a couple seconds on a cold cache.

## Why single-source results skip the header

If only one source actually returned something usable, fusion returns that result directly — no `[KIWIX — ...]` wrapper, no `---` separator. The header exists to stop a multi-section response from being misread as one continuous block (so an LLM consuming the merged text doesn't, say, infer a news article's location applies to the forecast section sitting right next to it). When there's only one section, there's nothing to disambiguate between, so the header would just be visual noise.

## Deduplication

Fusion results sometimes overlap heavily — a Kiwix article and a web search result can both be substantially about the same thing. `_deduplicate()` checks sentence-level overlap between sources and drops one if 60%+ of its sentences already appear, in substance, in a longer result from another source. This keeps fusion responses from repeating the same information twice under two different headers.

The choice of which side to drop is decided by actual content size — the source with fewer overlapping sentences is the one removed — not by which source happens to come first when Python iterates the results dict. An earlier version compared the two sides asymmetrically, in a way that could (and, measured against this project's own real cold-path latency distributions, frequently did) drop the *longer, more complete* source purely because of which one finished its network call first. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-order-dependent-deduplication-bug-and-its-real-bias-against-kiwix) for the measured directional bias this had against `kiwix` specifically.

This is a different mechanism from the same-source item merging described next — `_deduplicate()` compares *different* sources' results against each other before merging; the same-source merge below combines results that are *already* attributed to the *same* source, after they've survived this step.

## Merging consecutive same-source results

When [Query Decomposition](Query-Decomposition) splits a compound query into independent clauses and routes each one separately, two different clauses can legitimately resolve to the same source — *"indoor air quality and are the doors locked"* sends both halves to `ha`. `fusion._merge_same_source()` combines any consecutive same-source results into one logical block before headers ever get added, so the final answer shows one `[HA — ...]` section, not two redundant ones back to back.

The item separator between merged results (`"---"` for multi-item content, a bare blank line otherwise) is decided once for the whole group of same-source parts being combined, not independently per pairwise merge inside the chain. Combining two or more genuinely separate same-source results is, by definition, a multi-item situation the moment there are two of them — a chain that mixes single-item and multi-item parts now gets a consistent separator at every boundary, including the boundaries between parts that were each individually single-item on their own. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-per-pair-separator-bug) for the real query shape that exposed the inconsistency in the old, per-pair version.

This merge has a real, structural limitation that took three separate, sequential bug fixes to fully close — `_dedupe_nested_fusion_sections()` for cases where the overlap hides behind a `"fusion"` label, and `fusion._dedupe_items_across_blobs()` for cases where the headers correctly merge but the content underneath still repeats. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-same-source-merge-chain-three-bugs-found-in-sequence) for the full investigation — real bugs were found here, in production, and it took three rounds to close the gap completely.

## Configuration

| Setting | Default | What it actually controls |
|---------|---------|----------------------------|
| `FUSION_MAX_SOURCES` | 4 | Hard cap on how many sources one fusion query can touch, regardless of how many routing decided on |
| `FUSION_MAX_CHARS_PER_SOURCE` | 1500 | Per-source truncation before merging — keeps one verbose source from drowning out the others in the final response |
| `FUSION_TIMEOUT_SECONDS` | 15 | How long any single source gets before fusion gives up on it and moves on without it — and, as of v3.50.18, the actual ceiling on how long the *caller* waits for `search()` to return, not just how long the internal gather loop waits before giving up on a straggler (see below) |
| `FUSION_THREAD_POOL_SIZE` | 12 | Worker threads in the shared, long-lived thread pool every fusion call dispatches through |

## Concurrency and thread pool sizing

`search()` queries every chosen source through a single, shared, module-level `ThreadPoolExecutor` (`FUSION_THREAD_POOL_SIZE`, default 12) rather than spinning up and tearing down a fresh executor on every call. This wasn't always the case — see [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#unbounded-thread-creation-and-a-recurring-remotedisconnected-mystery) for why a brand-new per-call pool was a real, confirmed problem under concurrent load on a resource-constrained host, and [The-Benchmark-Investigation-Log](The-Benchmark-Investigation-Log) for how it surfaced.

Dispatch into the shared pool also propagates `router.suppress_cache_writes()`'s `ContextVar` state into each worker thread via `contextvars.copy_context().run(...)` — the same fix already applied to `router.py`'s own concurrent dispatch and `searxng.py`'s concurrent fetch. Without it, a worker thread running `kiwix.search()` could write a real routing-cache entry even while adversarial testing's suppression was active in the calling thread. This has zero effect on real user traffic (nothing in a real `/search` request ever sets the suppression flag) — it only matters for keeping adversarial testing's synthetic queries from touching production cache state. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-contextvar-propagation-gap) for the full mechanism.

**`FUSION_TIMEOUT_SECONDS` genuinely bounds the caller's wait, not just the internal gather loop.** A prior version used `with ThreadPoolExecutor(...) as executor:` — and that context manager's implicit `shutdown(wait=True)` on exit blocked until every dispatched source genuinely finished, regardless of what `as_completed()`'s own timeout had already given up on. A single source slow enough to hit its own configured ceiling (SearXNG's cold-tail behavior, for instance) could hold the entire call open for that source's full real duration, with `FUSION_TIMEOUT_SECONDS` having zero effect on how long the actual caller waited. Fixed by managing the shared pool's lifecycle explicitly instead of relying on the context-manager pattern — an abandoned straggler now keeps running in the background and is discarded once it finishes, with the caller correctly getting control back at the configured timeout. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#fusion_timeout_seconds-never-actually-bounded-the-callers-wait) for the measured before/after.

See [Confidence-Aware Fusion](Confidence-Aware-Fusion) for how `web` and `news` results are scored *before* they ever reach fusion's deduplication step, and [Multi-Book Fusion](Multi-Book-Fusion) for the Kiwix-specific case of fusing across more than one ZIM book.

---

## Development Notes

- **The `[FUSION — FUSION]` double-header bug.** `fusion.search()` itself never produces a literal `"fusion"` source name as a result label, but a caller merging multiple `route_with_source()` outputs together can — and if it doesn't check for that case, it ends up wrapping already-self-headered content in a second, redundant header. Found and fixed twice, at two separate call sites. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-fusion-fusion-bug-and-why-it-kept-coming-back) for both.
- **The mixed-speed timeout crash.** Pairing one fast source with one slow enough to hit `FUSION_TIMEOUT_SECONDS` used to crash the entire fusion call, discarding the fast source's already-successful result along with it. Fixed; a clean partial success now comes back instead. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-mixed-speed-timeout-crash) for the mechanism.
- **v3.50.18 — seven findings across `fusion.py` and its direct dependents, in one investigation.** A `ContextVar` propagation gap (the same fix already applied twice elsewhere in this codebase), unbounded per-request thread creation (a real, confirmed correlate of a previously-unexplained `RemoteDisconnected` failure), an order-dependent deduplication bug with a measured real bias against `kiwix`, five missing `_looks_empty()` failure phrases (one with a real, user-visible 30-minute stale-cache consequence), a title-only item-dedup false-positive risk (documented as a finding plus a direction, not yet fully fixed), a per-pair separator inconsistency in same-source merging, and — found last — `FUSION_TIMEOUT_SECONDS` never actually having bounded the caller's real wait time on any prior release. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs) for the full writeup of each.
