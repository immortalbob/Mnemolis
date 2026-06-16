# MiniSearch

A unified local knowledge search API for self-hosted homelabs. MiniSearch runs as a Docker container on your internal network and routes queries to the appropriate backend — offline knowledge, weather forecast, RSS news, or live web search — via a single endpoint.

Exposes both a **REST API** and an **MCP server** so any client can connect to it.

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
# Fill in FRESHRSS_USER, FRESHRSS_API_PASSWORD, your coordinates, and secret_key in searxng/settings.yml

docker compose up -d
```

### What's not in the full stack
The example compose intentionally excludes Home Assistant and Ollama — these are typically long-running services with their own existing setup and shouldn't be managed by MiniSearch's compose file.

If you're running Ollama or Home Assistant in Docker and want them reachable by MiniSearch, make sure they're also connected to `ai-net`:

```bash
docker network connect ai-net ollama
docker network connect ai-net homeassistant
```

Or add `ai-net` to their existing compose files under `networks:`. Container name resolution only works between containers on the same network — if MiniSearch can't reach Ollama by hostname, check network membership first.

### MiniSearch only

If you already have Kiwix, FreshRSS, and SearXNG running:

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

### Kiwix LLM-assisted book routing
MiniSearch uses Ollama to automatically select the best ZIM book for a given query. The book list is built dynamically from your Kiwix catalog at startup — no hardcoded list, no rebuild needed when you add new ZIMs.

| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_URL` | Ollama API endpoint | `http://192.168.3.162:11434` |
| `OLLAMA_MODEL` | Model to use for book selection | `qwen3:8b` |

Set these to your Ollama instance and preferred model. If left blank, MiniSearch falls back to Wikipedia for all Kiwix queries.

To force a catalog refresh after adding new ZIMs (without restarting the container):
```bash
curl -X POST http://your-host:8888/catalog/refresh
```

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
  "result": "# Molybdenum\nSource: wikipedia_en_all_maxi_2026-02\n\n..."
}
```

### `GET /sources`
Returns the list of available sources.

### `GET /health`
Health check.

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
3. Import and register it in `app/router.py` — add to `SOURCE_MAP` and optionally `INTENT_MAP`
4. Rebuild: `docker compose up -d --build`

The new source is automatically available via both REST and MCP.

## Project Structure

```
MiniSearch/
├── Dockerfile
├── docker-compose.yml              # your config (not committed)
├── docker-compose.example.yml      # full stack example
├── requirements.txt
├── minisearch_tool.py              # Open WebUI bridge tool
├── README.md
├── searxng/
│   └── settings.yml               # SearXNG config with JSON enabled
└── app/
    ├── main.py                     # FastAPI app + MCP mount
    ├── mcp_server.py               # MCP SSE server
    ├── router.py                   # Intent detection and source routing
    ├── config.py                   # Settings via environment variables
    └── sources/
        ├── kiwix.py                # Offline knowledge base
        ├── forecast.py             # Open-Meteo weather forecast
        ├── freshrss.py             # FreshRSS RSS reader
        └── searxng.py              # SearXNG web search
```

## Philosophy

Local-first, privacy-preserving, subscription-free. MiniSearch is designed for homelabs where the data stays home. Open-Meteo is the only external call — everything else routes to services you control.

## Roadmap

- [ ] Per-source result caching
- [ ] Additional source modules (Home Assistant, Jellyfin, etc.)

## Contributing

PRs welcome. New source modules are the easiest contribution — drop a file in `sources/`, register it in the router, done.
