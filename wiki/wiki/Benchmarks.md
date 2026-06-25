# Benchmarks

Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo. This page covers what those numbers actually show, and the real findings worth knowing before reading the raw data — including honest, unresolved anomalies that have shown up across more than one release.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~24ms.** Every feature and fix added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix, and most recently an entire battle-testing campaign (v3.20.0–v3.34.0) plus a full bulletproofing pass (v3.35.0–v3.44.0) that found and fixed real bugs in nearly every file in `app/` — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did roughly 25 releases ago. This is by design, not luck: every conditionally-triggered feature (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing) and every bug fix from the bulletproofing pass (word-boundary matching instead of substring search, a capped retry loop instead of an unbounded one) was a correctness change, not new computation on the hot path — confirmed directly in the v3.44.1 benchmark, not just assumed.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous discourse-framing phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. The actual measured improvements from the most recent full benchmark run (v3.44.1):

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Discourse-framing | 4200ms | 55ms | ~76x |
| Fusion (3 sources) | 1700ms | 31ms | ~55x |
| Kiwix disambiguation | 2100ms | 39ms | ~54x |
| Web search | 3900ms | 54ms | ~72x |

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique ambiguous query, ever, within the cache TTL" — not "pays this every time."

## A now-explained finding, and one still-open one

**`conditional`'s warm-cache p99 (2400ms) traced to a real, identifiable cause: a thundering-herd race on cache writes, not a caching bug.** `_resolve_conditional()` recurses into the full routing pipeline using the *extracted condition text* as the cache key (e.g. "the back door is unlocked" — not the original "if X, Y" phrasing), and that extracted text never appears as a standalone query anywhere else in the load test's query pool, so each of `CONDITIONAL_QUERIES`'s 4 fixed phrasings has to warm independently. With 20 concurrent Locust users and only 4 distinct conditions, multiple users can pick the *same* never-yet-cached condition within the same instant — before the first one to resolve it has actually written the cache entry — so several of them each pay the full LLM routing cost concurrently, even on a nominally "warm" run. `auto`'s tail likely shares the same root cause for the same reason: a small, fixed query pool under artificial concurrent load.

This is a real benchmark-methodology characteristic, not a Mnemolis bug — real homelab usage doesn't involve 20 concurrent users asking about the same back door lock in the same second, so this race only manifests under synthetic concurrent load against a tiny, repeated phrase pool. The actual fix, if pursued, is widening `CONDITIONAL_QUERIES`/`AUTO_QUERIES` in `tests/locustfile.py` to dilute the collision odds, not changing any application code.

**`uptime` has now shown an unexplained warm-cache tail twice, across two different releases (v3.17.0 and v3.44.1), and this one is still genuinely open.** Inconsistent with its own cold-cache numbers in the same run, and with most other releases' `uptime` benchmarks. Two independent occurrences makes this worth treating as a real, recurring pattern rather than one-off sampling noise, even though the actual root cause still isn't confirmed. Could be a genuine Uptime Kuma connection characteristic under load (a real Socket.IO reconnect cost on some fraction of requests, say) rather than pure noise. Worth a dedicated investigation in a future pass rather than continuing to flag it as an open question release after release.

Both are left in the record rather than re-run-until-clean, on the theory that an honest "this looked weird, and here's what we actually know" is more useful than a benchmark history that's been quietly massaged to only show clean results.

## Hardware context

These are homelab numbers, not a controlled cloud benchmark — they'll vary with your own LLM hardware (faster GPU genuinely means lower cold routing latency), network latency to HA/Uptime Kuma/your other sources, Kiwix ZIM file size and disk I/O speed, and how warm the routing cache happens to be at the moment you run them. Treat the *relative* patterns here (cold vs. warm, which features cost what) as the generalizable part, and the absolute millisecond figures as specific to one particular homelab's hardware.

## Running your own

Before a genuine cold-cache run, clear both caches explicitly — found via a real run that produced an artificially clean result instead of real cold numbers without this step:

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
