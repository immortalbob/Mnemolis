import time
import json
import logging
import os
import requests
from app.sources import kiwix, forecast, freshrss, searxng, uptime_kuma
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
    "uptime": [
        "uptime", "status", "is down", "what's down", "whats down",
        "any outages", "service status", "network status", "monitoring",
        "what services", "are all services", "is everything up",
        "what is offline", "what is online",
    ],
}

SOURCE_MAP = {
    "kiwix": kiwix.search,
    "forecast": forecast.search,
    "news": freshrss.search,
    "web": searxng.search,
    "uptime": uptime_kuma.search,
}

SOURCE_DESCRIPTIONS = {
    "kiwix": "Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs. Use for factual, encyclopedic, or technical questions.",
    "forecast": "3-day weather forecast. Use for any question about future weather conditions, temperature, rain, wind, or sunrise/sunset.",
    "news": "Recent RSS news articles from the user's feeds. Use for current events, headlines, or recent news.",
    "web": "Live web search via SearXNG. Use for current events, recent information, or anything that may have changed recently.",
    "uptime": "Uptime Kuma monitor status. Use when asked about service status, what is down, or network health.",
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
    "uptime": 60,     # 1 minute — status changes fast
}

NO_RESULT_PHRASES = [
    "no results found",
    "no recent articles",
    "not yet implemented",
    "could not fetch",
    "no books available",
    "could not determine",
]

# In-memory cache: key -> (result, timestamp)
_cache: dict[str, tuple[str, float]] = {}
_cache_dirty_count: int = 0
_CACHE_SAVE_INTERVAL: int = 5   # save to disk every N writes
_CACHE_MAX_SIZE: int = 500       # max entries before evicting oldest


# ---------------------------------------------------------------------------
# Cache internals
# ---------------------------------------------------------------------------

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


def _evict_oldest() -> None:
    """Remove the oldest cache entry."""
    if not _cache:
        return
    oldest_key = min(_cache, key=lambda k: _cache[k][1])
    del _cache[oldest_key]
    _LOGGER.debug("Evicted oldest cache entry: %s", oldest_key)


def _set_cached(source: str, query: str, result: str) -> None:
    global _cache_dirty_count
    key = _cache_key(source, query)
    # Evict oldest if at capacity (and this is a new entry)
    if key not in _cache and len(_cache) >= _CACHE_MAX_SIZE:
        _evict_oldest()
        _LOGGER.info("Cache at max size (%d), evicted oldest entry", _CACHE_MAX_SIZE)
    _cache[key] = (result, time.time())
    _cache_dirty_count += 1
    _LOGGER.info("Cached result for source='%s' query='%s'", source, query[:50])
    if _cache_dirty_count >= _CACHE_SAVE_INTERVAL:
        _save_cache()
        _cache_dirty_count = 0


def _save_cache() -> None:
    """Persist cache to disk."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(_cache, f)
    except Exception as e:
        _LOGGER.warning("Could not save cache to disk: %s", e)


def _looks_empty(result: str) -> bool:
    result_lower = result.lower()
    return any(phrase in result_lower for phrase in NO_RESULT_PHRASES)


# ---------------------------------------------------------------------------
# Cache public API
# ---------------------------------------------------------------------------

def load_cache() -> None:
    """Load cache from disk on startup."""
    global _cache
    try:
        if not os.path.exists(CACHE_FILE):
            _LOGGER.info("No cache file found at %s, starting fresh", CACHE_FILE)
            return
        with open(CACHE_FILE, "r") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            _LOGGER.warning("Cache file malformed (not a dict), starting fresh")
            _cache = {}
            return
        now = time.time()
        loaded = {}
        skipped = 0
        for key, value in raw.items():
            try:
                # Validate structure: must be [result_str, timestamp_float]
                if not isinstance(value, list) or len(value) != 2:
                    skipped += 1
                    continue
                result, timestamp = value
                if not isinstance(result, str) or not isinstance(timestamp, (int, float)):
                    skipped += 1
                    continue
                source = key.split(":")[0]
                ttl = CACHE_TTL.get(source, 3600)
                if now - timestamp < ttl:
                    loaded[key] = (result, float(timestamp))
            except Exception:
                skipped += 1
                continue
        _cache = loaded
        if skipped:
            _LOGGER.warning("Skipped %d malformed cache entries on load", skipped)
        _LOGGER.info("Loaded %d cache entries from disk", len(_cache))
    except json.JSONDecodeError as e:
        _LOGGER.warning("Cache file corrupted (JSON error: %s), starting fresh", e)
        _cache = {}
        # Rename corrupted file for inspection
        try:
            corrupt_path = CACHE_FILE + ".corrupt"
            os.rename(CACHE_FILE, corrupt_path)
            _LOGGER.info("Moved corrupted cache to %s", corrupt_path)
        except Exception:
            pass
    except Exception as e:
        _LOGGER.warning("Could not load cache from disk: %s", e)
        _cache = {}


def check_cached(source: str, query: str) -> bool:
    """Return True if a valid cached result exists for this source+query."""
    return _get_cached(source, query) is not None


def get_cache_stats() -> list[dict]:
    """Return cache entries with age and expiry info."""
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
    return entries


def get_cache_count() -> int:
    """Return number of cache entries."""
    return len(_cache)


def clear_cache() -> int:
    """Clear all cache entries and persist to disk. Returns count removed."""
    global _cache_dirty_count
    count = len(_cache)
    _cache.clear()
    _cache_dirty_count = 0
    _save_cache()
    return count


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(query: str, source: str = "auto") -> str:
    if source == "auto":
        source = detect_intent(query)
    else:
        _LOGGER.info("Source explicitly set to '%s' for query: '%s'", source, query[:50])

    handler = SOURCE_MAP.get(source)
    if not handler:
        return f"Unknown source '{source}'. Valid options: {', '.join(SOURCE_MAP.keys())}."

    cached = _get_cached(source, query)
    if cached:
        return cached

    result = handler(query)

    if _looks_empty(result) and source in FALLBACK_CHAIN:
        fallback_source = FALLBACK_CHAIN[source]
        _LOGGER.info("Result from '%s' looks empty, falling back to '%s'", source, fallback_source)
        fallback_handler = SOURCE_MAP.get(fallback_source)
        if fallback_handler:
            cached_fallback = _get_cached(fallback_source, query)
            if cached_fallback:
                return cached_fallback
            fallback_result = fallback_handler(query)
            if not _looks_empty(fallback_result):
                _LOGGER.info("Fallback to '%s' succeeded", fallback_source)
                _set_cached(fallback_source, query, fallback_result)
                return fallback_result
            _LOGGER.warning("Fallback to '%s' also returned empty result", fallback_source)

    if not _looks_empty(result):
        _set_cached(source, query, result)

    return result
