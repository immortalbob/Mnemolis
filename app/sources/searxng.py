import concurrent.futures
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
            timeout=settings.searxng_request_timeout_seconds,
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


def _alternate_phrasing_chain(query: str) -> tuple[str | None, list[dict]]:
    """The get_alternate_phrasing() + conditional second fetch chain,
    as a single unit suitable for running on its own thread — returns
    (alternate_query_or_None, alternate_results). Never raises: any
    failure here is non-fatal by design, the primary result stands on
    its own either way, exactly as before this was made concurrent.
    """
    alternate_query = get_alternate_phrasing(query)
    if not alternate_query:
        return None, []
    fetched = _fetch_searxng(alternate_query)
    return alternate_query, (fetched if fetched is not None else [])


def search(query: str) -> str:
    """Search the web via local SearXNG instance."""
    if not settings.searxng_url:
        return "SearXNG is not configured. Set SEARXNG_URL."

    # The primary fetch and the alternate-phrasing chain (LLM rephrase
    # call + a second, differently-worded fetch) run CONCURRENTLY, not
    # sequentially — found via tracing a real, live Adversarial Self-
    # Testing latency flag: paying for these one after another billed
    # roughly 4x a single fetch's cost as one source's latency, purely
    # from sequencing two operations that have no real data dependency
    # on each other (get_alternate_phrasing() only needs the ORIGINAL
    # query text, never the primary fetch's results).
    #
    # Verified safe to parallelize, not just assumed: _fetch_searxng()
    # is a pure function with no shared state, and the one real, live
    # concern — concurrent writes to the routing cache inside
    # get_alternate_phrasing() — was traced to a genuine, pre-existing
    # file-write race (see router.py's _atomic_write_json()) that's
    # already fixed; the in-memory dict mutation itself was already
    # safe under the GIL even before that fix. The primary fetch's
    # raise_on_timeout=True behavior (a real, specific, user-facing
    # error message — see "The SearXNG Timeout Lesson") is preserved
    # exactly: it's re-raised from the future's own result() call
    # below, the same as it would have been called inline.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        primary_future = executor.submit(_fetch_searxng, query, raise_on_timeout=True)
        alternate_future = executor.submit(_alternate_phrasing_chain, query)

        try:
            primary_results = primary_future.result()
        except requests.exceptions.Timeout:
            # The alternate-phrasing thread may still be running — it's
            # non-fatal and never raises, so no need to wait for or
            # cancel it; letting the `with` block's own context-manager
            # exit handle cleanup is correct and simpler than adding
            # explicit cancellation for a thread that was about to
            # finish on its own anyway.
            return (
                "Error reaching SearXNG: request timed out. If this happens "
                "consistently, check SearXNG's own request_timeout setting "
                "(see the wiki's \"SearXNG request timeout\" troubleshooting page)."
            )
        if primary_results is None:
            return "Error reaching SearXNG: connection failed."

        # Scoring below ranks the combined pool against the ORIGINAL
        # query only, so a result only survives because it's genuinely
        # relevant to what was asked, not because of how the alternate
        # phrasing happened to word it. A failed alternate fetch is
        # non-fatal — the primary result still stands.
        alternate_query, alternate_results = alternate_future.result()
        if alternate_query:
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
    for r in (primary_results[:settings.web_news_raw_result_budget] + alternate_results[:settings.web_news_raw_result_budget]):
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
