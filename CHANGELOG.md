# Changelog

All notable changes to MiniSearch are documented here.

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
