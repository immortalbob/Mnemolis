import asyncio
import logging
import sqlite3
import time
import requests
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from app.router import (
    route,
    SOURCE_MAP,
    detect_intent,
    check_cached,
    get_cache_stats,
    get_cache_count,
    clear_cache,
    load_cache,
    load_routing_cache,
    get_routing_cache_stats,
    clear_routing_cache,
)
from app.mcp_server import mcp_app
from app.sources.kiwix import get_books, refresh_catalog
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query logging — SQLite
# ---------------------------------------------------------------------------

_LOG_DB = "/app/data/query_log.db"


def _init_log_db():
    """Create query log table if it doesn't exist."""
    try:
        con = sqlite3.connect(_LOG_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                query TEXT NOT NULL,
                source_requested TEXT NOT NULL,
                source_used TEXT NOT NULL,
                cached INTEGER NOT NULL,
                success INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL
            )
        """)
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not initialize query log db: %s", e)


def _log_query(query: str, source_requested: str, source_used: str, cached: bool, success: bool, latency_ms: int):
    """Write a query log entry."""
    try:
        con = sqlite3.connect(_LOG_DB)
        con.execute(
            "INSERT INTO query_log (timestamp, query, source_requested, source_used, cached, success, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), query, source_requested, source_used, int(cached), int(success), latency_ms)
        )
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not write query log: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Kiwix catalog and cache on startup."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, get_books)
    await loop.run_in_executor(None, load_cache)
    await loop.run_in_executor(None, load_routing_cache)
    await loop.run_in_executor(None, _init_log_db)
    yield


app = FastAPI(
    title="Mnemolis",
    description="Unified local knowledge search API with multi-source fusion. Routes queries to Kiwix, Open-Meteo, FreshRSS, SearXNG, Uptime Kuma, or multiple sources concurrently.",
    version="3.5.0",
    lifespan=lifespan,
)

app.mount("/mcp", mcp_app)


class SearchRequest(BaseModel):
    query: str
    source: str = "auto"
    fusion_sources: list[str] | None = None  # only used when source="fusion"


class SearchResponse(BaseModel):
    query: str
    source_used: str
    result: str
    success: bool
    cached: bool = False
    error: Optional[str] = None


def _check_kiwix() -> dict:
    books = get_books()
    if not settings.kiwix_url:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.kiwix_url}/catalog/v2/entries?count=1", timeout=3)
        resp.raise_for_status()
        return {"status": "ok", "books": len(books)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_forecast() -> dict:
    if not settings.forecast_latitude or not settings.forecast_longitude:
        return {"status": "not_configured"}
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": settings.forecast_latitude, "longitude": settings.forecast_longitude, "current": "temperature_2m"},
            timeout=5,
        )
        resp.raise_for_status()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_news() -> dict:
    if not settings.freshrss_url or not settings.freshrss_user:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.freshrss_url}/api/greader.php/accounts/ClientLogin", timeout=3)
        # 401 means reachable but needs auth — that's fine, service is up
        if resp.status_code in (200, 401, 400):
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_web() -> dict:
    if not settings.searxng_url:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.searxng_url}/healthz", timeout=3)
        if resp.status_code in (200, 404):  # 404 means SearXNG is up but no /healthz route
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_uptime() -> dict:
    if not settings.uptime_kuma_url or not settings.uptime_kuma_username:
        return {"status": "not_configured"}
    try:
        resp = requests.get(settings.uptime_kuma_url, timeout=3)
        if resp.status_code in (200, 301, 302):
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_ha() -> dict:
    if not settings.ha_url or not settings.ha_token:
        return {"status": "not_configured"}
    try:
        resp = requests.get(
            f"{settings.ha_url}/api/",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=3,
        )
        if resp.status_code == 200:
            return {"status": "ok"}
        if resp.status_code == 401:
            return {"status": "error", "error": "Invalid token"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_llm() -> dict:
    if not settings.llm_url or not settings.llm_model:
        return {"status": "not_configured"}
    try:
        if settings.llm_api_type == "openai":
            resp = requests.get(f"{settings.llm_url}/v1/models", timeout=3)
        else:
            resp = requests.get(f"{settings.llm_url}/api/tags", timeout=3)
        if resp.status_code == 200:
            return {"status": "ok", "model": settings.llm_model, "api_type": settings.llm_api_type}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/health")
def health():
    """
    Health check. Returns container status, Kiwix books loaded, cache entry count,
    and connectivity status for every configured source.
    """
    books = get_books()
    sources = {
        "kiwix": _check_kiwix(),
        "forecast": _check_forecast(),
        "news": _check_news(),
        "web": _check_web(),
        "uptime": _check_uptime(),
        "ha": _check_ha(),
        "llm": _check_llm(),
    }
    # Overall status — ok if container is running regardless of source health
    return {
        "status": "ok",
        "kiwix_books_loaded": len(books),
        "cache_entries": get_cache_count(),
        "sources": sources,
    }


@app.get("/sources")
def list_sources():
    """List all available search sources including 'auto'."""
    return {"sources": list(SOURCE_MAP.keys()) + ["auto"]}


@app.get("/catalog")
def catalog():
    """List all books currently loaded from the Kiwix OPDS catalog."""
    books = get_books()
    return {"count": len(books), "books": books}


@app.post("/catalog/refresh")
def catalog_refresh():
    """
    Force a re-scan of the Kiwix OPDS catalog without restarting the container.
    Use this after adding new ZIM files to Kiwix.
    """
    books = refresh_catalog()
    return {"status": "refreshed", "count": len(books)}


@app.get("/cache")
def cache_stats():
    """
    Show all current result cache entries with age and remaining TTL.
    Result cache stores actual search results keyed by source and query.
    """
    entries = get_cache_stats()
    return {"count": len(entries), "entries": entries}


@app.post("/cache/clear")
def cache_clear():
    """Clear all result cache entries from memory and disk."""
    count = clear_cache()
    return {"status": "cleared", "entries_removed": count}


@app.get("/cache/routing")
def routing_cache_stats():
    """
    Show all current routing cache entries with age and remaining TTL.
    Routing cache stores source and Kiwix book selection decisions to avoid
    redundant Ollama calls for repeated queries.
    """
    entries = get_routing_cache_stats()
    return {"count": len(entries), "entries": entries}


@app.post("/cache/routing/clear")
def routing_cache_clear():
    """Clear all routing cache entries from memory and disk."""
    count = clear_routing_cache()
    return {"status": "cleared", "entries_removed": count}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    if request.source == "auto":
        intent = detect_intent(request.query)
        if isinstance(intent, list):
            resolved_source = "fusion"
            was_cached = check_cached("fusion", f"fusion[{','.join(sorted(intent))}]:{request.query}")
        else:
            resolved_source = intent
            was_cached = check_cached(resolved_source, request.query)
    else:
        resolved_source = request.source
        was_cached = check_cached(resolved_source, request.query)

    start = time.monotonic()
    try:
        result = route(request.query, request.source, request.fusion_sources)
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_query(request.query, request.source, resolved_source, was_cached, True, latency_ms)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result=result,
            success=True,
            cached=was_cached,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_query(request.query, request.source, resolved_source, was_cached, False, latency_ms)
        _LOGGER.error("Search failed for query '%s': %s", request.query, e)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result="",
            success=False,
            cached=False,
            error=str(e),
        )


@app.get("/logs")
def query_logs(limit: int = 50):
    """
    Show recent query log entries. Returns the most recent queries with
    timestamp, source, cached flag, success, and latency in milliseconds.
    """
    try:
        con = sqlite3.connect(_LOG_DB)
        rows = con.execute(
            "SELECT timestamp, query, source_requested, source_used, cached, success, latency_ms FROM query_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        con.close()
        entries = [
            {
                "timestamp": r[0],
                "query": r[1],
                "source_requested": r[2],
                "source_used": r[3],
                "cached": bool(r[4]),
                "success": bool(r[5]),
                "latency_ms": r[6],
            }
            for r in rows
        ]
        return {"count": len(entries), "entries": entries}
    except Exception as e:
        return {"count": 0, "entries": [], "error": str(e)}


@app.post("/logs/clear")
def logs_clear():
    """Clear all query log entries."""
    try:
        con = sqlite3.connect(_LOG_DB)
        cur = con.execute("DELETE FROM query_log")
        count = cur.rowcount
        con.commit()
        con.close()
        return {"status": "cleared", "entries_removed": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}
