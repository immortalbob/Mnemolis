# Sources

Every query Mnemolis answers eventually resolves to one or more of these seven sources. Each is a real, independent backend — there's no shared internal "knowledge base," just a router deciding which external service (or combination) is most likely to have the answer.

| Source | Backend | What it actually does |
|--------|---------|------------------------|
| `kiwix` | [Kiwix](https://www.kiwix.org/) | Offline encyclopedic/technical knowledge — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs, whatever ZIMs you've loaded |
| `forecast` | [Open-Meteo](https://open-meteo.com/) | 3-day weather forecast for your configured home coordinates |
| `news` | [FreshRSS](https://freshrss.github.io/FreshRSS/) | Your own RSS feeds, fetched via the GReader API |
| `web` | [SearXNG](https://searxng.github.io/searxng/) | Live web search through your self-hosted SearXNG instance |
| `uptime` | [Uptime Kuma](https://uptime.kuma.pet/) | Status of whatever you're monitoring — reports down services, or confirms everything's up |
| `ha` | [Home Assistant](https://www.home-assistant.io/) | Entity state summaries — lights, locks, sensors, motion, batteries, power |
| `changes` | Snapshot Engine (internal) | "What changed since X" — diffs across the four sources above that get periodically snapshotted |

Two more values exist but aren't really sources in their own right:

- **`fusion`** — not a backend, a *mode*. Tells the router to query several real sources concurrently and merge the results. See [Fusion](Fusion).
- **`auto`** — also not a backend. Tells the router to figure out which source(s) actually apply. See [Routing](Routing).

---

## `kiwix` — Offline Knowledge

Backed by a Kiwix server holding whatever ZIM files you've loaded — typically full Wikipedia plus a set of Stack Exchange communities relevant to homelab/technical work (Unix, Raspberry Pi, electronics, etc.), iFixit repair guides, FreeCodeCamp, and DevDocs reference material.

This is the most architecturally complex source by far — it dynamically discovers what books exist from your actual Kiwix catalog, has its own LLM-assisted book selection, multi-candidate disambiguation for ambiguous bare words, and multi-book fusion when a question genuinely spans more than one ZIM. See [Kiwix Catalog & Article Fetching](Kiwix-Catalog-and-Article-Fetching), [Kiwix Disambiguation](Kiwix-Disambiguation), [Kiwix Scoring](Kiwix-Scoring), and [Multi-Book Fusion](Multi-Book-Fusion) for the full mechanics.

**Falls back to `web`** if it returns nothing usable — see [Routing](Routing) for exactly what counts as "nothing usable."

## `forecast` — Weather

A thin wrapper around the Open-Meteo API, configured once with your home coordinates (`FORECAST_LATITUDE`, `FORECAST_LONGITUDE`). Returns a 3-day outlook — today, tomorrow, the day after — with high/low temps, wind, and sunrise/sunset, prefixed with your configured location name so a fused response can't be mistaken for weather somewhere else.

This is the only source that makes a network call outside your own infrastructure (Open-Meteo's public API) — see [About Mnemolis](About) for the local-first philosophy this is the one deliberate exception to.

## `news` — Your RSS Feeds

Reads from FreshRSS via its GReader-compatible API. This is genuinely *your* feeds, not a generic news search — if you haven't subscribed to something in FreshRSS, Mnemolis can't surface it here. For headline-style queries ("latest news", "what's happening") it returns your feed unfiltered, newest first. For queries with an actual topic, results are scored against the query the same way `web` results are — see [Confidence-Aware Fusion](Confidence-Aware-Fusion).

**Falls back to `web`** if scoring rejects every article in your feed as irrelevant to the actual query.

## `web` — Live Search

A SearXNG instance you run yourself, queried via its JSON API. For longer queries (3+ words), Mnemolis also tries one LLM-generated alternate phrasing and merges both result sets — see [Query Expansion](Query-Expansion). Every result is scored for relevance before being returned; SearXNG's own ranking isn't trusted blindly.

If you're seeing `"Error reaching SearXNG: connection failed"`, check [The SearXNG Timeout Lesson](The-SearXNG-Timeout-Lesson) before assuming it's a Mnemolis bug — it usually isn't.

## `uptime` — Service Monitoring

Connects to Uptime Kuma over its Socket.IO interface and reports a simple summary: either "all N services are up" or a list of what's currently down, pending, in maintenance, or — a real, distinct category — has no heartbeat data at all yet (a brand-new monitor, or one whose check interval hasn't fired since Uptime Kuma's own restart).

The Socket.IO connection itself is persistent, established once and reused across every call rather than reconnected fresh each time — kept alive for the app's full lifetime via `app/main.py`'s `lifespan()`, the same way the snapshot scheduler and MCP session manager are. A dead or stale connection (an Uptime Kuma restart, a dropped socket) is detected and transparently replaced on the next call, rather than silently reused in a broken state. See [Caching](Caching#why-uptimes-connection-is-persistent-even-though-its-cache-ttl-stays-short) for why this is a connection-level fix, not a caching one.

This is one of three sources with a genuinely binary, structured signal (up/down) — which matters specifically for [Conditional Query Detection](Conditional-Query-Detection)'s yes/no verdicts.

## `ha` — Home Assistant

Reads entity states directly from your Home Assistant instance via its REST API. Unlike the other sources, `ha` answers a category of question HA's own built-in voice assistant doesn't handle well — analytical, multi-entity summaries rather than single-device commands:

- "house status summary" — lights, locks, sensors, motion, batteries, all at once
- "indoor air quality" — CO2, temperature, humidity from indoor sensors specifically
- "security status" — locks, doors, recent motion with relative time ("2 hours ago")
- "battery status" — every device's battery level in one summary
- "outdoor conditions" — weather station sensors, distinct from `forecast`'s predictive data
- "how much power am I using" — current and historical consumption

Full setup details (generating a long-lived access token, the exact config vars) are in [Home Assistant Integration](Home-Assistant-Integration).

`ha`'s lock/unlock and door open/closed states are the other two structured, binary signals conditional detection can give a confident yes/no verdict against.

**If "is the front door locked" or similar specific entity questions ever returned "no matching entities found" even though the entity clearly existed, that's fixed now** — see [Home Assistant Integration](Home-Assistant-Integration#development-notes) for what happened and what to check if anything still looks wrong.

## `changes` — What's Different Since X

Not a live backend at all — it reads from Mnemolis's own snapshot history, captured every 2–60 minutes (interval varies per source) by a background scheduler, and diffs the most recent snapshot against an earlier one to report what actually changed: outages and recoveries, meaningful weather shifts, new headlines, lock/door/battery state changes.

Time-window phrases ("this morning," "while I was at work") resolve to a specific hour window using your configured `MORNING_START_HOUR` and `WORK_START_HOUR`. Full mechanics, including why outage/weather changes are collapsed to net change while news/HA events are reported individually, live in [Snapshot Engine & Changes](Snapshot-Engine-and-Changes).

---

## How a query reaches one of these

None of this matters until something decides *which* source(s) a given query should go to. That decision — keyword matching, LLM-assisted selection, and a few real, hard-won exceptions — is covered in full in [Routing](Routing).

---

## Development Notes

- **`uptime`'s "no heartbeat data" case used to be silently misreported as "in maintenance"** — a specific, false claim about a deliberately-configured state the monitor was never actually in. Found via a deliberate complexity-investigation pass and fixed by giving it its own honest label.
- **`uptime` used to open a brand-new Socket.IO connection, log in, and disconnect on every single call** — including every 2-minute `snapshot_uptime()` scheduler tick, independent of whether anyone was ever asking a live `uptime` question. This was a real, confirmed contributor to a warm-cache latency tail that had reproduced across three separate benchmark releases (v3.17.0, v3.44.0, v3.50.2). Fixed by making the connection persistent, reused across calls, with the underlying library's own confirmed liveness check (`sio.connected`, not a `.connected` property `UptimeKumaApi` doesn't actually have) used to detect and transparently replace a dead connection rather than reuse it silently broken. The v3.50.4 re-benchmark confirmed this is a real, substantial improvement (warm p95 1500ms → 470ms) but not a complete one — a real, smaller tail remains, with no confirmed root cause yet. See [Benchmarks](Benchmarks#a-partially-confirmed-fix-a-partially-effective-mitigation-and-one-fix-confirmed-holding-under-load) for the honest, measured result, and [Caching](Caching#why-uptimes-connection-is-persistent-even-though-its-cache-ttl-stays-short) for why this was a connection-level fix rather than a `CACHE_TTL_UPTIME_SECONDS` change.
- **Nearly every natural phrasing of a general news request used to be misclassified as a specific-topic query.** The check for "is this a headline-style request, or does it have an actual topic" only recognized formal grammatical filler as stop words, never the common request verbs people actually use out loud — a direct test against 9 realistic phrasings (`"tell me the news"`, `"give me the headlines"`, `"show me my feeds"`, and others) found all 9 failing, each one scored against literal words like "tell" or "give" instead of cleanly returning the unfiltered feed. Fixed by expanding the stop-word set to include common request verbs and modifiers.
