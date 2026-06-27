# Benchmarks

What the real performance numbers show, as of the most recent benchmark run (v3.50.9). Full raw tables for every benchmarked release live in `BENCHMARKS.md` in the repo root. For the chronological story behind these numbers — what was found, what got tried, and a real mistake one re-benchmark caught — see [The Benchmark Investigation Log](The-Benchmark-Investigation-Log) instead; this page is current-state reference, not history.

## The one number that's stayed constant since v3.5.0

**Aggregated median latency: ~24ms.** Every feature and fix added since then — confidence-aware fusion, Kiwix disambiguation, multi-book fusion, conditional query detection, the discourse-framing fix, an entire battle-testing campaign, a full bulletproofing pass, Adversarial Self-Testing, Cross-Source Temporal Pattern Detection, the latency-parallelization work, and the connection/caching fixes covered in the investigation log — has added real cost only to the *specific query shapes* that trigger it, never to the steady-state majority of traffic. A plain `"what is nitrogen"` query costs the same today as it did roughly 35 releases ago.

This is by design, not luck: every conditionally-triggered feature (disambiguation only fires for genuinely ambiguous single-word terms, conditional detection only fires for leading `"if X, Y"` phrasing) and every correctness fix in this project's history (word-boundary matching instead of substring search, a capped retry loop instead of an unbounded one) changed *what gets computed*, not *how much gets computed on every request* — confirmed directly across many benchmark runs, not just assumed.

## Cold cache vs. warm cache — the real shape of the cost

The expensive part of almost every feature here is a **first-time** LLM call: picking which Kiwix book to search, generating disambiguation candidates, choosing a routing decision for an ambiguous phrase. Once that decision is cached (see [Caching](Caching)), every subsequent identical query skips it entirely. Representative measured improvements from the v3.50.9 run:

| Query type | Cold (p98) | Warm (p98) | Improvement |
|------------|-----------|-----------|-------------|
| Web search | 2500ms | 36ms | ~70x |
| Kiwix disambiguation | 2900ms | 35ms | ~83x |
| Discourse-framing | 3100ms | 54ms | ~57x |
| `uptime` | 190ms | 69ms | ~3x |

This is the single most important pattern in every benchmark this project has run: a feature's cold-path tail latency can look alarming in isolation, but if it collapses this dramatically on cache hit, the real-world cost is "pays once per unique query, ever, within the cache TTL" — not "pays this every time." `uptime`'s own cold/warm numbers are both already low (190ms/69ms) since there's no LLM call involved at all; its own history of getting there is its own story, covered in the investigation log.

A few query types — `auto`, `conditional`, and `conditional_remainder` — have a real, known additional cost under synthetic concurrent load specifically: 20 simulated users picking from a finite query pool can occasionally collide on the same not-yet-cached query, each paying the cold-routing cost concurrently rather than one paying it and the rest hitting cache. This doesn't happen in real single-household usage (you're not asking about the same back-door lock from 20 places at once), and it's an active, ongoing area of tuning in `tests/locustfile.py` rather than a Mnemolis correctness issue — see the investigation log for the current state of that tuning.

## Hardware context

These are homelab numbers, not a controlled cloud benchmark — they'll vary with your own LLM hardware (faster GPU genuinely means lower cold routing latency), network latency to HA/Uptime Kuma/your other sources, Kiwix ZIM file size and disk I/O speed, and how warm the routing cache happens to be at the moment you run them. Treat the *relative* patterns here (cold vs. warm, which features cost what) as the generalizable part, and the absolute millisecond figures as specific to one particular homelab's hardware (MiniDock: Intel N100, 16GB RAM; Ollama on a separate host: i9-14900KF, RTX 4090).

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
