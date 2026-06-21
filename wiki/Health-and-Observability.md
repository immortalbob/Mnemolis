# Health & Observability

Three real gaps got found and closed during a deliberate operational-maturity review — not in response to a reported failure, but because the question "would I actually know if this silently broke?" turned out to have an honest "no" for several things. This page covers what `/health` and `/logs/stats` report today, and the reasoning behind each piece.

## `/health` — is everything actually reachable right now

```json
{
  "status": "ok",
  "kiwix_books_loaded": 27,
  "cache_entries": 12,
  "cache_max_size": 500,
  "routing_cache_entries": 340,
  "routing_cache_max_size": 1000,
  "snapshot_jobs": { "...": "see below" },
  "sources": { "...": "see below" }
}
```

**`sources`** runs a real, live check against every configured backend — not a check that a config value is merely present. `_check_kiwix()` actually queries the catalog; `_check_llm()` actually pings the configured model endpoint; `_check_web()` actually hits SearXNG. This is genuinely useful precisely because it catches real, current failures: a SearXNG instance whose request timeout is set too aggressively, or a Docker network that two containers have silently fallen off of, both show up here as `"status": "error"` with the actual underlying error message attached — not after search results degrade and someone has to debug it by hand, but immediately, the next time anyone checks.

**`cache_entries` / `cache_max_size` / `routing_cache_entries` / `routing_cache_max_size`** — see [Caching](Caching) for what these caches actually do. The pairing of current count against configured max exists specifically so growth toward either bound is visible at a glance, without needing to dig through logs or read code to even know a bound exists.

**`snapshot_jobs`** — see below.

## Background job health

Every [snapshot job](Snapshot-Engine-and-Changes) already caught its own exceptions internally and just logged a warning on failure. That's good defensive coding for "don't crash the scheduler" — but it also meant a job that started failing on *every single run* would produce zero externally visible signal beyond a log line nobody was necessarily watching. The scheduler object itself had no external visibility either; it's a local variable inside `main.py`'s startup code, never exposed to any endpoint.

`get_snapshot_job_health()` closes this using data that already existed — every snapshot is timestamped and stored, so there was no need for new instrumentation, just a query comparing "when did this job last actually succeed" against "how often is it supposed to run":

```json
{
  "uptime":   {"status": "ok", "last_snapshot": "...", "minutes_since_last_snapshot": 1.3, "expected_interval_minutes": 2},
  "forecast": {"status": "ok", "...": "..."},
  "news":     {"status": "stale", "minutes_since_last_snapshot": 240.0, "expected_interval_minutes": 60},
  "ha":       {"status": "never_ran", "expected_interval_minutes": 5}
}
```

Four possible states: **`ok`** (recent enough), **`stale`** (more than 3x its expected interval since the last success — a generous grace window meant to absorb normal jitter without false-alarming on a slightly delayed scheduler tick), **`never_ran`** (zero snapshots ever stored for this source), and **`unknown`** (a corrupted or unparseable timestamp — degrades gracefully rather than raising an exception that would take down the rest of `/health` over one bad row).

## `/logs/stats` — fallback visibility

[Routing](Routing#fallback-when-a-source-comes-back-empty) falls back from `kiwix` or `news` to `web` when a result looks empty. Whether that happened on any given query used to be completely invisible outside of reading raw logs — `source_used` correctly reported the *actual* source after a fallback, but nothing recorded *that a fallback occurred at all*.

```json
{
  "fallback_count": 4,
  "fallback_rate_pct": 2.1,
  "fallback_by_target": {
    "kiwix_or_news_fallback_to_web": 4
  }
}
```

This is tracked with a single boolean column (`fallback_occurred`) on the query log, computed by comparing the source that was originally intended (from `detect_intent()` for `auto` requests, or the explicit request source otherwise) against the source that actually ended up answering. Deliberately **not** done by changing `route_with_source()`'s own return signature — that function recurses into itself at four internal call sites for [conditional detection](Conditional-Query-Detection)'s condition and remainder handling, so widening its return type would have meant touching every one of those, a much larger and riskier change than a post-hoc comparison needed to be.

**Why the breakdown says `kiwix_or_news_fallback_to_web`, not separate counts for each** — `kiwix` and `news` both fall back to the same target, `web`. A single boolean genuinely cannot tell you which of the two originally intended sources triggered any specific fallback; querying per-original-source would run the identical SQL query under two different labels and double-count the same underlying rows. Reporting an honest, combined label is the correct tradeoff here — it's less granular than attributing fallbacks to a specific source, but it's *true*, which a confidently-wrong per-source breakdown would not have been.

## `/logs` — the raw query log

`GET /logs?limit=N` returns recent entries directly: timestamp, query, source requested, source used, cached flag, success, latency in milliseconds, and `fallback_occurred`. Useful for spot-checking a specific recent query's behavior without waiting for it to show up in aggregate stats.

## A real example of this paying off immediately

Within minutes of `/health` first reporting `snapshot_jobs` and the cache fields, it also caught something unrelated and already real: a SearXNG `request_timeout` setting that had genuinely been edited correctly in the config file, but never took effect because the container running SearXNG had never actually been restarted since the edit. `/health` reported `web: error, Read timed out (read timeout=3)` — the literal old value, live, immediately — rather than that mismatch surfacing only the next time someone happened to notice degraded search results and traced it back by hand.
