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

    # Explicit hour count — "in the last 3 hours", "in the past 2 hours"
    #
    # Found via a deliberate complexity-investigation pass: the original
    # regex (r"(\d+)\s*hour") matched ANY number adjacent to the word
    # "hour", regardless of context — a real, reachable compound query
    # like "any updates on my 3 hour delay flight, also what changed
    # today" would incorrectly resolve to a 3-hour window from the
    # unrelated "3 hour delay" phrase, silently ignoring the user's
    # actual, more relevant "today" signal and searching a window 8x
    # narrower than intended. Confirmed reachable: this source's
    # keyword routing is a substring match, not an exact-phrase
    # requirement, so any query containing a recognized "changes"
    # trigger anywhere (e.g. "what changed") routes here regardless of
    # what else the query mentions. Fixed by requiring an actual window
    # phrase (last/past/in) immediately before the number, rather than
    # treating any nearby number as a time-window request — verified
    # this doesn't reject genuine window phrasings ("in the last 3
    # hours", "in the past 2 hours", "in the last 5 hours or so") while
    # correctly rejecting both the original false-positive case and a
    # second one found during the same investigation ("24 hour clock
    # display").
    if "hour" in q:
        import re
        m = re.search(r"(?:last|past|in the last|in the past|within the last)\s*(\d+)\s*hour", q)
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
_ROUTING_CACHE_MAX_SIZE: int = settings.routing_cache_max_size  # max entries before evicting oldest


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


def _evict_oldest_routing() -> None:
    """Remove the oldest routing cache entry.

    Found via real usage — this cache had NO size limit at all until this
    was added. The result cache (_cache) already had this exact pattern;
    the routing cache's key space is genuinely larger in practice (every
    unique conditional query, discourse-framing phrase, and disambiguation
    candidate set gets its own entry on top of plain source-routing
    decisions), making unbounded growth over sustained real-world usage
    a real, not just theoretical, concern.
    """
    if not _routing_cache:
        return
    oldest_key = min(_routing_cache, key=lambda k: _routing_cache[k][1])
    del _routing_cache[oldest_key]
    _LOGGER.debug("Evicted oldest routing cache entry: %s", oldest_key)


def _set_routing(query: str, decision: str) -> None:
    """Cache a routing decision for a query."""
    key = _routing_cache_key(query)
    # Evict oldest if at capacity (and this is a new entry)
    if key not in _routing_cache and len(_routing_cache) >= _ROUTING_CACHE_MAX_SIZE:
        _evict_oldest_routing()
        _LOGGER.info("Routing cache at max size (%d), evicted oldest entry", _ROUTING_CACHE_MAX_SIZE)
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
        # Defensive cap on load too — in practice this rarely matters
        # since ROUTING_CACHE_TTL (1 hour) means anything surviving the
        # expiry filter above was written recently anyway, but a disk
        # file saved before _ROUTING_CACHE_MAX_SIZE existed could
        # theoretically still be over the limit. Keep only the most
        # recently-written entries if so, rather than silently allowing
        # an over-limit cache to persist across a restart.
        if len(_routing_cache) > _ROUTING_CACHE_MAX_SIZE:
            newest_keys = sorted(
                _routing_cache, key=lambda k: _routing_cache[k][1], reverse=True
            )[:_ROUTING_CACHE_MAX_SIZE]
            _routing_cache = {k: _routing_cache[k] for k in newest_keys}
            _LOGGER.info(
                "Routing cache loaded from disk exceeded max size, trimmed to %d most recent entries",
                _ROUTING_CACHE_MAX_SIZE
            )
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
    # Genuinely shared with fusion.py's own _looks_empty() — found via a
    # second, deliberate "bulletproofing" re-pass that these were two
    # separate, independently-maintained copies with an overlapping but
    # NOT identical phrase list, a real, significant drift (this
    # module's own copy was missing "not configured" and "could not
    # connect", meaning FALLBACK_CHAIN's real "news" -> "web" fallback
    # never triggered when FreshRSS was genuinely unconfigured — see
    # fusion.py's docstring for the full account, including the
    # opposite-direction gaps found in fusion.py's own list too).
    return fusion._looks_empty(result)


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

# Phrases signaling that a query frames its (possibly encyclopedic) topic
# as current public discourse rather than a pure knowledge lookup — "what's
# the deal with X everyone keeps talking about" reads to a small local LLM
# almost word-for-word onto news/web's own descriptions ("current events",
# "recent information"), since kiwix's description ("factual, encyclopedic,
# or technical questions") gives no signal that an evergreen topic can
# ALSO be currently trending in public conversation. Found via extensive
# real-usage testing: "mercury retrogade", "galaxy", "bitcoin", and "black
# holes" all reproducibly routed past kiwix to news/web when phrased this
# way, even though several of these are genuinely encyclopedic topics
# kiwix's disambiguation-backed search is well suited to answer.
#
# Rather than editing SOURCE_DESCRIPTIONS to nudge the LLM's free-text
# judgment (an indirect, unverifiable lever), this is detected explicitly
# and biases the routing decision directly: if discourse-framing language
# is present and kiwix wasn't already part of the LLM's chosen source(s),
# kiwix is added and the result escalated to fusion. This doesn't override
# or discard whatever the LLM found useful (web/news content was often
# genuinely relevant in testing) — it guarantees kiwix gets a real chance
# to contribute its disambiguation-backed encyclopedic answer alongside it,
# rather than being silently excluded for these specific phrasings.
#
# kiwix.py is the canonical source of this list — it ALSO uses these
# patterns to strip the discourse-framing phrase before building Kiwix
# search terms, since "everyone"/"obsessed"/"talking"/"keep" surviving as
# literal search noise was found to matter just as much as the routing
# decision itself (kiwix matching "Howard Wolowitz" for a bitcoin query
# because the search string was "what whole bitcoin everyone obsessed").
# Importing from kiwix.py rather than duplicating avoids drift between
# the two — router.py safely imports FROM kiwix (it already does, via
# `from app.sources import kiwix`), the reverse would be circular.
_DISCOURSE_FRAMING_PATTERNS = kiwix.DISCOURSE_FRAMING_PATTERNS


def _has_discourse_framing(query: str) -> bool:
    """Return True if the query frames its topic as current public
    discourse ("everyone keeps talking about X") rather than a pure
    knowledge lookup. See _DISCOURSE_FRAMING_PATTERNS for the rationale."""
    q = query.lower()
    return any(p in q for p in _DISCOURSE_FRAMING_PATTERNS)


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


def _escalate_multi_source_for_discourse_framing(query: str, sources: list[str]) -> list[str]:
    """
    If the query has discourse-framing language and kiwix isn't already
    among the given sources, add it.

    Extracted from _llm_detect(), where this exact pattern appeared
    twice — once for a cached fusion decision, once for a fresh
    multi-source LLM decision. Confirmed via direct testing that NOT
    re-caching the escalated result after this runs is correct, not an
    oversight: _has_discourse_framing() is re-evaluated fresh on every
    call regardless of what's cached, so the escalation self-heals on
    every request rather than depending on whether a stale cache entry
    happened to bake the bias in — verified directly that a query whose
    cached decision predates this bias still correctly escalates on
    every subsequent call, not just the first.
    """
    if _has_discourse_framing(query) and "kiwix" not in sources:
        return sources + ["kiwix"]
    return sources


def _escalate_single_source_for_discourse_framing(query: str, source: str) -> list[str] | None:
    """
    If the query has discourse-framing language and the given source
    isn't kiwix, return a [source, "kiwix"] list to escalate to fusion.
    Returns None if no escalation is needed (caller should keep using
    the plain single source in that case).

    Extracted from _llm_detect() alongside
    _escalate_multi_source_for_discourse_framing() — this is the
    single-source counterpart, also previously duplicated twice (once
    for a cached single-source decision, once for a fresh one).
    """
    if _has_discourse_framing(query) and source != "kiwix":
        return [source, "kiwix"]
    return None


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
                sources = _escalate_multi_source_for_discourse_framing(query, sources)
                _LOGGER.info("Routing cache hit (fusion): '%s' -> %s", query[:50], sources)
                return sources
        elif cached in SOURCE_MAP:
            # Apply the same discourse-framing bias to a cached decision —
            # otherwise a routing cache entry written before this fix
            # existed (or before kiwix was added) would silently bypass it
            # for up to its full TTL, since the cache check above returns
            # before the bias logic further down ever runs.
            escalated = _escalate_single_source_for_discourse_framing(query, cached)
            if escalated is not None:
                _LOGGER.info(
                    "Routing cache hit but discourse-framing detected — escalating '%s' to fusion: %s",
                    query[:50], escalated
                )
                return escalated
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
            sources = _escalate_multi_source_for_discourse_framing(query, sources)
            _LOGGER.info("LLM escalated to fusion: '%s' -> %s", query[:50], sources)
            _set_routing(f"source:{query}", ",".join(sources))
            return sources
        _LOGGER.warning("LLM returned multi-source but too few valid: '%s'", raw)

    # Single source response
    chosen = raw.strip(".").strip()
    if chosen in SOURCE_MAP and chosen != "fusion":
        escalated = _escalate_single_source_for_discourse_framing(query, chosen)
        if escalated is not None:
            _LOGGER.info(
                "Discourse-framing detected — escalating '%s' to fusion: %s",
                query[:50], escalated
            )
            _set_routing(f"source:{query}", ",".join(escalated))
            return escalated
        _LOGGER.info("LLM intent: '%s' -> %s", query[:50], chosen)
        _set_routing(f"source:{query}", chosen)
        return chosen

    if raw:
        _LOGGER.warning("LLM returned unknown source '%s', falling back to kiwix", raw)

    # Found alongside the same real bug in _llm_pick_fusion_sources(),
    # via the same complexity-investigation pass: this used to cache
    # the kiwix fallback under the same key a genuine success would
    # use, permanently locking a query into kiwix for the full routing
    # cache TTL after a single transient LLM hiccup — even though a
    # retry moments later would likely have succeeded with the actual,
    # correct source. Not caching the fallback gives every subsequent
    # identical query a fresh, real chance at a correct decision.
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

    # Found via a deliberate complexity-investigation pass: this used to
    # cache the failure-default fallback under the exact same key as a
    # genuine success — a single transient LLM hiccup (a truncated
    # response, a momentary parsing glitch) would permanently lock this
    # specific query into the generic ["kiwix", "web"] fallback for the
    # full routing cache TTL, even though a retry moments later would
    # likely have succeeded with a better, more specific source
    # selection. Confirmed directly: a query that failed once and would
    # have genuinely succeeded on a second attempt never even reached
    # the LLM the second time, since the cached failure short-circuited
    # the function before the real call. Not caching the fallback means
    # every subsequent identical query gets a fresh, real chance at
    # success, at the (acceptable, since this is a per-query auto-fusion
    # path, not the hot single-source routing path) cost of re-querying
    # the LLM each time until it actually succeeds.
    _LOGGER.warning("LLM returned invalid fusion sources '%s', using defaults", raw)
    return ["kiwix", "web"]


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

# Deliberately narrow conditional pattern — ONLY matches a leading "if X,
# Y" / "should X, Y" / "in case X, Y" structure with an explicit comma.
#
# "if" is genuinely ambiguous in English: it has both a conditional sense
# ("if it's raining, bring an umbrella") and a "whether" sense ("check if
# the lights are on" = "check whether the lights are on"). The whether
# sense never appears at the very start of a sentence followed by a
# comma — it's always embedded after a verb like "check"/"see"/"tell me"/
# "let me know". Restricting to the leading-comma form sidesteps that
# ambiguity entirely rather than trying to disambiguate verb context,
# which runs into genuinely unresolvable cases ("let me know if X" could
# mean either "tell me the current status" or "notify me if it changes" —
# even a human reader can't always tell without more context).
#
# Mid-sentence and trailing "if" ("remind me to bring an umbrella if it's
# raining") are NOT matched here — deliberately out of scope for now,
# since reliably distinguishing those from "whether" usage would require
# real grammatical parsing, not pattern matching, and the value of
# guessing wrong (misframing a search result) outweighs catching the
# easier, narrower, completely unambiguous leading-comma case correctly.
_CONDITIONAL_LEAD_PATTERN = re.compile(
    r"^(if|should|in case)\s+(.+?),\s*(.+)$", re.IGNORECASE
)


def detect_conditional(query: str) -> tuple[str, str, str] | None:
    """
    Detect a leading "if X, Y" / "should X, Y" / "in case X, Y" structure.

    Returns (condition, consequence, remainder) if matched, else None.
    The condition is the searchable part ("it's raining"); the
    consequence is plain text describing what the person wants to
    happen or know, which Mnemolis cannot act on (no reminder/trigger
    capability exists) but can reference when framing the response
    around the condition's actual answer. The remainder is any
    additional, genuinely separate content that followed a conjunction
    after the consequence — empty string if none.

    Found via real usage: "if any services are down, let me know, and
    also whats the weather" originally captured "let me know, and also
    whats the weather" as a single consequence, silently swallowing a
    completely unrelated second intent ("whats the weather") that should
    have been decomposed and searched independently. The remainder is
    now split off and returned separately so the caller can route it as
    its own real intent rather than either losing it entirely or letting
    it pollute the conditional's consequence text.
    """
    m = _CONDITIONAL_LEAD_PATTERN.match(query.strip())
    if not m:
        return None
    _, condition, consequence = m.groups()
    condition = condition.strip()
    consequence = consequence.strip()

    consequence_lower = consequence.lower()
    cut_points = [
        consequence_lower.find(conj) for conj in _CONJUNCTIONS
        if conj in consequence_lower
    ]
    cut_points = [p for p in cut_points if p != -1]
    remainder = ""
    if cut_points:
        cut = min(cut_points)
        # Find which conjunction matched at this exact cut point, so we
        # can strip it cleanly from the start of the remainder
        for conj in _CONJUNCTIONS:
            if consequence_lower[cut:cut + len(conj)] == conj:
                remainder = consequence[cut + len(conj):].strip()
                break
        consequence = consequence[:cut].strip()

    return condition, consequence, remainder


# Only these sources have a structured, reliably-interpretable yes/no
# signal in their output — HA's lock/door states and uptime's service
# status are genuine binary enums (locked/unlocked, up/down), not
# free-text that would require real semantic judgment to interpret.
# Forecast is included ONLY for explicit precipitation, not subjective
# conditions like "hot enough" (no universal threshold exists for that).
# Kiwix, web, and news are deliberately excluded — their content is
# open-ended free text with no structured signal to safely key off of,
# and guessing wrong here would actively mislead rather than just be
# unhelpful, which is worse than not attempting interpretation at all.
_YES_NO_INTERPRETABLE_SOURCES = {"ha", "uptime", "forecast"}


def _interpret_binary_state(
    condition_lower: str,
    result_lower: str,
    negative_condition_keywords: list[str],
    positive_condition_keywords: list[str],
    confirms_negative_result: callable,
    confirms_positive_result: callable,
) -> bool | None:
    """
    Shared logic for interpreting a structured source's result against
    a condition that asserts one of two opposite states (down/up,
    unlocked/locked, raining/clear) — extracted from _interpret_yes_no()
    after finding the uptime and ha branches shared this exact shape.

    A real, genuine substring trap was found and fixed while extracting
    this: "locked" is a literal substring of "unlocked", so checking for
    "locked" in a result before checking for "unlocked" produces a false
    positive on any result that's actually unlocked. The ORIGINAL code
    avoided this correctly by always checking "unlocked" first,
    regardless of which polarity the condition asserted — but a first,
    naive attempt at generalizing this checked whichever result-keyword
    matched the condition's OWN polarity first, which got the order
    backwards for the "condition asserts locked" case and silently
    returned the wrong answer. Fixed by always checking
    confirms_negative_result first, in a FIXED order independent of
    which condition polarity was detected — verified against 14 manually
    constructed test cases across all three real callers (uptime, ha,
    forecast) before this was trusted, including the exact substring-trap
    scenario that exposed the bug in the first naive version.

    confirms_negative_result/confirms_positive_result are caller-supplied
    functions (not fixed strings) specifically because uptime's result
    check is a compound condition ("all" in result AND "up" in result),
    not a single keyword the way ha's ("unlocked"/"locked") and
    forecast's ("rain"/"clear") are.
    """
    is_negative_condition = any(k in condition_lower for k in negative_condition_keywords)
    is_positive_condition = any(k in condition_lower for k in positive_condition_keywords)
    if not is_negative_condition and not is_positive_condition:
        return None

    if confirms_negative_result(result_lower):
        result_is_negative = True
    elif confirms_positive_result(result_lower):
        result_is_negative = False
    else:
        return None

    return result_is_negative if is_negative_condition else not result_is_negative


def _interpret_yes_no(condition: str, result: str, source: str) -> bool | None:
    """
    Attempt to determine whether `result` confirms or denies `condition`,
    restricted to sources with a genuinely structured, reliable signal.
    Returns True (condition holds), False (condition does not hold), or
    None if no safe interpretation is possible — callers must fall back
    to presenting the raw result without a yes/no claim when None.
    """
    if source not in _YES_NO_INTERPRETABLE_SOURCES:
        return None

    result_lower = result.lower()
    condition_lower = condition.lower()

    if source == "uptime":
        return _interpret_binary_state(
            condition_lower, result_lower,
            negative_condition_keywords=["down", "not up"],
            positive_condition_keywords=["up", "running", "working"],
            confirms_negative_result=lambda r: "down" in r,
            confirms_positive_result=lambda r: "all" in r and "up" in r,
        )

    if source == "ha":
        return _interpret_binary_state(
            condition_lower, result_lower,
            negative_condition_keywords=["unlocked"],
            positive_condition_keywords=["locked"],
            confirms_negative_result=lambda r: "unlocked" in r,
            confirms_positive_result=lambda r: "locked" in r,
        )

    if source == "forecast":
        # Only explicit precipitation — never attempt subjective
        # conditions ("hot enough", "nice out") which have no universal
        # threshold and would require a real guess, not a safe inference.
        # No positive_condition_keywords at all — deliberately one-
        # directional, verified the generalized helper handles an empty
        # list correctly (always returns None unless the condition
        # actually mentions rain).
        return _interpret_binary_state(
            condition_lower, result_lower,
            negative_condition_keywords=["rain", "raining"],
            positive_condition_keywords=[],
            confirms_negative_result=lambda r: "rain" in r or "storm" in r or "shower" in r,
            confirms_positive_result=lambda r: "clear" in r,
        )

    return None


def _frame_conditional_response(condition: str, consequence: str, condition_result: str, condition_source: str) -> str:
    """
    Compose the final response for a detected conditional query.

    When a safe yes/no interpretation exists (structured sources only —
    see _interpret_yes_no), state explicitly whether the consequence
    applies, e.g. "It IS raining, so you may want to bring an umbrella."
    Otherwise, present the real condition result plainly with a note
    that this was a conditional question, since Mnemolis has no
    reminder/trigger capability and guessing wrong on an open-ended
    condition (encyclopedic facts, subjective thresholds) would actively
    mislead rather than just be unhelpful.

    Takes the actual source string returned by route_with_source()
    directly, rather than guessing it from header text in the result —
    a single, non-decomposed result has no [SOURCE] header at all
    (headers are only added when merging multiple decomposed parts), so
    text-based source guessing silently failed for the most common case.
    """
    verdict = _interpret_yes_no(condition, condition_result, condition_source)

    if verdict is True:
        return (
            f"This was a conditional question: \"if {condition}, {consequence}.\"\n\n"
            f"It is the case that {condition} — so you may want to: {consequence}\n\n"
            f"{condition_result}"
        )
    if verdict is False:
        return (
            f"This was a conditional question: \"if {condition}, {consequence}.\"\n\n"
            f"It is NOT the case that {condition} — so the suggested action ({consequence}) may not apply.\n\n"
            f"{condition_result}"
        )

    # No safe interpretation available — present the real result plainly
    return (
        f"This was a conditional question: \"if {condition}, {consequence}.\" "
        f"Here is what was found regarding the condition — you'll need to judge "
        f"whether it applies:\n\n{condition_result}"
    )


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

    # Found via a deliberate, thorough complexity-investigation pass:
    # the pronoun "I" is always capitalized in English regardless of
    # sentence position, making it look exactly like a proper noun to
    # this naive capitalization check. "what's happening in Texas, plus
    # I need help with my router" was being incorrectly protected as a
    # bare proper-noun pair ("Texas" + "I"), causing the ENTIRE query to
    # not split at all — a genuinely common, natural way to phrase a
    # second, unrelated request, not a contrived edge case. "I" can
    # never be the real-world entity half of a genuine pair like "Iran
    # and Israel," so it's excluded explicitly rather than trying to
    # build a broader pronoun list — no other common English pronoun
    # (he/she/they/we) is unconditionally capitalized regardless of
    # context the way "I" uniquely is, so no other word produces this
    # exact false-positive shape.
    #
    # The symmetric check on before_tail closes an asymmetric gap found
    # during the SAME re-read that found the after_head case above:
    # "I and Texas" (the unusual word order, "I" directly adjacent to
    # the conjunction with no verb between them) still triggered the
    # false positive, even after the after_head fix. Verified this is
    # genuinely low-reachability through natural English — "I" is
    # almost always followed by a verb ("I want", "I think", "I need"),
    # not directly by a conjunction, so before_tail being exactly "I"
    # essentially never occurs in a real, natural compound request the
    # way after_head being "I" commonly does ("X, plus I need..."). Low
    # reachability isn't the same as zero, though, and the fix is cheap
    # — added for completeness rather than leaving a known, if narrow,
    # asymmetry in place.
    if after_head.rstrip(",.;:").lower() == "i" or before_tail.rstrip(",.;:").lower() == "i":
        return False

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
        # segment_start marks where the CURRENT accumulating part began;
        # search_from marks where to resume looking for the next
        # occurrence of this conjunction. These differ specifically when
        # a proper-noun pair is skipped — found via real usage: the
        # original version reset `remaining` (and therefore the next
        # part's start) to right after EVERY occurrence, including
        # skipped ones, which silently discarded real, meaningful text
        # that came before a protected pair ("also whats happening with
        # Iran and" was discarded entirely, just because "Iran and
        # Israel" needed protecting — the text before the pair was real
        # content from a genuinely separate intent, not part of the pair
        # at all). Now, skipping a proper-noun-pair occurrence advances
        # search_from (so we don't re-examine the same occurrence) but
        # leaves segment_start untouched, so that text accumulates into
        # the next real part instead of vanishing.
        segment_start = 0
        search_from = 0
        while True:
            idx = q_lower.find(conj, search_from)
            if idx == -1:
                break
            if _is_proper_noun_pair_at(q, idx, len(conj)):
                search_from = idx + len(conj)
                continue
            part = q[segment_start:idx].strip()
            if part:
                parts.append(part)
            segment_start = idx + len(conj)
            search_from = segment_start
        remaining = q[segment_start:].strip()
        if remaining:
            parts.append(remaining)

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


def _resolve_single_source(source: str, query: str) -> tuple[str, str]:
    """
    Resolve a single (non-fusion) source for a query: check cache, call
    the handler, and fall back to FALLBACK_CHAIN's target if the result
    looks empty — checking the fallback target's own cache first too.

    Extracted from two previously slightly-different inline
    implementations found during a deliberate refactoring pass (prompted
    by a cyclomatic-complexity check flagging route_with_source() as the
    most complex function in the codebase by a wide margin) — one inside
    the decomposition loop, one at the top level for a directly-routed
    query. Comparing them side by side surfaced a real, previously
    undetected inconsistency: the decomposition loop's fallback path
    called the fallback handler directly with no cache check, while the
    top-level path correctly checked _get_cached(fallback_source, query)
    first. This unified version follows the more correct, top-level
    behavior — a fallback result, once cached, should be served from
    cache the same way any other result is, regardless of which code
    path (direct routing or a decomposed sub-query) led to it.

    Returns (result, source_used) — source_used reflects the actual
    source that produced the result, which may differ from the
    originally-intended `source` argument if a fallback occurred.
    """
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


def _resolve_conditional(query: str, source: str) -> tuple[str, str] | None:
    """
    Detect and resolve a leading "if X, Y" conditional structure, if
    present. Returns (result, source_used) if the query was genuinely
    conditional, or None if it wasn't — callers should fall through to
    normal routing/decomposition on None, exactly as route_with_source()
    already did before this logic was a separate function.

    Extracted from route_with_source() during the same complexity-
    reduction effort that produced _resolve_single_source() — this is
    the second-most complex remaining piece of that function, and the
    one with the densest real bug history in the project (see the wiki's
    "The Recursion Design Bug" page for the full story of why this logic
    looks the way it does, including a depth-counter approach that was
    tried, found to have a real bug, and replaced with the simpler
    recursive-on-the-extracted-condition-text approach used here).

    Conditional detection only ever applies to source == "auto" — an
    explicit source request skips it entirely, the same way it already
    skips decomposition. Only the condition is searched; Mnemolis has no
    reminder/trigger capability to act on the consequence, but the
    response is framed around the condition's actual real answer rather
    than presenting it as an unconditional fact.

    Recurses into route_with_source() by passing the already-extracted
    CONDITION text, never the original "if X, Y" string — so the
    recursive call's input essentially never re-matches the leading
    "if/should/in case" pattern again, naturally self-limiting without
    needing an explicit recursion-depth counter.
    """
    if source != "auto":
        return None

    conditional = detect_conditional(query)
    if not conditional:
        return None

    condition, consequence, remainder = conditional
    _LOGGER.info(
        "Detected conditional query — condition=%r consequence=%r remainder=%r",
        condition[:50], consequence[:50], remainder[:50]
    )
    condition_result, condition_source = route_with_source(condition, "auto")
    framed = _frame_conditional_response(condition, consequence, condition_result, condition_source)

    # A real, separate intent followed the conditional statement
    # ("...let me know, and also whats the weather") — search it
    # independently and merge it in, rather than either losing it
    # entirely or letting it pollute the conditional's consequence.
    #
    # If the remainder itself decomposes into multiple distinct
    # sources, route_with_source() already returns "fusion" as
    # its reported source for an already-self-headered result
    # (each contributing source has its own [SOURCE — DESC]
    # header baked in). Wrapping that in ANOTHER header using the
    # literal string "fusion" produces the same nonsensical
    # "[FUSION — FUSION]" double-header bug found and fixed
    # earlier this session in the decomposition loop — this is
    # the same root cause showing up at a different call site
    # that needed the identical fix: only wrap genuinely
    # single-source results, pass fusion results through as-is.
    if remainder:
        remainder_result, remainder_source = route_with_source(remainder, "auto")
        if not _looks_empty(remainder_result):
            overall_source = "fusion" if remainder_source != condition_source else condition_source
            remainder_section = (
                remainder_result if remainder_source == "fusion"
                else f"{fusion._format_header(remainder_source)}\n{remainder_result}"
            )
            condition_section = (
                framed if condition_source == "fusion"
                else f"{fusion._format_header(condition_source)}\n{framed}"
            )
            merged_text = f"{condition_section}\n\n---\n\n{remainder_section}"
            return merged_text, overall_source

    return framed, condition_source


def _merge_decomposed_parts(parts: list[tuple[str, str]]) -> tuple[str, str]:
    """
    Merge a list of (source, result) tuples from decomposed sub-queries
    into one final, headered response string, plus the overall source
    label to report.

    Extracted from route_with_source()'s decomposition branch during the
    same complexity-reduction effort that produced _resolve_single_source()
    and _resolve_conditional() — this is the formatting/merging half of
    that branch; the per-sub-query resolution loop that builds `parts` in
    the first place stays inline, since it recurses into route_with_source()
    itself and extracting it cleanly would mean threading several more
    pieces of loop state through a function boundary for comparatively
    little complexity reduction.

    Consecutive results from the same source are merged into one block
    first, so e.g. "indoor air quality and are the doors locked" (both
    resolving to `ha`) returns one [HA] section, not two — via
    fusion._merge_same_source(), genuinely shared with fusion.search()'s
    own identical need to merge consecutive same-source results
    (originally a byte-for-byte duplicate, found and unified during a
    deliberate complexity-investigation pass on fusion.search() itself).
    A sub-query whose own intent resolved to internal fusion already
    contains its own per-source [SOURCE — DESC] headers (added by
    fusion.search() itself) — wrapping that block in another header at
    this outer level produces a nonsensical "[FUSION — FUSION]" label,
    since "fusion" isn't a real source with its own entry in
    _HEADER_LABELS, just self-headered content passing through. Only
    genuinely single-source results get wrapped here.
    """
    merged = fusion._merge_same_source(parts)

    sections = []
    for src, result in merged:
        if src == "fusion":
            sections.append(result)
        else:
            sections.append(f"{fusion._format_header(src)}\n{result}")

    # Multiple decomposed sources merged — report "fusion" as the overall
    # source if more than one distinct source contributed, otherwise
    # report the single source used
    distinct_sources = {s for s, _ in merged}
    overall_source = "fusion" if len(distinct_sources) > 1 else next(iter(distinct_sources))
    return "\n\n---\n\n".join(sections), overall_source


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

    Conditional detection (both at the top level and re-checked against
    each decomposed sub-query) recurses into this function by passing the
    already-extracted CONDITION text, never the original "if X, Y" string
    — so the recursive call's input essentially never re-matches the
    leading "if/should/in case" pattern again. This is naturally
    self-limiting without needing an explicit recursion-depth counter.
    An earlier version used a manual _depth parameter for this, but that
    introduced a real bug: the depth incremented before the conditional
    was actually consumed, so a sub-query's recursive call (still
    containing the full "if X, Y" text) had its OWN necessary
    conditional re-detection blocked by the very counter meant to guard
    against runaway recursion that was never actually possible.
    """
    # Conditional detection — only for auto routing, checked before
    # decomposition since "if X, Y" is structurally a single statement
    # with a condition and a consequence, not a flat list of independent
    # intents the way "X and Y" is.
    conditional_result = _resolve_conditional(query, source)
    if conditional_result is not None:
        return conditional_result

    # Query decomposition — only for auto routing
    if source == "auto":
        sub_queries = _decompose(query)
        if len(sub_queries) > 1:
            _LOGGER.info("Routing %d decomposed sub-queries for: '%s'", len(sub_queries), query[:50])
            parts = []  # list of (source, result) tuples
            for sub_q in sub_queries:
                # Each decomposed sub-query may itself contain a leading
                # "if X, Y" structure that the top-level conditional check
                # never sees — detect_conditional() only runs once, against
                # the FULL original query, before decomposition. A query
                # like "what is the weather and if the back door is
                # unlocked, let me know" doesn't start with "if" so the
                # top-level check correctly returns None, but the second
                # decomposed sub-query ("if the back door is unlocked, let
                # me know") absolutely does match and was never being
                # re-checked at all.
                #
                # Mirrors the top-level handling exactly: extract the
                # condition and search ONLY that (via a recursive call on
                # the condition text, not the original "if X, Y" string),
                # then frame the response. An earlier version of this fix
                # recursed on the original sub_q string with a manual
                # _depth counter meant to stop infinite recursion — but
                # that counter blocked the recursive call's OWN necessary
                # re-detection of the very same conditional it was meant
                # to handle, since the depth incremented before the
                # conditional was actually consumed. Passing the
                # already-extracted condition (not the still-"if"-prefixed
                # sub_q) sidesteps the whole problem: the condition text
                # essentially never re-matches the leading "if/should/in
                # case" pattern, so this naturally terminates without
                # needing any artificial depth limit at all.
                sub_conditional = detect_conditional(sub_q)
                if sub_conditional:
                    sub_condition, sub_consequence, sub_remainder = sub_conditional
                    sub_condition_result, sub_source = route_with_source(sub_condition, "auto")
                    sub_result = _frame_conditional_response(
                        sub_condition, sub_consequence, sub_condition_result, sub_source
                    )
                    if not _looks_empty(sub_condition_result):
                        parts.append((sub_source, sub_result))
                    # A real, separate intent followed the conditional
                    # within this one decomposed sub-query — search it
                    # independently too, rather than losing it or letting
                    # it pollute the consequence text
                    if sub_remainder:
                        remainder_result, remainder_source = route_with_source(sub_remainder, "auto")
                        if not _looks_empty(remainder_result):
                            parts.append((remainder_source, remainder_result))
                    continue

                intent = detect_intent(sub_q)
                if isinstance(intent, list):
                    # Found via a deliberate complexity-reduction
                    # investigation (comparing this dispatch against the
                    # top-level single-query fusion dispatch, the same
                    # side-by-side-comparison discipline that found two
                    # real bugs during the prior extraction pass): a
                    # decomposed sub-query that itself resolves to fusion
                    # had NO caching at all, unlike every other path in
                    # the system — every individual single-source
                    # sub-query result gets cached via
                    # _resolve_single_source(), the overall merged
                    # decomposed response gets no cache of its own either,
                    # but a sub-query-level fusion result fell through
                    # both, meaning a repeated compound query whose
                    # individual clause happened to resolve to multiple
                    # sources internally re-ran _llm_pick_fusion_sources()
                    # and re-queried every fusion source on every single
                    # request, even identical repeats. Fixed by using the
                    # exact same cache-key convention the top-level
                    # fusion path already uses.
                    sub_source = "fusion"
                    sub_fusion_key = ",".join(sorted(intent))
                    sub_cache_key = f"fusion[{sub_fusion_key}]:{sub_q}"
                    cached_sub_fusion = _get_cached("fusion", sub_cache_key)
                    if cached_sub_fusion:
                        sub_result = cached_sub_fusion
                    else:
                        sub_result = fusion.search(sub_q, intent)
                        if not _looks_empty(sub_result):
                            _set_cached("fusion", sub_cache_key, sub_result)
                else:
                    sub_result, sub_source = _resolve_single_source(intent, sub_q)
                if not _looks_empty(sub_result):
                    parts.append((sub_source, sub_result))

            if parts:
                return _merge_decomposed_parts(parts)
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

    return _resolve_single_source(source, query)
