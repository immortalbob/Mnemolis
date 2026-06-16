import logging
from app.sources import kiwix, forecast, freshrss, searxng

_LOGGER = logging.getLogger(__name__)

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

# Fallback chain — if a source returns no results, try these in order
FALLBACK_CHAIN = {
    "kiwix": "web",
    "news": "web",
}

NO_RESULT_PHRASES = [
    "no results found",
    "no recent articles",
    "not yet implemented",
    "could not fetch",
    "no books available",
    "could not determine",
]


def _looks_empty(result: str) -> bool:
    """Return True if the result string looks like a failure or empty response."""
    result_lower = result.lower()
    return any(phrase in result_lower for phrase in NO_RESULT_PHRASES)


def detect_intent(query: str) -> str:
    query_lower = query.lower()
    for source, triggers in INTENT_MAP.items():
        for trigger in triggers:
            if trigger in query_lower:
                _LOGGER.info("Intent detected: '%s' matched trigger '%s' -> %s", query[:50], trigger, source)
                return source
    _LOGGER.info("Intent detected: '%s' -> kiwix (default fallback)", query[:50])
    return "kiwix"


def route(query: str, source: str = "auto") -> str:
    if source == "auto":
        source = detect_intent(query)
    else:
        _LOGGER.info("Source explicitly set to '%s' for query: '%s'", source, query[:50])

    handler = SOURCE_MAP.get(source)
    if not handler:
        return f"Unknown source '{source}'. Valid options: {', '.join(SOURCE_MAP.keys())}."

    result = handler(query)

    # Fallback if result looks empty or failed
    if _looks_empty(result) and source in FALLBACK_CHAIN:
        fallback_source = FALLBACK_CHAIN[source]
        _LOGGER.info("Result from '%s' looks empty, falling back to '%s'", source, fallback_source)
        fallback_handler = SOURCE_MAP.get(fallback_source)
        if fallback_handler:
            fallback_result = fallback_handler(query)
            if not _looks_empty(fallback_result):
                _LOGGER.info("Fallback to '%s' succeeded", fallback_source)
                return fallback_result
            _LOGGER.warning("Fallback to '%s' also returned empty result", fallback_source)

    return result
