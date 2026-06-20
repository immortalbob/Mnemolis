"""
Mnemolis Query Expansion
Generates an alternate phrasing of a query so a source can be searched
twice with genuinely different wording, then scored against the original
query — the same score-and-verify principle that fixed Kiwix search term
disambiguation, applied here to web search.

Currently used only by the web (SearXNG) source. FreshRSS fetches and
locally re-scores your existing feed items rather than issuing a remote
query, so an alternate phrasing has nothing to act on there — it's
deliberately not wired into freshrss.py.
"""
import logging
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Minimum word count for a query to be eligible for expansion — very short
# queries have little phrasing variance to exploit and aren't worth the
# extra round-trip
_MIN_WORDS = 3


def _get_routing_fns():
    """Lazy import to avoid circular imports, same pattern as kiwix.py."""
    from app.router import _get_routing, _set_routing
    return _get_routing, _set_routing


def get_alternate_phrasing(query: str) -> str | None:
    """
    Ask the LLM for one alternate phrasing of the query that uses
    genuinely different words but preserves the same intent.

    Returns None if:
    - LLM isn't configured
    - The query is too short to benefit from rephrasing
    - The LLM's response fails sanity checks (empty, too long, or
      effectively identical to the original)

    Result is cached in the routing cache so repeated queries skip the
    LLM call.
    """
    if not settings.llm_url or not settings.llm_model:
        return None

    word_count = len(query.split())
    if word_count < _MIN_WORDS:
        return None

    get_routing, set_routing = _get_routing_fns()
    cache_key = f"altquery:{query.lower().strip()}"
    cached = get_routing(cache_key)
    if cached:
        _LOGGER.info("Routing cache hit for alternate phrasing: '%s' -> '%s'", query, cached)
        return cached

    from app.llm import complete

    prompt = (
        f"Rephrase the following search query using genuinely different "
        f"words while preserving the exact same meaning and intent. "
        f"Respond with ONLY the rephrased query, no explanation, no quotes.\n\n"
        f"Original query: {query}\n\n"
        f"Rephrased query:"
    )

    raw = complete(prompt, max_tokens=40) or ""
    alternate = raw.strip().strip(".").strip('"').strip("'")

    if not alternate:
        _LOGGER.warning("Alternate phrasing returned empty result for '%s'", query)
        return None

    if len(alternate.split()) > word_count * 2:
        _LOGGER.warning("Alternate phrasing '%s' too long relative to original, discarding", alternate)
        return None

    if alternate.lower().strip() == query.lower().strip():
        _LOGGER.info("Alternate phrasing identical to original for '%s', skipping expansion", query)
        return None

    _LOGGER.info("Alternate phrasing for '%s': '%s'", query, alternate)
    set_routing(cache_key, alternate)
    return alternate
