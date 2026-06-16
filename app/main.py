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
    yield


app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API. Routes queries to Kiwix, Open-Meteo, FreshRSS, or SearXNG.",
    version="2.5.0",
    lifespan=lifespan,
)

app.mount("/mcp", mcp_app)


class SearchRequest(BaseModel):
    query: str
    source: str = "auto"


class SearchResponse(BaseModel):
    query: str
    source_used: str
    result: str
    success: bool
    cached: bool = False
    error: Optional[str] = None


@app.get("/health")
def health():
    books = get_books()
    return {
        "status": "ok",
        "kiwix_books_loaded": len(books),
        "cache_entries": get_cache_count(),
    }


@app.get("/sources")
def list_sources():
    return {"sources": list(SOURCE_MAP.keys()) + ["auto"]}


@app.get("/catalog")
def catalog():
    books = get_books()
    return {"count": len(books), "books": books}


@app.post("/catalog/refresh")
def catalog_refresh():
    books = refresh_catalog()
    return {"status": "refreshed", "count": len(books)}


@app.get("/cache")
def cache_stats():
    """Show current cache entries and their age."""
    entries = get_cache_stats()
    return {"count": len(entries), "entries": entries}


@app.post("/cache/clear")
def cache_clear():
    """Clear all cached results."""
    count = clear_cache()
    return {"status": "cleared", "entries_removed": count}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    resolved_source = (
        request.source if request.source != "auto"
        else detect_intent(request.query)
    )
    was_cached = check_cached(resolved_source, request.query)

    try:
        result = route(request.query, request.source)
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
