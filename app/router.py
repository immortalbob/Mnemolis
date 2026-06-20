import re
import time
import json
import logging
import os
from app.sources import kiwix, forecast, freshrss, searxng, uptime_kuma, fusion, home_assistant
from app.snapshots import get_changes, format_changes
from app.config import settings

_LOGGER = logging.getLogger(__name__)

CACHE_FILE = "/app/data/cache.json"

INTENT_MAP = {
    "forecast": [
        "forecast", "weather", "tomorrow", "this weekend",
        "later today", "will it rain", "will it snow",
        "high temp", "low temp", "precipitation", "wind forecast",
        "going to be hot", "going to be cold",
    ],
    "news": [
        "news", "headlines", "feeds", "rss",
        "recent articles", "my feeds",
    ],
    "web": [
        "search the web", "google", "look it up online",
        "current events", "web search", "find online",
        "who won", "search online",
    ],
    "ha": [
        "which lights are on", "lights on", "lights off", "lights status",
        "house status", "home status", "house summary",
        "are the doors locked", "door locked", "doors locked",
        "indoor air", "air quality", "indoor sensors",
        "how much power", "power consumption", "energy usage",
        "battery status", "battery levels", "low battery",
        "any motion", "recent motion", "motion detected",
        "security status", "house secure",
        "outdoor conditions", "outside conditions",
    ],
    "changes": [
        "what changed", "any changes", "whats new", "what's new",
        "any new outages", "new outages", "any outages today",
        "weather change", "forecast change",
        "new articles", "new news", "new headlines",
        "anything different", "what happened today",
        "since last time", "changes today",
        "changed in the house", "house since", "anything changed",
        "while i was at work", "while i've been at work", "while at work",
        "since work", "since this morning", "this morning while",
        "since i left", "since i woke up",
    ],
    "uptime": [
        "uptime", "is down", "what's down", "whats down",
        "any outages", "service status", "network status",
        "are all services", "is everything up", "what is offline",
        "my services", "services up", "services down",
        "anything down", "everything down", "everything up",
        "network down", "network up",
        "what's offline", "whats offline", "anything offline",
        "any services", "check services",
        "is my network", "is the network",
        "server down", "server up", "server status",
        "is it running", "is it up", "is it down",
        "are they up", "are they down",
    ],
}

def _hours_since(hour_of_day: int) -> float:
    """Return the number of hours elapsed since the given hour today (local time).
    If that hour hasn't happened yet today, looks back to yesterday's occurrence.
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    target = now.replace(hour=hour_of_day, minute=0, second=0, microsecond=0)
    if target > now:
        target -= timedelta(days=1)
    elapsed = (now - target).total_seconds() / 3600
    return max(elapsed, 0.1)  # avoid zero/negative windows


def _resolve_changes_hours(query: str) -> float:
    """Resolve a changes query into a precise hours-since window.

    Time-window phrases are checked in order of specificity (most specific first)
    so "this morning while at work" doesn't get misread by a less specific match.
    """
    q = query.lower()

    # Explicit hour count — "in the last 3 hours"
    if "hour" in q:
        import re
        m = re.search(r"(\d+)\s*hour", q)
        if m:
            return float(m.group(1))

    # Specific time-of-day phrases — resolved against configured start hours
    if "this morning" in q or "since morning" in q or "since this morning" in q:
        return _hours_since(settings.morning_start_hour)

    if "at work" in q or "since work" in q or "while at work" in q or "while i was at work" in q or "while i've been at work" in q:
        return _hours_since(settings.work_start_hour)

    if "tonight" in q or "this evening" in q:
        return _hours_since(18)

    # Broader windows
    if "yesterday" in q or "since yesterday" in q:
        return 48.0

    if "week" in q:
        return 168.0

    if "today" in q:
        return 24.0

    # Default — no specific window detected
    return 24.0


def _search_changes(query: str) -> str:
    """Search changes source — returns detected changes from snapshots,
    resolving natural language time-window phrases into precise hour windows."""
    hours = _resolve_changes_hours(query)
    detected = get_changes(since_hours=hours)
    return format_changes(detected, since_hours=round(hours, 1))


SOURCE_MAP = {
    "kiwix": kiwix.search,
    "forecast": forecast.search,
    "news": freshrss.search,
    "web": searxng.search,
    "uptime": uptime_kuma.search,
    "ha": home_assistant.search,
    "changes": _search_changes,
    "fusion": None,  # handled specially in route() — accepts fusion_sources list
}

SOURCE_DESCRIPTIONS = {
    "kiwix": "Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs. Use for factual, encyclopedic, or technical questions.",
    "forecast": "3-day weather forecast. Use for any question about future weather conditions, temperature, rain, wind, or sunrise/sunset.",
    "news": "Recent RSS news articles from the user's feeds. Use for current events, headlines, or recent news.",
    "web": "Live web search via SearXNG. Use for current events, recent information, or anything that may have changed recently.",
    "uptime": "Uptime Kuma monitor status. Use when asked about service status, what is down, or network health.",
    "ha": "Home Assistant entity states. Use for house status summaries, which lights are on, door and lock status, battery levels, indoor sensors, motion events, or power consumption.",
    "changes": "Snapshot diff engine. Use when asked what changed, any new outages, weather changes, or new news since a given time.",
    "fusion": "Multi-source fusion — queries multiple sources concurrently and merges results. Use for complex queries that benefit from combining offline knowledge, live web, and recent news.",
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
    "uptime": 60,     # 1 minute
    "ha": 30,         # 30 seconds
    "changes": 120,   # 2 minutes — changes are near-real-time
    "fusion": 1800,   # 30 minutes
}

# ---------------------------------------------------------------------------
# Routing cache — stores source and book selection decisions to skip LLM calls
# ---------------------------------------------------------------------------

ROUTING_CACHE_FILE = "/app/data/routing_cache.json"
ROUTING_CACHE_TTL = 3600  # 1 hour — routing decisions are stable but not permanent
_routing_cache: dict[str, tuple[str, float]] = {}


def _routing_cache_key(query: str) -> str:
    return query.lower().strip()


def _get_routing(query: str) -> str | None:
    """Return cached routing decision for query, or None if not cached/expired."""
    key = _routing_cache_key(query)
    if key in _routing_cache:
        decision, timestamp = _routing_cache[key]
        if time.time() - timestamp < ROUTING_CACHE_TTL:
            _LOGGER.info("Routing cache hit for query: '%s' -> %s", query[:50], decision)
            return decision
        else:
            del _routing_cache[key]
    return None


def _set_routing(query: str, decision: str) -> None:
    """Cache a routing decision for a query."""
    key = _routing_cache_key(query)
    _routing_cache[key] = (decision, time.time())
    _LOGGER.info("Cached routing decision: '%s' -> %s", query[:50], decision)
    _save_routing_cache()


def _save_routing_cache() -> None:
    """Persist routing cache to disk."""
    try:
        os.makedirs(os.path.dirname(ROUTING_CACHE_FILE), exist_ok=True)
        with open(ROUTING_CACHE_FILE, "w") as f:
            json.dump(_routing_cache, f)
    except Exception as e:
        _LOGGER.warning("Could not save routing cache to disk: %s", e)


def load_routing_cache() -> None:
    """Load routing cache from disk on startup."""
    global _routing_cache
    try:
        if not os.path.exists(ROUTING_CACHE_FILE):
            return
        with open(ROUTING_CACHE_FILE, "r") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            _routing_cache.clear()
            return
        now = time.time()
        loaded = {}
        for key, value in raw.items():
            try:
                if not isinstance(value, list) or len(value) != 2:
                    continue
                decision, timestamp = value
                if not isinstance(decision, str) or not isinstance(timestamp, (int, float)):
                    continue
                if now - float(timestamp) < ROUTING_CACHE_TTL:
                    loaded[key] = (decision, float(timestamp))
            except Exception:
                continue
        _routing_cache.clear()
        _routing_cache.update(loaded)
        _LOGGER.info("Loaded %d routing cache entries from disk", len(_routing_cache))
    except json.JSONDecodeError as e:
        _LOGGER.warning("Routing cache corrupted: %s, starting fresh", e)
        _routing_cache.clear()
    except Exception as e:
        _LOGGER.warning("Could not load routing cache: %s", e)
        _routing_cache.clear()


def get_routing_cache_stats() -> list[dict]:
    """Return routing cache entries with age and expiry info."""
    now = time.time()
    entries = []
    for key, (decision, timestamp) in _routing_cache.items():
        age = int(now - timestamp)
        entries.append({
            "query": key,
            "decision": decision,
            "age_seconds": age,
            "ttl_seconds": ROUTING_CACHE_TTL,
            "expires_in": max(0, ROUTING_CACHE_TTL - age),
        })
    return entries


def clear_routing_cache() -> int:
    """Clear all routing cache entries. Returns count removed."""
    count = len(_routing_cache)
    _routing_cache.clear()
    _save_routing_cache()
    return count


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
_CACHE_MAX_SIZE: int = settings.cache_max_size  # max entries before evicting oldest


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

def _keyword_detect(query: str) -> str | list[str] | None:
    """Fast keyword-based intent detection.

    Returns:
    - A single source name if only one source matches
    - A list of source names if multiple sources match (auto-escalates to fusion)
    - None if no keywords match
    """
    query_lower = query.lower()
    matched = []
    for source, triggers in INTENT_MAP.items():
        for trigger in triggers:
            if trigger in query_lower:
                if source not in matched:
                    matched.append(source)
                    _LOGGER.info(
                        "Keyword intent: '%s' matched trigger '%s' -> %s",
                        query[:50], trigger, source
                    )
                break  # one trigger per source is enough

    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]

    _LOGGER.info("Multi-keyword match for '%s' -> escalating to fusion: %s", query[:50], matched)
    return matched


def _llm_detect(query: str) -> str | list[str]:
    """Ask LLM to pick the best source(s) for the query.

    Returns a single source name for focused queries, or a list of source names
    for complex multi-topic queries that benefit from fusion.

    The LLM decides in one call — if it returns a comma-separated list, the
    caller will trigger fusion automatically.

    Checks routing cache first to avoid redundant LLM calls.
    Falls back to 'kiwix' if LLM is not configured or returns invalid sources.
    """
    from app.llm import complete, is_configured

    # Check routing cache first
    cached = _get_routing(f"source:{query}")
    if cached:
        # If cached value contains a comma it was a fusion decision
        if "," in cached:
            sources = [s.strip() for s in cached.split(",") if s.strip() in SOURCE_MAP and s.strip() != "fusion"]
            if sources:
                _LOGGER.info("Routing cache hit (fusion): '%s' -> %s", query[:50], sources)
                return sources
        elif cached in SOURCE_MAP:
            _LOGGER.info("Routing cache hit: '%s' -> %s", query[:50], cached)
            return cached

    if not is_configured():
        return "kiwix"

    source_list = "\n".join(
        f"- {name}: {desc}"
        for name, desc in SOURCE_DESCRIPTIONS.items()
        if name != "fusion"
    )

    prompt = (
        f"You are a search router. Given a user query and a list of available search sources, "
        f"return the best source or sources.\n\n"
        f"Rules:\n"
        f"- For a focused single-topic query: return ONLY one source name\n"
        f"- For a complex or multi-topic query that needs multiple sources: return 2-3 source names separated by commas\n"
        f"- No explanation, no punctuation except commas between source names\n\n"
        f"Query: {query}\n\n"
        f"Available sources:\n{source_list}\n\n"
        f"Best source name(s):"
    )

    raw = (complete(prompt, max_tokens=30) or "").lower().strip()

    # Multi-source response — trigger fusion
    if "," in raw:
        sources = []
        for candidate in raw.split(","):
            candidate = candidate.strip().strip(".")
            if candidate in SOURCE_MAP and candidate != "fusion" and candidate not in sources:
                sources.append(candidate)
        if len(sources) >= 2:
            _LOGGER.info("LLM escalated to fusion: '%s' -> %s", query[:50], sources)
            _set_routing(f"source:{query}", ",".join(sources))
            return sources
        _LOGGER.warning("LLM returned multi-source but too few valid: '%s'", raw)

    # Single source response
    chosen = raw.strip(".").strip()
    if chosen in SOURCE_MAP and chosen != "fusion":
        _LOGGER.info("LLM intent: '%s' -> %s", query[:50], chosen)
        _set_routing(f"source:{query}", chosen)
        return chosen

    if raw:
        _LOGGER.warning("LLM returned unknown source '%s', falling back to kiwix", raw)

    _set_routing(f"source:{query}", "kiwix")
    return "kiwix"


def _llm_pick_fusion_sources(query: str) -> list[str]:
    """Ask LLM to pick 2-3 best sources for an explicit fusion query.
    Falls back to ["kiwix", "web"] if LLM is not configured."""
    from app.llm import complete, is_configured

    cache_key = f"fusion_sources:{query}"
    cached = _get_routing(cache_key)
    if cached:
        sources = [s.strip() for s in cached.split(",") if s.strip() in SOURCE_MAP and s.strip() != "fusion"]
        if sources:
            _LOGGER.info("Routing cache hit for fusion sources: '%s' -> %s", query[:50], sources)
            return sources

    if not is_configured():
        return ["kiwix", "web"]

    source_list = "\n".join(
        f"- {name}: {desc}"
        for name, desc in SOURCE_DESCRIPTIONS.items()
        if name != "fusion"
    )

    prompt = (
        f"You are a search router. Given a user query, pick 2 or 3 sources that together "
        f"would give the most complete answer. Return ONLY the source names separated by commas. "
        f"No explanation, no punctuation other than commas. Pick 2 sources for focused queries, "
        f"3 for complex or multi-topic queries.\n\n"
        f"Query: {query}\n\n"
        f"Available sources:\n{source_list}\n\n"
        f"Best source names (comma-separated):"
    )

    raw = (complete(prompt, max_tokens=30) or "").lower()
    chosen = []
    for candidate in raw.split(","):
        candidate = candidate.strip().strip(".")
        if candidate in SOURCE_MAP and candidate != "fusion" and candidate not in chosen:
            chosen.append(candidate)

    if len(chosen) >= 2:
        _LOGGER.info("LLM fusion sources for '%s': %s", query[:50], chosen)
        _set_routing(cache_key, ",".join(chosen))
        return chosen[:3]

    _LOGGER.warning("LLM returned invalid fusion sources '%s', using defaults", raw)
    defaults = ["kiwix", "web"]
    _set_routing(cache_key, ",".join(defaults))
    return defaults


def detect_intent(query: str) -> str | list[str]:
    """Detect intent using keyword matching first, LLM as fallback.

    Returns a single source name for focused queries, or a list of source names
    when the LLM determines fusion would give a better answer.
    """
    source = _keyword_detect(query)
    if source:
        return source
    _LOGGER.info("No keyword match for '%s', asking LLM for source selection", query[:50])
    return _llm_detect(query)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Query decomposition — conjunction splitting
# ---------------------------------------------------------------------------

# Conjunctions that signal independent sub-queries
_CONJUNCTIONS = [" and ", " also ", " plus ", " as well as ", " in addition "]

# Phrases that look like conjunctions but shouldn't split
# e.g. "Python and Rust", "Phoenix and Kingman", "cats and dogs"
_NOSPLIT_PATTERNS = [
    "compare", "difference between", "vs", "versus",
    "both", "either", "neither", "between",
]


def _is_proper_noun_pair_at(query: str, idx: int, conj_len: int) -> bool:
    """
    Given a specific conjunction occurrence (its start index and length),
    return True if THIS PARTICULAR occurrence looks like a bare
    proper-noun pair ("Iran and Israel") rather than two independent
    clauses — without making any judgment about the rest of the query.

    This is checked per-occurrence, not as a single global yes/no gate
    for the whole query. Found via real usage: a query can contain both
    a genuine proper-noun pair AND genuinely separate real intents in
    the same sentence ("what's happening with Iran and Israel right
    now, and also has anything weird happened with my back door, plus
    I keep getting a numpy import error on my pi") — a global gate that
    aborts ALL splitting the moment it finds ANY proper-noun pair
    anywhere incorrectly discards the real, separate door/GPIO intents
    too. Each conjunction occurrence must be judged on its own.
    """
    before = query[:idx].strip()
    after_full = query[idx + conj_len:]

    # Bound "after" to just the next clause — stop at the first comma or
    # the start of any other conjunction, so we're comparing against the
    # immediate next name, not the rest of a potentially long sentence
    after_lower = after_full.lower()
    cut_points = [after_lower.find(",")]
    for other_conj in _CONJUNCTIONS:
        p = after_lower.find(other_conj)
        if p != -1:
            cut_points.append(p)
    cut_points = [p for p in cut_points if p != -1]
    cut = min(cut_points) if cut_points else len(after_full)
    after = after_full[:cut].strip()

    before_words = before.split()
    after_words = after.split()
    if not before_words or not after_words:
        return False

    before_tail = before_words[-1]
    after_head = after_words[0].rstrip(",.;:")

    # Only the word immediately after the conjunction matters for "is
    # this a bare name" — "Israel right now" still starts with a bare
    # proper noun even though trailing filler ("right now") follows
    # within the same comma-bounded segment. The proper noun itself may
    # be 1-2 words ("Israel" or "New York"), but the entire bounded
    # segment doesn't need to be that short — trailing filler is fine.
    both_capitalized = before_tail[:1].isupper() and after_head[:1].isupper()
    after_name_is_short = (
        len(after_words) == 1
        or (len(after_words) >= 2 and after_words[1][:1].islower())
    )
    return both_capitalized and after_name_is_short


def _decompose(query: str) -> list[str]:
    """Split a query into independent sub-queries on conjunction words.

    Returns a list with the original query if no meaningful split is found,
    or a list of 2+ sub-queries if the query contains independent intents.

    Avoids splitting at any specific conjunction occurrence that's part
    of a comparison/single-concept query, or that joins a short
    proper-noun pair rather than two independent clauses — checked per
    occurrence, not as a single whole-query gate, since a long compound
    query can contain both a genuine proper-noun pair AND genuinely
    separate real intents in the same sentence.

    Tries every conjunction type and keeps whichever produces the most
    meaningful sub-queries — a query can contain multiple different
    conjunction words (e.g. "X, and also Y, and Z"), and the first
    conjunction encountered isn't necessarily the one that splits the
    query into its real intents. A query with one " also " and two
    " and "s should split on " and " (3 correct parts), not stop early
    on " also " just because it happens to produce >=2 technically-valid
    parts first.

    Also tries splitting on every conjunction occurrence at once,
    regardless of type — queries can genuinely mix conjunction words
    ("X, and also Y, plus Z, and W"), where no single conjunction type's
    isolated split would ever separate every intent, since each type's
    "leftover" half still contains the other conjunction words bundled
    inside it. Whichever approach (single-type or combined) produces the
    most meaningful parts wins.
    """
    q = query.strip()
    q_lower = q.lower()

    # Don't split comparison queries
    if any(p in q_lower for p in _NOSPLIT_PATTERNS):
        return [q]

    # Colloquial question phrases — "what's the deal with X", "what's up
    # with X" are real standalone intents regardless of what specific noun
    # follows, so a sub-query containing one of these anywhere should
    # always count as meaningful even if the trailing noun isn't itself
    # recognized as a content word. Matched as a substring anywhere in the
    # clause, not just at position zero — "and remind me what's up with X"
    # has the marker mid-clause, since the clause itself still carries the
    # leftover conjunction/filler word ("and remind me...") from wherever
    # the split actually occurred.
    _COLLOQUIAL_PHRASES = [
        "what's the deal with", "whats the deal with",
        "what's up with", "whats up with",
        "what's this about", "whats this about",
        "what's the story with", "whats the story with",
    ]

    best_split: list[str] | None = None

    def _filter_meaningful(parts: list[str]) -> list[str]:
        """
        A sub-query is meaningful if, after stripping stop words and
        filler, at least one real content word remains — OR it contains
        a recognized colloquial question phrase regardless of what
        follows it.

        This replaced a fixed allowlist of "intent words" (door, light,
        wifi, router, etc.) that had to be hand-extended every time a new
        domain came up — found via real usage that GPIO/Python/technical
        troubleshooting clauses had zero coverage in that list at all,
        silently dropping real content during decomposition ("Ive been
        getting a python pigpio no permission to update GPIO error on my
        pi" matched nothing and was discarded). Reusing kiwix.py's
        already-hardened _STOP_WORDS set means ANY real noun/topic word
        counts as meaningful, with no domain-specific list to maintain —
        the same logic kiwix.py already uses to decide what's left of a
        query once filler is stripped.
        """
        meaningful = []
        for p in parts:
            if len(p) <= 3:
                continue
            if any(s in p.lower() for s in _COLLOQUIAL_PHRASES):
                meaningful.append(p)
                continue
            # Strip trailing 's/'t contractions before stop-word matching —
            # "internet's" otherwise survives as "internet'" after a naive
            # split, the same class of bug found and fixed in kiwix.py
            normalized_words = [
                re.sub(r"['']\w*$", "", w) for w in p.lower().split()
            ]
            content_words = [
                w for w in normalized_words
                if w not in kiwix._STOP_WORDS and len(w) > 1
            ]
            if content_words:
                meaningful.append(p)
        return meaningful

    # Try every conjunction type in isolation, keep whichever single-type
    # split has the most meaningful parts
    for conj in sorted(_CONJUNCTIONS, key=len, reverse=True):
        if conj not in q_lower:
            continue

        parts = []
        remaining = q
        remaining_lower = remaining.lower()
        consumed = 0  # how much of the original query has been consumed so far
        while conj in remaining_lower:
            idx = remaining_lower.index(conj)
            # Skip this specific occurrence if it's a bare proper-noun
            # pair ("Iran and Israel") — don't split here, but keep
            # scanning for the NEXT occurrence of this conjunction rather
            # than aborting the whole split, since other occurrences in
            # the same query may be genuinely independent intents
            absolute_idx = consumed + idx
            if _is_proper_noun_pair_at(q, absolute_idx, len(conj)):
                remaining = remaining[idx + len(conj):]
                remaining_lower = remaining.lower()
                consumed += idx + len(conj)
                continue
            part = remaining[:idx].strip()
            if part:
                parts.append(part)
            remaining = remaining[idx + len(conj):]
            remaining_lower = remaining.lower()
            consumed += idx + len(conj)
        if remaining.strip():
            parts.append(remaining.strip())

        meaningful = _filter_meaningful(parts)
        if len(meaningful) >= 2 and (best_split is None or len(meaningful) > len(best_split)):
            best_split = meaningful

    # Also try splitting on EVERY conjunction occurrence at once, regardless
    # of type — a query can genuinely mix conjunction words ("X, and also Y,
    # plus Z, and W"), and no single conjunction type's isolated split would
    # ever separate all of those intents, since each one's "leftover" half
    # still contains the other conjunction words bundled inside it. Found
    # via real usage: a 5-intent query mixing "and also", "plus", "and",
    # "also" only ever produced 2 parts under the single-type approach,
    # because every type's split left the other three conjunctions stuck
    # together in one half or the other.
    all_matches = []
    for conj in _CONJUNCTIONS:
        start = 0
        while True:
            idx = q_lower.find(conj, start)
            if idx == -1:
                break
            # Skip THIS occurrence if it's a bare proper-noun pair —
            # other occurrences elsewhere in the same query are still
            # checked independently and may be genuinely real intents
            if not _is_proper_noun_pair_at(q, idx, len(conj)):
                all_matches.append((idx, idx + len(conj)))
            start = idx + len(conj)
    all_matches.sort()

    if len(all_matches) > 1:
        # Collapse adjacent/overlapping matches ("and also" = " and "
        # immediately followed by " also ") into a single split point,
        # otherwise the tiny gap between them becomes a near-empty fragment
        collapsed = []
        for start, end in all_matches:
            if collapsed and start <= collapsed[-1][1]:
                collapsed[-1] = (collapsed[-1][0], max(collapsed[-1][1], end))
            else:
                collapsed.append((start, end))

        parts = []
        last_end = 0
        for start, end in collapsed:
            part = q[last_end:start].strip()
            if part:
                parts.append(part)
            last_end = end
        remaining = q[last_end:].strip()
        if remaining:
            parts.append(remaining)

        meaningful = _filter_meaningful(parts)
        if len(meaningful) >= 2 and (best_split is None or len(meaningful) > len(best_split)):
            best_split = meaningful

    if best_split:
        _LOGGER.info("Decomposed query into %d parts: %s", len(best_split), best_split)
        return best_split

    return [q]


def route(query: str, source: str = "auto", fusion_sources: list[str] | None = None) -> str:
    """Backward-compatible wrapper around route_with_source() — returns
    just the result string for callers that don't need to know which
    source actually produced it (e.g. fusion.search() calling into
    individual sources, existing tests written against this signature)."""
    result, _actual_source = route_with_source(query, source, fusion_sources)
    return result


def route_with_source(query: str, source: str = "auto", fusion_sources: list[str] | None = None) -> tuple[str, str]:
    """
    Route a query to the appropriate source(s) and return both the result
    and the source that ACTUALLY produced it.

    This distinction matters because of fallback behavior — a query
    routed to 'kiwix' that returns no usable result can silently fall
    back to 'web', and the caller needs to know that happened rather than
    reporting 'kiwix' as the source_used when 'web' is what actually
    answered. Found via real usage: a GPIO troubleshooting query resolved
    to kiwix, kiwix's search came back empty for the long colloquial
    phrase, fell back to web, got good results — but the API response's
    source_used field still said 'kiwix' because main.py independently
    re-derived the intended source before calling route(), with no way
    to learn that an internal fallback had actually occurred.
    """
    # Query decomposition — only for auto routing
    if source == "auto":
        sub_queries = _decompose(query)
        if len(sub_queries) > 1:
            _LOGGER.info("Routing %d decomposed sub-queries for: '%s'", len(sub_queries), query[:50])
            parts = []  # list of (source, result) tuples
            for sub_q in sub_queries:
                intent = detect_intent(sub_q)
                if isinstance(intent, list):
                    sub_source = "fusion"
                    sub_result = fusion.search(sub_q, intent)
                else:
                    sub_source = intent
                    handler = SOURCE_MAP.get(sub_source)
                    cached = _get_cached(sub_source, sub_q)
                    if cached:
                        sub_result = cached
                    elif handler:
                        sub_result = handler(sub_q)
                        if _looks_empty(sub_result) and sub_source in FALLBACK_CHAIN:
                            fallback_source = FALLBACK_CHAIN[sub_source]
                            fallback_handler = SOURCE_MAP.get(fallback_source)
                            if fallback_handler:
                                fallback_result = fallback_handler(sub_q)
                                if not _looks_empty(fallback_result):
                                    sub_source = fallback_source
                                    sub_result = fallback_result
                        if not _looks_empty(sub_result):
                            _set_cached(sub_source, sub_q, sub_result)
                    else:
                        sub_result = ""
                if not _looks_empty(sub_result):
                    parts.append((sub_source, sub_result))

            if parts:
                # Merge consecutive same-source results to avoid duplicate headers
                merged = []
                current_source, current_result = parts[0]
                for source, result in parts[1:]:
                    if source == current_source:
                        current_result = current_result.rstrip() + "\n\n" + result.lstrip()
                    else:
                        merged.append((current_source, current_result))
                        current_source, current_result = source, result
                merged.append((current_source, current_result))

                # A sub-query whose own intent resolved to internal fusion
                # already contains its own per-source [SOURCE — DESC]
                # headers (added by fusion.search() itself). Wrapping that
                # block in another header at this outer level produced a
                # nonsensical "[FUSION — FUSION]" label, since "fusion"
                # isn't a real source with its own entry in
                # _HEADER_LABELS — it's just self-headered content passing
                # through. Only wrap genuinely single-source results here.
                sections = []
                for src, result in merged:
                    if src == "fusion":
                        sections.append(result)
                    else:
                        sections.append(f"{fusion._format_header(src)}\n{result}")

                # Multiple decomposed sources merged — report "fusion" as
                # the overall source if more than one distinct source
                # contributed, otherwise report the single source used
                distinct_sources = {s for s, _ in merged}
                overall_source = "fusion" if len(distinct_sources) > 1 else next(iter(distinct_sources))
                return "\n\n---\n\n".join(sections), overall_source
            # All sub-queries returned empty — fall through to single query routing

    if source == "auto":
        intent = detect_intent(query)
        # LLM may escalate to fusion for multi-topic queries
        if isinstance(intent, list):
            _LOGGER.info("Auto-escalating to fusion for query: '%s' sources=%s", query[:50], intent)
            fusion_sources = intent
            source = "fusion"
        else:
            source = intent
    else:
        _LOGGER.info("Source explicitly set to '%s' for query: '%s'", source, query[:50])

    # Handle fusion — LLM picks sources if none specified
    if source == "fusion":
        if not fusion_sources:
            fusion_sources = _llm_pick_fusion_sources(query)
        # Build a stable cache key from sorted sources
        fusion_key = ",".join(sorted(fusion_sources))
        cache_key_query = f"fusion[{fusion_key}]:{query}"
        cached = _get_cached("fusion", cache_key_query)
        if cached:
            return cached, "fusion"
        result = fusion.search(query, fusion_sources)
        if not _looks_empty(result):
            _set_cached("fusion", cache_key_query, result)
        return result, "fusion"

    handler = SOURCE_MAP.get(source)
    if not handler:
        return f"Unknown source '{source}'. Valid options: {', '.join(SOURCE_MAP.keys())}.", source

    cached = _get_cached(source, query)
    if cached:
        return cached, source

    result = handler(query)

    if _looks_empty(result) and source in FALLBACK_CHAIN:
        fallback_source = FALLBACK_CHAIN[source]
        _LOGGER.info("Result from '%s' looks empty, falling back to '%s'", source, fallback_source)
        fallback_handler = SOURCE_MAP.get(fallback_source)
        if fallback_handler:
            cached_fallback = _get_cached(fallback_source, query)
            if cached_fallback:
                return cached_fallback, fallback_source
            fallback_result = fallback_handler(query)
            if not _looks_empty(fallback_result):
                _LOGGER.info("Fallback to '%s' succeeded", fallback_source)
                _set_cached(fallback_source, query, fallback_result)
                return fallback_result, fallback_source
            _LOGGER.warning("Fallback to '%s' also returned empty result", fallback_source)

    if not _looks_empty(result):
        _set_cached(source, query, result)

    return result, source
