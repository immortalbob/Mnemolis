from fastapi import FastAPI
from pydantic import BaseModel
from starlette.routing import Mount

from app.router import route, SOURCE_MAP
from app.mcp_server import mcp_app

app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API. Routes queries to Kiwix, Open-Meteo, FreshRSS, or SearXNG.",
    version="2.0.0",
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sources")
def list_sources():
    return {"sources": list(SOURCE_MAP.keys()) + ["auto"]}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    from app.router import detect_intent
    resolved_source = request.source if request.source != "auto" else detect_intent(request.query)
    result = route(request.query, request.source)
    return SearchResponse(
        query=request.query,
        source_used=resolved_source,
        result=result,
    )
