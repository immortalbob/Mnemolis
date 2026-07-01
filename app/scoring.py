"""
Mnemolis Shared Scoring
Relevance scoring for source results that don't have built-in ranking —
currently used by the web (SearXNG) and news (FreshRSS) sources.

Kiwix has its own dedicated scoring (app/sources/kiwix.py::_score_result)
tuned specifically for encyclopedia/Q&A article ranking — it isn't reused
here because its scoring signals (exact title match, Wikipedia bonus,
list-article penalty) don't transfer to web search snippets or news
headlines. This module covers the genuinely shared mechanic: stemmed
keyword overlap between a query and a title/content pair, plus a penalty
for results that describe a website rather than being an actual article.
"""
import logging
import re
from app.sources.kiwix import _stem, _STOP_WORDS

_LOGGER = logging.getLogger(__name__)


# Title patterns that indicate a homepage/about-page/site-label result
# rather than a specific article.
#
# Split into two groups found via a deliberate function-by-function read:
# the original single list applied `title_lower == p OR title_lower.startswith(p)`
# uniformly, which is correct for multi-word phrases ("welcome to", "about us")
# that always signal a generic site page when they open a title, but wrong for
# single-word patterns like "home", "error", "404" — "Home prices rise 5% in
# October" starts with "home" but is a legitimate real news article about housing
# data; "Error in climate data causes alarm" starts with "error" but is a
# real article about data quality. Confirmed real, measurable impact: a news
# article titled "Home prices rise 5%..." about housing was scored -8 against
# a query for "home prices" (net negative after keyword overlap) while the
# functionally-identical article "October sees 5% rise in home prices" scored
# +12 — a 20-point swing purely from the false-positive generic-result penalty.
#
# Fix: single-word patterns use exact-match (==) only; multi-word patterns
# keep startswith() since a title opening with "Welcome to" or "About us"
# is always a site page, never a real article, regardless of what follows.
_GENERIC_TITLE_EXACT = {
    "home", "homepage", "welcome", "login", "register", "404", "error",
    "sign in", "sign up", "log in",
}

_GENERIC_TITLE_PREFIX = [
    "welcome to", "official site", "official website",
    "about us", "about this site", "contact us", "privacy policy",
    "terms of service", "terms of use",
    "page not found",
]

# Phrases in a snippet/content that indicate generic site description
# rather than article substance.
_GENERIC_CONTENT_PATTERNS = [
    "official website of", "welcome to our website", "this website uses cookies",
    "subscribe to our newsletter", "follow us on", "all rights reserved",
]


def normalize_url(url: str) -> str:
    """
    Normalize a URL for deduplication purposes — strips scheme, leading
    'www.', trailing slashes, and query string/fragment so that
    'https://www.example.com/page/' and 'http://example.com/page'
    are recognized as the same underlying page.

    This is intentionally lossy (not a real URL for fetching) — it exists
    only to compare two URLs for "are these the same article" purposes.
    """
    if not url:
        return ""
    normalized = url.strip().lower()
    normalized = normalized.split("://", 1)[-1]  # strip scheme
    normalized = normalized.split("?", 1)[0]      # strip query string
    normalized = normalized.split("#", 1)[0]      # strip fragment
    if normalized.startswith("www."):
        normalized = normalized[4:]
    normalized = normalized.rstrip("/")
    return normalized


def _keywords(text: str) -> set[str]:
    """Extract stemmed, stop-word-filtered keywords from text.

    Found via a deliberate "bulletproofing" pass: the original filter
    (len(w) > 1) dropped every single-character token, including
    genuinely meaningful ones — "c" (the programming language), "r"
    (the statistics language). Confirmed this was a real, significant
    scoring failure, not just a theoretical gap: for the query
    "tutorial for the c programming language," a result titled "C
    Programming Language Tutorial for Beginners" scored LOWER than an
    unrelated "JavaScript Programming Language Tutorial" result, since
    "c" — the one word that would have actually distinguished them —
    was silently dropped by both sides, leaving only the generic shared
    words ("programming," "tutorial," "language") to decide the score.

    Fixed by keeping single characters specifically when they're
    alphanumeric, rather than broadly lowering the length threshold to
    0 — verified the broader change would have reintroduced real noise:
    a bare "-" (common in real text like "C++ vs C# - which is better")
    survives `.strip()` untouched (the hyphen isn't in the stripped
    character set) and would become a scored "keyword" under a blanket
    len(w) > 0 filter, awarding meaningless points for two results that
    happen to share a stray hyphen. The isalnum() check excludes that
    case while still correctly preserving "c", "c#", and "c++" (the
    latter two never hit the single-character branch at all, since
    they're multi-character before and after stripping).
    """
    words = text.lower().split()
    keywords = set()
    for w in words:
        if w in _STOP_WORDS:
            continue
        # Found via a deliberate function-by-function read: possessive
        # forms ("Apple's", "cats'") were scoring differently from their
        # base forms ("Apple", "cats") because the apostrophe is INTERIOR
        # to the token — str.strip() only removes characters from the
        # ENDS of a string, so "Apple's".strip("...\"'...") returns
        # "Apple's" unchanged (the apostrophe after 's' is at the end of
        # "Apple" in the original token "Apple's", but "Apple's" as a
        # whole has no trailing apostrophe after the strip removes the
        # outer quote chars). After stemming, "apple'" != "apple", so a
        # query for "Apple profit" missed the title "Apple's profit rose"
        # by a full title-keyword-match bonus (6 points) — confirmed
        # directly with a real scoring test. Normalized before the
        # existing strip+stem pipeline: strip "'s" (singular possessive),
        # "'" (plural possessive as in "cats'"), then let the rest of the
        # pipeline handle what remains. Verified safe for the documented
        # edge cases: contractions ("don't", "won't", "isn't") end in
        # "t" not "'s?" so are untouched; "c++", "c#", "c" are untouched;
        # bare "'s" → "" which the length/isalnum guard correctly drops.
        w = re.sub(r"'s?$", "", w)
        stripped = w.strip(".,!?;:\"'()[]{}")
        if len(stripped) > 1 or (len(stripped) == 1 and stripped.isalnum()):
            keywords.add(_stem(stripped))
    return keywords


def _is_generic_result(title: str, content: str, url: str = "") -> bool:
    """
    Return True if a result looks like a homepage/about-page/site
    description rather than a specific article. Used to penalize results
    that describe a website instead of containing relevant content.
    """
    title_lower = title.lower().strip()
    content_lower = content.lower().strip()

    # Title is just a generic site label — exact match for single-word
    # patterns, startswith for multi-word phrases (see list comments above)
    if title_lower in _GENERIC_TITLE_EXACT:
        return True
    if any(title_lower.startswith(p) for p in _GENERIC_TITLE_PREFIX):
        return True

    # "Page Not Found" and "Not Found" can appear after a 404 code rather
    # than at the start of the title ("404 - Page Not Found") so a plain
    # startswith check misses them — checking as a substring catches both
    # forms without over-broadening the single-word exact-match patterns.
    if "page not found" in title_lower or "not found" in title_lower and title_lower.startswith("404"):
        return True

    # Content reads like a generic site description
    if any(p in content_lower for p in _GENERIC_CONTENT_PATTERNS):
        return True

    # URL is a bare domain root (no path beyond a trailing slash) AND
    # the content is suspiciously short — likely a landing page, not an article
    if url:
        # Found via a deliberate "bulletproofing" pass: query strings
        # and fragments were never stripped before this path check, so
        # a genuine bare-root URL with a tracking parameter attached
        # (e.g. "https://example.com/?utm_source=twitter" — a real,
        # common pattern, not contrived) was incorrectly treated as
        # "has a real path," skipping the generic-result penalty it
        # should have received. Stripped first, mirroring
        # normalize_url()'s own approach, before checking for a path —
        # verified a genuine article path WITH tracking parameters
        # still correctly registers as having a real path either way.
        no_query = url.split("?", 1)[0].split("#", 1)[0]
        path_part = no_query.split("://", 1)[-1].split("/", 1)
        has_path = len(path_part) > 1 and path_part[1].strip("/") != ""
        if not has_path and len(content_lower) < 40:
            return True

    return False


def score_text_result(
    query: str,
    title: str,
    content: str,
    url: str = "",
    recency_bonus: int = 0,
) -> int:
    """
    Score a web/news result's relevance to a query using stemmed keyword
    overlap, with a penalty for generic/homepage-style results and an
    optional recency bonus for time-sensitive sources like news.

    Scoring breakdown:
    - Title keyword overlap: +6 per matching stemmed keyword
    - Content keyword overlap: +2 per matching stemmed keyword (normalized
      by content length so long articles don't win purely on word count)
    - Exact title match (case-insensitive, stop words ignored): +15
    - Generic/homepage result penalty: -20
    - Recency bonus: passed in by the caller (news source computes this
      from a published timestamp; web has no equivalent and passes 0)
    """
    query_keywords = _keywords(query)
    title_keywords = _keywords(title)
    content_keywords = _keywords(content)

    score = 0

    # Exact match on the meaningful query words vs title words
    if query_keywords and query_keywords == title_keywords:
        score += 15

    title_overlap = len(query_keywords & title_keywords)
    score += title_overlap * 6

    content_overlap = len(query_keywords & content_keywords)
    content_len = max(len(content_keywords), 1)
    score += int((content_overlap / content_len) * 20)

    if _is_generic_result(title, content, url):
        score -= 20

    score += recency_bonus

    return score


def filter_and_rank(
    results: list[dict],
    query: str,
    score_threshold: int = 0,
    top_n: int = 10,
    title_key: str = "title",
    content_key: str = "content",
    url_key: str = "url",
) -> list[dict]:
    """
    Score a list of results against a query, drop anything at or below
    score_threshold, then cap the survivors at top_n.

    Args:
        results: list of result dicts (web search results, news articles, etc.).
            If a result dict has a "_recency_bonus" key, its value is added
            directly to that result's score — this is the real, current
            mechanism for factoring recency into ranking; callers (e.g.
            freshrss.py) attach this key to each dict before calling, since
            an earlier id(result)-keyed dict approach didn't survive across
            calls and was abandoned in favor of this simpler convention.
        query: the original user query to score relevance against
        score_threshold: results scoring at or below this are dropped
        top_n: maximum number of results to keep after filtering
        title_key/content_key/url_key: dict keys to pull text from, since
            web and news results don't share identical field names

    Returns:
        Filtered, score-sorted list of the original result dicts (unmodified,
        no extra keys added) — caller decides what to do with the ranking.
    """
    scored = []
    for r in results:
        title = r.get(title_key, "") or ""
        content = r.get(content_key, "") or ""
        url = r.get(url_key, "") or ""
        recency = r.get("_recency_bonus", 0)
        score = score_text_result(query, title, content, url, recency_bonus=recency)
        scored.append((score, r))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    survivors = [r for score, r in scored if score > score_threshold]
    dropped = len(results) - len(survivors)
    if dropped:
        _LOGGER.info("filter_and_rank: dropped %d/%d results below threshold %d", dropped, len(results), score_threshold)

    return survivors[:top_n]
