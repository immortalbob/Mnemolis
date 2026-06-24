# Configuration Reference

Every setting is an environment variable, set in `docker-compose.yml`. This page groups them by what they actually control, rather than the README's single flat table — useful for understanding *why* a default is what it is, not just what it is.

## Backend connections

| Variable | Default | Notes |
|----------|---------|-------|
| `KIWIX_URL` | `http://kiwix:8080` | |
| `FRESHRSS_URL` | `http://freshrss` | |
| `FRESHRSS_USER` | _(blank)_ | |
| `FRESHRSS_API_PASSWORD` | _(blank)_ | A separate password from your normal FreshRSS login — generated specifically for API access |
| `FRESHRSS_MAX_ARTICLES` | `10` | |
| `SEARXNG_URL` | `http://searxng:8080` | |
| `SEARXNG_REQUEST_TIMEOUT_SECONDS` | `15` | Mnemolis's own client-side wait time for a SearXNG response — separate from SearXNG's own server-side `request_timeout` setting (see [The SearXNG Timeout Lesson](The-SearXNG-Timeout-Lesson)). Set this to match or exceed whatever SearXNG is itself configured to wait, or the documented timeout fix on the SearXNG side won't fully take effect — Mnemolis would still cut the connection first |
| `UPTIME_KUMA_URL` | _(blank)_ | Leaving this blank disables the `uptime` source entirely, rather than erroring — `/health` will simply not report a status for it |
| `UPTIME_KUMA_USERNAME` / `UPTIME_KUMA_PASSWORD` | _(blank)_ | |
| `HA_URL` | _(blank)_ | Same graceful-disable behavior as `UPTIME_KUMA_URL` |
| `HA_TOKEN` | _(blank)_ | See [Home Assistant Integration](Home-Assistant-Integration) for how to generate this |

## LLM backend

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_URL` | _(blank)_ | Leaving this blank disables every LLM-assisted feature — [Routing](Routing) falls back to keyword-only matching, [Kiwix Disambiguation](Kiwix-Disambiguation) and [Query Expansion](Query-Expansion) never trigger, Kiwix book selection falls back to a fixed "search Wikipedia first" rule. Mnemolis still works, with meaningfully less of its actual intelligence available |
| `LLM_MODEL` | `qwen3:8b` | |
| `LLM_API_TYPE` | `ollama` | The other supported value is `openai`, for any OpenAI-compatible endpoint |

## Weather (`forecast`)

| Variable | Default | Notes |
|----------|---------|-------|
| `FORECAST_LATITUDE` / `FORECAST_LONGITUDE` | _(blank)_ | Required for `forecast` to work at all |
| `FORECAST_LOCATION_NAME` | _(blank)_ | Used to prefix forecast responses, so a [fused](Fusion) response can't be misread as weather somewhere else |
| `FORECAST_TIMEZONE` | `UTC` | |
| `FORECAST_PRECIP_THRESHOLD_PCT` | `20` | Precipitation probability above which the forecast text actually mentions rain chance |
| `FORECAST_WIND_THRESHOLD_MPH` | `15` | Wind speed above which the forecast text mentions wind |
| `FORECAST_TEMP_CHANGE_THRESHOLD` | `5.0` | How large a temperature shift between [snapshots](Snapshot-Engine-and-Changes) needs to be before `changes` reports it as meaningful — a half-degree difference between two snapshots isn't worth surfacing |

## Time-window phrase resolution

| Variable | Default | Notes |
|----------|---------|-------|
| `MORNING_START_HOUR` | `6` | What hour (local time, 0-23) "this morning" looks back to in [`changes`](Snapshot-Engine-and-Changes#time-window-phrases) queries. A value outside 0-23 (e.g. `24` for midnight, a natural mistake) is wrapped via modulo rather than rejected — `24` is treated as `0` |
| `WORK_START_HOUR` | `9` | Same, for "while at work" / "since work" |

## Fusion

| Variable | Default | Notes |
|----------|---------|-------|
| `FUSION_MAX_SOURCES` | `4` | Hard cap on how many sources one [fusion](Fusion) query can touch. Setting this to `0` doesn't disable fusion — it correctly returns "no valid sources specified" rather than the raw crash it used to produce |
| `FUSION_MAX_CHARS_PER_SOURCE` | `1500` | Per-source truncation before merging |
| `FUSION_TIMEOUT_SECONDS` | `15` | How long any single source gets before fusion moves on without it |

## Caching

| Variable | Default | Notes |
|----------|---------|-------|
| `CACHE_MAX_SIZE` | `500` | Max [result cache](Caching#result-cache) entries before oldest-eviction |
| `ROUTING_CACHE_MAX_SIZE` | `1000` | Max [routing cache](Caching#routing-cache) entries before oldest-eviction — larger than the result cache's default, since the routing cache's real key space (every unique conditional query, discourse-framing phrase, and disambiguation candidate set) is genuinely bigger |
| `ROUTING_CACHE_TTL_SECONDS` | `3600` | How long a routing decision (source, Kiwix book, disambiguation candidates) stays cached before the LLM gets asked again |
| `CACHE_TTL_KIWIX_SECONDS` | `86400` | Result cache TTL for `kiwix` (24 hours — offline encyclopedic content barely changes within a day) |
| `CACHE_TTL_FORECAST_SECONDS` | `1800` | Result cache TTL for `forecast` |
| `CACHE_TTL_NEWS_SECONDS` | `900` | Result cache TTL for `news` |
| `CACHE_TTL_WEB_SECONDS` | `3600` | Result cache TTL for `web` |
| `CACHE_TTL_UPTIME_SECONDS` | `60` | Result cache TTL for `uptime` |
| `CACHE_TTL_HA_SECONDS` | `30` | Result cache TTL for `ha` (the shortest of any source — lights and locks change state constantly) |
| `CACHE_TTL_CHANGES_SECONDS` | `120` | Result cache TTL for `changes` |
| `CACHE_TTL_FUSION_SECONDS` | `1800` | Result cache TTL for `fusion` |

## Kiwix tuning

| Variable | Default | Notes |
|----------|---------|-------|
| `KIWIX_SEARCH_LIMIT` | `15` | Results requested per book per search — higher values give [scoring](Kiwix-Scoring) more candidates to find the right answer among when common terms collide with brand-name results |
| `KIWIX_MAX_BOOKS` | `2` | Max books the LLM can select for one query — raise this to allow broader [multi-book fusion](Multi-Book-Fusion), at the cost of more searches per query |
| `KIWIX_ARTICLE_MAX_CHARS` | `3000` | How many characters of a fetched article's body to keep before scoring/fusion ever sees it — distinct from `FUSION_MAX_CHARS_PER_SOURCE`, which truncates the already-combined multi-source response, not an individual Kiwix article on its own |
| `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT` | `0.5` | The actual, central decision threshold for [multi-book fusion](Multi-Book-Fusion): a second book's best result must score at least this fraction of the leading book's top score to be included. Lower for more aggressive fusion, raise for more conservative |

## Web & news scoring

| Variable | Default | Notes |
|----------|---------|-------|
| `WEB_NEWS_SCORE_THRESHOLD` | `0` | Results from [confidence-aware fusion](Confidence-Aware-Fusion) scoring at or below this are dropped |
| `WEB_NEWS_TOP_N` | `10` | Max results kept after scoring |
| `WEB_NEWS_RAW_RESULT_BUDGET` | `25` | How many raw, unscored results to pull from each web search before scoring filters them down — the scoring pipeline's *input* budget, distinct from `WEB_NEWS_TOP_N`'s *output* cap |
| `QUERY_EXPANSION_MIN_WORDS` | `3` | Minimum query length (in words) for web search [query expansion](Query-Expansion) to trigger |

## Snapshot diff thresholds

| Variable | Default | Notes |
|----------|---------|-------|
| `BATTERY_LOW_THRESHOLD_PCT` | `20.0` | Battery level below which a [snapshot diff](Snapshot-Engine-and-Changes) reports "low" |
| `SNAPSHOT_STALE_GRACE_MULTIPLIER` | `3` | How many multiples of a job's own expected interval can pass before [`/health`](Health-and-Observability#background-job-health) flags it as "stale" rather than "ok" — lower for tighter alerting on flakier hardware, raise if normal scheduler jitter on your own hardware is wider than the default assumes |

## Adversarial self-testing

| Variable | Default | Notes |
|----------|---------|-------|
| `ADVERSARIAL_TEST_ENABLED` | `true` | Master on/off switch for [adversarial self-testing](Adversarial-Self-Testing). `false` skips DB init, never registers the scheduler job, and makes `POST /adversarial/trigger` a safe no-op |
| `ADVERSARIAL_TEST_INTERVAL_MINUTES` | `60` | How often the scheduler tick fires |
| `ADVERSARIAL_TEST_BATCH_SIZE` | `8` | Queries generated per tick — cheap to raise, since generation is pure combinatorics with no LLM calls in the hot path |
| `ADVERSARIAL_TEST_LATENCY_OUTLIER_MULTIPLIER` | `1.5` | How many multiples of a recipe's own historical p95 latency counts as a real outlier |
| `ADVERSARIAL_TEST_LATENCY_OUTLIER_FLOOR_MS` | `1000` | A floor below which latency is never flagged regardless of the multiplier |
| `ADVERSARIAL_TEST_LATENCY_OUTLIER_MIN_SAMPLES` | `10` | How many historical samples a recipe needs before the latency-outlier check engages at all |
| `ADVERSARIAL_TEST_PART_COUNT_MISMATCH_TOLERANCE` | `2` | How far a `multi_intent_chain` query's intended-intent count and its result's header count can diverge before it's flagged |

## Security

| Variable | Default | Notes |
|----------|---------|-------|
| `API_KEYS` | _(blank — auth disabled)_ | Comma-separated list of valid keys. Protects `POST /search` and `GET /changes` specifically — every other endpoint (`/health`, `/areas`, `/backup`, `/cache`, etc.) stays unauthenticated regardless of this setting, so monitoring tools and discovery requests aren't blocked. Clients send the key via the `X-API-Key` header. Leaving this blank matches the trust model of a homelab sitting behind your own firewall |

## Operational

| Variable | Default | Notes |
|----------|---------|-------|
| `LOG_LEVEL` | `INFO` | `INFO` is what actually shows the interesting decisions — decomposition splits, disambiguation candidates, article selection. This wasn't always true: application logging was silently disabled project-wide for a real stretch of this project's history (the root logger defaulted to `WARNING` with no handler configured), meaning every `_LOGGER.info()` call across the entire codebase was being swallowed before anyone could see it. Fixed once, and worth knowing about if you're ever debugging on a build old enough to predate that fix |

## Where to go from setting a value to understanding what it actually does

Most of the notes above link to the wiki page that covers the real mechanism a setting controls — [Routing](Routing), [Caching](Caching), [Fusion](Fusion), [Kiwix Scoring](Kiwix-Scoring), and so on. The numeric default itself is rarely the interesting part; the page it links to explains why that number, specifically, was chosen.
