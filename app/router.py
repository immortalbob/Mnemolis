from app.sources import kiwix, forecast, freshrss, searxng

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


def detect_intent(query: str) -> str:
    query_lower = query.lower()
    for source, triggers in INTENT_MAP.items():
        for trigger in triggers:
            if trigger in query_lower:
                return source
    return "kiwix"


def route(query: str, source: str = "auto") -> str:
    if source == "auto":
        source = detect_intent(query)
    handler = SOURCE_MAP.get(source)
    if not handler:
        return f"Unknown source '{source}'. Valid options: {', '.join(SOURCE_MAP.keys())}."
    return handler(query)
