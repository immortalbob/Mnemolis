# MiniSearch

A unified local knowledge search API for self-hosted homelabs. MiniSearch runs as a Docker container on your internal network and routes queries to the appropriate backend — offline knowledge, weather forecast, RSS news, or live web search — via a single endpoint.

## Integrations

MiniSearch is currently consumed by two clients:

- **[minisearch_tool.py](minisearch_tool.py)** — Open WebUI tool that gives your chat models a single `search()` function routing to all four backends
- **[MiniSearch Intents](https://github.com/immortalbob/minisearch_intents)** — Home Assistant custom integration that registers MiniSearch as a native LLM API, selectable from any HA conversation agent (Ollama, OpenAI, etc.)

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
- An existing Docker network (default: `ai-net`)
- One or more of the supported backends running and reachable on the same network

## Quick Start

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
MiniSearch queries SearXNG's JSON API. Make sure `json` is enabled in your SearXNG `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

## API

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

## Adding a New Source

1. Create `app/sources/your_source.py` with a `search(query: str) -> str` function
2. Add any config vars to `app/config.py` and `docker-compose.yml`
3. Import and register it in `app/router.py` — add to `SOURCE_MAP` and optionally `INTENT_MAP`
4. Rebuild: `docker compose up -d --build`

## Project Structure

```
MiniSearch/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── minisearch_tool.py      # Open WebUI bridge tool
├── README.md
└── app/
    ├── main.py             # FastAPI app
    ├── router.py           # Intent detection and source routing
    ├── config.py           # Settings via environment variables
    └── sources/
        ├── kiwix.py        # Offline knowledge base
        ├── forecast.py     # Open-Meteo weather forecast
        ├── freshrss.py     # FreshRSS RSS reader
        └── searxng.py      # SearXNG web search
```

## Philosophy

Local-first, privacy-preserving, subscription-free. MiniSearch is designed for homelabs where the data stays home. Open-Meteo is the only external call — everything else routes to services you control.

## Roadmap

- [ ] Per-source result caching
- [ ] Additional source modules (Home Assistant, Jellyfin, etc.)

## Contributing

PRs welcome. New source modules are the easiest contribution — drop a file in `sources/`, register it in the router, done.
