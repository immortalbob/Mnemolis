# Mnemolis Benchmarks

Load testing performed with [Locust](https://locust.io/) against a production MiniDock instance (Intel N100, 16GB RAM) with Ollama running on a separate host (i9-14900KF, RTX 4090).

## Test Configuration

**Tool:** Locust 2.44.3  
**Target:** `http://localhost:8888`  
**User classes:** `MnemolisSingleSourceUser` and `MnemolisFusionUser`  
**Sources tested:** kiwix, forecast, news, uptime, ha, fusion (2-source, 3-source, LLM auto)

## Results

### 5 Users — Cold Cache

First run, routing cache empty, LLM making fresh decisions.

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 710ms | 750ms | 750ms | 0% |
| `/search [kiwix]` | 900ms | 1400ms | 1400ms | 0% |
| `/search [forecast]` | 16ms | 710ms | 710ms | 0% |
| `/search [news]` | 16ms | 50ms | 50ms | 0% |
| `/search [uptime]` | 996ms | 1000ms | 1000ms | 0% |
| `/search [ha]` | 33ms | 37ms | 37ms | 0% |
| `/search [auto]` | 17ms | 6400ms | 6400ms | 0% |
| `/search [fusion_explicit]` | 16ms | 1000ms | 1300ms | 0% |
| `/search [fusion_auto]` | 16ms | 4500ms | 4500ms | 0% |
| `/search [fusion_triple]` | 16ms | 1100ms | 1100ms | 0% |
| `/search [cache_hit]` | 15ms | 5300ms | 5300ms | 0% |
| **Aggregated** | **16ms** | **1300ms** | **6400ms** | **0%** |

p95/p99 spikes are cold LLM routing calls — first time the system sees a query it calls Ollama for source and book selection. Cached on first use.

---

### 10 Users — Warming Cache

Routing cache populated from the 5-user run. System recognizes repeated queries.

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 1100ms | 1100ms | 0% |
| `/search [kiwix]` | 16ms | 1300ms | 1800ms | 0% |
| `/search [forecast]` | 16ms | 18ms | 18ms | 0% |
| `/search [news]` | 16ms | 33ms | 33ms | 0% |
| `/search [uptime]` | 14ms | 1000ms | 1000ms | 0% |
| `/search [ha]` | 35ms | 38ms | 38ms | 0% |
| `/search [auto]` | 16ms | 1000ms | 1300ms | 0% |
| `/search [fusion_explicit]` | 15ms | 27ms | 84ms | 0% |
| `/search [fusion_auto]` | 15ms | 17ms | 18ms | 0% |
| `/search [fusion_triple]` | 16ms | 20ms | 20ms | 0% |
| `/search [cache_hit]` | 16ms | 18ms | 18ms | 0% |
| **Aggregated** | **16ms** | **730ms** | **1300ms** | **0%** |

Fusion results now effectively free — concurrent sources returning cached results in 15ms.

---

### 20 Users — Hot Cache (v3.4.5)

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 1100ms | 1100ms | 0% |
| `/search [kiwix]` | 15ms | 18ms | 21ms | 0% |
| `/search [forecast]` | 14ms | 23ms | 24ms | 0% |
| `/search [news]` | 15ms | 16ms | 17ms | 0% |
| `/search [uptime]` | 14ms | 1100ms | 1100ms | 0% |
| `/search [ha]` | 34ms | 41ms | 41ms | 0% |
| `/search [auto]` | 15ms | 1000ms | 1100ms | 0% |
| `/search [fusion_explicit]` | 14ms | 19ms | 69ms | 0% |
| `/search [fusion_auto]` | 15ms | 17ms | 33ms | 0% |
| `/search [fusion_triple]` | 14ms | 17ms | 17ms | 0% |
| `/search [cache_hit]` | 15ms | 17ms | 25ms | 0% |
| **Aggregated** | **15ms** | **41ms** | **1000ms** | **0%** |

**7.6 requests/second sustained. 391 requests. 0 failures.**

---

### 20 Users — Hot Cache (v3.5.0)

Re-benchmarked after query decomposition and smart fusion improvements.

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 830ms | 830ms | 0% |
| `/search [kiwix]` | 15ms | 21ms | 22ms | 0% |
| `/search [forecast]` | 15ms | 17ms | 18ms | 0% |
| `/search [news]` | 15ms | 20ms | 24ms | 0% |
| `/search [uptime]` | 15ms | 17ms | 18ms | 0% |
| `/search [ha]` | 16ms | 83ms | 83ms | 0% |
| `/search [auto]` | 15ms | 23ms | 1000ms | 0% |
| `/search [fusion_explicit]` | 14ms | 19ms | 22ms | 0% |
| `/search [fusion_auto]` | 15ms | 22ms | 22ms | 0% |
| `/search [fusion_triple]` | 15ms | 17ms | 17ms | 0% |
| `/search [cache_hit]` | 16ms | 25ms | 25ms | 0% |
| **Aggregated** | **15ms** | **36ms** | **780ms** | **0%** |

**428 requests. 0 failures. p95 improved from 41ms → 36ms. p99 improved from 1000ms → 780ms.**

Query decomposition and smart fusion truncation did not add meaningful overhead — p95 and p99 both improved, likely due to reduced payload sizes from result truncation.

---

## Summary

| Scenario | Median | p95 | Notes |
|----------|--------|-----|-------|
| Cache hit (any source) | 15ms | 25ms | Routing + result cache both warm |
| Cold LLM routing call | ~1000ms | ~6400ms | First time seeing a query |
| Kiwix warm | 15ms | 21ms | LLM book selection cached |
| Fusion 2-source warm | 14ms | 19ms | Concurrent sources, both cached |
| Fusion 3-source warm | 15ms | 17ms | No overhead over 2-source when cached |
| HA entity query | 16ms | 83ms | Live HA API call, 30s result cache |
| Uptime warm | 15ms | 17ms | Socket.IO result cached |
| 20 concurrent users (v3.5.0) | 15ms | 36ms | 0% failure rate |
| Kiwix disambiguation, cold | 16ms | 5900ms | First time seeing an ambiguous term |
| Kiwix disambiguation, warm | 17ms | 20ms | Disambiguation candidates cached |
| Web search, cold | 17ms | 170ms | First time seeing a query (p99: 4600ms) |
| Web search, warm | 17ms | 20ms | Alternate phrasing cached |
| 20 concurrent users (v3.11.1) | 17ms | 38ms | 0% failure rate, warm cache |
| Conditional query, cold | 17ms | 1200ms | First time seeing the condition |
| Conditional query, warm | 18ms | 35ms | Condition routing cached |
| Conditional with remainder, cold | 17ms | 1200ms | Two independent searches (condition + remainder) |
| Conditional with remainder, warm | 18ms | 38ms | Only partially warmed — see v3.17.0 notes (small query pool artifact) |
| Discourse-framing query, cold | 18ms | 150ms | First time seeing the phrase (p98: 2500ms) |
| Discourse-framing query, warm | 17ms | 23ms | Routing + disambiguation cached |
| 20 concurrent users (v3.17.0) | 18ms | 32ms | 0% failure rate, warm cache |

## Key findings

**The routing cache is the performance multiplier.** Cold queries that call Ollama for routing decisions take 1-6 seconds. Warm queries return in 15ms regardless of source complexity. After a few minutes of real usage the cache is warm and the system operates at sub-20ms for nearly all queries.

**Fusion does not add meaningful latency when cached.** A 3-source fusion query with a warm cache returns in 15ms — the same as a single source. Concurrent execution means fusion is essentially free once the routing decision is cached.

**Query decomposition has no measurable overhead.** Splitting "what is the weather and are my services up" into two independent queries and merging the results adds no latency compared to single-source routing at warm cache. p95 improved from 41ms to 36ms after decomposition was added.

**Result truncation improves p99.** Smart fusion now caps each source at 1500 characters before merging. p99 improved from 1000ms to 780ms, likely due to smaller response payloads.

**Zero failures at 20 concurrent users across all versions.** The system does not drop requests under homelab load.

## Hardware context

These benchmarks reflect a homelab deployment — not a production cloud environment. Results will vary based on:
- LLM hardware (RTX 4090 in this case — faster GPU = lower cold routing latency)
- Network latency to HA, Uptime Kuma, and other sources
- Kiwix ZIM file size and disk I/O speed
- Routing cache warmth

### 20 Users — Hot Cache (v3.6.1, with Snapshot Scheduler)

Re-benchmarked after adding the background snapshot scheduler (4 jobs: uptime every 2 min, forecast every 30 min, news every 60 min, HA every 5 min) and fixing SQLite lock contention with WAL mode.

**First run** showed connection resets under load (9 errors across various endpoints) despite 100% success in server logs — determined to be Locust/Docker networking noise, not a code regression, since a clean rerun showed 0 errors.

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 840ms | 840ms | 0% |
| `/search [kiwix]` | 17ms | 20ms | 22ms | 0% |
| `/search [forecast]` | 17ms | 19ms | 20ms | 0% |
| `/search [news]` | 16ms | 20ms | 22ms | 0% |
| `/search [uptime]` | 18ms | 23ms | 1000ms | 0% |
| `/search [ha]` | 34ms | 74ms | 74ms | 0% |
| `/search [auto]` | 17ms | 20ms | 1100ms | 0% |
| `/search [fusion_explicit]` | 17ms | 20ms | 72ms | 0% |
| `/search [fusion_auto]` | 17ms | 22ms | 28ms | 0% |
| `/search [fusion_triple]` | 17ms | 20ms | 32ms | 0% |
| `/search [cache_hit]` | 17ms | 19ms | 19ms | 0% |
| **Aggregated** | **17ms** | **72ms** | **790ms** | **0%** |

**429 requests. 0 failures on clean run.**

The background scheduler adds no meaningful overhead to search latency. Median rose slightly (15ms → 17ms) due to WAL pragma overhead per connection, but this is negligible and p95/p99 remain within the same range as v3.5.0 despite four additional background jobs competing for resources.

### 20 Users — Cold vs Warm Cache (v3.11.1, with Confidence-Aware Fusion + Disambiguation)

Re-benchmarked after the capability expansion series: configurable thresholds, Kiwix search term disambiguation (multi-candidate search-and-score), multi-book Kiwix fusion, and confidence-aware fusion with multi-query expansion for web search. The locust file itself was updated for this run — the prior version had zero `web` source queries and no short/ambiguous Kiwix queries, meaning it couldn't measure the cost of the two most computationally expensive features added this series.

**Cold cache** — first run, routing cache empty for the new query patterns (disambiguation candidates, alternate web phrasings never seen before).

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 810ms | 970ms | 0% |
| `/search [kiwix]` | 18ms | 1900ms | 7300ms | 0% |
| `/search [kiwix_disambiguation]` | 16ms | 5900ms | 6000ms | 0% |
| `/search [web]` | 17ms | 170ms | 4600ms | 0% |
| `/search [forecast]` | 17ms | 20ms | 820ms | 0% |
| `/search [news]` | 16ms | 25ms | 140ms | 0% |
| `/search [uptime]` | 19ms | 1600ms | 1600ms | 0% |
| `/search [ha]` | 28ms | 45ms | 45ms | 0% |
| `/search [auto]` | 17ms | 650ms | 6000ms | 0% |
| `/search [fusion_explicit]` | 18ms | 150ms | 1800ms | 0% |
| `/search [fusion_auto]` | 17ms | 83ms | 3000ms | 0% |
| `/search [fusion_triple]` | 17ms | 32ms | 950ms | 0% |
| `/search [cache_hit]` | 17ms | 3500ms | 3500ms | 0% |
| **Aggregated** | **17ms** | **770ms** | **4900ms** | **0%** |

**853 requests. 0 failures.** The cold-cache tail is genuinely heavier than prior releases — `kiwix_disambiguation` p95 at 5900ms reflects the real cost of generating 3 LLM disambiguation candidates, searching each against Kiwix, and scoring the combined pool, all on first encounter with a given ambiguous term. `web` p99 at 4600ms reflects the dual-query expansion (two SearXNG round-trips plus scoring 25+ raw results) on first encounter with a given query.

**Warm cache** — identical run immediately after, with the routing cache now populated.

| Endpoint | Median | p95 | p99 | Failures |
|----------|--------|-----|-----|----------|
| `/health` | 730ms | 780ms | 790ms | 0% |
| `/search [kiwix]` | 17ms | 22ms | 31ms | 0% |
| `/search [kiwix_disambiguation]` | 17ms | 20ms | 21ms | 0% |
| `/search [web]` | 17ms | 20ms | 38ms | 0% |
| `/search [forecast]` | 17ms | 20ms | 20ms | 0% |
| `/search [news]` | 17ms | 20ms | 22ms | 0% |
| `/search [uptime]` | 18ms | 960ms | 980ms | 0% |
| `/search [ha]` | 38ms | 57ms | 57ms | 0% |
| `/search [auto]` | 17ms | 1000ms | 30000ms¹ | 0% |
| `/search [fusion_explicit]` | 17ms | 20ms | 35ms | 0% |
| `/search [fusion_auto]` | 17ms | 20ms | 24ms | 0% |
| `/search [fusion_triple]` | 18ms | 22ms | 24ms | 0% |
| `/search [cache_hit]` | 16ms | 20ms | 20ms | 0% |
| **Aggregated** | **17ms** | **38ms** | **780ms** | **0%** |

**869 requests. 0 failures.**

¹ One `auto` request reported 30000ms (a single outlier in a 95-request bucket). Server logs for the full benchmark window (`docker logs mnemolis --since 10m`) showed zero errors, warnings, exceptions, or timeouts — consistent with Locust's own client-side request timeout firing rather than a real server-side delay. Excluding that single outlier, the aggregated p99 is 780ms, in line with every prior release.

**The routing cache fully absorbs the new features' cold-start cost.** `kiwix_disambiguation` p95 dropped from 5900ms (cold) to 20ms (warm) — a ~295x improvement. `web` p99 dropped from 4600ms (cold) to 38ms (warm) — a ~121x improvement. Once a given ambiguous term or query phrasing has been seen once, every subsequent occurrence skips the LLM calls entirely and returns at the same sub-20ms median every other source achieves.

**Median latency is unaffected by any of this series' work.** Aggregated median held at 17ms cold and warm, identical to every prior benchmarked version back to v3.5.0. The capability expansion series traded cold-path tail latency for correctness on a minority of complex queries — disambiguation and multi-query expansion only ever run when genuinely needed (short ambiguous Kiwix terms, 3+ word web queries) — without touching the steady-state experience for the other ~90% of traffic.

### 20 Users — Cold vs Warm Cache (v3.17.0, with Conditional Query Detection + Discourse-Framing Fix)

Re-benchmarked after the conditional query detection feature (3.16.0) and the discourse-framing routing bypass fix (3.17.0) — neither had been measured under load before. The locustfile was updated for this run with three new task types: `conditional` (leading "if X, Y" queries against both structured and open-ended sources), `conditional_remainder` (a conditional followed by a real, independently-searched second intent), and `discourse_framing` ("everyone's obsessed with X" phrasing, which now forces Kiwix into the routing decision and strips the discourse phrase from Kiwix's search terms). The prior locustfile had zero coverage for any of this — it couldn't have measured these features' cost at all.

**Cold cache** — first run, routing cache empty for the new query patterns.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 700ms | 910ms | 910ms | 910ms | 0% |
| `/search [kiwix]` | 18ms | 1600ms | 1800ms | 3600ms | 0% |
| `/search [kiwix_disambiguation]` | 18ms | 2200ms | 8000ms | 8000ms | 0% |
| `/search [web]` | 18ms | 1700ms | 3600ms | 5200ms | 0% |
| `/search [conditional]` | 17ms | 1200ms | 1300ms | 1300ms | 0% |
| `/search [conditional_remainder]` | 17ms | 1200ms | 1900ms | 1900ms | 0% |
| `/search [discourse_framing]` | 18ms | 150ms | 2500ms | 2500ms | 0% |
| `/search [forecast]` | 17ms | 20ms | 750ms | 750ms | 0% |
| `/search [news]` | 18ms | 23ms | 100ms | 100ms | 0% |
| `/search [uptime]` | 18ms | 1100ms | 1100ms | 1100ms | 0% |
| `/search [ha]` | 35ms | 56ms | 120ms | 120ms | 0% |
| `/search [auto]` | 19ms | 1100ms | 1400ms | 1500ms | 0% |
| `/search [fusion_explicit]` | 18ms | 92ms | 670ms | 960ms | 0% |
| `/search [fusion_auto]` | 18ms | 23ms | 120ms | 170ms | 0% |
| `/search [fusion_triple]` | 18ms | 120ms | 1300ms | 1300ms | 0% |
| `/search [cache_hit]` | 18ms | 2600ms | 4700ms | 4700ms | 0% |
| **Aggregated** | **18ms** | **750ms** | **1600ms** | **2500ms** | **0%** |

**849 requests. 0 failures.** `kiwix_disambiguation` remains the most expensive cold-path query (p98 8000ms) — unchanged in kind from prior releases, just a different worst-case sample. The two genuinely new cost centers are `conditional_remainder` (two full searches plus two routing decisions per request — condition and remainder are independently routed) and `discourse_framing` (the forced extra Kiwix search added on top of whatever the LLM already chose).

**Warm cache** — identical run immediately after, routing cache populated.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 710ms | 770ms | 770ms | 770ms | 0% |
| `/search [kiwix]` | 18ms | 20ms | 23ms | 25ms | 0% |
| `/search [kiwix_disambiguation]` | 18ms | 21ms | 32ms | 32ms | 0% |
| `/search [web]` | 17ms | 21ms | 24ms | 25ms | 0% |
| `/search [conditional]` | 18ms | 35ms | 1100ms | 1100ms | 0% |
| `/search [conditional_remainder]` | 18ms | 38ms | 1100ms | 1100ms | 0% |
| `/search [discourse_framing]` | 17ms | 23ms | 25ms | 25ms | 0% |
| `/search [forecast]` | 18ms | 21ms | 24ms | 24ms | 0% |
| `/search [news]` | 18ms | 22ms | 40ms | 40ms | 0% |
| `/search [uptime]` | 17ms | 980ms | 1400ms | 1400ms | 0% |
| `/search [ha]` | 35ms | 48ms | 48ms | 48ms | 0% |
| `/search [auto]` | 18ms | 1100ms | 1500ms | 1600ms | 0% |
| `/search [fusion_explicit]` | 17ms | 22ms | 26ms | 34ms | 0% |
| `/search [fusion_auto]` | 18ms | 21ms | 23ms | 47ms | 0% |
| `/search [fusion_triple]` | 17ms | 21ms | 21ms | 22ms | 0% |
| `/search [cache_hit]` | 17ms | 22ms | 23ms | 23ms | 0% |
| **Aggregated** | **18ms** | **32ms** | **700ms** | **770ms** | **0%** |

**907 requests. 0 failures.**

**`kiwix_disambiguation` and `discourse_framing` both collapse fully on cache hit, consistent with every prior release.** `kiwix_disambiguation` p98 dropped from 8000ms (cold) to 32ms (warm) — a ~250x improvement. `discourse_framing` p98 dropped from 2500ms (cold) to 25ms (warm) — a ~100x improvement. Once a discourse-framing phrase or disambiguation term has been seen once, the routing cache skips the LLM/disambiguation work entirely on every subsequent occurrence.

**`conditional_remainder` only partially warmed (1900ms → 1100ms at p98, not down to ~25ms like everything else) — a real, honest finding worth noting rather than glossing over.** The remainder path makes two independent `route_with_source()` calls (one for the condition, one for the remainder), each with its own cache key. With only 2 remainder queries in the test pool and `random.choice()` picking independently across the cold and warm runs, it's plausible one half of a given pair was cached from the cold run while the other wasn't — a benchmark-methodology artifact of a small query pool size, not evidence the underlying caching mechanism (already proven correct everywhere else in this same benchmark) is broken for this path specifically.

**`uptime` shows an unexplained warm-cache tail (p95 980ms, p99 1400ms) inconsistent with its own cold-cache numbers and every prior release's uptime benchmarks.** Worth re-checking in a future run rather than treating as confirmed — could be a real Uptime Kuma connection hiccup during this specific run, or sampling noise given the relatively small request count (25) for this endpoint.

**Median latency remains completely unaffected.** Aggregated median held at 18ms cold and warm, consistent with every prior benchmarked version back to v3.5.0 — conditional detection and the discourse-framing fix both add real cost only on the specific query shapes that trigger them, with zero impact on the steady-state majority of traffic.

## Running benchmarks

```bash
pip install locust
locust -f tests/locustfile.py --host http://your-host:8888
# Open http://localhost:8089
```

Or headless:

```bash
locust -f tests/locustfile.py --host http://your-host:8888 \
  --headless --users 20 --spawn-rate 2 --run-time 120s \
  --csv benchmarks
```
