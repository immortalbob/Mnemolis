import logging
import requests
from app.config import settings
from app.scoring import filter_and_rank, normalize_url
from app.query_expansion import get_alternate_phrasing

_LOGGER = logging.getLogger(__name__)


def _fetch_searxng(query: str, raise_on_timeout: bool = False) -> list[dict] | None:
    """Fetch raw SearXNG results for a single query.
    Returns None on failure (connection error, bad response) so callers
    can distinguish 'the request failed' from 'the request succeeded but
    found nothing' — returns an empty list for the latter.

    raise_on_timeout: if True, a genuine timeout re-raises as
    requests.exceptions.Timeout instead of being swallowed into the same
    generic None every other failure produces. Found via a deliberate
    complexity-investigation pass: search() always returned the same
    hardcoded "Error reaching SearXNG: connection failed" message
    regardless of the real cause — even though the actual exception
    (already logged below) could be a timeout, a refused connection, a
    bad HTTP status, or malformed JSON. Given timeouts are this
    project's own documented, historically real failure mode for
    SearXNG specifically (see the wiki's "The SearXNG Timeout Lesson"),
    distinguishing it from other failures gives a meaningfully more
    accurate, more actionable error message. Used only for the primary
    fetch in search() — the alternate-query fetch always uses the
    default False, since that failure is genuinely non-fatal (the
    primary result still stands either way) and doesn't need its own
    distinct user-facing message.
    """
    try:
        resp = requests.get(
            f"{settings.searxng_url}/search",
            params={"q": query, "format": "json", "language": "en"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.exceptions.Timeout as e:
        _LOGGER.warning("SearXNG request timed out for query '%s': %s", query[:50], e)
        if raise_on_timeout:
            raise
        return None
    except Exception as e:
        _LOGGER.warning("SearXNG request failed for query '%s': %s", query[:50], e)
        return None


def search(query: str) -> str:
    """Search the web via local SearXNG instance."""
    if not settings.searxng_url:
        return "SearXNG is not configured. Set SEARXNG_URL."

    try:
        primary_results = _fetch_searxng(query, raise_on_timeout=True)
    except requests.exceptions.Timeout:
        return (
            "Error reaching SearXNG: request timed out. If this happens "
            "consistently, check SearXNG's own request_timeout setting "
            "(see the wiki's \"SearXNG request timeout\" troubleshooting page)."
        )
    if primary_results is None:
        return "Error reaching SearXNG: connection failed."

    # Multi-query expansion — search a second, differently-phrased
    # version of the query and merge raw results. Scoring below ranks
    # the combined pool against the ORIGINAL query only, so a result
    # only survives because it's genuinely relevant to what was asked,
    # not because of how the alternate phrasing happened to word it.
    # A failed alternate fetch is non-fatal — the primary result still stands.
    alternate_results = []
    alternate_query = get_alternate_phrasing(query)
    if alternate_query:
        fetched = _fetch_searxng(alternate_query)
        if fetched is not None:
            alternate_results = fetched
        _LOGGER.info(
            "Web query expansion: '%s' -> also searched '%s' (%d extra raw results)",
            query[:50], alternate_query[:50], len(alternate_results)
        )

    if not primary_results and not alternate_results:
        _LOGGER.info("SearXNG returned no results for query: %s", query[:50])
        return "No results found via web search."

    # Merge and dedupe by normalized URL — "https://www.x.com/page/" and
    # "http://x.com/page" are the same article, just different scheme/www
    # variants, so raw string comparison alone misses these duplicates.
    # Pull a generous raw set (not just top 5) so confidence-aware scoring
    # has enough candidates to actually filter from, relying on SearXNG's
    # own ranking alone misses the point of scoring at all.
    seen_urls = set()
    raw_results = []
    for r in (primary_results[:25] + alternate_results[:25]):
        url = r.get("url", "")
        normalized = normalize_url(url)
        if normalized and normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        raw_results.append(r)

    ranked = filter_and_rank(
        raw_results,
        query,
        score_threshold=settings.web_news_score_threshold,
        top_n=settings.web_news_top_n,
        title_key="title",
        content_key="content",
        url_key="url",
    )

    if not ranked:
        _LOGGER.info("SearXNG: all %d results scored below threshold for query: %s", len(raw_results), query[:50])
        return "No sufficiently relevant results found via web search."

    output = []
    for r in ranked:
        title = r.get("title", "No title")
        url = r.get("url", "")
        content = r.get("content", "").strip()
        output.append(f"**{title}**\n{content}\n{url}")
    _LOGGER.info(
        "SearXNG returned %d raw results, %d after scoring for query: %s",
        len(raw_results), len(ranked), query[:50]
    )
    return "\n\n---\n\n".join(output)
