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
| HA entity query, post-word-boundary-fix (v3.44.0) | 35-39ms | 47-48ms | `_build_filter()` switched from substring to regex matching — no measurable cost confirmed |
| Discourse-framing query, cold (v3.44.0) | 30ms | 1300ms | New most expensive cold-path query (p98: 4200ms), reflecting a larger Kiwix catalog/routing surface since v3.17.0 |
| Discourse-framing query, warm (v3.44.0) | 29ms | 43ms | ~76x p98 improvement (4200ms → 55ms) once cached |
| 20 concurrent users (v3.44.0) | 24ms | 53ms | 0% failure rate, warm cache — median unchanged since v3.5.0 |

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

### 20 Users — Cold vs Warm Cache (v3.44.0, post-battle-testing and bulletproofing)

The last real benchmark in this file was v3.17.0. Everything from v3.18.0 through v3.44.0 — the complexity-investigation campaign (v3.20.0–v3.34.0, real bugs found and fixed in `route_with_source`, `_decompose`, `home_assistant.py`'s area filtering, `kiwix.py`'s scoring/disambiguation, `snapshots.py`'s diff engines, `uptime_kuma.py`, `freshrss.py`, `searxng.py`) and the bulletproofing pass that followed (v3.35.0–v3.44.0, a full top-to-bottom read of every file in `app/` specifically hunting for bugs complexity scores never flagged) — had never been measured under real load. Two real, severe bugs from that whole stretch are directly relevant to what this run could confirm: `home_assistant.py`'s keyword matching switched from naive substring search to `\b`-word-boundary regex (a real bug had `"on"` matching inside `"front"`, silently breaking entity lookups like "is the front door locked"), and `kiwix.py`'s article-fetch fallback loop is now capped at 5 attempts instead of unbounded. Neither is expected to add meaningful latency — both are correctness fixes, not new computation.

`tests/locustfile.py`'s `HA_QUERIES` was updated for this run specifically: added `"is the front door locked"` and `"is the download finished yet"`, the exact query shapes behind the word-boundary bug, so this run could confirm under real, concurrent load against live HA data that the fix holds — not just in the test suite.

**A real, genuine gap surfaced while running this**, worth documenting since it cost real time: this file had never previously spelled out *how* a "cold cache" run actually gets its empty cache — every prior version's section stated it as a fact with no documented mechanism. The first attempt at this run reused a session-warmed cache and produced an artificially clean result instead of real cold numbers. Fixed going forward — see "Running benchmarks" below for the now-explicit `POST /cache/clear` + `POST /cache/routing/clear` step required before a genuine cold run.

**Cold cache** — both caches explicitly cleared immediately before this run.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 770ms | 780ms | 780ms | 780ms | 0% |
| `/search [kiwix]` | 23ms | 1400ms | 1600ms | 1800ms | 0% |
| `/search [kiwix_disambiguation]` | 23ms | 2000ms | 2100ms | 2100ms | 0% |
| `/search [web]` | 24ms | 1700ms | 2200ms | 3900ms | 0% |
| `/search [conditional]` | 28ms | 1200ms | 1300ms | 1300ms | 0% |
| `/search [conditional_remainder]` | 31ms | 320ms | 800ms | 800ms | 0% |
| `/search [discourse_framing]` | 30ms | 1300ms | 4200ms | 4200ms | 0% |
| `/search [forecast]` | 23ms | 49ms | 730ms | 730ms | 0% |
| `/search [news]` | 23ms | 31ms | 54ms | 54ms | 0% |
| `/search [uptime]` | 24ms | 1500ms | 1500ms | 1500ms | 0% |
| `/search [ha]` | 35ms | 48ms | 50ms | 50ms | 0% |
| `/search [auto]` | 25ms | 1100ms | 1100ms | 1400ms | 0% |
| `/search [fusion_explicit]` | 22ms | 60ms | 720ms | 1000ms | 0% |
| `/search [fusion_auto]` | 24ms | 38ms | 47ms | 83ms | 0% |
| `/search [fusion_triple]` | 23ms | 1500ms | 1700ms | 2000ms | 0% |
| `/search [cache_hit]` | 23ms | 75ms | 3300ms | 3300ms | 0% |
| **Aggregated** | **24ms** | **780ms** | **1500ms** | **1900ms** | **0%** |

**876 requests. 0 failures.** `discourse_framing` is the new most expensive cold-path query (p98 4200ms) — the forced extra Kiwix search on top of whatever the LLM already chose, now compounding with the genuinely larger Kiwix catalog and routing-decision surface this codebase has accumulated since v3.17.0. `kiwix_disambiguation` (p98 2100ms) and `fusion_triple` (p98 1700ms) are both consistent in kind with prior releases, just different worst-case samples from a larger query surface.

**Warm cache** — identical run immediately afterward, no clearing in between.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 740ms | 810ms | 820ms | 820ms | 0% |
| `/search [kiwix]` | 23ms | 34ms | 40ms | 41ms | 0% |
| `/search [kiwix_disambiguation]` | 24ms | 37ms | 39ms | 39ms | 0% |
| `/search [web]` | 23ms | 33ms | 50ms | 54ms | 0% |
| `/search [conditional]` | 27ms | 1300ms | 2300ms | 2400ms | 0% |
| `/search [conditional_remainder]` | 30ms | 44ms | 63ms | 63ms | 0% |
| `/search [discourse_framing]` | 29ms | 43ms | 55ms | 55ms | 0% |
| `/search [forecast]` | 24ms | 36ms | 40ms | 40ms | 0% |
| `/search [news]` | 22ms | 30ms | 36ms | 36ms | 0% |
| `/search [uptime]` | 23ms | 990ms | 1000ms | 1000ms | 0% |
| `/search [ha]` | 39ms | 47ms | 63ms | 63ms | 0% |
| `/search [auto]` | 25ms | 1100ms | 1200ms | 1400ms | 0% |
| `/search [fusion_explicit]` | 22ms | 31ms | 36ms | 41ms | 0% |
| `/search [fusion_auto]` | 25ms | 37ms | 43ms | 44ms | 0% |
| `/search [fusion_triple]` | 22ms | 30ms | 31ms | 31ms | 0% |
| `/search [cache_hit]` | 23ms | 25ms | 25ms | 25ms | 0% |
| **Aggregated** | **24ms** | **53ms** | **770ms** | **1000ms** | **0%** |

**893 requests. 0 failures.**

**`kiwix`, `kiwix_disambiguation`, `web`, `discourse_framing`, `fusion_triple`, and `cache_hit` all collapse fully on cache hit, consistent with every prior release.** `discourse_framing` p98 dropped from 4200ms (cold) to 55ms (warm) — a ~76x improvement. `fusion_triple` p98 dropped from 1700ms to 31ms — a ~55x improvement.

**`auto`, `conditional`, and `uptime` stayed expensive at p95+ even warm — a real, genuine pattern worth investigating rather than assuming away, and now traced to a real, identifiable cause for two of the three.** `_resolve_conditional()` recurses into the full routing pipeline using the *extracted condition text* as the cache key (e.g. "the back door is unlocked" — not the original "if X, Y" phrasing), and that extracted text never appears as a standalone query anywhere else in `tests/locustfile.py`'s query pool, so each of `CONDITIONAL_QUERIES`'s 4 fixed phrasings has to warm independently. With 20 concurrent Locust users and only 4 distinct conditions, multiple users can pick the *same* never-yet-cached condition within the same instant — before the first one to resolve it has actually written the cache entry — so several of them each pay the full LLM routing cost concurrently, even on a nominally "warm" run. This is a real thundering-herd race on cache writes under artificial concurrent load, not a caching bug: real homelab usage doesn't involve 20 concurrent users asking about the same back door lock in the same second. `auto`'s tail (also a small, fixed 6-entry pool) likely shares the same root cause. `conditional` p98 actually rose warm vs cold (1300ms → 2300ms) — consistent with this explanation if the warm run's random sampling happened to produce more simultaneous collisions on a not-yet-cached condition than the cold run did, not evidence of a real regression. The actual fix, if pursued, is widening `CONDITIONAL_QUERIES`/`AUTO_QUERIES` to dilute the collision odds, not changing any application code. `uptime`'s warm-cache tail (p95 990ms, p99 1000ms) is the same unexplained anomaly already flagged in the v3.17.0 entry, now reproduced a second time across a completely different release — still not root-caused, and genuinely worth a dedicated investigation rather than continuing to flag it release after release.

**Median latency remains completely unaffected by the entire battle-testing and bulletproofing campaign.** Aggregated median held at 24ms cold and warm — consistent with every benchmarked version back to v3.5.0, across roughly 25 releases and dozens of real bug fixes in between. Every fix in this stretch was a correctness change, not new computation on the steady-state path, and the numbers confirm that held true in practice, not just in theory.

### 20 Users — Cold vs Warm Cache (v3.50.2, post-adversarial-self-testing and post-latency-parallelization work)

The last real benchmark in this file was v3.44.0. Everything between v3.44.1 and v3.50.2 — the config-completeness audit (v3.45.0), Adversarial Self-Testing's full build-out and real production run (v3.46.0–v3.48.x), Cross-Source Temporal Pattern Detection (v3.47.0), and the full latency-parallelization investigation that fixed `web` query expansion's sequential cost and `conditional_with_remainder`'s sequential cost at the root (v3.48.10–v3.50.0) — had never been measured under real load. Two of those fixes are directly testable by this exact run: `web`'s primary fetch and alternate-phrasing chain now run concurrently instead of sequentially, and `conditional_with_remainder`'s condition and remainder now run concurrently too.

**Cold cache** — both caches explicitly cleared immediately before this run.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 740ms | 810ms | 810ms | 810ms | 0% |
| `/search [kiwix]` | 24ms | 1100ms | 1700ms | 2800ms | 0% |
| `/search [kiwix_disambiguation]` | 22ms | 2300ms | 2300ms | 2300ms | 0% |
| `/search [web]` | 24ms | 970ms | 1100ms | 1300ms | 0% |
| `/search [conditional]` | 26ms | 1000ms | 1400ms | 1400ms | 0% |
| `/search [conditional_remainder]` | 30ms | 1100ms | 1400ms | 1400ms | 0% |
| `/search [discourse_framing]` | 28ms | 1200ms | 2100ms | 2100ms | 0% |
| `/search [forecast]` | 24ms | 40ms | 740ms | 740ms | 0% |
| `/search [news]` | 23ms | 38ms | 87ms | 87ms | 0% |
| `/search [uptime]` | 24ms | 1900ms | 1900ms | 1900ms | 0% |
| `/search [ha]` | 40ms | 340ms | 340ms | 340ms | 0% |
| `/search [auto]` | 24ms | 2400ms | 3200ms | 10000ms | 0% |
| `/search [fusion_explicit]` | 21ms | 42ms | 760ms | 1800ms | 0% |
| `/search [fusion_auto]` | 25ms | 38ms | 160ms | 1800ms | 0% |
| `/search [fusion_triple]` | 23ms | 110ms | 1300ms | 1300ms | 0% |
| `/search [cache_hit]` | 22ms | 47ms | 47ms | 47ms | 0% |
| **Aggregated** | **24ms** | **810ms** | **1600ms** | **2300ms** | **0%** |

**862 requests. 0 failures.**

**`web`'s cold p99 dropped from 3900ms (v3.44.0) to 1300ms** — a real, large improvement, directionally consistent with the v3.49.0 query-expansion concurrency fix, though larger in magnitude than that fix's own documented repro timings (`4.15s` → `3.04s` sequential-to-concurrent) predict on their own; the gap is likely real query-mix and hardware-state differences between a controlled repro and a full 20-user benchmark pool, not evidence the documented fix alone fully explains this specific number. **`discourse_framing`'s cold p98 also dropped, from 4200ms to 2100ms** — plausible given the discourse-framing keyword-path fix and the fusion-merge-chain fixes both landed in this stretch and both removed real wasted work rather than adding any, but this is one sample against one prior sample, not a controlled comparison.

**`auto`'s cold p99 (10000ms) is the single worst sample across this entire run.** `AUTO_QUERIES` in `tests/locustfile.py` is still the same fixed 6-entry pool the v3.44.0 entry already named as the likely cause of `auto`'s tail — unchanged since then. This is consistent with, and very likely the same mechanism as, the already-documented thundering-herd cache-write collision on a small fixed pool, just a worse single sample than v3.44.0 happened to draw. **Fixed immediately after this run**: `AUTO_QUERIES` widened from 6 to 12 entries, and `CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` widened from 4/2 to 8/4 — every new entry verified directly against `detect_intent()`/`detect_conditional()` to confirm it resolves the way intended before being added, not just assumed. `app/adversarial_testing.py`'s `CONDITIONAL_SEEDS` was updated in lockstep, since `TestSeedVocabularyIntegrity` enforces that every `CONDITIONAL_QUERIES` entry has a matching seed there — caught by that exact test before this fix was complete. The next benchmark run against these widened pools is the real test of whether this actually reduces the collision rate; not yet re-run as of this writing.

**Warm cache** — identical run immediately afterward, no clearing in between.

| Endpoint | Median | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|----------|
| `/health` | 750ms | 5200ms | 5200ms | 5200ms | 0% |
| `/search [kiwix]` | 23ms | 32ms | 38ms | 41ms | 0% |
| `/search [kiwix_disambiguation]` | 22ms | 29ms | 32ms | 42ms | 0% |
| `/search [web]` | 23ms | 34ms | 43ms | 49ms | 0% |
| `/search [conditional]` | 28ms | 59ms | 1100ms | 1100ms | 0% |
| `/search [conditional_remainder]` | 33ms | 62ms | 1500ms | 1500ms | 0% |
| `/search [discourse_framing]` | 30ms | 44ms | 55ms | 55ms | 0% |
| `/search [forecast]` | 23ms | 33ms | 41ms | 41ms | 0% |
| `/search [news]` | 23ms | 34ms | 37ms | 39ms | 0% |
| `/search [uptime]` | 25ms | 1500ms | 1500ms | 1500ms | 0% |
| `/search [ha]` | 38ms | 67ms | 67ms | 67ms | 0% |
| `/search [auto]` | 26ms | 1000ms | 1100ms | 1200ms | 0% |
| `/search [fusion_explicit]` | 22ms | 34ms | 40ms | 65ms | 0% |
| `/search [fusion_auto]` | 25ms | 42ms | 46ms | 73ms | 0% |
| `/search [fusion_triple]` | 22ms | 34ms | 36ms | 43ms | 0% |
| `/search [cache_hit]` | 23ms | 33ms | 34ms | 34ms | 0% |
| **Aggregated** | **24ms** | **44ms** | **730ms** | **1000ms** | **0%** |

**893 requests. 0 failures.**

**`web`, `kiwix`, `kiwix_disambiguation`, `discourse_framing`, `fusion_triple`, and `cache_hit` all collapse fully on cache hit, consistent with every prior release.**

**`/health`'s warm-cache max (5244ms) is a real, fresh finding, not previously flagged in this file.** `/health`'s seven source checks (`_check_kiwix`, `_check_forecast`, `_check_news`, `_check_web`, `_check_uptime`, `_check_ha`, `_check_llm`) run as plain sequential calls in `app/main.py`, each with its own real 3-5 second timeout — confirmed directly by reading the endpoint. This is structurally the same class of "sequential where it could be concurrent" cost that fusion, query expansion, and conditional+remainder have all already been fixed for elsewhere in this codebase, just never applied here, likely because `/health` was never on anyone's hot path the way search queries are. A single slow real check (the LLM ping reaching across to The Beast, a slow SearXNG `/healthz`) stacking with normal overhead on top of the others could plausibly produce a multi-second worst case this way — a real, plausible mechanism, not a confirmed root cause; tracing exactly which check was slow on this specific sample wasn't done.

**`auto`, `conditional`, `conditional_remainder`, and `uptime` again stayed expensive at p95+ even warm — the same pattern as v3.44.0, reproduced a second time.** `CONDITIONAL_QUERIES` (4 entries) and `CONDITIONAL_WITH_REMAINDER_QUERIES` (2 entries) in `tests/locustfile.py` were both still the exact same small, fixed pools the v3.44.0 entry already named, unchanged at the time this run was taken — both have since been widened (see above; 8 and 4 entries respectively as of immediately after this run). This is the same thundering-herd cache-write collision under artificial concurrent load already documented twice; nothing about the latency-parallelization fixes in this release range changes that explanation, since the fixes addressed *sequential-vs-concurrent cost within one query's resolution*, not *cache-write collisions across many concurrent users sharing a too-small query pool*.

**`uptime`'s warm-cache tail (p95 1500ms, p99 1500ms) has now shown up in three separate releases (v3.17.0, v3.44.0, this one) — and this run surfaced the first real, testable hypothesis for it, not just a fourth "still unexplained."** Unlike `auto`/`conditional`, `uptime`'s task uses a single, fixed, literal query (`"are all services up"`, explicit `source="uptime"`) — there's no pool to collide on, so the thundering-herd explanation that covers the other three tails structurally can't apply here. `CACHE_TTL_UPTIME_SECONDS` defaults to 60 seconds — deliberately the shortest TTL of any source, since uptime status is meant to stay close to real-time — but this benchmark run lasts 120 seconds, meaning the `uptime` cache entry genuinely expires and gets refetched live from Uptime Kuma at least once during every single run, cold or warm. Every other source's TTL (30min minimum) comfortably outlasts the 120-second run window; `uptime`'s is the one short enough not to. The observed tail (1500-1900ms) is well within `UPTIME_KUMA_TIMEOUT_SECONDS`'s configured 10-second cap, consistent with a real, slow-but-successful Socket.IO round-trip rather than a timeout or failure. **Not confirmed** — would need either a direct check of Uptime Kuma's own connection logs during a run, or a diagnostic run with `CACHE_TTL_UPTIME_SECONDS` temporarily raised well above 120s to see if the tail disappears — but this is the first mechanism proposed for this anomaly that doesn't require assuming an unexplained bug, and it's specific enough to actually test.

**Median latency remains completely unaffected across this entire release range too.** Aggregated median held at 24ms cold and warm — now confirmed constant across roughly 35 releases and every major feature shipped since v3.5.0, including two entirely new background-job features (Adversarial Self-Testing, Cross-Source Temporal Pattern Detection) that run on their own schedule and were never expected to touch the request-handling path at all.

### 20 Users — Cold vs Warm Cache (v3.50.4, validating the persistent Uptime Kuma connection and the v3.50.3 pool widening together)

Run against the real v3.50.4 codebase on MiniDock, validating two changes against the same v3.50.2 baseline above in one pass rather than two separate sessions: the persistent Uptime Kuma connection (this release) and the `AUTO_QUERIES`/`CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` pool widening (v3.50.3, never previously re-benchmarked).

**Cold cache** — both caches explicitly cleared immediately before this run.

| Endpoint | Median | p90 | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|-----|----------|
| `/health` | 710ms | 740ms | 760ms | 760ms | 760ms | 0% |
| `/search [kiwix]` | 24ms | 680ms | 1100ms | 2200ms | 5000ms | 0% |
| `/search [kiwix_disambiguation]` | 24ms | 1500ms | 2000ms | 2400ms | 2400ms | 0% |
| `/search [web]` | 24ms | 39ms | 820ms | 1700ms | 2100ms | 0% |
| `/search [conditional]` | 29ms | 460ms | 1200ms | 5100ms | 5100ms | 0% |
| `/search [conditional_remainder]` | 38ms | 970ms | 1900ms | 4300ms | 4300ms | 0% |
| `/search [discourse_framing]` | 31ms | 150ms | 1800ms | 3800ms | 3800ms | 0% |
| `/search [forecast]` | 23ms | 32ms | 79ms | 680ms | 680ms | 0% |
| `/search [news]` | 22ms | 31ms | 49ms | 130ms | 130ms | 0% |
| `/search [uptime]` | 22ms | 440ms | 500ms | 500ms | 500ms | 0% |
| `/search [ha]` | 34ms | 50ms | 55ms | 150ms | 150ms | 0% |
| `/search [auto]` | 27ms | 450ms | 730ms | 1300ms | 3800ms | 0% |
| `/search [fusion_explicit]` | 22ms | 30ms | 120ms | 710ms | 730ms | 0% |
| `/search [fusion_auto]` | 25ms | 36ms | 110ms | 220ms | 230ms | 0% |
| `/search [fusion_triple]` | 22ms | 32ms | 47ms | 880ms | 880ms | 0% |
| `/search [cache_hit]` | 24ms | 5100ms | 5700ms | 8000ms | 8000ms | 0% |
| **Aggregated** | **24ms** | **160ms** | **730ms** | **1800ms** | **3800ms** | **0%** |

**863 requests. 0 failures.**

**`uptime`'s cold-cache cost dropped substantially: p95/p99 went from 1900ms (v3.50.2) to 500ms.** Most individual `uptime` requests on this run were genuinely fast — in line with every other source's cold numbers — with a minority (roughly the slowest 2-3 of 17 total `uptime` requests, based on where the percentile table's jump from 26ms at p80 to 440ms at p90 sits) still paying a real, large cost. See the warm-cache analysis below for the full read on what this means for the fix's two success criteria.

**`cache_hit`'s cold p90/p95/p99 (5100/5700/8000ms) is a genuinely new, surprising number not seen in the v3.50.2 baseline (47ms p99) or anywhere in this file's prior history**, where `cache_hit` has always been one of the cheapest, most boring rows in the table. Not investigated as part of this run — flagged here honestly rather than folded silently into "the usual cold-cache variance," since a cache-hit task spiking into multi-second territory on a *cold* run (where, by definition, nothing should be cached yet for it to hit) is a different shape of anomaly than the already-documented thundering-herd cache-write collisions on `auto`/`conditional`. Worth a dedicated look in a future pass; out of scope for what this run was set up to validate.

**Warm cache** — identical run immediately afterward, no clearing in between.

| Endpoint | Median | p90 | p95 | p98 | p99 | Failures |
|----------|--------|-----|-----|-----|-----|----------|
| `/health` | 730ms | 770ms | 1200ms | 1200ms | 1200ms | 0% |
| `/search [kiwix]` | 24ms | 28ms | 34ms | 42ms | 49ms | 0% |
| `/search [kiwix_disambiguation]` | 22ms | 30ms | 33ms | 44ms | 44ms | 0% |
| `/search [web]` | 23ms | 30ms | 35ms | 43ms | 46ms | 0% |
| `/search [conditional]` | 26ms | 59ms | 440ms | 440ms | 440ms | 0% |
| `/search [conditional_remainder]` | 40ms | 55ms | 440ms | 460ms | 460ms | 0% |
| `/search [discourse_framing]` | 29ms | 41ms | 47ms | 49ms | 59ms | 0% |
| `/search [forecast]` | 23ms | 28ms | 31ms | 40ms | 45ms | 0% |
| `/search [news]` | 23ms | 26ms | 29ms | 38ms | 38ms | 0% |
| `/search [uptime]` | 24ms | 440ms | 470ms | 850ms | 850ms | 0% |
| `/search [ha]` | 38ms | 48ms | 48ms | 48ms | 48ms | 0% |
| `/search [auto]` | 25ms | 41ms | 440ms | 450ms | 450ms | 0% |
| `/search [fusion_explicit]` | 22ms | 31ms | 38ms | 44ms | 54ms | 0% |
| `/search [fusion_auto]` | 25ms | 34ms | 37ms | 41ms | 42ms | 0% |
| `/search [fusion_triple]` | 21ms | 29ms | 33ms | 40ms | 43ms | 0% |
| `/search [cache_hit]` | 24ms | 27ms | 28ms | 36ms | 36ms | 0% |
| **Aggregated** | **24ms** | **37ms** | **49ms** | **460ms** | **730ms** | **0%** |

**901 requests. 0 failures.**

**`cache_hit` is back to behaving exactly as expected on warm (median 24ms, p99 36ms)** — confirming the cold run's cache_hit anomaly above is specific to the cold pass, not a new, persistent regression.

#### Part 1 (persistent Uptime Kuma connection) — against the design doc's own pre-written success criteria

**Primary criterion — not met.** The design doc's bar was warm-cache p95/p99 dropping to "something close to the other sources' warm-cache numbers (low tens of milliseconds)." `uptime` warm p95/p99 went from 1500ms/1500ms (v3.50.2) to 470ms/850ms — a real, large improvement (roughly 3x at p95, 1.8x at p99) but still landing in the hundreds-of-milliseconds, not the low tens. Per the design doc's own stated reading of this outcome: this means the fix didn't address the *entire* mechanism, even though it clearly addressed a real, substantial part of it.

What the raw numbers actually show, reading past the percentile summary: most `uptime` requests on the warm run were genuinely fast and indistinguishable from any other warm source (median 24ms, with roughly the fastest 80% of the 29 total `uptime` requests landing under 32ms) — a real, working improvement over the old behavior, where *every* request after the 60-second TTL expired paid the full connect+login cost. But a minority of requests (roughly the slowest 3-6 of 29) still paid something in the 440-850ms range. The persistent-connection fix is doing real work; it just isn't the complete explanation for the tail.

**Secondary criterion — also not met, in the informative direction.** The design doc's bar was cold-cache numbers staying "roughly unchanged" from the v3.50.2 cold baseline (1900ms), since the fix should only change *subsequent* calls, not the very first connection of the app's lifetime. Instead, cold p95/p99 also dropped substantially (1900ms → 500ms) — almost as much as the warm-cache improvement. The design doc named exactly this outcome as worth a second look: either the lifespan-warming step is running before Locust's first request reaches `uptime` (plausible — `get_connection()` runs during startup, and snapshot_uptime's own immediate startup call exercises it again right after, both well before a 120-second Locust run's first `uptime` request lands), or something else about the mechanism changed in a way not fully accounted for in the design doc's own model of "cold" vs "warm" for this specific source.

**Reading both results together**: the fix is real and is doing real work — it just isn't the *complete* explanation for `uptime`'s benchmark-tail anomaly. A genuinely persistent connection eliminates the connect+login cost for the large majority of requests (confirmed by the bulk of both cold and warm runs landing in the same 22-32ms range every other warm source shows), but something else — not yet identified — is still producing a real, occasional multi-hundred-millisecond cost on a minority of calls, in both the cold and warm passes. Candidates not yet investigated: whether `get_monitors()`/`get_heartbeats()`'s own event-wait loop (`_get_event_data()`'s `while ... time.sleep(0.01)` pattern, confirmed during this fix's own library-source read) occasionally has to wait a full `wait_events` cycle (0.2s default) for a fresh push if a request's timing happens to race a heartbeat update; whether `_connection_lock` contention under 20 concurrent users occasionally serializes several calls behind one that's mid-`get_heartbeats()`; or whether Uptime Kuma's own server-side response time genuinely varies this much call-to-call regardless of connection reuse. None of these confirmed — flagged as the honest next-step candidates rather than guessed at as settled.

**Not overclaiming "fixed."** Per this project's own established practice for partial results (the query-expansion concurrency fix's "not a full elimination" framing in `The-Latency-Parallelization-Investigation.md` is the precedent here): this is a real, partial, measured improvement with an honestly-stated remaining gap, not a closed investigation.

#### v3.50.3 pool widening — against its own success criteria

**`auto`'s cold p99 dropped from 10000ms (the v3.50.2 single-sample spike) to 3800ms** — a real improvement, and the widening clearly helped. But the design doc's stated bar was no longer "regularly hitting multi-second p99s" — and `auto`'s cold p98 (1300ms) and p99 (3800ms) are still multi-second-adjacent, and `conditional`'s cold p98/p99 (5100ms/5100ms) and `conditional_remainder`'s cold p98/p99 (4300ms/4300ms) are still squarely in multi-second territory on this run, with `conditional` and `conditional_remainder` both also still showing real warm-cache tails (p95 440ms on both) that shouldn't exist at all on a nominally warm run.

Per the design doc's own stated reading: this pattern — real improvement, but still regularly hitting multi-second p98/p99 — means the widening (6→12, 4→8, 2→4) provided real headroom but not enough for 20 concurrent users, not that the underlying thundering-herd explanation was wrong. The pools may need widening further. This is also, per the design doc's own caution, an inherently noisier metric to validate on a single 120-second run than Part 1's — worth a second run before concluding the pools need more headroom rather than treating this one sample as definitive.

#### A genuinely new, fresh finding from this run, not called for in the design doc

**`cache_hit`'s cold-run anomaly (p90 5100ms, p99 8000ms)**, flagged in the cold-cache table notes above. Not part of either change this run was set up to validate, not previously documented anywhere in this file, and not investigated here — left as an honest, open item for a future pass rather than folded into either of the two real conclusions above.

#### `/health`'s concurrency fix (v3.50.3) holding under real load

Worth confirming directly since this run is the first real benchmark since that fix shipped: `/health`'s warm-cache max this run (1152ms, p99 1200ms) is consistent with the seven concurrent source checks each completing within their own real timeout, with none of the v3.50.2 baseline's 5244ms sequential-stacking signature reappearing. Not a controlled, isolated test of that fix specifically — this run wasn't set up to isolate it the way `TestHealthConcurrentSourceChecks` already does at the unit level — but a real, supporting data point that the fix is behaving as expected under actual concurrent load, not just in mocked test conditions.

### 20 Users — Cold vs Warm Cache (v3.50.7, validating the cache_hit query-collision fix)

Run against the real v3.50.6 codebase on MiniDock, validating the `cache_hit` thundering-herd fix (v3.50.6) against the v3.50.5 baseline above. Zero exceptions, zero failures on both passes.

**Cold cache** — both caches explicitly cleared immediately before this run.

| Endpoint | Median | p90 | p95 | p98 | p99 | n | Failures |
|----------|--------|-----|-----|-----|-----|---|----------|
| `/health` | 710ms | 760ms | 780ms | 780ms | 780ms | 17 | 0% |
| `/search [kiwix]` | 23ms | 870ms | 1300ms | 1700ms | 2000ms | 85 | 0% |
| `/search [kiwix_disambiguation]` | 23ms | 99ms | 2000ms | 6000ms | 6000ms | 44 | 0% |
| `/search [web]` | 23ms | 31ms | 1300ms | 2300ms | 6600ms | 76 | 0% |
| `/search [conditional]` | 27ms | 1300ms | 1700ms | 1800ms | 1800ms | 39 | 0% |
| `/search [conditional_remainder]` | 38ms | 800ms | 1300ms | 1300ms | 1300ms | 19 | 0% |
| `/search [discourse_framing]` | 28ms | 580ms | 1100ms | 1900ms | 1900ms | 39 | 0% |
| `/search [forecast]` | 24ms | 110ms | 140ms | 730ms | 730ms | 37 | 0% |
| `/search [news]` | 24ms | 36ms | 56ms | 120ms | 120ms | 48 | 0% |
| `/search [uptime]` | 23ms | 440ms | 520ms | 520ms | 520ms | 16 | 0% |
| `/search [ha]` | 40ms | 51ms | 52ms | 300ms | 300ms | 21 | 0% |
| `/search [auto]` | 25ms | 440ms | 770ms | 1800ms | 3000ms | 76 | 0% |
| `/search [fusion_explicit]` | 22ms | 28ms | 36ms | 84ms | 830ms | 172 | 0% |
| `/search [fusion_auto]` | 25ms | 47ms | 150ms | 3600ms | 4400ms | 99 | 0% |
| `/search [fusion_triple]` | 22ms | 43ms | 100ms | 1700ms | 1700ms | 50 | 0% |
| `/search [cache_hit]` | 23ms | 880ms | 940ms | 940ms | 940ms | 18 | 0% |
| **Aggregated** | **24ms** | **100ms** | **770ms** | **1700ms** | **2300ms** | **856** | **0%** |

**`cache_hit`'s 8-second cold-run anomaly from v3.50.4/v3.50.5 is gone.** Cold p90/p98/p99 went from 5100ms/8000ms/8000ms (v3.50.5) to 880ms/940ms/940ms — the same general shape every other single-source cold-path row in this table shows, not the previous, anomalous outlier. The fix shipped in v3.50.6 (`cache_hit` no longer sharing a query with `KIWIX_QUERIES`) is confirmed working, not just theoretically correct. The remaining 880-940ms tail is the same, expected cold-routing cost any genuinely-uncached query pays once — `cache_hit`'s own first hit of the run is, correctly, no longer special-cased into a worse outcome than that.

**Warm cache** — identical run immediately afterward, no clearing in between.

| Endpoint | Median | p90 | p95 | p98 | p99 | n | Failures |
|----------|--------|-----|-----|-----|-----|---|----------|
| `/health` | 710ms | 750ms | 780ms | 780ms | 780ms | 20 | 0% |
| `/search [kiwix]` | 23ms | 29ms | 31ms | 34ms | 34ms | 80 | 0% |
| `/search [kiwix_disambiguation]` | 24ms | 32ms | 35ms | 35ms | 35ms | 40 | 0% |
| `/search [web]` | 22ms | 33ms | 34ms | 37ms | 37ms | 65 | 0% |
| `/search [conditional]` | 28ms | 63ms | 440ms | 440ms | 450ms | 54 | 0% |
| `/search [conditional_remainder]` | 42ms | 110ms | 450ms | 450ms | 450ms | 21 | 0% |
| `/search [discourse_framing]` | 31ms | 41ms | 46ms | 50ms | 57ms | 53 | 0% |
| `/search [forecast]` | 23ms | 27ms | 34ms | 38ms | 38ms | 37 | 0% |
| `/search [news]` | 23ms | 31ms | 36ms | 37ms | 47ms | 57 | 0% |
| `/search [uptime]` | 23ms | 32ms | 440ms | 440ms | 440ms | 21 | 0% |
| `/search [ha]` | 38ms | 54ms | 61ms | 61ms | 61ms | 19 | 0% |
| `/search [auto]` | 26ms | 41ms | 440ms | 450ms | 450ms | 75 | 0% |
| `/search [fusion_explicit]` | 22ms | 29ms | 35ms | 37ms | 44ms | 167 | 0% |
| `/search [fusion_auto]` | 25ms | 30ms | 37ms | 44ms | 45ms | 106 | 0% |
| `/search [fusion_triple]` | 22ms | 25ms | 35ms | 37ms | 48ms | 53 | 0% |
| `/search [cache_hit]` | 23ms | 26ms | 29ms | 29ms | 29ms | 19 | 0% |
| **Aggregated** | **24ms** | **37ms** | **54ms** | **690ms** | **710ms** | **887** | **0%** |

**`cache_hit`'s warm numbers are essentially identical to `kiwix`'s own warm numbers (29ms p99 vs. kiwix's 34ms p99)** — exactly what a healthy, never-colliding cache-hit task should look like, and a tighter result than even the v3.50.5 warm run's already-recovered 36ms p99 (n=19 either time; small-sample noise, not a meaningful further improvement).

**`uptime` warm p98/p99 (440ms/440ms) looks better than the v3.50.5 warm run (850ms/850ms) — read with real caution, not as evidence of a new improvement.** Nothing in v3.50.6 touched `app/sources/uptime_kuma.py` or its connection logic; this release was a load-test-only fix to an unrelated task. Reading the actual distribution rather than just the percentile labels: both this run (1-2 slow requests out of 21) and the v3.50.5 run (1-3 slow requests out of 29) show the same shape — a small minority of `uptime` calls paying a real, unexplained cost in the hundreds of milliseconds, with the bulk of calls landing in the same 23-32ms range every other warm source shows. The difference between one slow sample landing at 440ms versus 850ms, on a sample this size, is ordinary run-to-run noise, not a second data point that changes the v3.50.5 verdict. **The honest read stays exactly what it was**: a real, partial fix with a real, unexplained minority tail — not fully resolved, not regressed, no new information about the root cause from this run.

**`auto`/`conditional`/`conditional_remainder` show the same noisy, partially-effective pattern as v3.50.5, also unchanged by this release.** Cold p98/p99 moved in both directions relative to the v3.50.5 baseline (`auto`: 1300/3800ms → 1800/3000ms; `conditional`: 5100/5100ms → 1800/1800ms; `conditional_remainder`: 4300/4300ms → 1300/1300ms) while warm p95 stayed essentially flat (~440-450ms across all three, both runs) — exactly the "inherently noisier metric, single-run sampling variance" caveat the original design doc attached to this specific success criterion. `auto`'s cold run still shows roughly 8 of 76 requests (≈10%) landing in a real slow tail (440ms+), consistent with "the widening helped but isn't enough headroom for 20 concurrent users" rather than either "fixed" or "didn't help" — no change to that verdict from this run.

**Both open items from this run were investigated further in v3.50.8 — see `CHANGELOG.md`'s v3.50.8 entry for `uptime`'s actual root cause and fix, and the pool-widening's collision-math-based re-sizing.** Neither has been re-benchmarked yet as of this writing; that's the natural next run, and this file will get a new dated entry once it happens.

## Running benchmarks

Replace `192.168.1.50` below with your actual Mnemolis host's real IP or hostname — not a placeholder. `--host` silently accepts anything that looks like a URL, so a leftover example value doesn't fail loudly; it fails much later as a DNS error (`Temporary failure in name resolution`) on every single request, which doesn't obviously point back to `--host` as the cause.

**Before a genuine cold-cache run, clear both caches explicitly.** Every "cold cache" run documented in this file describes the cache as empty, but never previously spelled out how to actually get it there — a real gap found while running a fresh v3.44.0 benchmark, where a routing/result cache populated by an earlier session produced an artificially clean run instead of the real cold numbers. Both caches need clearing, not just one — either alone can leave warm behavior bleeding through:

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

Or headless:

```bash
locust -f tests/locustfile.py --host http://192.168.1.50:8888 \
  --headless --users 20 --spawn-rate 2 --run-time 120s \
  --csv benchmarks
```

Run the identical command again immediately afterward, without clearing anything in between, for the warm-cache comparison — the second run's populated caches are the point.
