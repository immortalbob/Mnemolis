# Benchmarks

Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo. This page covers what those numbers actually show, and the real findings worth knowing before reading the raw data — including honest, unresolved anomalies that have shown up across more than one release.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~24ms.** Every feature and fix added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix, an entire battle-testing campaign (v3.20.0–v3.34.0), a full bulletproofing pass (v3.35.0–v3.44.0), and most recently Adversarial Self-Testing, Cross-Source Temporal Pattern Detection, the full latency-parallelization investigation, `/health`'s own concurrency fix, and the persistent Uptime Kuma connection (v3.45.0–v3.50.4) — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did roughly 35 releases ago. This is by design, not luck: every conditionally-triggered feature (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing) and every bug fix from the bulletproofing pass (word-boundary matching instead of substring search, a capped retry loop instead of an unbounded one) was a correctness change, not new computation on the hot path — confirmed directly in the v3.44.1 benchmark, and confirmed again in the v3.50.4 run, not just assumed.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous discourse-framing phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. The actual measured improvements from the most recent full benchmark run (v3.50.4):

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Discourse-framing | 3800ms | 49ms | ~78x |
| Fusion (3 sources) | 880ms | 40ms | ~22x |
| Kiwix disambiguation | 2400ms | 44ms | ~55x |
| Web search | 1700ms | 43ms | ~40x |

`web`, `discourse_framing`, and `kiwix`/`kiwix_disambiguation`'s cold-path numbers move around release to release with real query-mix and concurrent-load variance — see `BENCHMARKS.md`'s v3.50.4 entry for the full table and the honest caveats on what this run did and didn't confirm about the two changes it was set up to validate.

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique ambiguous query, ever, within the cache TTL" — not "pays this every time."

## A partially-confirmed fix, a partially-effective mitigation, and one fix confirmed holding under load

**`conditional`'s warm-cache tail traced to a real, identifiable cause: a thundering-herd race on cache writes, not a caching bug.** `_resolve_conditional()` recurses into the full routing pipeline using the *extracted condition text* as the cache key (e.g. "the back door is unlocked" — not the original "if X, Y" phrasing), and that extracted text never appears as a standalone query anywhere else in the load test's query pool, so each of `CONDITIONAL_QUERIES`'s fixed phrasings has to warm independently. With 20 concurrent Locust users and only a handful of distinct conditions, multiple users can pick the *same* never-yet-cached condition within the same instant — before the first one to resolve it has actually written the cache entry — so several of them each pay the full LLM routing cost concurrently, even on a nominally "warm" run. `auto`'s tail shares the same root cause for the same reason: a small, fixed query pool under artificial concurrent load. This reproduced identically across the v3.44.0 and v3.50.2 benchmark runs — `auto`'s cold p99 hit a full 10 seconds in the v3.50.2 run, the single worst sample yet — which is why `AUTO_QUERIES`/`CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` were widened (6→12, 4→8, 2→4) immediately after that run, in v3.50.3.

**The v3.50.4 re-benchmark confirms the widening helped, but not enough to fully clear the bar set for it.** `auto`'s cold p99 dropped from the v3.50.2 spike (10000ms) to 3800ms — a real, large improvement — but `auto`'s own cold p98 (1300ms), and `conditional`/`conditional_remainder`'s cold p98/p99 (5100ms and 4300ms respectively) are all still squarely in multi-second territory, and both `conditional` and `conditional_remainder` still show a real warm-cache tail (p95 ~440ms on both) that a fully-warmed pool shouldn't have at all. Per the same reasoning that motivated the widening in the first place: this means the pools likely need *more* headroom for 20 concurrent users, not that the thundering-herd explanation was wrong — see `BENCHMARKS.md`'s v3.50.4 entry for the full numbers and the honest read on what "partially effective" means here.

This is a real benchmark-methodology characteristic, not a Mnemolis bug — real homelab usage doesn't involve 20 concurrent users asking about the same back door lock in the same second, so this race only manifests under synthetic concurrent load against a tiny, repeated phrase pool. The actual fix, if pursued further, is widening these pools again in `tests/locustfile.py` to dilute the collision odds further, not changing any application code.

**`uptime`'s warm-cache tail, reproduced across v3.17.0, v3.44.0, and v3.50.2, is now confirmed to have a real, partially-effective fix — not a full resolution.** The v3.50.2 run's hypothesis (a fresh Socket.IO connect+login cycle on every cache miss, not the 60-second `CACHE_TTL_UPTIME_SECONDS` itself) was confirmed correct by directly reading `app/sources/uptime_kuma.py`: there was no persistent connection at all, just a fresh connect-login-disconnect cycle on every single call. v3.50.4 made the connection persistent, reused across calls and warmed once at app startup via `lifespan()`, the same pattern already used for the snapshot scheduler.

**The actual v3.50.4 re-benchmark result is real progress with an honest, remaining gap.** `uptime`'s warm-cache p95/p99 dropped from 1500ms (v3.50.2) to 470ms/850ms — a genuine 2-3x improvement, and most individual requests in both the cold and warm v3.50.4 runs landed in the same 22-32ms range every other warm source shows, confirming the persistent connection is doing real work for the bulk of traffic. But the design doc's own pre-written success bar was "low tens of milliseconds, matching other sources" at p95/p99 — and a result still in the hundreds of milliseconds, even much-improved hundreds, means the fix didn't capture the *entire* mechanism. A genuinely surprising companion finding: `uptime`'s *cold*-cache numbers also dropped substantially (1900ms → 500ms), which the fix's own design doc explicitly flagged in advance as worth investigating further if it happened, since the persistent-connection fix should only change *subsequent* calls, not the very first connection of the app's lifetime. Neither the remaining warm-cache tail nor the unexpectedly-improved cold numbers have a confirmed root cause yet — see `BENCHMARKS.md`'s v3.50.4 entry for the specific, not-yet-investigated candidates (event-wait timing in the underlying library, lock contention under concurrent load, genuine server-side variance).

**`/health`'s concurrency fix (v3.50.3) is now confirmed holding under real load, not just in mocked test conditions.** `/health`'s seven backend checks (`_check_kiwix`, `_check_forecast`, `_check_news`, `_check_web`, `_check_uptime`, `_check_ha`, `_check_llm`) used to run as plain sequential calls in `app/main.py`, each with its own real 3-5 second timeout — confirmed directly by reading the endpoint, and responsible for the v3.50.2 warm run's 5244ms `/health` max. Fixed with a `ThreadPoolExecutor`, the same pattern `fusion.py` already established. The v3.50.4 re-benchmark's `/health` numbers (warm max 1152ms, p99 1200ms) show no recurrence of that sequential-stacking signature — a real, supporting data point under actual concurrent load, though this run wasn't set up to isolate the fix the way `TestHealthConcurrentSourceChecks` already does at the unit level.

**The `cache_hit` anomaly from the v3.50.4 run is now explained and fixed — and it was never a backend bug.** `cache_hit`'s cold-cache p90/p99 (5100ms/8000ms) had no precedent anywhere in this file's history, where `cache_hit` has always been one of the cheapest rows in the table regardless of release — surprising enough to warrant tracing down rather than leaving as an open item. Root cause: `cache_hit`'s task used the literal query `"what is nitrogen"`, which was *also* the first entry in `KIWIX_QUERIES` — the pool the much-more-frequent, highest-weighted `kiwix_search` task draws from at random. On a cold run, both tasks could independently draw the identical, not-yet-cached key at nearly the same instant; `_resolve_single_source()`'s check-then-call-then-write sequence has no per-key lock, so both genuinely missed and both paid the full cold-routing cost concurrently — the exact same thundering-herd shape already documented for `auto`/`conditional`'s own small pools, just not previously recognized as applying to `cache_hit` too. Fixed by giving `cache_hit` its own dedicated query, confirmed (via a direct, automated check) to never collide with any other pool. A load-test fix, not an application-code one — `app/router.py`'s actual caching logic is unchanged.

All of these are left in the record rather than re-run-until-clean, on the theory that an honest "this looked weird, and here's what we actually confirmed and didn't" is more useful than a benchmark history that's been quietly massaged to only show clean results.

## Hardware context

These are homelab numbers, not a controlled cloud benchmark — they'll vary with your own LLM hardware (faster GPU genuinely means lower cold routing latency), network latency to HA/Uptime Kuma/your other sources, Kiwix ZIM file size and disk I/O speed, and how warm the routing cache happens to be at the moment you run them. Treat the *relative* patterns here (cold vs. warm, which features cost what) as the generalizable part, and the absolute millisecond figures as specific to one particular homelab's hardware.

## Running your own

Before a genuine cold-cache run, clear both caches explicitly — skipping this step produces an artificially clean result instead of real cold numbers:

```bash
curl -X POST http://192.168.1.50:8888/cache/clear
curl -X POST http://192.168.1.50:8888/cache/routing/clear
```

Then run cold:

```bash
pip install locust
locust -f tests/locustfile.py --host http://192.168.1.50:8888
# Open http://localhost:8089
```

Replace `192.168.1.50` with your actual Mnemolis host's real IP or hostname — not a placeholder. `--host` silently accepts anything that looks like a URL, so a leftover example value doesn't fail loudly; it fails much later as a wall of opaque DNS errors (`Temporary failure in name resolution`) on every single request, which doesn't obviously point back to `--host` as the cause.

Or headless, for a repeatable cold/warm comparison — run the identical command twice, with no clearing in between for the second (warm) pass:

```bash
locust -f tests/locustfile.py --host http://192.168.1.50:8888 \
  --headless --users 20 --spawn-rate 2 --run-time 120s \
  --csv benchmarks
```

If you add a new feature with its own conditionally-triggered cost (the way disambiguation, conditional detection, and the discourse-framing fix all did), it's worth checking whether `locustfile.py` actually has a task type that exercises it — a benchmark can only measure what it's actually pointed at, and more than one feature in this project's history shipped before its real load-time cost had ever been measured at all, simply because nothing in the load test was constructed to trigger it.
