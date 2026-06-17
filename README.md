# MiniSearch

A unified local knowledge search API for self-hosted homelabs. MiniSearch runs as a Docker container on your internal network and routes queries to the appropriate backend — offline knowledge, weather forecast, RSS news, live web search, or service monitoring — via a single endpoint.

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
 MiniSearch_Intents
          │
          ▼
     MiniSearch
          │
          ├──────────────┐
          │              │
          ▼              ▼
      LLM Backend   Source Providers
          │        ├─ Kiwix
          │        ├─ FreshRSS
          │        ├─ SearXNG
          │        ├─ Open-Meteo
          │        └─ Uptime Kuma
          │
          ▼
   Source Selection
   Book Selection
   Query Routing
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
                Open WebUI
                     │
                REST API
                     │

Claude Desktop ──────┼────── MCP

Cursor ──────────────┼────── MCP

                     ▼

               MiniSearch

                     ▲

                     │ REST API

                     ▲

          MiniSearch_Intents

                     ▲

                     │

            Home Assistant
```

## Integrations

| Client | Protocol | How |
|--------|----------|-----|
| [Open WebUI](minisearch_tool.py) | REST | Lightweight tool that POSTs to `/search` |
| [MiniSearch Intents](https://github.com/immortalbob/minisearch_intents) | REST | Native HA LLM API integration |
| Any MCP client (Claude Desktop, Cursor, etc.) | MCP/SSE | Connect to `http://your-host:8888/mcp/sse` |

## Sources

| Source | Backend | Description |
|--------|---------|-------------|
| `kiwix` | [Kiwix](https://www.kiwix.org/) | Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs |
| `forecast` | [Open-Meteo](https://open-meteo.com/) | 3-day weather forecast, no API key required |
| `news` | [FreshRSS](https://freshrss.github.io/FreshRSS/) | Recent articles from your RSS feeds via GReader API |
| `web` | [SearXNG](https://searxng.github.io/searxng/) | Live web search via your local SearXNG instance |
| `uptime` | [Uptime Kuma](https://uptime.kuma.pet/) | Service monitor status — reports any down services |
| `auto` | — | MiniSearch detects intent and picks the best source |

## Requirements

- Docker + Docker Compose
- A Docker network for container communication (default: `ai-net`)
- One or more of the supported backends running and reachable on the same network

## Quick Start

### Full stack (recommended)

The repo includes an example compose file and SearXNG config to get all services running together:

```bash
git clone https://github.com/immortalbob/MiniSearch
cd MiniSearch

# Create the shared network if it doesn't exist
docker network create ai-net

# Copy and edit the example compose file
cp docker-compose.example.yml docker-compose.yml
# Fill in credentials, your coordinates, and secret_key in searxng/settings.yml

docker compose up -d
```

### What's not in the full stack
The example compose intentionally excludes Home Assistant, your LLM backend, and Uptime Kuma — these are typically long-running services with their own existing setup.

If you're running any of these in Docker and want them reachable by MiniSearch, connect them to `ai-net`:

```bash
docker network connect ai-net ollama
docker network connect ai-net homeassistant
```

### MiniSearch only

If you already have the backends running:

```bash
git clone https://github.com/immortalbob/MiniSearch
cd MiniSearch
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
| `FORECAST_LATITUDE` | Forecast location latitude | `35.1894` |
| `FORECAST_LONGITUDE` | Forecast location longitude | `-114.0530` |
| `FORECAST_LOCATION_NAME` | Human-readable location name | `Kingman, Arizona` |
| `FORECAST_TIMEZONE` | Timezone for forecast times | `America/Phoenix` |
| `UPTIME_KUMA_URL` | Uptime Kuma URL | _(blank — disables uptime source)_ |
| `UPTIME_KUMA_USERNAME` | Uptime Kuma username | |
| `UPTIME_KUMA_PASSWORD` | Uptime Kuma password | |
| `LLM_URL` | LLM backend URL for intelligent routing | _(blank — disables LLM routing)_ |
| `LLM_MODEL` | Model to use for source and book selection | `qwen3:8b` |
| `LLM_API_TYPE` | API format: `ollama` or `openai` | `ollama` |

### FreshRSS API setup
1. Enable API access: **Administration → Authentication → Allow API access**
2. Set an API password: **Profile → API password**
3. Use that password for `FRESHRSS_API_PASSWORD` (it's separate from your login password)

### SearXNG JSON format
MiniSearch queries SearXNG's JSON API. The included `searxng/settings.yml` already has this enabled. If you're using an existing SearXNG instance, make sure `json` is in your formats list:

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
MiniSearch uses a local LLM backend in two ways:

1. **Source selection** — when `auto` is used and no keyword matches, the LLM picks the best source based on the query
2. **Book selection** — once routed to Kiwix, the LLM picks the best 1-2 ZIM books from your catalog for the query

Routing decisions are cached for 1 hour so repeated queries skip the LLM call entirely.

**Supported backends** via `LLM_API_TYPE`:
- `ollama` — Ollama native API (default). Works with any Ollama-served model.
- `openai` — OpenAI-compatible API. Works with llama-server, LM Studio, and any OpenAI-compatible endpoint.

The book list is built dynamically from your Kiwix catalog at startup — no hardcoded list, no rebuild needed when you add new ZIMs. To force a refresh after adding ZIMs:

```bash
curl -X POST http://your-host:8888/catalog/refresh
```

If `LLM_URL` is left blank, MiniSearch falls back to keyword-based routing and Wikipedia for all Kiwix queries.

## REST API

### `POST /search`

```json
{
  "query": "what is molybdenum",
  "source": "auto"
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
Returns status, number of Kiwix books loaded, and current result cache entry count.

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

## Caching

MiniSearch caches results in memory and persists them to disk so the cache survives container restarts. TTLs are set per source:

| Source | TTL |
|--------|-----|
| `kiwix` | 24 hours |
| `web` | 1 hour |
| `forecast` | 30 minutes |
| `news` | 15 minutes |
| `uptime` | 1 minute |

Routing decisions (which source and Kiwix books to use) are cached separately for 1 hour.

## MCP

MiniSearch exposes an MCP server via SSE at `/mcp/sse` on the same port as the REST API.

### Connecting Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "minisearch": {
      "url": "http://your-host-ip:8888/mcp/sse"
    }
  }
}
```

### Connecting any MCP client

SSE endpoint: `http://your-host-ip:8888/mcp/sse`

The MCP server exposes a single `search` tool with the same interface as the REST API.

## Kiwix ZIM files

Download ZIM files from [library.kiwix.org](https://library.kiwix.org) and place them in `./data/kiwix/`. The example compose mounts this directory into the Kiwix container automatically.

Popular ZIMs for a homelab stack:
- `wikipedia_en_all_maxi` — full English Wikipedia
- `unix.stackexchange.com_en_all` — Unix & Linux Stack Exchange
- `raspberrypi.stackexchange.com_en_all` — Raspberry Pi Stack Exchange
- `ifixit_en_all` — iFixit repair guides
- `freecodecamp_en_all` — FreeCodeCamp
- `devdocs_en_python` — Python DevDocs

## Adding a New Source

1. Create `app/sources/your_source.py` with a `search(query: str) -> str` function
2. Add any config vars to `app/config.py` and `docker-compose.yml`
3. Import and register it in `app/router.py` — add to `SOURCE_MAP`, `INTENT_MAP`, `SOURCE_DESCRIPTIONS`, and `CACHE_TTL`
4. Rebuild: `docker compose up -d --build`

The new source is automatically available via both REST and MCP.

## Running Tests

```bash
docker exec minisearch python3 -m pytest /app/tests/ -v
```

126 tests covering intent routing, cache logic, routing cache, Kiwix scoring, search term cleaning, FreshRSS article filtering, and all source modules via mocking.

## Project Structure

```
MiniSearch/
├── Dockerfile
├── docker-compose.yml              # your config (not committed)
├── docker-compose.example.yml      # full stack example
├── requirements.txt
├── pytest.ini
├── CHANGELOG.md
├── minisearch_tool.py              # Open WebUI bridge tool
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
│   └── test_uptime_kuma.py         # Uptime Kuma status parsing via mocking
└── app/
    ├── main.py                     # FastAPI app + MCP mount + cache/catalog endpoints
    ├── mcp_server.py               # MCP SSE server
    ├── router.py                   # Intent detection, source routing, and caching
    ├── llm.py                      # LLM client — Ollama native and OpenAI-compatible
    ├── config.py                   # Settings via environment variables
    └── sources/
        ├── kiwix.py                # Offline knowledge base — dynamic catalog + LLM routing
        ├── forecast.py             # Open-Meteo weather forecast
        ├── freshrss.py             # FreshRSS RSS reader
        ├── searxng.py              # SearXNG web search
        └── uptime_kuma.py          # Uptime Kuma service monitoring
```

## Philosophy

Local-first, privacy-preserving, subscription-free. MiniSearch is designed for homelabs where the data stays home. Open-Meteo is the only external call — everything else routes to services you control.

## Roadmap

- [ ] Additional source modules (Home Assistant, Jellyfin, etc.)
- [ ] Source fusion — query multiple sources concurrently and merge results (v3.0)

## Contributing

PRs welcome. New source modules are the easiest contribution — drop a file in `sources/`, register it in the router, done.

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

- [MiniSearch Intents](https://github.com/immortalbob/minisearch_intents) — native Home Assistant LLM integration for MiniSearch
- [MiniSense-T7S3](https://github.com/immortalbob/MiniSense-T7S3) — ESP32-S3 room sensor node with voice assistant and CO2 monitoring
