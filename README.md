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
        Partial failure OK — best effort
                     │
      Merge with [SOURCE] attribution headers
                     │
               Single Response
```

Fusion queries all specified sources concurrently, filters empty or failed results, and merges the remainder with source attribution headers. If only one source returns results, it is returned directly without headers.

### Query Decomposition

```text
   source="auto"
        │
        ▼
 Conjunction scan
 "and", "also", "plus", "as well as"
        │
        ▼
 Nosplit check
 "compare", "vs", "between", etc.
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
   │             │
   └─────────────┤
                 ▼
          Single Response
     with [SOURCE] attribution
```

Decomposition only applies to `source="auto"`. Explicit source requests (`source="kiwix"`) skip decomposition entirely. Consecutive results from the same source are merged under a single header — "indoor air quality and are the doors locked" returns one `[HA]` block, not two.

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
| `KIWIX_SEARCH_LIMIT` | Results requested per book per Kiwix search — higher values help the scoring function find the right answer among brand-name collisions | `15` |
| `KIWIX_MAX_BOOKS` | Maximum number of Kiwix books the LLM can select for a single query — raise for broader multi-book fusion | `2` |

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

### LLM-assisted routing
Mnemolis uses a local LLM backend in three ways:

1. **Source selection** — when `auto` is used and no keyword matches, the LLM picks the best source based on the query. For complex multi-topic queries it returns multiple sources, triggering fusion automatically.
2. **Book selection** — once routed to Kiwix, the LLM picks the best 1-2 ZIM books from your catalog for the query
3. **Fusion source selection** — when `fusion` is used without specifying sources, the LLM picks the best 2-3 sources for the query

**Auto-fusion escalation** — `source="auto"` now detects multi-topic queries at the keyword level too. If a query matches triggers from multiple sources (e.g. "weather" + "services up"), fusion is triggered automatically without an LLM call.

Routing decisions are cached for 1 hour so repeated queries skip the LLM call entirely.

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
Returns status, number of Kiwix books loaded, cache entry count, and connectivity status for every configured source.

### `GET /catalog`
Lists all books currently loaded from the Kiwix OPDS catalog.

### `POST /catalog/refresh`
Forces a re-scan of the Kiwix catalog without restarting the container.

### `GET /cache`
Shows all current result cache entries with age and remaining TTL.

### `POST /cache/clear`
Clears all result cache entries from memory and disk.

### `GET /cache/routing`
Shows all current routing cache entries — source and Kiwix book selection decisions cached to avoid redundant LLM calls.

### `POST /cache/routing/clear`
Clears all routing cache entries from memory and disk.

### `GET /backup`
Downloads a tarball of all Mnemolis data — result cache, routing cache, query log, and snapshot history. See [Backup & Restore](#backup--restore) below.

### `GET /backup/info`
Shows file sizes and last-modified times for each data file without creating a backup.

### `GET /areas`
Lists all detected Home Assistant areas with entity counts and matching natural-language aliases.

### `GET /changes`
Returns meaningful changes detected across snapshot sources within the last N hours. Optional `?hours=N` parameter (default 24). Detects service outages and recoveries, forecast temperature shifts ≥5°, precipitation changes, and new news headlines.

### `POST /snapshots/trigger`
Manually trigger all snapshot jobs immediately.

### `GET /logs`
Returns recent query log entries — timestamp, query, source used, cached flag, success, and latency in milliseconds. Optional `?limit=N` parameter (default 50).

### `POST /logs/clear`
Clears all query log entries.

### `GET /logs/stats`
Returns query log statistics — Time To First Knowledge (TTFK), cache hit rate, success rate, average latency by source, top 10 most-asked queries, unique query count, and learned query count.

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

## Adding a New Source

1. Create `app/sources/your_source.py` with a `search(query: str) -> str` function
2. Add any config vars to `app/config.py` and `docker-compose.yml`
3. Import and register it in `app/router.py` — add to `SOURCE_MAP`, `INTENT_MAP`, `SOURCE_DESCRIPTIONS`, and `CACHE_TTL`
4. Rebuild: `docker compose up -d --build`

The new source is automatically available via both REST and MCP — and immediately fusable with any other source.

## Backup & Restore

All Mnemolis state — result cache, routing cache, query log, and snapshot history — lives in four files under `/app/data`, backed by the `minisearch_data` Docker volume.

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

699 tests covering FastAPI endpoints, API key authentication, HA area discoverability, backup/restore, intent routing, query decomposition, time-window phrase resolution, multi-keyword fusion escalation, cache logic and persistence, routing cache, Kiwix scoring/stemming/catalog parsing/book selection/multi-candidate search term disambiguation/multi-book fusion, definitional query detection, list article penalties, HA area detection, the core HA entity matching engine, search term cleaning, FreshRSS authentication, forecast formatting/location attribution/configurable thresholds, uptime heartbeat parsing, fusion validation/header formatting/configurable limits, LLM client behavior for both Ollama and OpenAI-compatible backends, MCP tool server dispatch, snapshot diff engines and scheduled job functions with configurable thresholds, SQL injection and security hardening, Hypothesis property-based fuzz testing, concurrency safety, settings configuration, all source modules via mocking, and Home Assistant entity filtering.

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
│   ├── test_router.py              # intent detection, cache, fallback logic
│   ├── test_routing_cache.py       # routing cache logic
│   ├── test_kiwix.py               # scoring and search term cleaning
│   ├── test_freshrss.py            # general query detection, article scoring
│   ├── test_freshrss_network.py    # FreshRSS network calls via mocking
│   ├── test_forecast.py            # forecast parsing and thresholds via mocking
│   ├── test_searxng.py             # SearXNG search and guard via mocking
│   ├── test_uptime_kuma.py         # Uptime Kuma status parsing via mocking
│   ├── test_fusion.py              # fusion merging, truncation, deduplication, same-source merging
│   ├── test_home_assistant.py      # HA entity filtering, exclusions, formatting
│   ├── test_main.py                # FastAPI endpoint tests
│   ├── test_snapshots.py            # snapshot diff engine tests
│   └── locustfile.py               # Locust load testing suite
└── app/
    ├── main.py                     # FastAPI app + MCP mount + cache/catalog endpoints
    ├── snapshots.py                # Snapshot engine — scheduler, diff logic, change detection
    ├── mcp_server.py               # MCP SSE server
    ├── router.py                   # Intent detection, source routing, and caching
    ├── llm.py                      # LLM client — Ollama native and OpenAI-compatible
    ├── config.py                   # Settings via environment variables
    └── sources/
        ├── kiwix.py                # Offline knowledge base — dynamic catalog + LLM routing
        ├── forecast.py             # Open-Meteo weather forecast
        ├── freshrss.py             # FreshRSS RSS reader
        ├── searxng.py              # SearXNG web search
        ├── uptime_kuma.py          # Uptime Kuma service monitoring
        ├── home_assistant.py       # Home Assistant entity state summaries
        └── fusion.py               # Multi-source concurrent fusion
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

## Part of the MiniNet stack

- [Mnemolis Intents](https://github.com/immortalbob/mnemolis_intents) — native Home Assistant LLM integration for Mnemolis
- [Mnemovox-T7S3](https://github.com/immortalbob/Mnemovox-T7S3) — ESP32-S3 voice satellite with CO2, temperature, and humidity sensing

## License

MIT — see [LICENSE](LICENSE)
