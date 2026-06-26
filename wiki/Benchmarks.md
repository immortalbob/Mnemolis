# Benchmarks

Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo. This page covers what those numbers actually show, and the real findings worth knowing before reading the raw data — including honest, unresolved anomalies that have shown up across more than one release.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~24ms.** Every feature and fix added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix, an entire battle-testing campaign (v3.20.0–v3.34.0), a full bulletproofing pass (v3.35.0–v3.44.0), and most recently Adversarial Self-Testing, Cross-Source Temporal Pattern Detection, the full latency-parallelization investigation, `/health`'s own concurrency fix, the persistent Uptime Kuma connection, and the `cache_hit` query-collision fix (v3.45.0–v3.50.7) — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did roughly 35 releases ago. This is by design, not luck: every conditionally-triggered feature (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing) and every bug fix from the bulletproofing pass (word-boundary matching instead of substring search, a capped retry loop instead of an unbounded one) was a correctness change, not new computation on the hot path — confirmed directly in the v3.44.1 benchmark, and confirmed again in the v3.50.7 run, not just assumed.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous discourse-framing phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. The actual measured improvements from the most recent full benchmark run (v3.50.7):

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Discourse-framing | 1900ms | 50ms | ~38x |
| Kiwix disambiguation | 6000ms | 35ms | ~171x |
| Web search | 2300ms | 37ms | ~62x |
| `cache_hit` | 940ms | 29ms | ~32x |

`web`, `discourse_framing`, `kiwix`/`kiwix_disambiguation`, and `cache_hit`'s cold-path numbers move around release to release with real query-mix and concurrent-load variance — see `BENCHMARKS.md`'s v3.50.7 entry for the full table and the honest caveats on what this run did and didn't confirm.

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique ambiguous query, ever, within the cache TTL" — not "pays this every time."

## What got fully fixed, what got partially fixed, and what's still genuinely unresolved

**`conditional`'s warm-cache tail traced to a real, identifiable cause: a thundering-herd race on cache writes, not a caching bug.** `_resolve_conditional()` recurses into the full routing pipeline using the *extracted condition text* as the cache key (e.g. "the back door is unlocked" — not the original "if X, Y" phrasing), and that extracted text never appears as a standalone query anywhere else in the load test's query pool, so each of `CONDITIONAL_QUERIES`'s fixed phrasings has to warm independently. With 20 concurrent Locust users and only a handful of distinct conditions, multiple users can pick the *same* never-yet-cached condition within the same instant — before the first one to resolve it has actually written the cache entry — so several of them each pay the full LLM routing cost concurrently, even on a nominally "warm" run. `auto`'s tail shares the same root cause for the same reason: a small, fixed query pool under artificial concurrent load. This reproduced identically across the v3.44.0 and v3.50.2 benchmark runs — `auto`'s cold p99 hit a full 10 seconds in the v3.50.2 run, the single worst sample yet — which is why `AUTO_QUERIES`/`CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` were widened (6→12, 4→8, 2→4) immediately after that run, in v3.50.3.

**The v3.50.4/v3.50.7 re-benchmarks confirmed the v3.50.3 widening helped, but not enough to fully clear the bar set for it — and v3.50.8 worked out why a simple "double it again" wouldn't have fixed that.** `auto`'s cold p99 dropped from the v3.50.2 spike (10000ms) to a still-multi-second-adjacent 3000-3800ms across both re-benchmarks, and `conditional`/`conditional_remainder` kept real warm-cache tails that a fully-warmed pool shouldn't show at all. Modeling the actual collision mechanics rather than re-guessing: with 20 concurrent Locust users, the *expected number of pool entries hit by 2 or more users* isn't monotonically decreasing in pool size — it's closer to a birthday-paradox curve that peaks around pool_size ≈ 10–12 before declining. `conditional_with_remainder`'s old 4-entry pool sat at the very start of that curve (≈3.9 of 4 entries expected to collide — essentially total collision, matching exactly what kept showing up), and a naive doubling to 8 would have moved it further up the curve, not down it.

v3.50.8 widened past that peak instead of just doubling again: `AUTO_QUERIES` 12→24, `CONDITIONAL_QUERIES` 8→20, `CONDITIONAL_WITH_REMAINDER_QUERIES` 4→12 — sizes chosen from the worked-out model, not an arbitrary multiple. A genuine, modeled limitation worth stating plainly: getting the expected colliding-entry count meaningfully below 1 at this concurrency level would need pools in the 150-200 entry range, which isn't realistic to hand-write and individually verify as natural queries. These new sizes are a real, modeled improvement past the worst part of the curve, not a claim of full elimination — not yet re-benchmarked as of this writing, so how much this specific change actually moved the numbers is still an open, falsifiable question for the next run.

This is a real benchmark-methodology characteristic, not a Mnemolis bug — real homelab usage doesn't involve 20 concurrent users asking about the same back door lock in the same second, so this race only manifests under synthetic concurrent load against a query pool small relative to the simulated user count.

**`uptime`'s warm-cache tail, reproduced across v3.17.0, v3.44.0, and v3.50.2, now has its full root cause found and fixed — not just a partial mitigation.** The v3.50.2 run's hypothesis (a fresh Socket.IO connect+login cycle on every cache miss, not the 60-second `CACHE_TTL_UPTIME_SECONDS` itself) was confirmed correct by directly reading `app/sources/uptime_kuma.py`: there was no persistent connection at all, just a fresh connect-login-disconnect cycle on every single call. v3.50.4 made the connection persistent, reused across calls and warmed once at app startup via `lifespan()`, the same pattern already used for the snapshot scheduler. This measurably helped (warm p95 dropped from 1500ms to 470ms) but left a smaller, real tail that reproduced identically across two further benchmark runs (v3.50.6, v3.50.7) — the exact same ~440ms value every single time, a strong signal this was a deterministic cost, not noise, even though its source hadn't been traced yet.

**v3.50.8 traced it: `uptime_kuma_api`'s own `_get_event_data()` pays an unconditional 0.2-second `wait_events` sleep on every call, even when the awaited data was already complete.** Confirmed directly, not inferred — a standalone reproduction against the installed library (constructing a mock with its internal event data already fully populated, then calling the real, unpatched method against it) measured the full 0.2s sleep firing regardless. Two such calls per `search()` (`get_monitors()` + `get_heartbeats()`) is exactly the ~0.4s structural floor that had shown up identically in every run. This is a real, deliberate design choice in the upstream library — Uptime Kuma's server sends one `heartbeatList` push per monitor after login, and `wait_events` exists to let trailing per-monitor pushes land before the client treats that initial batch as complete — but the genuine risk window only covers the *first* call after a fresh connect/login. Every later call on an already-settled persistent connection has nothing left to wait for, confirmed directly: the steady-state push handler appends one complete record per call, with no multi-message batching at all.

Fixed by shrinking `wait_events` only after a connection's first call genuinely settles, keeping the library's full, safe default for that one call where it's still needed. `search()`'s public contract, `CACHE_TTL_UPTIME_SECONDS`, and `UPTIME_KUMA_TIMEOUT_SECONDS` are all unchanged. Not yet re-benchmarked against real hardware as of this writing — the fix is verified correct and timing-proven in the test suite (`TestWaitEventsSettling`), the same "verified in tests, real-hardware confirmation still pending" status `/health`'s own concurrency fix carried for one release before its own re-benchmark confirmed it.

**`/health`'s concurrency fix (v3.50.3) is now confirmed holding under real load, not just in mocked test conditions.** `/health`'s seven backend checks (`_check_kiwix`, `_check_forecast`, `_check_news`, `_check_web`, `_check_uptime`, `_check_ha`, `_check_llm`) used to run as plain sequential calls in `app/main.py`, each with its own real 3-5 second timeout — confirmed directly by reading the endpoint, and responsible for the v3.50.2 warm run's 5244ms `/health` max. Fixed with a `ThreadPoolExecutor`, the same pattern `fusion.py` already established. The v3.50.4 re-benchmark's `/health` numbers (warm max 1152ms, p99 1200ms) show no recurrence of that sequential-stacking signature — a real, supporting data point under actual concurrent load, though this run wasn't set up to isolate the fix the way `TestHealthConcurrentSourceChecks` already does at the unit level.

**The `cache_hit` anomaly from the v3.50.4 run is now explained, fixed, and confirmed fixed by a real re-benchmark — not just theoretically correct.** `cache_hit`'s cold-cache p90/p99 (5100ms/8000ms) had no precedent anywhere in this file's history, where `cache_hit` has always been one of the cheapest rows in the table regardless of release — surprising enough to warrant tracing down rather than leaving as an open item. Root cause: `cache_hit`'s task used the literal query `"what is nitrogen"`, which was *also* the first entry in `KIWIX_QUERIES` — the pool the much-more-frequent, highest-weighted `kiwix_search` task draws from at random. On a cold run, both tasks could independently draw the identical, not-yet-cached key at nearly the same instant; `_resolve_single_source()`'s check-then-call-then-write sequence has no per-key lock, so both genuinely missed and both paid the full cold-routing cost concurrently — the exact same thundering-herd shape already documented for `auto`/`conditional`'s own small pools, just not previously recognized as applying to `cache_hit` too. Fixed (v3.50.6) by giving `cache_hit` its own dedicated query, confirmed via a direct, automated check to never collide with any other pool. A load-test fix, not an application-code one — `app/router.py`'s actual caching logic is unchanged. **The v3.50.7 re-benchmark confirms the fix actually closed the gap**: cold p90/p98/p99 dropped from 5100ms/8000ms/8000ms to 880ms/940ms/940ms — the same shape every other single-source cold-path row shows, no longer an outlier — and the warm run lands `cache_hit` at 29ms p99, essentially identical to `kiwix`'s own 34ms. The same re-benchmark also re-confirmed `uptime`'s and the pool-widening's verdicts from v3.50.5/v3.50.6 are unchanged (neither was touched by the `cache_hit` fix) — see `BENCHMARKS.md`'s v3.50.7 entry for the full numbers and the explicit caution against reading ordinary run-to-run noise as a second data point on either of those.

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
