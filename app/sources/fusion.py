"""
Mnemolis Fusion Source
Queries multiple sources concurrently and merges results.
"""
import logging
import concurrent.futures
from app.config import settings

_LOGGER = logging.getLogger(__name__)


def _looks_empty(result: str) -> bool:
    """Check if a result is empty or an error.

    Genuinely shared with router.py, which used to carry a separate,
    independently-maintained copy of this exact function with an
    overlapping but NOT identical phrase list — found via a second,
    deliberate "bulletproofing" re-pass specifically looking for the
    same kind of cross-file drift already found and fixed once this
    release cycle (_merge_same_source). The drift here was real and
    significant, in both directions:

    router.py's list was missing "not configured" and "could not
    connect" — meaning a genuinely real, reachable scenario (FreshRSS
    unconfigured, asking for news) returned the literal config-error
    string "FreshRSS is not configured. Set FRESHRSS_URL and
    FRESHRSS_USER." as if it were real, successful content, since
    router.py's _looks_empty() never recognized it as empty — and
    FALLBACK_CHAIN's real, configured "news" -> "web" fallback never
    triggered as a result. Confirmed directly: route_with_source("give
    me the news", "news") with FRESHRSS_URL unset returned the raw
    config-error message with source_used="news", not the automatic
    fallback to "web" the fallback chain is clearly designed to
    provide.

    fusion.py's own list was separately missing "unknown source" (the
    real fix from a previous pass this same release cycle) and "error
    reaching" — found while verifying the unified list against every
    real failure message every source file actually produces:
    "Error reaching SearXNG: connection failed." doesn't contain a bare
    "error:" (the colon comes after "SearXNG", not immediately after
    "Error"), so it needed its own, more specific phrase.

    router.py already imports this module directly (it calls
    fusion.search() for internal multi-source dispatch), making this
    the safe home for the shared, canonical version — the reverse
    import direction would create a circular import.
    """
    if not result:
        return True
    result_lower = result.lower()
    empty_phrases = [
        "no results found", "no recent articles", "not yet implemented",
        "could not fetch", "no books available", "could not determine",
        "unknown source", "not configured", "could not connect",
        "error:", "error reaching",
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
    """Merge consecutive results from the same source into one block.

    Genuinely shared with app/router.py's _merge_decomposed_parts(),
    which used to carry a byte-for-byte identical copy of this exact
    logic — found during a deliberate complexity-investigation pass on
    this function. router.py already imports this module directly
    (it calls fusion.search() for internal multi-source dispatch), so
    this is the safe import direction; fusion.py never imports from
    router.py, avoiding a circular import the reverse direction would
    create.

    Only ever compares the OUTER tuple label — if one part's source is
    the literal string "fusion" (an already-headered, nested multi-
    source blob) and a separate part's source is a bare label that
    happens to ALSO be one of the sources nested inside that blob, this
    function has no way to see the duplication, since it can't look
    inside an opaque "fusion"-labeled string. router.py's
    _merge_decomposed_parts() runs a second, separate pass —
    _dedupe_nested_fusion_sections() — after this one specifically to
    catch that case, operating on the final assembled text rather than
    on these tuples. Found via a real, live duplicate-section bug.

    Deduplicates individual items BETWEEN the two blobs being merged
    here, via _dedupe_items_across_blobs() below, before joining them —
    done at THIS exact point deliberately, while the boundary between
    "result from call 1" and "result from call 2" is still completely
    unambiguous (two distinct strings, not yet concatenated). A second,
    real bug found verifying the first nested-fusion-section fix: two
    independent calls to the same backend (e.g. one nested inside an
    internal-fusion sub-query, one a separately-decomposed clause's own
    bare resolution) can return overlapping items — confirmed via a
    real FreshRSS "general query, return everything" case where the
    same recent headlines legitimately came back from two separate,
    redundant calls. Deduping AFTER the plain "\\n\\n" join below would
    require re-finding that same boundary inside already-merged text,
    where it's no longer reliably distinguishable from an ordinary
    paragraph break within one blob's own content — tried first,
    confirmed broken via a failing test, fixed by moving the dedup
    earlier instead."""
    if not parts:
        return parts
    merged = []
    current_source, current_result = parts[0]
    for source, result in parts[1:]:
        if source == current_source:
            current_result, result, is_multi_item = _dedupe_items_across_blobs(current_result, result)
            separator = "\n\n---\n\n" if is_multi_item else "\n\n"
            current_result = current_result.rstrip() + separator + result.lstrip()
        else:
            merged.append((current_source, current_result))
            current_source, current_result = source, result
    merged.append((current_source, current_result))
    return merged


def _dedupe_items_across_blobs(first: str, second: str) -> tuple[str, str, bool]:
    """Remove items from `second` that already appear in `first`,
    before the two get joined by _merge_same_source() above.

    Splits each blob on the real, established "\\n\\n---\\n\\n" item
    separator every multi-item source (freshrss.py's news, searxng.py's
    web) already uses for its own individual result blocks. Dedup key
    is each item's first line — the "**Title** (Source)" or "**Title**"
    line every one of these sources leads with — exact match only,
    never a fuzzy/similarity comparison, so this can only ever remove a
    genuinely identical leading line, not something that merely looks
    similar.

    A true no-op (returns both inputs completely unchanged) for any
    blob with no real overlap — the overwhelming common case — and for
    any content that isn't built from this "**Title**"-leading,
    "---"-separated item convention at all (e.g. a single-item plain-
    text result, or Home Assistant's differently-shaped bulleted-list
    content), since those simply won't split into multiple items with
    a shared leading line to begin with.

    Returns (first, second, is_multi_item) — the third value tells the
    caller whether either blob looked like real multi-item list
    content, so the caller can join the two with the real "\\n\\n---\\n\\n"
    item separator instead of a bare "\\n\\n" that would otherwise leave
    the boundary between the two original blobs visually indistinguishable
    from an ordinary paragraph break within either one's own content —
    confirmed via a real failing test this exact ambiguity is what
    silently broke a first version of this fix's own dedup logic
    downstream.
    """
    first_items = first.split("\n\n---\n\n")
    second_items = second.split("\n\n---\n\n")
    is_multi_item = len(first_items) > 1 or len(second_items) > 1
    if not is_multi_item:
        return first, second, False  # neither blob looks like a multi-item list — nothing to dedupe

    seen_titles = {item.strip().split("\n", 1)[0].strip() for item in first_items if item.strip()}
    deduped_second_items = [
        item for item in second_items
        if item.strip().split("\n", 1)[0].strip() not in seen_titles
    ]
    if len(deduped_second_items) == len(second_items):
        return first, second, True  # multi-item, but nothing was actually a duplicate
    return first, "\n\n---\n\n".join(deduped_second_items), True


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

    # Found via a deliberate "bulletproofing" pass: FUSION_MAX_SOURCES
    # is a plain, unvalidated int — setting it to 0 (a plausible
    # misconfiguration, e.g. someone trying to "disable" fusion
    # entirely) capped `valid` to an empty list AFTER the only existing
    # empty-list check above, meaning ThreadPoolExecutor(max_workers=0)
    # crashed with a raw ValueError ("max_workers must be greater than
    # 0") instead of the sensible "no valid sources" message already
    # used for the genuinely equivalent case just above. Re-checking
    # for emptiness after capping reuses that same, already-correct
    # error path rather than introducing a second one.
    max_sources = settings.fusion_max_sources
    if len(valid) > max_sources:
        _LOGGER.warning(
            "Fusion request has %d sources, capping at %d",
            len(valid), max_sources
        )
        valid = valid[:max_sources]

    if not valid:
        return "No valid sources specified for fusion query."

    _LOGGER.info("Fusion query: '%s' sources=%s", query[:50], valid)

    # Query all sources concurrently
    fusion_timeout = settings.fusion_timeout_seconds
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(valid)) as executor:
        futures = {
            executor.submit(SOURCE_MAP[s], query): s
            for s in valid
        }
        try:
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
        except concurrent.futures.TimeoutError:
            # Found via a deliberate complexity-investigation pass: this
            # is as_completed()'s OWN overall timeout, distinct from the
            # per-future future.result(timeout=...) timeout caught
            # inside the loop above. as_completed() raises this for the
            # entire iteration once the deadline passes, regardless of
            # how many individual futures had already completed — and
            # since this was previously uncaught here, a single slow
            # source mixed with a fast one crashed the ENTIRE fusion
            # call, discarding the fast source's genuinely successful
            # result along with it, even though that data already
            # existed and was sitting in `results`. This directly
            # undermined fusion's own documented graceful-degradation
            # design — "filters empty or failed results... if only one
            # source returns results, it is returned directly" — by
            # turning a partial success into a total, opaque failure
            # instead. Any future not already in `results` by the time
            # this fires is genuinely still running past the deadline;
            # mark it as failed without losing whatever real results
            # were already gathered before the timeout.
            _LOGGER.warning("Fusion: overall timeout reached, %d source(s) still running", len(futures) - len(results))
            for future, source in futures.items():
                if source not in results:
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
    #
    # Found via a deliberate "bulletproofing" pass: this used to call
    # _merge_same_source() here too, with a comment claiming it "fixes
    # duplicate [HA] from decomposition" — but that scenario can't
    # actually occur AT THIS CALL SITE. `valid` (built at the top of
    # this function) is already deduplicated via its own `seen` set
    # before `parts` is ever built, so `parts` here can never contain
    # two entries for the same source — there's nothing for
    # _merge_same_source() to merge. The comment's real scenario (two
    # independently-decomposed sub-queries both resolving to the same
    # source, e.g. "ha") genuinely happens in router.py's own
    # _merge_decomposed_parts(), the OTHER real call site for this
    # shared function — that one still needs it; this one never did.
    parts = [(s, truncated[s]) for s in valid if s in truncated]

    merged = "\n\n---\n\n".join(
        f"{_format_header(source)}\n{result}" for source, result in parts
    )

    _LOGGER.info(
        "Fusion: merged %d sources for query '%s'",
        len(parts), query[:50]
    )
    return merged
