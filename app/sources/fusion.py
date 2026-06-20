"""
Mnemolis Fusion Source
Queries multiple sources concurrently and merges results.
"""
import logging
import concurrent.futures
from app.config import settings

_LOGGER = logging.getLogger(__name__)


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


def _truncate(result: str, max_chars: int | None = None) -> str:
    """Truncate a result to max_chars, preserving whole lines.
    Defaults to settings.fusion_max_chars_per_source when not specified."""
    if max_chars is None:
        max_chars = settings.fusion_max_chars_per_source
    if len(result) <= max_chars:
        return result
    truncated = result[:max_chars]
    # Cut at last newline to avoid mid-line truncation
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars // 2:
        truncated = truncated[:last_newline]
    return truncated.rstrip() + "\n…"


def _deduplicate(results: dict[str, str]) -> dict[str, str]:
    """Remove sources whose content is substantially contained in another source's result.
    
    Checks sentence-level overlap — if 60%+ of a source's sentences already
    appear in a longer result, drop it as redundant.
    """
    if len(results) <= 1:
        return results

    def sentences(text: str) -> set[str]:
        return {
            s.strip().lower() for s in text.replace("\n", ". ").split(".")
            if len(s.strip()) > 20
        }

    sources = list(results.keys())
    keep = set(sources)

    for i, s1 in enumerate(sources):
        if s1 not in keep:
            continue
        for s2 in sources[i+1:]:
            if s2 not in keep:
                continue
            sents1 = sentences(results[s1])
            sents2 = sentences(results[s2])
            if not sents1 or not sents2:
                continue
            # Check if s2 is mostly contained in s1
            overlap = len(sents1 & sents2)
            if overlap / len(sents2) >= 0.6:
                _LOGGER.info("Fusion: dropping '%s' as redundant with '%s'", s2, s1)
                keep.discard(s2)
            elif overlap / len(sents1) >= 0.6:
                _LOGGER.info("Fusion: dropping '%s' as redundant with '%s'", s1, s2)
                keep.discard(s1)

    return {s: r for s, r in results.items() if s in keep}


def _merge_same_source(parts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge consecutive results from the same source into one block."""
    if not parts:
        return parts
    merged = []
    current_source, current_result = parts[0]
    for source, result in parts[1:]:
        if source == current_source:
            current_result = current_result.rstrip() + "\n\n" + result.lstrip()
        else:
            merged.append((current_source, current_result))
            current_source, current_result = source, result
    merged.append((current_source, current_result))
    return merged


_HEADER_LABELS = {
    "kiwix": "ENCYCLOPEDIC KNOWLEDGE — UNRELATED TO OTHER SECTIONS BELOW",
    "forecast": "WEATHER FORECAST FOR YOUR CONFIGURED HOME LOCATION",
    "news": "RECENT NEWS HEADLINES — GENERAL, NOT LOCATION-SPECIFIC UNLESS STATED",
    "web": "LIVE WEB SEARCH RESULTS",
    "uptime": "YOUR HOMELAB SERVICE STATUS",
    "ha": "YOUR HOME ASSISTANT ENTITY STATES",
    "changes": "DETECTED CHANGES SINCE LAST SNAPSHOT",
}


def _format_header(source: str) -> str:
    """Build a fusion section header. Includes a descriptive label to prevent
    the LLM from cross-referencing unrelated sections (e.g. inferring location
    from a news article when reading the forecast section)."""
    label = _HEADER_LABELS.get(source, source.upper())
    return f"[{source.upper()} — {label}]"


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

    max_sources = settings.fusion_max_sources
    if len(valid) > max_sources:
        _LOGGER.warning(
            "Fusion request has %d sources, capping at %d",
            len(valid), max_sources
        )
        valid = valid[:max_sources]

    _LOGGER.info("Fusion query: '%s' sources=%s", query[:50], valid)

    # Query all sources concurrently
    fusion_timeout = settings.fusion_timeout_seconds
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(valid)) as executor:
        futures = {
            executor.submit(SOURCE_MAP[s], query): s
            for s in valid
        }
        for future in concurrent.futures.as_completed(futures, timeout=fusion_timeout):
            source = futures[future]
            try:
                result = future.result(timeout=fusion_timeout)
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

    # Deduplicate — drop sources whose content is mostly covered by another
    successful = _deduplicate(successful)

    if len(successful) == 1:
        source, result = next(iter(successful.items()))
        return result

    # Truncate verbose results before merging
    truncated = {s: _truncate(r) for s, r in successful.items()}

    # Build parts list preserving source order
    parts = [(s, truncated[s]) for s in valid if s in truncated]

    # Merge consecutive same-source results (fixes duplicate [HA] from decomposition)
    parts = _merge_same_source(parts)

    merged = "\n\n---\n\n".join(
        f"{_format_header(source)}\n{result}" for source, result in parts
    )

    _LOGGER.info(
        "Fusion: merged %d sources for query '%s'",
        len(parts), query[:50]
    )
    return merged
