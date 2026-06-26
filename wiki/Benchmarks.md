# Benchmarks

Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo. This page covers what those numbers actually show, and the real findings worth knowing before reading the raw data — including honest, unresolved anomalies that have shown up across more than one release.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~24ms.** Every feature and fix added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix, an entire battle-testing campaign (v3.20.0–v3.34.0), a full bulletproofing pass (v3.35.0–v3.44.0), and most recently Adversarial Self-Testing, Cross-Source Temporal Pattern Detection, and the full latency-parallelization investigation (v3.45.0–v3.50.2) — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did roughly 35 releases ago. This is by design, not luck: every conditionally-triggered feature (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing) and every bug fix from the bulletproofing pass (word-boundary matching instead of substring search, a capped retry loop instead of an unbounded one) was a correctness change, not new computation on the hot path — confirmed directly in the v3.44.1 benchmark, and confirmed again in the v3.50.2 run, not just assumed.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous discourse-framing phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. The actual measured improvements from the most recent full benchmark run (v3.50.2):

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Discourse-framing | 2100ms | 55ms | ~38x |
| Fusion (3 sources) | 1300ms | 36ms | ~36x |
| Kiwix disambiguation | 2300ms | 32ms | ~72x |
| Web search | 1300ms | 43ms | ~30x |

`web` and `discourse_framing`'s cold-path numbers both dropped substantially from the v3.44.0 run (web: 3900ms → 1300ms; discourse-framing: 4200ms → 2100ms) — directionally consistent with real fixes shipped in between (the query-expansion concurrency fix, the discourse-framing keyword-path and fusion-merge fixes), though the exact magnitude of either drop isn't cleanly attributable to a single documented fix; see `BENCHMARKS.md`'s v3.50.2 entry for the honest caveat on both.

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique ambiguous query, ever, within the cache TTL" — not "pays this every time."

## A now-explained finding, one with a real hypothesis for the first time, and one fresh finding

**`conditional`'s warm-cache tail traced to a real, identifiable cause: a thundering-herd race on cache writes, not a caching bug.** `_resolve_conditional()` recurses into the full routing pipeline using the *extracted condition text* as the cache key (e.g. "the back door is unlocked" — not the original "if X, Y" phrasing), and that extracted text never appears as a standalone query anywhere else in the load test's query pool, so each of `CONDITIONAL_QUERIES`'s 4 fixed phrasings has to warm independently. With 20 concurrent Locust users and only 4 distinct conditions, multiple users can pick the *same* never-yet-cached condition within the same instant — before the first one to resolve it has actually written the cache entry — so several of them each pay the full LLM routing cost concurrently, even on a nominally "warm" run. `auto`'s tail shares the same root cause for the same reason: a small, fixed query pool (6 entries) under artificial concurrent load. This has now reproduced identically across the v3.44.0 and v3.50.2 benchmark runs — `auto`'s cold p99 hit a full 10 seconds in the v3.50.2 run, the single worst sample yet, since neither `AUTO_QUERIES` nor `CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` has actually been widened in `tests/locustfile.py` despite being recommended twice now.

This is a real benchmark-methodology characteristic, not a Mnemolis bug — real homelab usage doesn't involve 20 concurrent users asking about the same back door lock in the same second, so this race only manifests under synthetic concurrent load against a tiny, repeated phrase pool. The actual fix, if pursued, is widening these pools in `tests/locustfile.py` to dilute the collision odds, not changing any application code.

**`uptime` has now shown an unexplained warm-cache tail in three separate releases (v3.17.0, v3.44.0, v3.50.2) — and the v3.50.2 run produced the first real, testable hypothesis for it.** Unlike `auto`/`conditional`, `uptime`'s benchmark task uses a single, fixed, literal query (`"are all services up"`) with no pool to collide on — the thundering-herd explanation above structurally can't apply here. `CACHE_TTL_UPTIME_SECONDS` defaults to 60 seconds, deliberately the shortest TTL of any source since uptime status is meant to stay close to real-time — but the benchmark run itself lasts 120 seconds, meaning the `uptime` cache entry genuinely expires and gets refetched live from Uptime Kuma at least once during every run, cold or warm. Every other source's TTL (30 minutes or more) comfortably outlasts the run window; `uptime`'s is the only one short enough not to. The observed tail (1500-1900ms) sits well within `UPTIME_KUMA_TIMEOUT_SECONDS`'s 10-second cap — consistent with a real, slow-but-successful Socket.IO round-trip, not a timeout. **Still not confirmed** — would need a direct check of Uptime Kuma's own connection logs during a run, or a diagnostic run with the TTL temporarily raised well above 120s — but this is a real, specific, falsifiable mechanism rather than an open question with no candidate explanation.

**A fresh finding from the v3.50.2 run, not previously flagged anywhere in this file: `/health` itself can take several real seconds in the worst case, because its source checks ran sequentially.** `/health`'s seven backend checks (`_check_kiwix`, `_check_forecast`, `_check_news`, `_check_web`, `_check_uptime`, `_check_ha`, `_check_llm`) were plain sequential calls in `app/main.py`, each with its own real 3-5 second timeout — confirmed directly by reading the endpoint. This is structurally the same "sequential where it could be concurrent" shape that fusion, query expansion, and conditional+remainder have all already been fixed for elsewhere in this codebase; `/health` was just never on anyone's hot path the way search queries are, so it never got the same treatment. The v3.50.2 warm run's `/health` max (5244ms) is consistent with one or two of those seven checks each taking close to their full real timeout and stacking sequentially. **Fixed immediately after this run** — see [Health & Observability](Health-and-Observability#health-is-everything-actually-reachable-right-now) for the current, concurrent behavior. Not yet re-benchmarked against real hardware; the fix is verified correct in the test suite (`TestHealthConcurrentSourceChecks`), but the real-world worst-case improvement hasn't been measured with a fresh Locust run.

All three are left in the record rather than re-run-until-clean, on the theory that an honest "this looked weird, and here's what we actually know" is more useful than a benchmark history that's been quietly massaged to only show clean results.

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
