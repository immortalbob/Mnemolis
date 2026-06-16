import logging
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from app.router import route, SOURCE_MAP, detect_intent
from app.mcp_server import mcp_app
from app.sources.kiwix import get_books, refresh_catalog

_LOGGER = logging.getLogger(__name__)

app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API. Routes queries to Kiwix, Open-Meteo, FreshRSS, or SearXNG.",
    version="2.2.0",
)

# Mount MCP SSE server at /mcp
app.mount("/mcp", mcp_app)


class SearchRequest(BaseModel):
    query: str
    source: str = "auto"


class SearchResponse(BaseModel):
    query: str
    source_used: str
    result: str
    success: bool
    error: Optional[str] = None


@app.on_event("startup")
async def startup():
    """Pre-load Kiwix catalog on startup."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_books)


@app.get("/health")
def health():
    books = get_books()
    return {"status": "ok", "kiwix_books_loaded": len(books)}


@app.get("/sources")
def list_sources():
    return {"sources": list(SOURCE_MAP.keys()) + ["auto"]}


@app.get("/catalog")
def catalog():
    """List all books currently loaded from Kiwix catalog."""
    books = get_books()
    return {"count": len(books), "books": books}


@app.post("/catalog/refresh")
def catalog_refresh():
    """Force refresh the Kiwix book catalog."""
    books = refresh_catalog()
    return {"status": "refreshed", "count": len(books)}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    resolved_source = request.source if request.source != "auto" else detect_intent(request.query)
    try:
        result = route(request.query, request.source)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result=result,
            success=True,
        )
    except Exception as e:
        _LOGGER.error("Search failed for query '%s': %s", request.query, e)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result="",
            success=False,
            error=str(e),
        )
