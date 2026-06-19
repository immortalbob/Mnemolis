# Changelog

All notable changes to MiniSearch are documented here.

---

## [3.6.1]

### Added
- **HA Snapshot Engine (Phase 2)** — `snapshot_ha()` captures raw entity states from `/api/states` every 5 minutes, filtered to locks, door/motion/window binary sensors, and battery sensors
- **`_diff_ha()`** — detects lock state changes, door open/closed transitions, and battery levels crossing below 20%. Lights and switches intentionally excluded — too noisy for a "what changed" summary.
- **`tests/test_snapshots.py::TestDiffHA`** — 12 new tests covering lock changes, door changes, battery threshold crossing, light exclusion, new entity handling, malformed JSON, and multiple simultaneous changes
- **WAL mode + busy timeout** — all SQLite connections (`query_log.db`, `snapshots.db`) now use `PRAGMA journal_mode=WAL` and a 10-second busy timeout via a shared `_connect()` helper, reducing lock contention between the snapshot scheduler and concurrent search requests
- **Architecture diagrams updated** — Voice Assistant Flow and Multi-Client Architecture now show the Snapshot Engine and decomposition routing path. New **Snapshot Engine** diagram added showing scheduler → storage → diff → `/changes` flow

### Fixed
- **HA snapshot noise filter** — initial implementation captured all `binary_sensor` domain entities regardless of device class, pulling in irrelevant entities (kiosk browser toggles, dark mode switches). Narrowed to `device_class in (door, motion, window, opening)` only.

### Changed
- Version bumped to 3.6.1
- `/snapshots/trigger` now includes HA in manually triggered snapshots
- Scheduler now runs 4 jobs: uptime (2 min), forecast (30 min), news (60 min), HA (5 min)

**Total test count: 366**

See `BENCHMARKS.md` for updated load test results — WAL mode fix verified, 0 connection errors, p95/p99 within v3.5.0 range despite added scheduler load.

---

## [3.6.0]

### Added
- **Snapshot Engine** — `app/snapshots.py` — periodic background snapshots of Uptime Kuma, Open-Meteo, and FreshRSS stored to SQLite at `/app/data/snapshots.db`
- **APScheduler** — background scheduler starts on container startup, takes snapshots every 2 minutes (uptime), 30 minutes (forecast), 60 minutes (news)
- **Diff engine** — detects meaningful changes between consecutive snapshots:
  - `_diff_uptime()` — service outages and recoveries
  - `_diff_forecast()` — high/low temp changes ≥5°, precipitation appearing or disappearing
  - `_diff_news()` — new article headlines, capped at 5 per diff, deduplication across walk
- **`GET /changes?hours=N`** — returns detected changes across all snapshot sources within the last N hours (default 24)
- **`POST /snapshots/trigger`** — manually trigger all snapshot jobs immediately
- **`source="changes"`** — routes "what changed today", "any new outages", "what happened today" etc. to the snapshot diff engine automatically via keyword detection
- **Immediate startup snapshots** — all three sources snapshot on container startup so `/changes` has data immediately
- **`apscheduler`** added to `requirements.txt`
- **`tests/test_snapshots.py`** — 30 new tests across 5 classes covering `_diff_uptime`, `_diff_forecast`, `_diff_news`, and `format_changes`

### Changed
- `INTENT_MAP` — `changes` source added with 14 trigger keywords
- `SOURCE_MAP` — `changes` source registered
- `SOURCE_DESCRIPTIONS` — `changes` described for LLM routing
- `CACHE_TTL` — `changes` cached for 2 minutes
- Version bumped to 3.6.0

### Known limitations (Phase 1)
- HA entity-level snapshots not yet implemented — "what changed in the house" is Phase 2
- Snapshot diffs are text-based — no semantic understanding of magnitude beyond threshold rules

**Total test count: 354**

---

## [3.5.3]

### Added
- **Missing tests across all source modules** — comprehensive coverage audit followed by additions to six test files:
  - `test_forecast.py` — `TestDegreesToCardinal` (6 tests), `TestFmtTime` (4 tests)
  - `test_uptime_kuma.py` — `TestGetStatusFromHeartbeats` (6 tests)
  - `test_fusion.py` — `TestLooksEmpty` (8 tests)
  - `test_home_assistant.py` — `TestHAHelperFunctions` (9 tests), `TestBuildFilter` (5 tests)
  - `test_freshrss.py` — `TestGetToken` (4 tests)
  - `test_router.py` — `TestLlmPickFusionSources` (5 tests)

### Changed
- Version bumped to 3.5.3

**Total test count: 331**

---

## [3.5.2]

### Added
- **`GET /logs/stats`** — query log statistics endpoint surfacing Time To First Knowledge (TTFK), cache hit rate, success rate, average latency by source, top 10 most-asked queries, unique query count, and learned query count
- **`POST /logs/clear`** — restored missing endpoint for clearing query log entries
- **`tests/test_main.py`** — 27 new tests covering all FastAPI endpoints: `/health`, `/sources`, `/cache`, `/cache/routing`, `/logs`, `/logs/stats`

### Changed
- Version bumped to 3.5.2

**Total test count: 284**

---

## [3.5.1]

### Changed
- **Public readiness scrub** — removed personal location data from example files
- `docker-compose.example.yml` — forecast coordinates, location name, and timezone blanked with placeholder comments
- `app/config.py` — default coordinates set to `0.0`, location name blank, timezone defaulting to `UTC`
- `tests/locustfile.py` — personal IP replaced with `your-host`
- `README.md` — forecast defaults shown as blank, example HA IP neutralized
- **`LICENSE`** added — MIT license
- README updated with license section

---

## [3.5.0]

### Added
- **Query decomposition** — `source="auto"` queries are now split on conjunction words (`and`, `also`, `plus`, `as well as`, `in addition`) into independent sub-queries, each routed and executed separately. "What is the weather and are my services up" becomes two independent queries — one to `forecast`, one to `uptime` — merged with source attribution headers.
- **`_decompose()`** in `router.py` — conjunction splitting with nosplit guard for comparison queries ("compare Python and Rust"), location pairs ("weather in Phoenix and Kingman"), and country names ("Iran and Israel"). Requires sub-queries to contain at least one intent word or known source trigger noun to be considered a valid standalone query.
- **Smart fusion — same-source merging** — consecutive results from the same source are merged under a single `[SOURCE]` header. "Indoor air quality and are the doors locked" now returns one `[HA]` block, not two.
- **Smart fusion — result truncation** — each source result is capped at 1500 characters before merging, cutting at a clean newline boundary. Prevents one verbose source from dominating the merged output.
- **Smart fusion — deduplication** — sentence-level overlap detection drops sources whose content is 60%+ duplicated in another source's result. Handles cases where news and web return the same story.
- **`_truncate()`**, **`_deduplicate()`**, **`_merge_same_source()`** added to `fusion.py`
- **Query decomposition diagram** added to README
- **14 new decomposition tests** in `test_router.py` — `TestDecompose` class covering conjunction splitting, nosplit patterns, triple splits, area-based queries, and explicit source bypass
- **15 new fusion tests** — `TestFusionTruncate`, `TestFusionDeduplicate`, `TestFusionMergeSameSource`

### Changed
- Version bumped to 3.5.0
- `test_router.py` — `TestAutoFusionEscalation` updated to reflect decomposition behavior replacing direct fusion for multi-topic auto queries

**Total test count: 257**

See `BENCHMARKS.md` for updated load test results — p95 improved from 41ms → 36ms, p99 from 1000ms → 780ms at 20 concurrent users. Query decomposition adds no measurable overhead.

---

## [3.4.5]

### Added
- **`tests/locustfile.py`** — Locust load testing suite with two user classes: `MnemolisSingleSourceUser` (all 7 sources with realistic task weights) and `MnemolisFusionUser` (explicit 2-source, LLM auto-selection, and triple source fusion)
- **`BENCHMARKS.md`** — documented load test results at 5, 10, and 20 concurrent users. 15ms median at 20 users, 0 failures across 391 requests, fusion 3-source at 14ms warm cache
- **`.dockerignore`** — excludes `__pycache__`, `.pyc`, and `.pyo` files from Docker builds, preventing stale bytecode from being baked into the image

### Changed
- Kiwix search terms now stemmed after stop word removal — "galaxies" → "galaxy", "batteries" → "battery" — improves Kiwix article matching for plural queries
- Version bumped to 3.4.5

---

## [3.4.0]

### Added
- **HA area awareness** — `source="ha"` now detects room/area names in queries and filters results to entities assigned to that area in Home Assistant. "What lights are in the living room" returns only living room entities. "Temperature in the master bedroom" returns only master bedroom sensors.
- **`_get_area_entities()`** — fetches area → entity mapping from HA's template API using `area_entities()`. Builds a complete room registry on each query.
- **`_detect_area()`** — natural language area detection with alias support. Handles "living room" → `living_room`, "master bedroom" → `master_bedroom`, "outside/outdoors" → `outside`, and all 12 defined areas.
- **`_AREA_ALIASES`** — maps natural language phrases to HA area IDs. Longest match wins — "master bedroom" correctly matches over "bedroom".
- **15 new tests** — `TestAreaDetection` (11 tests) and `TestAreaSearch` (4 tests) covering area detection, longest match, unknown area fallback, state filter with area filter, and keyword fallback when no area detected.

### Changed
- Version bumped to 3.4.0
- README updated — all MiniSearch Intents references updated to Mnemolis Intents with correct GitHub URLs

**Total test count: 230**

---

## [3.3.0]

### Added
- **Source health endpoint** — `GET /health` now returns connectivity status for every configured source: kiwix, forecast, news, web, uptime, ha, and llm. Each check is lightweight — just enough to confirm the service is reachable and configured. LLM check shows model name and API type.
- **Query logging** — SQLite-backed query log at `/app/data/query_log.db`. Every search is logged with timestamp, query text, source requested, source used, cached flag, success flag, and latency in milliseconds.
- `GET /logs?limit=50` — view recent query log entries, newest first
- `POST /logs/clear` — clear all query log entries
- **Kiwix `_is_definitional_query()`** — detects definitional/overview queries ("what is", "what are", "tell me about", "explain", "how does", "history of", etc.) to apply appropriate scoring bonuses
- **Wikipedia scoring bonus** — +8 for definitional queries, +3 for all others. Ensures encyclopedic sources are preferred for overview queries over Q&A threads.
- **List/index article penalty** — -10 for articles whose title starts with "List of", "Lists of", "Index of", "Outline of", "Category:". Prevents navigation pages from winning over content articles.
- **Stemmed word-level title matching** — multi-word queries like "what are galaxies" now correctly match single-word titles like "Galaxy" via per-word stem comparison (+15 bonus)
- **Intent-aware book selection prompt** — LLM book selection prompt now includes a hint about query intent, directing the model to prefer encyclopedic or technical sources appropriately
- **`.dockerignore`** — excludes `__pycache__` and `.pyc` files to prevent stale compiled bytecode from being baked into the image

### Changed
- Version bumped to 3.3.0
- `GET /health` response now includes `sources` dict with per-source status

### Known limitations
- Brand name ambiguity — "galaxies" returns Samsung Galaxy articles because Kiwix's search engine indexes hundreds of Samsung Galaxy phone articles. Scoring correctly prefers the astronomical "Galaxy" article when both are returned, but Kiwix often doesn't surface the main article. Tracked for future improvement via search term disambiguation.
- Generic noun ambiguity — "battery" returns military fortification articles (battery = artillery position). Same root cause.

**Total test count: 215**

---

## [3.2.0]

### Added
- **Home Assistant source module** — `source="ha"` queries HA entity states for analytical summaries that go beyond HA's built-in single-entity intent handling
- `app/sources/home_assistant.py` — keyword-based entity filtering by domain and device class, position-aware phrase matching (longer phrases take priority), deduplication, readable grouped output with time-ago motion events and rounded numeric values
- `HA_URL` and `HA_TOKEN` config vars
- `ha` added to `SOURCE_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL` (30 second TTL), `INTENT_MAP`, MCP tool schema
- 37 new tests in `tests/test_home_assistant.py` covering guards, exclusions, light/lock/environmental/battery/motion queries, and value formatting

### What the HA source handles
Queries HA can't answer natively with its built-in intents:
- **House/security summaries** — "house status", "security status", "are the doors locked"
- **Environmental** — "indoor air quality", "room temperature", "CO2 levels"
- **Outdoor conditions** — "outdoor conditions" (weather station sensors)
- **Battery status** — "battery status", "which devices have low battery"
- **Motion history** — "any recent motion", "security status" with time-ago formatting
- **Power consumption** — "how much power am I using"
- **Auto-fusion** — "house status and what's the weather" automatically fuses `ha` + `forecast`

### Changed
- Version bumped to 3.2.0

**Total test count: 202**

---

## [3.1.0]

### Added
- **Smart auto-fusion escalation** — `source="auto"` now escalates to fusion automatically when a query spans multiple topics. Keyword matching checks all sources before returning, and if multiple sources match, fusion is triggered with those sources — no LLM call needed.
- **LLM fusion escalation** — when no keywords match, the LLM now decides in a single call whether to use one source or multiple. Returns comma-separated source names for complex queries, triggering fusion automatically.
- **Kiwix suffix stemming** — `_stem()` function added to `kiwix.py`. Strips common suffixes (`-s`, `-es`, `-ies`, `-ing`, `-ed`) before scoring so "marsupials" correctly matches "Marsupial", "foxes" matches "Fox", etc. Word-level title and excerpt scoring now uses stemmed terms.
- **Expanded uptime intent triggers** — 15 new trigger phrases added including "my services", "services up/down", "anything down", "everything up/down", "network down/up", "anything offline", "server status", "is it running", "is it up/down", "are they up/down"
- **22 new tests** — `TestKeywordDetectMulti`, `TestNewUptimeTriggers`, `TestAutoFusionEscalation`, `TestStem`, and stemmed scoring tests

### Changed
- `_keyword_detect` now scans all sources before returning — single match returns string, multiple matches return list for fusion escalation
- `_llm_detect` updated with smarter prompt — returns single source or comma-separated list in one call
- `detect_intent` return type updated to `str | list[str]`
- `route()` updated to handle list return from `detect_intent`

**Total test count: 179**

---

## [3.0.0]

### Added
- **Source fusion** — `source="fusion"` queries multiple sources concurrently using `ThreadPoolExecutor`, merges results with source attribution headers, handles partial failures gracefully
- **`app/sources/fusion.py`** — new fusion source module. Validates sources, deduplicates, caps at 4, times out at 15 seconds per source, filters empty/failed results, returns single source directly without headers when only one succeeds
- **LLM fusion source selection** — when `fusion` is used without specifying sources, the LLM picks the best 2-3 sources for the query. Decision cached in routing cache for 1 hour.
- **`fusion_sources` parameter** — optional `list[str]` field on `POST /search` and MCP tool schema. Explicitly specifies which sources to fuse.
- **Fusion cache key** — stable cache key from sorted source list ensures same sources in any order share a cache entry
- **28 new fusion tests** — `tests/test_fusion.py` covering merging, headers, single source passthrough, validation, deduplication, max cap, partial failure, all failure, empty result filtering, and cache behavior
- **Fusion diagram** in README illustrating concurrent source querying and merge

### Changed
- Version bumped to 3.0.0
- FastAPI description updated to mention fusion
- `fusion` added to `SOURCE_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL` (30 min TTL)
- `route()` signature updated to accept `fusion_sources: list[str] | None`
- MCP tool schema updated with `fusion` in source enum and `fusion_sources` array parameter
- Dead import (`from app.config import settings`) removed from `fusion.py`

---

## [2.9.0]

### Added
- `app/llm.py` — unified LLM client supporting both Ollama native API and OpenAI-compatible API (llama-server, LM Studio, etc.)
- `LLM_API_TYPE` config var — set to `"ollama"` (default) or `"openai"` to switch backends
- Routing cache tests — 28 new tests covering all routing cache operations including corruption handling
- Source guards — FreshRSS and SearXNG return clean error messages when not configured

### Changed
- `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_API_TYPE` renamed to `LLM_URL`, `LLM_MODEL`, `LLM_API_TYPE` — better reflects support for any compatible backend
- All LLM calls in `router.py` and `kiwix.py` now route through `llm.py` helper
- `clear_routing_cache` and `load_routing_cache` fixed to use `.clear()` and `.update()` instead of reassignment — prevents stale reference issues

### Fixed
- Routing cache `clear()` and `load()` used dict reassignment instead of mutation, causing external references to see stale data

---

## [2.8.0] — Upcoming

### Added
- Routing cache — source and Kiwix book selection decisions are cached for 1 hour, persisted to disk, eliminating redundant Ollama calls for repeated queries
- `GET /cache/routing` — inspect routing cache entries
- `POST /cache/routing/clear` — clear routing cache
- Source guards — FreshRSS and SearXNG return clean error messages when not configured rather than attempting connection
- API endpoint docstrings — all endpoints now have descriptions visible in `/docs`
- `CHANGELOG.md`

---

## [2.7.0]

### Added
- Test suite — 71 tests covering intent routing, cache logic, Kiwix scoring, search term cleaning, and FreshRSS article filtering
- `pytest` and `pytest.ini` added, tests baked into Docker build

### Fixed
- `_is_general_query` now checks full query string before word-level matching — fixes `"what's happening"` detection
- `_score_result` stop word fallback removed — prevents noise words from inflating article scores
- Pydantic V2 deprecation warning resolved — `config.py` updated to use `ConfigDict`

---

## [2.6.0]

### Changed
- Intent routing hardening — removed 10 overly broad trigger words causing incorrect source routing
- `"recent"` and `"latest"` removed from FreshRSS general query bypass
- `"will it be"` and `"tonight"` removed from forecast triggers
- Dead code removed — `STATUS_LABELS` dict in `uptime_kuma.py`
- `forecast.py` — inline note on `%-I` Linux-only time formatting

---

## [2.5.0]

### Added
- `asyncio` moved to top-level import in `main.py`
- FastAPI startup uses modern `lifespan` context manager
- `load_cache` renamed from `_load_cache` — now a proper public function
- `check_cached`, `get_cache_stats`, `get_cache_count`, `clear_cache` — clean public cache API
- Kiwix `_search_book` and `_fetch_article` now log warnings on failure
- `_score_result` moved to module level in `kiwix.py`
- Logging added to `freshrss.py` and `searxng.py`

---

## [2.4.0]

### Added
- Smart source routing — keyword matching runs first, Ollama called only when no keyword matches
- Per-source result caching with disk persistence — cache survives container restarts
- Cache batched disk writes — saves every 5 writes instead of on every set
- Cache max size (500 entries) with LRU eviction
- Cache corruption hardening — malformed cache renamed to `.corrupt`, container starts clean
- `GET /cache` — inspect cache entries with age and TTL
- `POST /cache/clear` — clear all cached results
- `cached` field in search response

---

## [2.3.0]

### Added
- Uptime Kuma source module — reports service monitor status via Socket.IO API
- `uptime` source added to `SOURCE_MAP`, `INTENT_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL`
- `UPTIME_KUMA_URL`, `UPTIME_KUMA_USERNAME`, `UPTIME_KUMA_PASSWORD` config vars

---

## [2.2.0]

### Added
- FreshRSS query filtering — articles scored by keyword relevance, general queries bypass filtering
- `_is_general_query` — detects broad news requests and returns full feed
- Stop word lists in `freshrss.py` and `kiwix.py`

---

## [2.1.0]

### Added
- Kiwix stop word stripping — query cleaned before sending to Kiwix search engine
- Improved `_score_result` — exact title match bonus (+20), title-starts-with bonus (+10), normalized excerpt scoring, stop word awareness
- Search limit increased from 3 to 5 results per book
- Multi-book search — LLM selects up to 2 books, results deduplicated and scored across both

---

## [2.0.0]

### Added
- MCP server via SSE at `/mcp/sse` — any MCP client can connect
- Dynamic Kiwix catalog discovery — book list built from OPDS catalog at startup, no hardcoded list
- LLM-assisted Kiwix book selection via Ollama
- `POST /catalog/refresh` — force catalog re-scan without restart
- `GET /catalog` — list loaded books
- `LLM_URL`, `LLM_MODEL` config vars
- `FORECAST_TIMEZONE` config var
- Structured search response — `success`, `cached`, `error` fields
- Source fallback chain — kiwix falls back to web on empty results

---

## [1.0.0]

### Added
- Initial release
- FastAPI container with `POST /search` endpoint
- Sources: Kiwix, FreshRSS, Open-Meteo, SearXNG
- `auto` intent routing via keyword matching
- `GET /health`, `GET /sources`
- Open WebUI bridge tool (`mnemolis_tool.py`)
- Docker Compose with `mnemo-net` network
