import asyncio
import logging
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

_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Kiwix catalog and cache on startup."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, get_books)
    await loop.run_in_executor(None, load_cache)
    await loop.run_in_executor(None, load_routing_cache)
    yield


app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API with multi-source fusion. Routes queries to Kiwix, Open-Meteo, FreshRSS, SearXNG, Uptime Kuma, or multiple sources concurrently.",
    version="3.2.0",
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


@app.get("/health")
def health():
    """
    Health check. Returns container status, number of Kiwix books loaded,
    and current result cache entry count.
    """
    books = get_books()
    return {
        "status": "ok",
        "kiwix_books_loaded": len(books),
        "cache_entries": get_cache_count(),
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

    try:
        result = route(request.query, request.source, request.fusion_sources)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result=result,
            success=True,
            cached=was_cached,
        )
    except Exception as e:
        _LOGGER.error("Search failed for query '%s': %s", request.query, e)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result="",
            success=False,
            cached=False,
            error=str(e),
        )
