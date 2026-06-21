# Benchmarks

Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo. This page covers what those numbers actually show, and the real findings worth knowing before reading the raw data — including two honest, unresolved anomalies from the most recent run.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~18ms.** Every feature added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did several major versions ago. This is by design, not luck: every one of those features is conditionally triggered (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing), so a query that doesn't match any of those conditions never pays for logic it doesn't need.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous discourse-framing phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. The actual measured improvements from the most recent full benchmark run:

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Kiwix disambiguation | 8000ms | 32ms | ~250x |
| Discourse-framing | 2500ms | 25ms | ~100x |

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique ambiguous query, ever, within the cache TTL" — not "pays this every time."

## Two honest, unresolved findings from the most recent run

Worth documenting precisely *because* they weren't immediately explained, rather than smoothing them over:

**`conditional_remainder` only partially warmed** (1900ms cold → 1100ms warm at p98, not down to ~25ms like everything else measured). The remainder-handling path in [Conditional Query Detection](Conditional-Query-Detection) makes two independent routing decisions per request — one for the condition, one for the remainder — each with its own cache key. With a small query pool in the load test and random selection happening independently across the cold and warm runs, it's plausible one half of a given pair was cached from the cold run while the other wasn't. Flagged as a likely benchmark-methodology artifact from a small test pool, not confirmed evidence the underlying caching mechanism is broken for this path — but also not yet proven one way or the other.

**`uptime` showed an unexplained warm-cache tail** (p95 980ms, p99 1400ms) inconsistent with its own cold-cache numbers in the same run, and with every prior release's `uptime` benchmarks. Could be a real Uptime Kuma connection hiccup during that specific run, or just sampling noise given the relatively small request count for that endpoint. Worth re-checking in a future benchmark run rather than treating either explanation as settled.

Both are left in the record rather than re-run-until-clean, on the theory that an honest "this looked weird and we don't fully know why yet" is more useful than a benchmark history that's been quietly massaged to only show clean results.

## Hardware context

These are homelab numbers, not a controlled cloud benchmark — they'll vary with your own LLM hardware (faster GPU genuinely means lower cold routing latency), network latency to HA/Uptime Kuma/your other sources, Kiwix ZIM file size and disk I/O speed, and how warm the routing cache happens to be at the moment you run them. Treat the *relative* patterns here (cold vs. warm, which features cost what) as the generalizable part, and the absolute millisecond figures as specific to one particular homelab's hardware.

## Running your own

```bash
pip install locust
locust -f tests/locustfile.py --host http://your-host:8888
# Open http://localhost:8089
```

Or headless, for a repeatable cold/warm comparison:

```bash
locust -f tests/locustfile.py --host http://your-host:8888 \
  --headless --users 20 --spawn-rate 2 --run-time 120s \
  --csv benchmarks
```

If you add a new feature with its own conditionally-triggered cost (the way disambiguation, conditional detection, and the discourse-framing fix all did), it's worth checking whether `locustfile.py` actually has a task type that exercises it — a benchmark can only measure what it's actually pointed at, and more than one feature in this project's history shipped before its real load-time cost had ever been measured at all, simply because nothing in the load test was constructed to trigger it.
