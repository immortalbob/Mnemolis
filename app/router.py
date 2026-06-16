import time
import json
import logging
import os
import requests
from app.sources import kiwix, forecast, freshrss, searxng
from app.config import settings

_LOGGER = logging.getLogger(__name__)

CACHE_FILE = "/app/data/cache.json"

INTENT_MAP = {
    "forecast": [
        "forecast", "weather", "tomorrow", "tonight", "this weekend",
        "later today", "will it rain", "will it snow", "will it be",
        "high temp", "low temp", "precipitation", "wind forecast",
        "going to be hot", "going to be cold",
    ],
    "news": [
        "news", "headlines", "articles", "feeds", "rss",
        "what's happening", "latest", "recent articles", "my feeds",
    ],
    "web": [
        "search the web", "google", "look it up online",
        "current events", "web search", "look up", "find online",
        "search for", "what is happening", "who won", "did they",
        "search online",
    ],
}

SOURCE_MAP = {
    "kiwix": kiwix.search,
    "forecast": forecast.search,
    "news": freshrss.search,
    "web": searxng.search,
}

SOURCE_DESCRIPTIONS = {
    "kiwix": "Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs. Use for factual, encyclopedic, or technical questions.",
    "forecast": "3-day weather forecast. Use for any question about future weather conditions, temperature, rain, wind, or sunrise/sunset.",
    "news": "Recent RSS news articles from the user's feeds. Use for current events, headlines, or recent news.",
    "web": "Live web search via SearXNG. Use for current events, recent information, or anything that may have changed recently.",
}

# Fallback chain — if a source returns no results, try these in order
FALLBACK_CHAIN = {
    "kiwix": "web",
    "news": "web",
}

# Cache TTL in seconds per source
CACHE_TTL = {
    "kiwix": 86400,   # 24 hours
    "forecast": 1800, # 30 minutes
    "news": 900,      # 15 minutes
    "web": 3600,      # 1 hour
}

# In-memory cache: key -> (result, timestamp)
_cache: dict[str, tuple[str, float]] = {}


def _load_cache() -> None:
    """Load cache from disk on startup."""
    global _cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                raw = json.load(f)
            now = time.time()
            # Filter out expired entries on load
            loaded = {}
            for key, (result, timestamp) in raw.items():
                source = key.split(":")[0]
                ttl = CACHE_TTL.get(source, 3600)
                if now - timestamp < ttl:
                    loaded[key] = (result, timestamp)
            _cache = loaded
            _LOGGER.info("Loaded %d cache entries from disk", len(_cache))
    except Exception as e:
        _LOGGER.warning("Could not load cache from disk: %s", e)
        _cache = {}


def _save_cache() -> None:
    """Persist cache to disk."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(_cache, f)
    except Exception as e:
        _LOGGER.warning("Could not save cache to disk: %s", e)

NO_RESULT_PHRASES = [
    "no results found",
    "no recent articles",
    "not yet implemented",
    "could not fetch",
    "no books available",
    "could not determine",
]


def _cache_key(source: str, query: str) -> str:
    return f"{source}:{query.lower().strip()}"


def _get_cached(source: str, query: str) -> str | None:
    key = _cache_key(source, query)
    if key in _cache:
        result, timestamp = _cache[key]
        ttl = CACHE_TTL.get(source, 3600)
        if time.time() - timestamp < ttl:
            _LOGGER.info("Cache hit for source='%s' query='%s'", source, query[:50])
            return result
        else:
            del _cache[key]
    return None


def _set_cached(source: str, query: str, result: str) -> None:
    key = _cache_key(source, query)
    _cache[key] = (result, time.time())
    _LOGGER.info("Cached result for source='%s' query='%s'", source, query[:50])
    _save_cache()


def _looks_empty(result: str) -> bool:
    result_lower = result.lower()
    return any(phrase in result_lower for phrase in NO_RESULT_PHRASES)


def _keyword_detect(query: str) -> str | None:
    """Fast keyword-based intent detection. Returns source name or None if no match."""
    query_lower = query.lower()
    for source, triggers in INTENT_MAP.items():
        for trigger in triggers:
            if trigger in query_lower:
                _LOGGER.info(
                    "Keyword intent: '%s' matched trigger '%s' -> %s",
                    query[:50], trigger, source
                )
                return source
    return None


def _llm_detect(query: str) -> str:
    """Ask Ollama to pick the best source for the query. Falls back to kiwix."""
    if not settings.ollama_url or not settings.ollama_model:
        return "kiwix"

    source_list = "\n".join(
        f"- {name}: {desc}"
        for name, desc in SOURCE_DESCRIPTIONS.items()
    )

    prompt = (
        f"You are a search router. Given a user query and a list of available search sources, "
        f"return ONLY the exact source name that best matches the query. "
        f"No explanation, no punctuation, just the source name.\n\n"
        f"Query: {query}\n\n"
        f"Available sources:\n{source_list}\n\n"
        f"Best source name:"
    )

    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 20},
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "").strip()
        if not raw:
            thinking = data.get("thinking", "")
            lines = [l.strip() for l in thinking.splitlines() if l.strip()]
            raw = lines[-1] if lines else ""
        chosen = raw.strip(".").strip().lower()

        if chosen in SOURCE_MAP:
            _LOGGER.info("LLM intent: '%s' -> %s", query[:50], chosen)
            return chosen

        _LOGGER.warning("LLM returned unknown source '%s', falling back to kiwix", chosen)

    except Exception as e:
        _LOGGER.warning("LLM source detection failed: %s", e)

    return "kiwix"


def detect_intent(query: str) -> str:
    """Detect intent using keyword matching first, Ollama as fallback."""
    source = _keyword_detect(query)
    if source:
        return source
    _LOGGER.info("No keyword match for '%s', asking LLM for source selection", query[:50])
    return _llm_detect(query)


def route(query: str, source: str = "auto") -> str:
    if source == "auto":
        source = detect_intent(query)
    else:
        _LOGGER.info("Source explicitly set to '%s' for query: '%s'", source, query[:50])

    handler = SOURCE_MAP.get(source)
    if not handler:
        return f"Unknown source '{source}'. Valid options: {', '.join(SOURCE_MAP.keys())}."

    # Check cache
    cached = _get_cached(source, query)
    if cached:
        return cached

    result = handler(query)

    # Fallback if result looks empty or failed
    if _looks_empty(result) and source in FALLBACK_CHAIN:
        fallback_source = FALLBACK_CHAIN[source]
        _LOGGER.info("Result from '%s' looks empty, falling back to '%s'", source, fallback_source)
        fallback_handler = SOURCE_MAP.get(fallback_source)
        if fallback_handler:
            # Check cache for fallback too
            cached_fallback = _get_cached(fallback_source, query)
            if cached_fallback:
                return cached_fallback
            fallback_result = fallback_handler(query)
            if not _looks_empty(fallback_result):
                _LOGGER.info("Fallback to '%s' succeeded", fallback_source)
                _set_cached(fallback_source, query, fallback_result)
                return fallback_result
            _LOGGER.warning("Fallback to '%s' also returned empty result", fallback_source)

    # Cache successful results
    if not _looks_empty(result):
        _set_cached(source, query, result)

    return result
