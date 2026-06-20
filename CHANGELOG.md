# Changelog

All notable changes to Mnemolis are documented here.

---

## [3.13.0]

### Fixed — Mixed-Conjunction-Type Decomposition
The known limitation documented at the end of the prior release — a query mixing multiple different conjunction types (e.g. one `" and "` and one `" plus "`) only ever achieved a single-conjunction-type split — is now fixed.

- **`_decompose()` now also tries splitting on every conjunction occurrence at once, regardless of type**, in addition to the existing single-type-in-isolation approach, keeping whichever produces the most meaningful sub-queries. A query mixing "and also," "plus," "and," and "also" (5 genuine intents) previously collapsed to 2 parts under the single-type approach — every type's isolated split left the other conjunction words bundled inside whichever half didn't get split on. Splitting on all occurrences at once correctly separates all 5.
- **Adjacent/overlapping conjunction matches are collapsed into one split point** — "and also" (two conjunctions back to back) produces one boundary, not an empty fragment between them.
- **Possessive contraction bug in `_INTENT_WORDS` matching** — "internet's" (with the apostrophe) never matched the bare "internet" entry via exact word-membership comparison, the same class of bug previously found and fixed in `kiwix.py`'s stop-word stripping. Normalizing the apostrophe before the membership check fixes both the same way.
- Verified against the real production query that surfaced this: a deliberately extreme 5-intent, 3-conjunction-type, colloquially-phrased test query now correctly decomposes into all 5 parts and routes each independently — internet/wifi troubleshooting, Mercury retrogade, front door/windows, AC/weather, and Raspberry Pi GPIO permissions all landed in separate, correctly-attributed sections.

### Added
- 5 new regression tests in `test_router.py`, including an exact reproduction of the real 5-intent failing query
- 1 existing test corrected — the mixed-conjunction fix retroactively improved an earlier session's wifi/router/sunspots test case from a 3-way split to a genuinely better 4-way split (wifi and router are now correctly separated rather than merged under one "and"), so that test's expected count was updated to reflect the improved behavior

### Changed
- Version bumped to 3.13.0

**Total test count: 791**

### Roadmap — New Known Limitation Documented
**Mercury-retrograde-style queries with current-events framing can route past Kiwix disambiguation entirely.** Found via real testing: "what's the deal with that whole mercury retrograde thing everyone keeps talking about" — decomposition correctly isolated this as its own clean sub-query, but the keyword/LLM source-selection layer (`detect_intent()`, separate from decomposition and separate from Kiwix's own internal disambiguation) resolved it to `web,news` fusion, never reaching Kiwix or its disambiguation logic at all. The phrase "everyone keeps talking about" reads as current-discourse framing to the router, which is a defensible interpretation, not a clear-cut bug — "fixing" it by biasing harder toward Kiwix risks misrouting genuinely news-flavored queries instead. Needs deliberate design (likely: detecting when a query contains both an encyclopedic noun phrase AND a discourse-framing phrase, and explicitly trying fusion across kiwix+news+web rather than picking only news+web) rather than a quick patch. Tracked for a future session.

---

## [3.12.0]

### Fixed — Colloquial Query Handling (Found via Real Open WebUI Usage)
A deliberate stress-testing session running genuinely messy, multi-clause, colloquial questions through Open WebUI surfaced seven real bugs across decomposition, disambiguation, and fusion header composition — all verified against real production output before and after each fix, not just unit tests in isolation.

**Kiwix search term extraction (`app/sources/kiwix.py`):**
- **Apostrophe/contraction bug** — "what's" survived stop-word filtering as a stray `"what'"` token (the trailing "s" got stripped by stemming, leaving a dangling apostrophe that never matched the "what" stop word), polluting search terms and preventing disambiguation from ever triggering on colloquial phrasing. Fixed by normalizing contractions before stop-word matching.
- **Colloquial definitional patterns missing** — `_is_definitional_query()` only recognized formal phrasing ("what is," "tell me about"). Added "what's the deal with," "what's up with," "what's this about," "what's the story with."
- **Expanded `_STOP_WORDS`** — added colloquial filler ("deal," "thing," "stuff," "keep," "hearing," "up," "going") that previously survived filtering and polluted single-word disambiguation candidates.
- **`_build_search_terms()` extracted** as its own standalone, directly-testable function — was previously inline inside `search()`, which meant a prior version of the test suite tested its own separate re-implementation of the logic rather than the real code path, and could have passed while the actual implementation had this exact bug.

**Query decomposition (`app/router.py`):**
- **Conjunction-priority bug** — `_decompose()` stopped at the first conjunction type (by length) that produced ≥2 "meaningful" sub-queries, rather than trying every conjunction and keeping whichever split actually produced the most genuine intents. A query with one `" also "` and two `" and "`s would incorrectly split on `" also "` even when `" and "` produced a better 3-way split.
- **Missing singular intent words** — `_INTENT_WORDS` only contained plural forms ("doors," "lights," "locks," "sensors"), so clauses using the singular ("the back door," "the light," "the sensor") failed the meaningful-intent check and were silently dropped from decomposition entirely.
- **Missing network/connectivity vocabulary** — "wifi," "router," "network," "reboot," "restart," "online," "offline," "down" weren't recognized as real intent signals at all.
- **Colloquial phrase detection added** — "what's the deal with X" and similar now count as a real standalone intent regardless of what specific noun follows, generalizing better than an ever-growing noun list.
- **Colloquial phrase position bug** — the detection above only matched via `.startswith()`, missing real phrasing like "and remind me what's up with X" where the marker phrase is mid-clause, not at position zero (the clause still carries leftover conjunction/filler words from wherever the split occurred). Changed to a substring check.

**Fusion header composition (`app/router.py`):**
- **`[FUSION — FUSION]` double-header bug** — when a decomposed sub-query's own intent resolved to internal fusion across multiple sources, `fusion.search()` already returns content with its own per-source `[SOURCE — DESC]` headers. The outer decomposition loop wrapped that already-headered block in another header using the literal string `"fusion"` as the source name — which has no entry in `_HEADER_LABELS` — producing a nonsensical double-wrapped header around content that was already correctly labeled internally. Fixed by passing fusion sub-results through unwrapped at the outer level.

### Documented — Known Limitation (Not Yet Fixed)
A query containing multiple different conjunction types (e.g. one `" and "` and one `" plus "`) only achieves a single-conjunction-type split, since `_decompose()` picks one best conjunction type for the whole query rather than splitting on mixed conjunction types within the same decomposition pass. True mixed-conjunction splitting is a harder problem than anything fixed this release — tracked for future consideration, not chased prematurely.

### Added
- 14 new regression tests across `test_kiwix.py` and `test_router.py`, each verified against the real (not approximated) implementation before being added, covering every fix above plus the position-bug follow-up

### Changed
- `tests/test_kiwix.py` — `TestSearchTermCleaning` previously duplicated the search term extraction logic locally rather than testing the real `search()` code path; now calls the extracted `_build_search_terms()` directly, closing a gap where the test suite could pass while the real implementation was broken
- Version bumped to 3.12.0

**Total test count: 787**

---

## [3.11.1]

### Added — Documentation Accuracy Pass + Fresh Benchmarks
No code changes to core behavior. A full README and benchmark refresh following the capability expansion series, since both had drifted from what the codebase actually does.

- **README diagram audit** — Source Fusion diagram corrected (was showing the old bare `[SOURCE]` header format, missing the web/news scoring step entirely). New Kiwix Internal Flow diagram added — disambiguation, multi-candidate search, scoring, and multi-book fusion had zero visual documentation despite being the most architecturally complex part of the system.
- **README "Project Structure" rewritten** — was missing `app/scoring.py`, `app/query_expansion.py`, and 8 entire test files. Verified file-count-accurate against the real filesystem (23 test files, 15 app files).
- **README LLM-assisted routing list expanded** from 3 to 5 actual uses — added search term disambiguation and web query expansion, which existed in code but were undocumented.
- **README factual corrections** — `/changes` endpoint docs referenced a hardcoded "≥5°" instead of the now-configurable `FORECAST_TEMP_CHANGE_THRESHOLD`; Kiwix book selection referenced hardcoded "1-2" instead of `KIWIX_MAX_BOOKS`; Backup & Restore section still referenced the pre-rename `minisearch_data` volume name; "Part of the MiniNet stack" corrected to "Mnemo-net" (the actual current network/stack name).
- **`tests/locustfile.py` updated** — the load test had zero `web` source queries and no short/ambiguous Kiwix queries, meaning it was structurally incapable of measuring the cost of the two most computationally expensive features added this series (disambiguation, multi-query expansion). Added `WEB_QUERIES` and `KIWIX_DISAMBIGUATION_QUERIES` task groups.
- **Fresh benchmarks (BENCHMARKS.md)** — re-run with the updated locustfile, cold and warm cache, 20 concurrent users. Confirms the routing cache fully absorbs the new features' cold-start cost: `kiwix_disambiguation` p95 dropped ~295x (5900ms → 20ms) cold-to-warm, `web` p99 dropped ~121x (4600ms → 38ms). Aggregated median held at 17ms in both runs, unchanged from every prior benchmarked version back to v3.5.0 — the capability expansion series traded cold-path tail latency for correctness on a minority of complex queries without touching steady-state performance.

### Changed
- Version bumped to 3.11.1

---

## [3.11.0]

### Added — Confidence-Aware Fusion with Expanded Ingest
Fourth of five capability-expansion items. Web (SearXNG) and news (FreshRSS) results were previously trusted wholesale with zero relevance scoring — unlike Kiwix's dedicated scoring, these sources just returned whatever the upstream API gave back. This release builds real scoring infrastructure and adds multi-query expansion on top.

**Part A — `app/scoring.py` (new shared module):**
- `score_text_result()` — stemmed keyword overlap (title + content), exact-title-match bonus, generic/homepage-result penalty, optional recency bonus
- `_is_generic_result()` — detects homepage/about-page/site-description results rather than actual articles (generic title patterns, generic content phrases, bare-domain-root + short-content heuristic)
- `filter_and_rank()` — drops results at or below a configurable score threshold, caps survivors at a configurable top-N
- `normalize_url()` — strips scheme, `www.`, trailing slashes, query strings, and fragments for deduplication purposes
- `WEB_NEWS_SCORE_THRESHOLD` (default 0) and `WEB_NEWS_TOP_N` (default 10) config vars

**Part B — wired into the sources:**
- `searxng.py` — now pulls up to 25 raw results (was hardcoded top 5) and scores/filters/caps them instead of trusting SearXNG's own ranking
- `freshrss.py` — specific-query path now uses the shared scorer instead of its own duplicated logic; added a recency bonus (3 tiers: 1hr/6hr/24hr) so fresher articles rank higher; general-query bypass ("news", "headlines") preserved exactly as before

**Part C — multi-query expansion (web only):**
- `app/query_expansion.py` (new) — `get_alternate_phrasing()` asks the LLM for one genuinely different phrasing of a query (≥3 words, LLM configured), routing-cached, with sanity checks rejecting empty/oversized/identical responses
- `searxng.py` — when an alternate phrasing is available, searches both the original and alternate query, merges and deduplicates by normalized URL, scores the combined pool against the **original** query only — so a result survives because it's genuinely relevant to what was asked, not because of how the alternate phrasing happened to word it
- Deliberately **not** wired into FreshRSS — FreshRSS fetches and locally re-scores your existing feed items rather than issuing a remote query, so an alternate phrasing has nothing to act on there

### Fixed
- **Real bug** — `_fetch_searxng()` returning `[]` on both genuine connection failure and successful-but-empty results meant a SearXNG outage was silently reported as "no results found" instead of a real error. Now returns `None` on failure, `[]` only for a genuinely empty successful response.
- **Real bug** — duplicate results from the same article (e.g. `https://www.example.com/page/` and `https://example.com/page`) weren't deduplicated across primary/alternate query merges because comparison used raw URL strings. Fixed with `normalize_url()`.
- **Test fragility** — `TestGetChangesNetCollapsing` in `test_snapshots.py` used hardcoded absolute dates (`2026-06-19T08:00:00Z`) in tests that compare against a 24-hour rolling window. These silently failed once real time passed the window relative to the hardcoded dates. Replaced with a `_ago(minutes_ago)` helper generating timestamps relative to the actual current time, so these tests can never expire again.

### Verified
Tested against real production data across genuinely different domains — network troubleshooting, personal finance, home security, baking — confirming scoring, generic-result filtering, and deduplication all generalize well rather than being overfit to any one query style.

### Changed
- Version bumped to 3.11.0
- 64 new tests across `test_scoring.py`, `test_query_expansion.py`, `test_searxng.py`, `test_freshrss.py`, and the `test_snapshots.py` fix

**Total test count: 764**

### Roadmap
Fourth of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done), multi-book Kiwix fusion (done), confidence-aware fusion with expanded ingest (done). Remaining: recursive/conditional decomposition.

---

## [3.10.0]

### Added — Multi-Book Kiwix Fusion
Third of five capability-expansion items. When a query genuinely spans multiple Kiwix books — "python raspberry pi gpio setup" touching both a Raspberry Pi Stack Exchange thread and an Electronics Stack Exchange thread — Mnemolis now merges the best result from each relevant book instead of returning only the single highest-scoring article.

- **`_fuse_multi_book_results()`** — takes the best-scoring result per book, fetches each article, truncates using the existing fusion truncation logic (`settings.fusion_max_chars_per_source`), and merges with `[BOOK NAME]` attribution headers sorted by relevance
- **Relevance gate** — fusion only triggers when a second or third book's top result scores within 50% of the leading book's score. Prevents an LLM book-selection misfire from injecting an irrelevant book into an otherwise clean single-topic answer.
- **`KIWIX_MAX_BOOKS`** config var (default 2) — raise this to let the LLM select more books per query, enabling broader multi-book fusion (e.g. Python + Raspberry Pi + Unix Stack Exchange together) on hardware with the GPU headroom to handle more concurrent Kiwix requests per search
- **`KIWIX_SEARCH_LIMIT`** config var (default 15) — results requested per book per search, raised from the prior hardcoded 5 to give scoring more candidates when common terms get crowded out by brand-name collisions
- Verified against real production data: "python raspberry pi gpio setup" correctly fuses Raspberry Pi SE + Electronics SE. "What is nitrogen" correctly fuses Wikipedia (encyclopedic) + Wiktionary (etymology/pronunciation) — a genuinely complementary pairing the relevance gate identified without being explicitly told to expect it.
- **22 new tests** — `TestFuseMultiBookResults` (6), `TestSearchMultiBookFusionIntegration` (3), `TestConfigurableMaxBooks` (4), plus config default tests for both new settings

### Changed
- `_pick_books_with_llm()` — `max_books` parameter now defaults to `settings.kiwix_max_books` instead of a hardcoded `2`
- `_search_book()` — `limit` parameter now defaults to `settings.kiwix_search_limit` instead of a hardcoded `5`
- Version bumped to 3.10.0

**Total test count: 699**

### Roadmap
Third of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done), multi-book Kiwix fusion (done). Remaining: confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

---

## [3.9.0]

### Added — Kiwix Search Term Disambiguation (Multi-Candidate, Score-and-Verify)
Solves the long-tracked "galaxy returns Samsung phones, battery returns military fortifications" known limitation — a problem that survived three single-guess prompting attempts before landing on the right architecture.

- **`_should_disambiguate()`** — eligibility check: definitional query, Wikipedia selected, single-word search term, LLM configured
- **`_get_disambiguation_candidates()`** — asks the LLM for 3 candidate disambiguation terms taking genuinely different angles (broad field name, specific synonym, bare word with no qualifier), rather than trusting one blind guess
- **`search()` rewritten** — searches every candidate term against the selected book(s), merges and deduplicates results by URL, and lets the existing `_score_result()` scoring function pick the actual winner from the combined pool — grounded in real Kiwix results rather than LLM speculation about an index it can't see
- Verified against the exact production failures: "what are galaxies" now correctly returns the **Galaxy** astronomy article (was: Samsung Galaxy J7 phone). "How do batteries work" now correctly returns the **AA battery** article (was: military fortifications, then Electric vehicle battery)
- **3 attempted single-term prompting strategies were tried and discarded before this architecture**, documented here for anyone revisiting this problem: (1) broad category hint ("galaxy astronomy") — the disambiguation word itself dominated the search, surfacing dozens of unrelated astronomy portal pages instead of the target article; (2) rare/specific qualifier ("galaxy celestial") — collided with an entirely unrelated topic (Marvel Comics characters who happen to share thematic vocabulary with the target domain); (3) abandoning word-injection for scoring-only fixes was considered but rejected as insufficiently general. The working fix required searching multiple candidates and verifying against real results, not guessing better.

### Fixed
- **Real bug** — the single-word disambiguation term builder was including incidental content words ("how do batteries **work**" → disambiguating "battery work" as one phrase) due to picking the longest word from the full search_terms string without isolating it correctly in an earlier iteration. Now correctly isolates the single longest stemmed word before passing it to candidate generation.
- **Misaligned Snapshot Engine diagram in README** — column branches didn't line up under their labels. Redrawn with corrected alignment and updated the stale "Temp Δ≥5°" reference to reflect the now-configurable threshold.

### Changed
- Version bumped to 3.9.0
- 18 new tests — `TestShouldDisambiguate` (5), `TestGetDisambiguationCandidates` (8), `TestSearchMultiCandidateScoring` (5) — replacing the single-candidate disambiguation tests from the abandoned approaches

**Total test count: 685**

### Roadmap
Second of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done). Remaining: multi-book Kiwix fusion, confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

---

## [3.8.2]

### Added — Configurable Thresholds
First step in the capability expansion roadmap. Eight previously hardcoded values are now deployment-configurable, with zero behavior change for anyone who doesn't touch them.

- **`FORECAST_PRECIP_THRESHOLD_PCT`** (default 20) — precipitation probability above which the forecast mentions rain chance
- **`FORECAST_WIND_THRESHOLD_MPH`** (default 15) — wind speed above which the forecast mentions wind
- **`FORECAST_TEMP_CHANGE_THRESHOLD`** (default 5.0) — temperature shift between snapshots that counts as a meaningful weather change in `/changes`
- **`BATTERY_LOW_THRESHOLD_PCT`** (default 20.0) — battery level below which a snapshot diff reports "low"
- **`FUSION_MAX_SOURCES`** (default 4) — maximum sources allowed in a single fusion query
- **`FUSION_MAX_CHARS_PER_SOURCE`** (default 1500) — characters per source result before truncation
- **`FUSION_TIMEOUT_SECONDS`** (default 15) — maximum wait time for any single fusion source
- **`CACHE_MAX_SIZE`** (default 500) — result cache entries before oldest-eviction kicks in, useful to lower on memory-constrained hardware

Deliberately scoped to deployment-preference values, not algorithm-internal tuning weights (Kiwix scoring bonuses, fusion deduplication overlap threshold remain fixed — these aren't user preferences, they're tuned constants).

### Changed
- `app/sources/fusion.py` — `FUSION_TIMEOUT`, `FUSION_MAX_SOURCES`, `FUSION_MAX_CHARS_PER_SOURCE` module constants removed in favor of reading `settings` directly at call time, so changes take effect without a restart-triggering code change
- `app/router.py` — `_CACHE_MAX_SIZE` now initializes from `settings.cache_max_size` instead of a hardcoded `500`
- Version bumped to 3.8.2
- README — all 8 new config vars documented in the Configuration table

### Roadmap
First of five capability-expansion items planned, in increasing difficulty: configurable thresholds (done), Kiwix search term disambiguation, multi-book Kiwix fusion, confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

**Total test count: 665**

---

## [3.8.1]

### Fixed
- **Real bug — non-deterministic Kiwix book selection on empty LLM response.** `_pick_books_with_llm()` had a substring-matching flaw: when the LLM returned an empty or whitespace-only string (network hiccup, timeout, blank model output), the empty candidate string would match via Python's `"" in name` against whatever book name happened to come first in unordered set iteration — silently picking a random book instead of correctly falling back to Wikipedia-first. Found through a full repo-wide test coverage audit, not through user-reported behavior. Fixed by skipping empty candidates before the substring match.

### Added — Full Repo Test Coverage Audit
A deliberate, file-by-file audit confirming every module has direct test coverage, not just coverage by proxy through higher-level integration tests.

- **`tests/test_llm.py`** (26 tests) — first direct coverage of `app/llm.py`, the module backing every routing decision in the system. Covers `is_configured()`, Ollama native completion including the "thinking model" fallback behavior, OpenAI-compatible completion, connection/timeout/HTTP error handling, and payload structure verification.
- **`tests/test_mcp_server.py`** (19 tests) — first direct coverage of `app/mcp_server.py`. Covers tool schema definition, call dispatch (unknown tool, missing query, successful routing, fusion_sources passthrough, exception handling), and Starlette app construction.
- **`tests/test_config.py`** (21 tests) — first direct coverage of `app/config.py` defaults and constructibility. Caught and fixed an env-isolation flaw in the tests themselves: `Settings()` reads live environment variables, so naive "default value" tests were silently asserting against this container's real production config rather than class-level fallback values.
- **`tests/test_cache_persistence.py`** (24 tests) — direct coverage of cache eviction at capacity and disk persistence, including the exact `.corrupt` file rename recovery path observed live in production earlier this project.
- **`tests/test_kiwix_network.py`** (39 tests) — direct coverage of OPDS catalog XML parsing and pagination, LLM book-selection dispatch (including 3 new regression tests for the bug above), Kiwix search HTML scraping with Stack Exchange tag-page exclusion, and article content extraction with multi-selector fallback.
- **`tests/test_snapshot_jobs.py`** (19 tests) — direct coverage of the four APScheduler job functions, including a regression test for the kiosk/dark-mode binary sensor pollution bug fixed in a previous release.
- **26 additional tests** in `test_main.py` (catalog endpoints, API key auth) and `test_home_assistant.py` (`_get_states`, `_format_entity`, `_matches_filter` — the core entity matching engine, previously untested despite extensive higher-level coverage).

### Changed
- Version bumped to 3.8.1
- Fixed stale "MiniSearch" references in `mcp_server.py` docstrings (the project rename's last stragglers)
- Distribution tarball folder corrected to `mnemolis/` (was still `minisearch/`)

**Total test count: 646** (up from 521 — 125 new tests this release)

---

## [3.8.0]

### Added
- **`GET /areas`** — lists all detected Home Assistant areas with entity counts and the natural-language phrases that resolve to each one (e.g. "living room", "master bath"). Returns `not_configured` if HA isn't set up, `error` if the area registry can't be reached.
- **`list_areas()`** in `home_assistant.py` — builds on the existing `_get_area_entities()` and `_AREA_ALIASES` from HA area awareness, exposing them via a clean public function
- **API key authentication** — opt-in, backward compatible. `API_KEYS` config var accepts a comma-separated list of valid keys. When unset (default), auth is fully disabled and all existing integrations continue working unchanged.
- **`require_api_key()`** FastAPI dependency — validates the `X-API-Key` header against configured keys
- Auth applied to **`POST /search`** and **`GET /changes`** only — the two endpoints that return query results or house/service state. `/health`, `/areas`, `/backup`, and all other endpoints remain open for monitoring tools and discovery.
- **21 new tests** — `TestListAreas` (9 tests), `TestAreasEndpoint` (2 tests), `TestAPIKeyAuth` (13 tests covering disabled-by-default passthrough, missing/wrong/correct key handling, multi-key support, whitespace trimming, and confirming unprotected endpoints stay open)

### Changed
- Version bumped to 3.8.0

**Total test count: 473**

---

## [3.7.1]

### Fixed
- **`_search_changes` test coverage gap** — the function actually wired into `SOURCE_MAP["changes"]` had no direct test, only its helper `_resolve_changes_hours` did. Added `TestSearchChanges` (4 tests) covering the real entry point.
- **`mnemolis_tool.py`** — `fusion_sources: list[str] = None` corrected to `list[str] | None = None`. Docstring updated with time-window phrase examples ("this morning," "while at work," "in the last N hours") for the `changes` source.
- **Docker volume naming** — `docker-compose.yml` volume renamed `minisearch_data` → `mnemolis_data`, matching the project rename. `TZ: "America/Phoenix"` added (was present on the live deployment but had drifted out of the tracked file).
- **`docker-compose.example.yml`** — was missing a persistent data volume for Mnemolis entirely. Anyone following the public example would have had nothing for `/backup` to back up. Added `mnemolis_data` volume and mount.
- **Distribution tarball** — internal folder name corrected from `minisearch/` to `mnemolis/`.

### Documented
- **Docker Compose volume project-prefixing** — added a README section explaining that Compose prefixes named volumes with the project name (defaulting to the working directory's folder name), so a volume named `mnemolis_data` in YAML may actually be created as `{foldername}_mnemolis_data`. Includes verification commands and a `COMPOSE_PROJECT_NAME` workaround for a stable prefix regardless of folder name. Discovered during a real production volume migration where renamed volumes silently pointed at fresh empty storage instead of the intended data.

### Changed
- Version bumped to 3.7.1

**Total test count: 452**

---

## [3.7.0]

### Added — Real-World Bugfixes from Production Usage
A session of real Open WebUI usage against Mnemolis surfaced three distinct issues, all fixed and validated against live production data.

- **Forecast location attribution** — `forecast.search()` now prefixes output with "In {location}, " when `FORECAST_LOCATION_NAME` is configured. Previously the forecast text never stated whose weather it was, and an LLM reasoning over fused context incorrectly inferred location from an unrelated news article mentioning a different city.
- **Descriptive fusion section headers** — `_format_header()` added to `fusion.py`. Headers now read `[FORECAST — WEATHER FORECAST FOR YOUR CONFIGURED HOME LOCATION]` and `[NEWS — RECENT NEWS HEADLINES — GENERAL, NOT LOCATION-SPECIFIC UNLESS STATED]` instead of bare `[FORECAST]`/`[NEWS]`, explicitly warning the LLM against cross-referencing unrelated sections to infer facts.
- **Time-window phrase resolution for `source="changes"`** — `_resolve_changes_hours()` and `_hours_since()` added to `router.py`. "This morning," "while at work," "since work," "tonight," "since yesterday" now resolve to precise hour windows instead of collapsing into a fixed 24-hour default. Explicit hour counts ("in the last 3 hours") take priority over vaguer phrases.
- **`morning_start_hour`** (default 6) and **`work_start_hour`** (default 9) added to `config.py` — configurable reference times for resolving "this morning" and "while at work" phrases.
- **Net-change collapsing for flapping sources** — `get_changes()` now compares only the first and last snapshot in the window for `uptime` and `forecast` (sources prone to round-tripping back to baseline — a brief outage that resolves, precipitation that appears then disappears). `news` and `ha` continue reporting every individual event since each is independently meaningful. Eliminates noisy alarm/resolved pairs that don't reflect current state.
- **18 new tests** — `TestResolveChangesHours` (12 tests), `TestHoursSince` (3 tests), `TestLocationNamePrefix` (2 tests), `TestFormatHeader` (4 tests), `TestGetChangesNetCollapsing` (5 tests)

### Fixed
- **Test isolation bug** — `test_concurrent_snapshot_writes_no_crash` in `test_security.py` was writing directly to the production `snapshots.db` instead of an isolated temp database, polluting real snapshot history with literal "snapshot content N" test strings. Now properly isolated with `SNAPSHOT_DB` patched to a temp file.
- **Container timezone** — `docker-compose.yml` now sets `TZ` explicitly. Without it, the container defaulted to UTC while the host ran local time, causing time-window calculations to be off by the UTC offset.

### Changed
- Version bumped to 3.7.0
- Existing fusion/decomposition header tests updated to match new descriptive header format (substring match on `[SOURCE` rather than exact `[SOURCE]`)

**Total test count: 448**

---

## [3.6.3]

### Added — Hardening Pass
- **`tests/test_security.py`** — 27 tests covering SQL injection resistance, path traversal attempts against the backup endpoint, token/secret leakage checks in health responses and error messages, fuzz input (very long queries, unicode/emoji, null bytes, pure punctuation, empty/whitespace), and concurrency tests using real threads against cache clear, log clear, snapshot writes, and concurrent backup downloads
- **`tests/test_property.py`** — Hypothesis property-based tests across 9 pure functions: `_decompose`, `_stem`, `_score_result`, `_is_definitional_query`, `_build_filter`, `_detect_area`, `_is_excluded`, all 4 snapshot diff functions, `_looks_empty`, `_truncate`, `_deduplicate`. Each property runs 100-300 randomly generated examples, totaling thousands of input combinations tested automatically.
- **`hypothesis`** added to `requirements.txt`

### Verified
- No SQL injection vulnerabilities — all queries use parameterized placeholders
- No path traversal possible — backup endpoint uses a fixed file list, ignores all query params
- No token/secret leakage — HA token and FreshRSS password confirmed absent from `/health` responses and connection error messages
- No crashes under adversarial input — confirmed across decomposition, stemming, scoring, HA filtering, and all snapshot diff engines
- No race conditions — confirmed under concurrent cache clear + search, log clear + log write, concurrent snapshot writes, and concurrent backup downloads

### Changed
- Version bumped to 3.6.3

**Total test count: 422**

---

## [3.6.2]

### Added
- **`GET /backup`** — downloads a tarball of all Mnemolis state (result cache, routing cache, query log, snapshot history) as `mnemolis-backup-{timestamp}.tar.gz`
- **`GET /backup/info`** — shows file sizes and last-modified times for each data file without creating a backup
- **Backup & Restore section in README** — manual backup command, cron automation example, and full restore procedure using a throwaway alpine container against the named Docker volume
- **6 new tests** — `TestBackupEndpoint` covering file dict structure, known files present, content-type header, filename format, and valid tar structure

### Fixed
- **`POST /logs/clear`** — restored a third time after being accidentally dropped during endpoint insertion. Verified present in route list post-fix.

### Changed
- Version bumped to 3.6.2

**Total test count: 372**

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
