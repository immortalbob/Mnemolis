from fastapi import FastAPI
from pydantic import BaseModel
from app.router import route, SOURCE_MAP

app = FastAPI(
    title="MiniSearch",
    description="Unified local knowledge search API. Routes queries to Kiwix, Open-Meteo, FreshRSS, or SearXNG.",
    version="1.0.0",
)


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
