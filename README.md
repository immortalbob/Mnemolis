# Mnemolis

A unified local knowledge search API for self-hosted homelabs. Mnemolis runs as a Docker container on your internal network and routes queries to the appropriate backend — offline knowledge, weather forecast, RSS news, live web search, service monitoring, or multiple sources concurrently — via a single endpoint.

Exposes both a **REST API** and an **MCP server** so any client can connect to it.

## Architecture

### Voice Assistant Flow

```text
ESP32 Voice Assistant
          │
          ▼
   Home Assistant
          │
          ▼
 Mnemolis Intents
          │
          ▼
     Mnemolis
          │
          ├────────────────────┐
          │                    │
          ▼                    ▼
      LLM Backend         Source Providers
          │               ├─ Kiwix
          │               ├─ FreshRSS
          ▼               ├─ SearXNG
   Smart Routing          ├─ Open-Meteo
   ├─ Single source       ├─ Uptime Kuma
   ├─ Auto-fusion         ├─ Home Assistant
   └─ Decomposition  ────►└─ Snapshot Engine (changes)
          │
          ▼
      Response
          │
          ▼
 Home Assistant TTS
          │
          ▼
      ESP32
```

### Multi-Client Architecture

```text
   Open WebUI    Claude Desktop    Cursor    Home Assistant
       │                │             │     (Mnemolis Intents)
    REST API            MCP          MCP          REST API
       │                │             │               │
       └────────────────┴─────────────┴───────────────┘
                                │
                                ▼
                           Mnemolis
                                │
                          Smart Routing
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        Single Source     Auto-Fusion       Decomposition
       (keyword or LLM) (multi-keyword/LLM) (conjunction split)
              │                 │                 │
              └─────────────────┴─────────────────┘
                                │
                           ┌────┴────┐
                           ▼         ▼
                    REST API       MCP/SSE
                           │         │
               Home Assistant    Any MCP
             (Mnemolis Intents)   Client
                           │
                    Voice Pipeline
```

### Snapshot Engine

```text
              Background Scheduler (APScheduler)
                            │
        ┌──────────┬────────┬───────────┐
        ▼          ▼        ▼           ▼
     Uptime     Forecast   News         HA
     (2 min)    (30 min)  (60 min)    (5 min)
        │          │        │           │
        └──────────┴────────┴───────────┘
                          │
                          ▼
                  Store snapshot
              (SQLite, JSON for HA)
                          │
              Retain last 288 per source
                          │
                          ▼
              Diff consecutive snapshots
        ┌──────────┬────────┬───────────┐
        ▼          ▼        ▼           ▼
    Outages/    Temp/      New        Lock/door/
    Recovery    Precip   headlines     battery
   (net change) changes               changes
   (configurable thresholds)
        └──────────┴────────┴───────────┘
                          │
                          ▼
              GET /changes?hours=N
           source="changes" (auto-routed)
                          │
                          ▼
                Formatted summary
             "what changed today?"
```

### Source Fusion

```text
   source="auto"                    source="fusion"
        │                                 │
        ▼                                 ▼
 Keyword scan all sources      LLM picks 2-3 sources
 Multiple match? → fuse        (or you specify explicitly)
 Single match? → direct              │
        │                            │
        └────────────┬───────────────┘
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
       Kiwix        HA        Forecast
      FreshRSS   SearXNG      Uptime
    (any combination of available sources,
         queried concurrently)
         │           │           │
         └───────────┴───────────┘
                     │
        Filter empty / failed results
        web/news results scored + ranked
        (app/scoring.py — keyword overlap,
         generic-result penalty, recency)
        Partial failure OK — best effort
                     │
   Merge with [SOURCE — DESCRIPTION] headers
   (descriptive label prevents cross-source
    inference, e.g. weather ≠ news location)
                     │
               Single Response
```

Fusion queries all specified sources concurrently, filters empty or failed results, and merges the remainder with descriptive source attribution headers (e.g. `[FORECAST — WEATHER FORECAST FOR YOUR CONFIGURED HOME LOCATION]`) so the LLM reading the fused response can't mistakenly infer facts across unrelated sections. If only one source returns results, it is returned directly without headers.

### Query Decomposition

```text
   source="auto"
        │
        ▼
 Nosplit check
 "compare", "vs", "between", etc.
        │
        ▼
 Try every conjunction type
 "and", "also", "plus", "as well as"
 (not just the first one found —
  keep whichever split produces
  the most genuine sub-intents)
        │
        ▼
 Each candidate sub-query must contain
 either a recognized intent word/noun
 OR a colloquial phrase anywhere in it
 ("what's the deal with X",
  "what's up with X", etc.)
        │
   ┌────┴────┐
   ▼         ▼
Single    Multiple
intent    intents
   │         │
   │    ┌────┴────────────┐
   │    ▼                 ▼
   │  Sub-query 1    Sub-query 2
   │    │                 │
   │  Route            Route
   │  independently    independently
   │    │                 │
   │    └────────┬────────┘
   │             │
   │    Same source? → Merge headers
   │    Different?  → Keep separate
   │    Resolved to internal fusion?
   │      → Pass through unwrapped
   │        (already self-headered)
   │             │
   └─────────────┤
                 ▼
          Single Response
     with [SOURCE — DESCRIPTION] attribution
```

Decomposition only applies to `source="auto"`. Explicit source requests (`source="kiwix"`) skip decomposition entirely. Consecutive results from the same source are merged under a single header — "indoor air quality and are the doors locked" returns one `[HA]` block, not two. Casual, colloquial phrasing ("the wifi has been acting flaky and also can you check the front door, and what's the deal with sunspots") decomposes the same way formal phrasing does — this was a real gap found and fixed through testing against genuinely messy real-world queries, not synthetic ones.

A clause counts as a real, independent intent if any genuine content word survives stop-word stripping — there's no fixed list of recognized topics to maintain, so technical/programming vocabulary ("pigpio," "gpio," "compiler") is handled the same as home/network vocabulary, with no per-domain extension required. Bare proper-noun pairs joined by a conjunction ("weather in Phoenix and Kingman," "what's happening with Iran and Israel") are protected from being mistaken for two separate intents — checked at each conjunction occurrence independently, so a single sentence can contain both a genuine proper-noun pair and other, completely unrelated real intents without one being mistaken for the other.

Queries mixing multiple different conjunction types in one sentence ("X, and also Y, plus Z, and W") are also handled — Mnemolis tries every conjunction type in isolation as well as splitting on every conjunction occurrence at once, and keeps whichever approach produces the most genuine sub-intents.

Queries phrased with current-discourse framing ("what's the deal with that whole mercury retrograde thing everyone keeps talking about") are detected explicitly and biased toward including Kiwix in the routing decision — these phrasings previously routed past Kiwix to news/web entirely, since the LLM router's news/web descriptions matched this kind of phrasing almost word-for-word while Kiwix's description gave no signal that an evergreen topic can also be currently trending in public conversation. The discourse-framing phrase itself is also stripped before Kiwix's search terms are built, so words like "everyone" and "obsessed" never pollute the actual search.

**Known limitation:** a single, genuinely ambiguous bare word (e.g. "galaxy," with both astronomy and pop-culture senses in the corpus) can still land on a thematically-related but imprecise match — this is a search-relevance question distinct from the routing bypass described above, since it persists even when Kiwix is correctly included and search terms are clean.

### Conditional Query Detection

```text
   "if X, Y" / "should X, Y" / "in case X, Y"
                    │
                    ▼
   Leading-comma pattern match
   (deliberately narrow — see below)
                    │
            ┌───────┴───────┐
            ▼               ▼
        No match          Match
            │               │
    Route normally    Extract condition, consequence,
    (decomposition,        and any remainder text
     conditional check          │
     re-applied to        Search ONLY the condition
     each sub-query)            │
            │           Source a structured,
            │           binary signal?
            │         (ha / uptime / forecast-rain)
            │               │
            │       ┌───────┴───────┐
            │       ▼               ▼
            │      No              Yes
            │       │               │
            │  Present real    State explicit verdict
            │  result, note    ("It IS/IS NOT the
            │  it's conditional    case that X...")
            │       │               │
            │       └───────┬───────┘
            │               ▼
            │      Remainder present?
            │       (real intent that
            │        followed the
            │        conditional)
            │               │
            │       ┌───────┴───────┐
            │       ▼               ▼
            │      No              Yes
            │       │               │
            │   Return         Search remainder
            │   framed         independently,
            │   response       merge into response
            └───────┬───────────────┘
                    ▼
              Final Response
```

Detection is deliberately narrow — only the leading `"if X, Y"` form with an explicit comma is recognized. "If" is genuinely ambiguous in English: it has both a conditional sense ("if it's raining, bring an umbrella") and a "whether" sense ("check if the lights are on" = "check whether the lights are on"). The whether sense never appears at the start of a sentence followed by a comma, so restricting to that form sidesteps the ambiguity entirely rather than guessing at verb-based disambiguation — phrasing like "let me know if X" is genuinely ambiguous even to a human reader (could mean "tell me the current status" or "notify me if it changes") and is intentionally not detected. Mid-sentence and trailing `"if"` ("remind me to bring an umbrella if it's raining") are also out of scope for the same reason.

Mnemolis has no reminder, alert, or trigger capability — it cannot act on a conditional's consequence. Instead, the response is framed honestly around the condition's actual real answer. For structured, genuinely binary sources (HA lock/door states, uptime up/down, forecast rain/clear), the response states an explicit verdict: "It is NOT the case that the back door is unlocked — so the suggested action (let me know) may not apply." For open-ended or subjective conditions (encyclopedic facts, "is it hot enough"), no verdict is fabricated — the real result is presented honestly with a note that judgment is needed, since guessing wrong would actively mislead rather than just be unhelpful.

Decomposed sub-queries are re-checked for their own embedded conditional structure, since the top-level check only ever runs once against the original full query — "what is the weather and if the back door is unlocked, let me know" doesn't start with "if," so the second decomposed sub-query needs its own re-detection, which happens automatically.

### Kiwix Internal Flow

```text
              query
                │
                ▼
       LLM picks 1-2 books
     (or Wikipedia-first fallback
        if LLM not configured)
                │
                ▼
   Definitional query? Single word?
   Wikipedia selected? LLM configured?
                │
        ┌───────┴───────┐
        ▼               ▼
       No              Yes
        │               │
        │      Ask LLM for 3 candidate
        │      disambiguation terms
        │      (broad field / specific
        │       synonym / bare word)
        │               │
        └───────┬───────┘
                ▼
   Search each book × each candidate
   term, merge results, dedupe by URL
                │
                ▼
        Score every result
     against the ORIGINAL query
   (exact match, stemmed title/excerpt
    overlap, Wikipedia bonus, list penalty)
                │
                ▼
   2+ books scoring within 50% of
   the leading book's top result?
                │
        ┌───────┴───────┐
        ▼               ▼
       No              Yes
        │               │
  Return single    Fuse best result per
  best-scoring     book — multi-book
  article           fusion response
```

This is the layer that fixed the "galaxy returns Samsung phones, battery returns military fortifications" problem — rather than trusting one LLM guess about which search term will work, Mnemolis tries several candidates and verifies against real Kiwix results, scored the same way regardless of which term found them.

## Integrations

| Client | Protocol | How |
|--------|----------|-----|
| [Open WebUI](mnemolis_tool.py) | REST | Lightweight tool that POSTs to `/search` |
| [Mnemolis Intents](https://github.com/immortalbob/mnemolis_intents) | REST | Native HA LLM API integration |
| Any MCP client (Claude Desktop, Cursor, etc.) | MCP/SSE | Connect to `http://your-host:8888/mcp/sse` |

## Sources

| Source | Backend | Description |
|--------|---------|-------------|
| `kiwix` | [Kiwix](https://www.kiwix.org/) | Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs |
| `forecast` | [Open-Meteo](https://open-meteo.com/) | 3-day weather forecast, no API key required |
| `news` | [FreshRSS](https://freshrss.github.io/FreshRSS/) | Recent articles from your RSS feeds via GReader API |
| `web` | [SearXNG](https://searxng.github.io/searxng/) | Live web search via your local SearXNG instance |
| `uptime` | [Uptime Kuma](https://uptime.kuma.pet/) | Service monitor status — reports any down services |
| `ha` | [Home Assistant](https://www.home-assistant.io/) | Entity state summaries — lights, locks, sensors, motion, batteries, power |
| `changes` | Snapshot Engine | Detected changes since last snapshot — outages, weather shifts, new headlines |
| `fusion` | — | Query multiple sources concurrently and merge results |
| `auto` | — | Mnemolis detects intent and picks the best source |

## Requirements

- Docker + Docker Compose
- A Docker network for container communication (default: `mnemo-net`)
- One or more of the supported backends running and reachable on the same network

## Quick Start

### Full stack (recommended)

The repo includes an example compose file and SearXNG config to get all services running together:

```bash
git clone https://github.com/immortalbob/Mnemolis
cd Mnemolis

# Create the shared network if it doesn't exist
docker network create mnemo-net

# Copy and edit the example compose file
cp docker-compose.example.yml docker-compose.yml
# Fill in credentials, your coordinates, and secret_key in searxng/settings.yml

docker compose up -d
```

### What's not in the full stack
The example compose intentionally excludes Home Assistant, your LLM backend, and Uptime Kuma — these are typically long-running services with their own existing setup.

If you're running any of these in Docker and want them reachable by Mnemolis, connect them to `mnemo-net`:

```bash
docker network connect mnemo-net ollama
docker network connect mnemo-net homeassistant
```

### Mnemolis only

If you already have the backends running:

```bash
git clone https://github.com/immortalbob/Mnemolis
cd Mnemolis
# Edit docker-compose.yml with your settings
docker compose up -d
```

Hit `http://your-host:8888/health` to confirm it's running.
Full API docs at `http://your-host:8888/docs`.

## Configuration

All settings are passed as environment variables in `docker-compose.yml`:

| Variable | Description | Default |
|----------|-------------|---------|
| `KIWIX_URL` | Kiwix container URL | `http://kiwix:8080` |
| `FRESHRSS_URL` | FreshRSS container URL | `http://freshrss` |
| `FRESHRSS_USER` | FreshRSS username | |
| `FRESHRSS_API_PASSWORD` | FreshRSS API password | |
| `FRESHRSS_MAX_ARTICLES` | Max articles to fetch | `10` |
| `SEARXNG_URL` | SearXNG container URL | `http://searxng:8080` |
| `FORECAST_LATITUDE` | Forecast location latitude | _(blank)_ |
| `FORECAST_LONGITUDE` | Forecast location longitude | _(blank)_ |
| `FORECAST_LOCATION_NAME` | Human-readable location name | _(blank)_ |
| `FORECAST_TIMEZONE` | Timezone for forecast times | `UTC` |
| `UPTIME_KUMA_URL` | Uptime Kuma URL | _(blank — disables uptime source)_ |
| `UPTIME_KUMA_USERNAME` | Uptime Kuma username | |
| `UPTIME_KUMA_PASSWORD` | Uptime Kuma password | |
| `HA_URL` | Home Assistant URL | _(blank — disables HA source)_ |
| `HA_TOKEN` | Home Assistant long-lived access token | |
| `LLM_URL` | LLM backend URL for intelligent routing | _(blank — disables LLM routing)_ |
| `LLM_MODEL` | Model to use for source and book selection | `qwen3:8b` |
| `LLM_API_TYPE` | API format: `ollama` or `openai` | `ollama` |
| `MORNING_START_HOUR` | Reference hour (0-23, local time) for resolving "this morning" in changes queries | `6` |
| `WORK_START_HOUR` | Reference hour (0-23, local time) for resolving "while at work" in changes queries | `9` |
| `API_KEYS` | Comma-separated list of valid API keys. Protects `POST /search` and `GET /changes`. | _(blank — auth disabled)_ |
| `FORECAST_PRECIP_THRESHOLD_PCT` | Precipitation probability (%) above which the forecast mentions rain chance | `20` |
| `FORECAST_WIND_THRESHOLD_MPH` | Wind speed (mph) above which the forecast mentions wind | `15` |
| `FORECAST_TEMP_CHANGE_THRESHOLD` | Temperature shift (°) between snapshots that counts as a meaningful weather change | `5.0` |
| `BATTERY_LOW_THRESHOLD_PCT` | Battery level (%) below which a snapshot diff reports "low" | `20.0` |
| `FUSION_MAX_SOURCES` | Maximum number of sources allowed in a single fusion query | `4` |
| `FUSION_MAX_CHARS_PER_SOURCE` | Characters per source result before truncation in fusion output | `1500` |
| `FUSION_TIMEOUT_SECONDS` | Maximum time to wait for any single source in a fusion query | `15` |
| `CACHE_MAX_SIZE` | Maximum result cache entries before oldest-eviction kicks in | `500` |
| `ROUTING_CACHE_MAX_SIZE` | Maximum routing cache entries before oldest-eviction kicks in | `1000` |
| `KIWIX_SEARCH_LIMIT` | Results requested per book per Kiwix search — higher values help the scoring function find the right answer among brand-name collisions | `15` |
| `KIWIX_MAX_BOOKS` | Maximum number of Kiwix books the LLM can select for a single query — raise for broader multi-book fusion | `2` |
| `WEB_NEWS_SCORE_THRESHOLD` | Web/news results scoring at or below this are dropped as irrelevant | `0` |
| `WEB_NEWS_TOP_N` | Maximum web/news results kept after scoring | `10` |
| `LOG_LEVEL` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) — `INFO` shows decomposition splits, disambiguation candidates, and article selection decisions | `INFO` |

### FreshRSS API setup
1. Enable API access: **Administration → Authentication → Allow API access**
2. Set an API password: **Profile → API password**
3. Use that password for `FRESHRSS_API_PASSWORD` (it's separate from your login password)

### SearXNG JSON format
Mnemolis queries SearXNG's JSON API. The included `searxng/settings.yml` already has this enabled. If you're using an existing SearXNG instance, make sure `json` is in your formats list:

```yaml
search:
  formats:
    - html
    - json
```

Also generate a unique `secret_key` in `searxng/settings.yml`:

```bash
openssl rand -hex 32
```

### SearXNG request timeout

SearXNG's default `request_timeout` is `3.0` seconds — too short for several real, commonly-used engines (Google, Wikipedia, Startpage, DuckDuckGo have all been observed taking 15-25+ seconds to respond under normal conditions, not just when rate-limited). With the default timeout, these requests get killed before they have a chance to succeed, which surfaces in Mnemolis as `"Error reaching SearXNG: connection failed."` even though SearXNG itself is healthy and the network path is fine — the actual cause is visible in SearXNG's own logs (`docker logs searxng`) as `HTTP requests timeout (search duration : 20.5s, timeout: 3.0s)`, not in anything Mnemolis logs.

If you see this error, check SearXNG's own logs first before assuming a Mnemolis or network problem. Raise the timeout in your SearXNG `settings.yml`:

```yaml
outgoing:
  request_timeout: 10.0
  max_request_timeout: 20.0
```

`max_request_timeout` is commented out by default — uncomment it, since some individual engines (e.g. `360search`, shipped already configured for `timeout: 20.0` in the default SearXNG config) need more time than the global default, and the maximum acts as a ceiling on those per-engine overrides. Restart SearXNG after changing this (`docker restart searxng` or `docker compose restart searxng`, depending on how it's deployed).

Separately, real per-engine rate limiting (e.g. Brave returning `SearxEngineTooManyRequestsException: Too many request (suspended_time=180)`) is a different, normal occurrence under heavy query volume — SearXNG self-recovers after the suspension window without intervention.

### LLM-assisted routing
Mnemolis uses a local LLM backend in five ways:

1. **Source selection** — when `auto` is used and no keyword matches, the LLM picks the best source based on the query. For complex multi-topic queries it returns multiple sources, triggering fusion automatically. If the query frames its topic as current public discourse ("everyone keeps talking about X," "everyone's obsessed with X"), Kiwix is added to the decision when it would otherwise be excluded — this pattern reproducibly routed encyclopedic topics past Kiwix to news/web alone, since news/web's descriptions naturally match this kind of phrasing more closely than Kiwix's does.
2. **Book selection** — once routed to Kiwix, the LLM picks the best books from your catalog for the query, up to `KIWIX_MAX_BOOKS` (default 2)
3. **Search term disambiguation** — for short, definitional Kiwix queries (e.g. "what is a galaxy"), the LLM generates 3 candidate disambiguation terms to break brand-name/homonym collisions. Each candidate is actually searched and scored against real Kiwix results rather than trusting a single guess — see [Kiwix Internal Flow](#kiwix-internal-flow).
4. **Fusion source selection** — when `fusion` is used without specifying sources, the LLM picks the best 2-3 sources for the query
5. **Web query expansion** — for web searches of 3+ words, the LLM generates one alternate phrasing so SearXNG is queried twice and results merged, scored against your original query — see [Confidence-aware fusion](#confidence-aware-fusion-web-news)

**Auto-fusion escalation** — `source="auto"` now detects multi-topic queries at the keyword level too. If a query matches triggers from multiple sources (e.g. "weather" + "services up"), fusion is triggered automatically without an LLM call.

Routing decisions (including disambiguation candidates and alternate phrasings) are cached for 1 hour so repeated queries skip the LLM call entirely.

Query decomposition and conditional detection (see [Query Decomposition](#query-decomposition) and [Conditional Query Detection](#conditional-query-detection) above) are deliberately pure pattern matching with no LLM involvement at all — they need to run on every single query before any LLM call happens at all, including when no LLM is configured.

**Supported backends** via `LLM_API_TYPE`:
- `ollama` — Ollama native API (default)
- `openai` — OpenAI-compatible API (llama-server, LM Studio, etc.)

The book list is built dynamically from your Kiwix catalog at startup. To force a refresh after adding ZIMs:

```bash
curl -X POST http://your-host:8888/catalog/refresh
```

If `LLM_URL` is left blank, Mnemolis falls back to keyword-based routing and Wikipedia for all Kiwix queries.

### Timezone configuration
Set `TZ` in `docker-compose.yml` to your local timezone (e.g. `America/New_York`). Without it, the container defaults to UTC, which causes time-window phrases in `changes` queries ("this morning," "while at work") to be calculated against the wrong reference time — off by your UTC offset.

```yaml
environment:
  TZ: "America/New_York"
```

### API key authentication (optional)
By default, Mnemolis has no authentication — anyone on your network can query it. This matches the trust model of a homelab where Mnemolis sits behind your own firewall.

To require an API key for `POST /search` and `GET /changes`:

```yaml
environment:
  API_KEYS: "your-secret-key-here"
```

Multiple keys are supported, comma-separated:

```yaml
environment:
  API_KEYS: "key-for-open-webui,key-for-claude-desktop"
```

Clients must send the key in the `X-API-Key` header:

```bash
curl -X POST http://your-host:8888/search \
  -H "X-API-Key: your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query": "what is nitrogen", "source": "kiwix"}'
```

All other endpoints (`/health`, `/areas`, `/backup`, `/cache`, etc.) remain unauthenticated regardless of this setting, so monitoring tools and discovery requests aren't blocked.

### Home Assistant setup
Generate a long-lived access token in Home Assistant:
1. Go to your **Profile** (click your username in the sidebar)
2. Scroll to **Long-lived access tokens**
3. Click **Create Token**, give it a name, copy the token
4. Set `HA_URL` to your HA instance URL (e.g. `http://192.168.1.100:8123`)
5. Set `HA_TOKEN` to the generated token

The `ha` source handles analytical queries that go beyond HA's built-in single-entity intent handling:
- **"house status summary"** — lights, locks, sensors, motion, batteries
- **"indoor air quality"** — CO2, temperature, humidity from indoor sensors
- **"security status"** — locks, doors, recent motion with time-ago
- **"battery status"** — all device battery levels
- **"outdoor conditions"** — weather station sensors
- **"how much power am I using"** — current and historical consumption

The `ha` source also participates in fusion — "house status and what's the weather" automatically fuses `ha` + `forecast`.

## REST API

### `POST /search`

Single source:

```json
{
  "query": "what is molybdenum",
  "source": "auto"
}
```

Fusion — LLM picks sources automatically:

```json
{
  "query": "what is happening with the space program lately",
  "source": "fusion"
}
```

Fusion — explicit source list:

```json
{
  "query": "what is happening with the space program lately",
  "source": "fusion",
  "fusion_sources": ["kiwix", "web", "news"]
}
```

Response:

```json
{
  "query": "what is molybdenum",
  "source_used": "kiwix",
  "result": "# Molybdenum\nSource: wikipedia_en_all_maxi_2026-02\n\n...",
  "success": true,
  "cached": false,
  "error": null
}
```

### `GET /sources`
Returns the list of available sources.

### `GET /health`
Returns status, number of Kiwix books loaded, result and routing cache entry counts alongside their configured max sizes (so growth toward either bound is visible without digging through logs or code), and connectivity status for every configured source — these are real, live network checks against each dependency, not just a check that a config value is present.

### `GET /catalog`
Lists all books currently loaded from the Kiwix OPDS catalog.

### `POST /catalog/refresh`
Forces a re-scan of the Kiwix catalog without restarting the container.

### `GET /cache`
Shows all current result cache entries with age and remaining TTL.

### `POST /cache/clear`
Clears all result cache entries from memory and disk.

### `GET /cache/routing`
Shows all current routing cache entries — source and Kiwix book selection decisions cached to avoid redundant LLM calls. Bounded at `ROUTING_CACHE_MAX_SIZE` (default 1000), evicting the oldest entry once full.

### `POST /cache/routing/clear`
Clears all routing cache entries from memory and disk.

### `GET /backup`
Downloads a tarball of all Mnemolis data — result cache, routing cache, query log, and snapshot history. See [Backup & Restore](#backup--restore) below.

### `GET /backup/info`
Shows file sizes and last-modified times for each data file without creating a backup.

### `GET /areas`
Lists all detected Home Assistant areas with entity counts and matching natural-language aliases.

### `GET /changes`
Returns meaningful changes detected across snapshot sources within the last N hours. Optional `?hours=N` parameter (default 24). Detects service outages and recoveries, forecast temperature shifts above `FORECAST_TEMP_CHANGE_THRESHOLD` (default 5°), precipitation changes, and new news headlines.

### `POST /snapshots/trigger`
Manually trigger all snapshot jobs immediately.

### `GET /logs`
Returns recent query log entries — timestamp, query, source requested, source used, cached flag, success, latency in milliseconds, and whether a `FALLBACK_CHAIN` fallback occurred (e.g. a `kiwix` request that resolved to `web`). Optional `?limit=N` parameter (default 50).

### `POST /logs/clear`
Clears all query log entries.

### `GET /logs/stats`
Returns query log statistics — Time To First Knowledge (TTFK), cache hit rate, success rate, fallback count and rate, average latency by source, top 10 most-asked queries, unique query count, and learned query count.

Fallback statistics are reported as `fallback_by_target` rather than by original source — when multiple sources share the same fallback target (`kiwix` and `news` both fall back to `web`), a single boolean column genuinely cannot distinguish which one triggered any individual fallback, so this is reported as an honest, combined label (e.g. `kiwix_or_news_fallback_to_web`) instead of guessing at an attribution the underlying data doesn't actually support.

## Caching

Mnemolis caches results in memory and persists them to disk so the cache survives container restarts. TTLs are set per source:

| Source | TTL |
|--------|-----|
| `kiwix` | 24 hours |
| `web` | 1 hour |
| `fusion` | 30 minutes |
| `forecast` | 30 minutes |
| `news` | 15 minutes |
| `uptime` | 1 minute |
| `ha` | 30 seconds |

Routing decisions (which source, Kiwix books, and fusion source sets to use) are cached separately for 1 hour.

## MCP

Mnemolis exposes an MCP server via SSE at `/mcp/sse` on the same port as the REST API.

### Connecting Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mnemolis": {
      "url": "http://your-host-ip:8888/mcp/sse"
    }
  }
}
```

### Connecting any MCP client

SSE endpoint: `http://your-host-ip:8888/mcp/sse`

The MCP server exposes a single `search` tool with the same interface as the REST API, including `fusion_sources` support.

## Kiwix ZIM files

Download ZIM files from [library.kiwix.org](https://library.kiwix.org) and place them in `./data/kiwix/`. The example compose mounts this directory into the Kiwix container automatically.

Popular ZIMs for a homelab stack:
- `wikipedia_en_all_maxi` — full English Wikipedia
- `unix.stackexchange.com_en_all` — Unix & Linux Stack Exchange
- `raspberrypi.stackexchange.com_en_all` — Raspberry Pi Stack Exchange
- `ifixit_en_all` — iFixit repair guides
- `freecodecamp_en_all` — FreeCodeCamp
- `devdocs_en_python` — Python DevDocs

### Multi-book fusion

When a query genuinely spans multiple books — "python raspberry pi gpio setup" touching both Python and Raspberry Pi Stack Exchange — Mnemolis merges the best result from each relevant book instead of returning only the single highest-scoring article. Fusion only triggers when a second or third book's top result scores within 50% of the leading book's score, so an LLM book-selection misfire doesn't inject an irrelevant book into an otherwise clean answer.

Raise `KIWIX_MAX_BOOKS` (default 2) to let the LLM select more books per query for broader multi-book fusion — useful if you have the GPU headroom for more concurrent Kiwix requests per search.

## Confidence-aware fusion (web & news)

Unlike Kiwix, the web (SearXNG) and news (FreshRSS) sources don't have a built-in relevance ranking worth trusting on its own. Mnemolis scores every result against the query using stemmed keyword overlap, penalizes homepage/about-page results that describe a site rather than containing an actual article, and drops anything scoring at or below `WEB_NEWS_SCORE_THRESHOLD` before capping the survivors at `WEB_NEWS_TOP_N`.

For web search specifically, Mnemolis also tries a second, differently-phrased version of longer queries (3+ words) when an LLM is configured — merging results from both searches and scoring everything against your original query, not the alternate phrasing. This catches results that one specific wording of a question would have missed. Duplicate articles reached via slightly different URLs (`www.` vs not, trailing slash, query string) are recognized as the same result and merged.

This doesn't apply to FreshRSS, which fetches and locally re-scores your existing feed items rather than issuing a remote query — there's no alternate phrasing to act on there.

## Adding a New Source

1. Create `app/sources/your_source.py` with a `search(query: str) -> str` function
2. Add any config vars to `app/config.py` and `docker-compose.yml`
3. Import and register it in `app/router.py` — add to `SOURCE_MAP`, `INTENT_MAP`, `SOURCE_DESCRIPTIONS`, and `CACHE_TTL`
4. Rebuild: `docker compose up -d --build`

The new source is automatically available via both REST and MCP — and immediately fusable with any other source.

## Backup & Restore

All Mnemolis state — result cache, routing cache, query log, and snapshot history — lives in four files under `/app/data`, backed by the `mnemolis_data` Docker volume (see the volume naming note below for how Docker Compose actually names it).

### Backing up

```bash
curl -o mnemolis-backup.tar.gz http://your-host:8888/backup
```

Check what would be included first:

```bash
curl -s http://your-host:8888/backup/info | python3 -m json.tool
```

Automate it with cron:

```bash
0 3 * * * curl -s -o /path/to/backups/mnemolis-$(date +\%Y\%m\%d).tar.gz http://your-host:8888/backup
```

### A note on volume naming

Docker Compose automatically prefixes named volumes with your **project name**, which defaults to the name of the folder `docker-compose.yml` lives in. If your folder is `minisearch/`, a volume named `mnemolis_data` in the YAML actually gets created as `minisearch_mnemolis_data` — not `mnemolis_data`.

This matters when restoring or migrating data manually with `docker run -v`. Always check the real volume name first:

```bash
docker volume ls | grep data
# or, for a running container:
docker inspect mnemolis --format '{{json .Mounts}}' | python3 -m json.tool
```

Use the exact name Docker reports — not the bare name written in `docker-compose.yml` — in any manual `docker run -v` commands. Set `COMPOSE_PROJECT_NAME` in a `.env` file alongside `docker-compose.yml` if you want a stable, predictable volume prefix regardless of folder name:

```bash
echo "COMPOSE_PROJECT_NAME=mnemolis" > .env
```

### Restoring

```bash
# Stop the container
docker compose down

# Extract the backup into the data volume
docker run --rm -v mnemolis_data:/app/data -v $(pwd):/backup alpine \
  sh -c "cd /app/data && tar xzf /backup/mnemolis-backup.tar.gz"

# Restart
docker compose up -d
```

### What's NOT in the backup

Kiwix ZIM files, your `docker-compose.yml` configuration, and `searxng/settings.yml` are not included — back those up separately as part of your normal homelab backup routine. The `/backup` endpoint only covers Mnemolis's own state: caches, logs, and snapshot history.

## Running Tests

```bash
docker exec mnemolis python3 -m pytest /app/tests/ -v
```

For load testing:

```bash
pip install locust
locust -f tests/locustfile.py --host http://your-host:8888
```

See `BENCHMARKS.md` for documented results.

875 tests covering FastAPI endpoints, API key authentication, HA area discoverability, backup/restore, intent routing with accurate fallback-source reporting and discourse-framing routing bias, fallback occurrence detection and reporting, bounded routing cache eviction, query decomposition with stop-word-based content detection/colloquial phrase handling/mixed-conjunction-type splitting/proper-noun-pair protection, conditional query detection with honest scoped yes/no interpretation and recursive sub-query re-detection, time-window phrase resolution, multi-keyword fusion escalation, cache logic and persistence, routing cache, Kiwix scoring/stemming/catalog parsing/book selection/multi-candidate search term disambiguation with corrected eligibility checks/multi-book fusion/discourse-framing phrase stripping, shared web/news relevance scoring with generic-result penalty and URL normalization, multi-query expansion, definitional query detection including colloquial patterns, list article penalties, HA area detection, the core HA entity matching engine, search term cleaning and contraction normalization, FreshRSS authentication with recency-aware scoring, forecast formatting/location attribution/configurable thresholds, uptime heartbeat parsing, fusion validation/header formatting/configurable limits, LLM client behavior for both Ollama and OpenAI-compatible backends, MCP tool server dispatch, snapshot diff engines and scheduled job functions with configurable thresholds, application logging configuration, SQL injection and security hardening, Hypothesis property-based fuzz testing, concurrency safety, settings configuration, all source modules via mocking, and Home Assistant entity filtering.

## Project Structure

```
Mnemolis/
├── Dockerfile
├── docker-compose.yml              # your config (not committed)
├── docker-compose.example.yml      # full stack example
├── requirements.txt
├── pytest.ini
├── CHANGELOG.md
├── BENCHMARKS.md
├── mnemolis_tool.py                # Open WebUI bridge tool
├── README.md
├── searxng/
│   └── settings.yml               # SearXNG config with JSON enabled
├── tests/
│   ├── test_router.py              # intent detection, cache, decomposition, conditional detection, time-window resolution
│   ├── test_routing_cache.py       # routing cache logic and corruption handling
│   ├── test_cache_persistence.py   # cache eviction, disk persistence, .corrupt recovery
│   ├── test_config.py              # settings defaults and env isolation
│   ├── test_kiwix.py               # scoring, stemming, search term cleaning, discourse-framing phrase stripping (pure logic)
│   ├── test_kiwix_network.py       # catalog parsing, book selection, disambiguation, multi-book fusion
│   ├── test_freshrss.py            # general query detection, recency bonus
│   ├── test_freshrss_network.py    # FreshRSS network calls via mocking
│   ├── test_forecast.py            # forecast parsing, location attribution, configurable thresholds
│   ├── test_searxng.py             # SearXNG search, query expansion, scoring integration
│   ├── test_scoring.py             # shared web/news relevance scoring, generic-result penalty, URL normalization
│   ├── test_query_expansion.py     # alternate phrasing generation and sanity checks
│   ├── test_uptime_kuma.py         # Uptime Kuma status parsing via mocking
│   ├── test_fusion.py              # fusion merging, truncation, deduplication, header formatting
│   ├── test_home_assistant.py      # HA entity filtering, area detection, the core matching engine
│   ├── test_main.py                # FastAPI endpoint tests, API key auth, catalog/areas endpoints
│   ├── test_llm.py                 # Ollama/OpenAI-compatible LLM client behavior
│   ├── test_mcp_server.py          # MCP tool schema and call dispatch
│   ├── test_snapshots.py           # snapshot diff engines, net-change collapsing
│   ├── test_snapshot_jobs.py       # scheduled snapshot job functions
│   ├── test_security.py            # SQL injection, path traversal, fuzz, concurrency
│   ├── test_property.py            # Hypothesis property-based fuzz testing
│   └── locustfile.py               # Locust load testing suite
└── app/
    ├── main.py                     # FastAPI app + MCP mount + cache/catalog/areas endpoints + API key auth
    ├── snapshots.py                # Snapshot engine — scheduler, diff logic, change detection
    ├── mcp_server.py               # MCP SSE server
    ├── router.py                   # Intent detection, source routing, decomposition, conditional detection, caching
    ├── llm.py                      # LLM client — Ollama native and OpenAI-compatible
    ├── scoring.py                  # Shared relevance scoring for web/news — keyword overlap, generic-result penalty
    ├── query_expansion.py          # Alternate query phrasing for web search multi-query expansion
    ├── config.py                   # Settings via environment variables
    └── sources/
        ├── kiwix.py                # Offline knowledge base — catalog, disambiguation, multi-book fusion, discourse-framing phrase stripping
        ├── forecast.py             # Open-Meteo weather forecast
        ├── freshrss.py             # FreshRSS RSS reader with confidence-aware scoring
        ├── searxng.py              # SearXNG web search with multi-query expansion
        ├── uptime_kuma.py          # Uptime Kuma service monitoring
        ├── home_assistant.py       # Home Assistant entity state summaries, area awareness
        └── fusion.py               # Multi-source concurrent fusion with descriptive headers
```

## Philosophy

Local-first, privacy-preserving, subscription-free. Mnemolis is designed for homelabs where the data stays home. Open-Meteo is the only external network call — every other source (Kiwix, FreshRSS, SearXNG, Uptime Kuma, Home Assistant) runs on your own infrastructure.

## Contributing

PRs welcome. New source modules are the easiest contribution — drop a file in `sources/`, register it in the router, done. The new source is immediately available via REST, MCP, and fusion with no additional work.

### Proposed modules

Looking for contributors interested in building out additional sources:

- **Jellyfin** — search local media library by title, genre, or actor
- **Paperless-ngx** — search scanned documents and OCR'd content
- **Mealie** — search self-hosted recipe library
- **Grocy** — query pantry inventory, shopping list, or expiring items
- **Calibre** — search local ebook library
- **Navidrome** — search self-hosted music library by artist, album, or track
- **Immich** — search local photo library by date, album, or description

Each source only needs a single `search(query: str) -> str` function. See any existing file in `app/sources/` as a reference.

## Part of the Mnemo-net stack

- [Mnemolis Intents](https://github.com/immortalbob/mnemolis_intents) — native Home Assistant LLM integration for Mnemolis
- [Mnemovox-T7S3](https://github.com/immortalbob/Mnemovox-T7S3) — ESP32-S3 voice satellite with CO2, temperature, and humidity sensing

## License

MIT — see [LICENSE](LICENSE)
