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

### 20 Users — Hot Cache

Fully warm routing cache. All repeated queries bypassing LLM entirely.

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

## Summary

| Scenario | Median | p95 | Notes |
|----------|--------|-----|-------|
| Cache hit (any source) | 15ms | 18ms | Routing + result cache both warm |
| Cold LLM routing call | ~1000ms | ~6400ms | First time seeing a query |
| Kiwix warm | 15ms | 18ms | LLM book selection cached |
| Fusion 2-source warm | 14ms | 19ms | Concurrent sources, both cached |
| Fusion 3-source warm | 14ms | 17ms | No overhead over 2-source when cached |
| HA entity query | 34ms | 41ms | Live HA API call, no result cache |
| Uptime cold | ~1000ms | ~1100ms | Socket.IO connect overhead |
| 20 concurrent users | 15ms | 41ms | 0% failure rate |

## Key findings

**The routing cache is the performance multiplier.** Cold queries that call Ollama for routing decisions take 1-6 seconds. Warm queries return in 15ms regardless of source complexity. After a few minutes of real usage the cache is warm and the system operates at sub-20ms for nearly all queries.

**Fusion does not add meaningful latency when cached.** A 3-source fusion query with a warm cache returns in 14ms — the same as a single source. Concurrent execution means fusion is essentially free once the routing decision is cached.

**HA is the only always-live source.** Home Assistant entity queries bypass the result cache (30 second TTL) and always make a live API call. 34ms median is fast enough for voice assistant use.

**Uptime Kuma has consistent Socket.IO overhead.** ~1000ms for cold connects, but the result is cached for 60 seconds so real-world impact is minimal.

**Zero failures at 20 concurrent users.** The system does not drop requests under homelab load.

## Hardware context

These benchmarks reflect a homelab deployment — not a production cloud environment. Results will vary based on:
- LLM hardware (RTX 4090 in this case — faster GPU = lower cold routing latency)
- Network latency to HA, Uptime Kuma, and other sources
- Kiwix ZIM file size and disk I/O speed
- Routing cache warmth

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
