import time
import logging
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from app.router import route, SOURCE_MAP, detect_intent, _cache, CACHE_TTL
from app.mcp_server import mcp_app
from app.sources.kiwix import get_books, refresh_catalog

_LOGGER = logging.getLogger(__name__)

app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API. Routes queries to Kiwix, Open-Meteo, FreshRSS, or SearXNG.",
    version="2.3.0",
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


@app.on_event("startup")
async def startup():
    import asyncio
    from app.router import _load_cache
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_books)
    await loop.run_in_executor(None, _load_cache)


@app.get("/health")
def health():
    books = get_books()
    return {
        "status": "ok",
        "kiwix_books_loaded": len(books),
        "cache_entries": len(_cache),
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
    now = time.time()
    entries = []
    for key, (result, timestamp) in _cache.items():
        source, query = key.split(":", 1)
        ttl = CACHE_TTL.get(source, 3600)
        age = int(now - timestamp)
        entries.append({
            "source": source,
            "query": query,
            "age_seconds": age,
            "ttl_seconds": ttl,
            "expires_in": max(0, ttl - age),
        })
    return {"count": len(entries), "entries": entries}


@app.post("/cache/clear")
def cache_clear():
    """Clear all cached results."""
    from app.router import _save_cache
    count = len(_cache)
    _cache.clear()
    _save_cache()
    return {"status": "cleared", "entries_removed": count}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    from app.router import _get_cached
    resolved_source = request.source if request.source != "auto" else detect_intent(request.query)

    # Check if result will be from cache for response metadata
    was_cached = _get_cached(resolved_source, request.query) is not None

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
