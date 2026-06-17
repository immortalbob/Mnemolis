"""
MiniSearch Fusion Source
Queries multiple sources concurrently and merges results.
"""
import logging
import concurrent.futures

_LOGGER = logging.getLogger(__name__)

# Maximum time to wait for any single source in a fusion query
FUSION_TIMEOUT = 15  # seconds

# Maximum number of sources allowed in a single fusion query
FUSION_MAX_SOURCES = 4


def _looks_empty(result: str) -> bool:
    """Check if a result is empty or an error."""
    if not result:
        return True
    result_lower = result.lower()
    empty_phrases = [
        "no results found", "no recent articles", "not yet implemented",
        "could not fetch", "no books available", "could not determine",
        "not configured", "could not connect", "error:",
    ]
    return any(phrase in result_lower for phrase in empty_phrases)


def search(query: str, sources: list[str] | None = None) -> str:
    """
    Query multiple sources concurrently and merge results.

    Args:
        query: The search query
        sources: List of source names to query. If None, uses default ["kiwix", "web"]

    Returns:
        Merged result string with source attribution headers.
        If only one source succeeds, returns its result directly without a header.
    """
    # Import here to avoid circular imports
    from app.router import SOURCE_MAP

    if not sources:
        sources = ["kiwix", "web"]

    # Validate and deduplicate
    valid = []
    seen = set()
    for s in sources:
        if s == "fusion":
            _LOGGER.warning("Fusion source cannot reference itself — skipping")
            continue
        if s not in SOURCE_MAP:
            _LOGGER.warning("Unknown source '%s' in fusion request — skipping", s)
            continue
        if s not in seen:
            valid.append(s)
            seen.add(s)

    if not valid:
        return "No valid sources specified for fusion query."

    if len(valid) > FUSION_MAX_SOURCES:
        _LOGGER.warning(
            "Fusion request has %d sources, capping at %d",
            len(valid), FUSION_MAX_SOURCES
        )
        valid = valid[:FUSION_MAX_SOURCES]

    _LOGGER.info("Fusion query: '%s' sources=%s", query[:50], valid)

    # Query all sources concurrently
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(valid)) as executor:
        futures = {
            executor.submit(SOURCE_MAP[s], query): s
            for s in valid
        }
        for future in concurrent.futures.as_completed(futures, timeout=FUSION_TIMEOUT):
            source = futures[future]
            try:
                result = future.result(timeout=FUSION_TIMEOUT)
                results[source] = result
                _LOGGER.info("Fusion: source '%s' returned %d chars", source, len(result))
            except concurrent.futures.TimeoutError:
                _LOGGER.warning("Fusion: source '%s' timed out", source)
                results[source] = None
            except Exception as e:
                _LOGGER.warning("Fusion: source '%s' failed: %s", source, e)
                results[source] = None

    # Filter successful non-empty results — preserve original source order
    successful = {
        s: r for s in valid
        if (r := results.get(s)) and not _looks_empty(r)
    }

    if not successful:
        return "No results returned from any source in fusion query."

    # Single successful source — return directly without header
    if len(successful) == 1:
        source, result = next(iter(successful.items()))
        _LOGGER.info("Fusion: only '%s' returned results, returning directly", source)
        return result

    # Multiple sources — merge with attribution headers
    parts = []
    for source, result in successful.items():
        parts.append(f"[{source.upper()}]\n{result}")

    _LOGGER.info(
        "Fusion: merged %d sources for query '%s'",
        len(successful), query[:50]
    )
    return "\n\n---\n\n".join(parts)
