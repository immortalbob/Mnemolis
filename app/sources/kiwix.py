import re
import logging
import requests
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Lazy imports to avoid circular imports
def _get_routing_fns():
    from app.router import _get_routing, _set_routing
    return _get_routing, _set_routing

# ---------------------------------------------------------------------------
# Dynamic book discovery
# ---------------------------------------------------------------------------

_book_cache: list[dict] = []


def _fetch_catalog_page(url: str) -> list[dict]:
    """Fetch one page of the Kiwix OPDS catalog and return book entries."""
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "dc": "http://purl.org/dc/terms/",
        }
        books = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)

            # Full versioned name lives in the text/html link href
            # e.g. /content/wikipedia_en_all_maxi_2026-02
            full_name = ""
            for link in entry.findall("atom:link", ns):
                if link.get("type") == "text/html":
                    href = link.get("href", "")
                    full_name = href.rstrip("/").split("/content/")[-1]
                    break

            if full_name and title_el is not None:
                books.append({
                    "name": full_name,
                    "title": title_el.text.strip() if title_el.text else "",
                    "summary": summary_el.text.strip() if summary_el is not None and summary_el.text else "",
                })
        return books
    except Exception as e:
        _LOGGER.warning("Kiwix catalog fetch failed for %s: %s", url, e)
        return []


def get_books() -> list[dict]:
    """Return cached book list, fetching from Kiwix catalog if needed."""
    global _book_cache
    if _book_cache:
        return _book_cache

    _LOGGER.info("Fetching Kiwix catalog...")
    all_books = []
    start = 0
    page_size = 10  # Kiwix default page size

    while True:
        url = f"{settings.kiwix_url}/catalog/v2/entries?lang=eng&start={start}&count={page_size}"
        page = _fetch_catalog_page(url)
        if not page:
            break
        all_books.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    if all_books:
        _book_cache = all_books
        _LOGGER.info("Loaded %d books from Kiwix catalog", len(all_books))
    else:
        _LOGGER.warning("No books found in Kiwix catalog")

    return _book_cache


def refresh_catalog() -> list[dict]:
    """Force refresh the book cache."""
    global _book_cache
    _book_cache = []
    return get_books()


# ---------------------------------------------------------------------------
# LLM-assisted book selection
# ---------------------------------------------------------------------------

def _pick_books_with_llm(query: str, books: list[dict], max_books: int = 2) -> list[str]:
    """Ask Ollama to pick the best books for the query. Returns ranked list of book names.
    Checks routing cache first to avoid redundant Ollama calls."""
    if not books:
        return []

    # Check routing cache
    get_routing, set_routing = _get_routing_fns()
    cache_key = f"books:{query}"
    cached = get_routing(cache_key)
    if cached:
        # Stored as comma-separated book names
        cached_books = [b.strip() for b in cached.split(",") if b.strip()]
        book_names = {b["name"] for b in books}
        valid = [b for b in cached_books if b in book_names]
        if valid:
            _LOGGER.info("Routing cache hit for book selection: '%s' -> %s", query[:50], valid)
            return valid

    from app.llm import complete, is_configured

    if not is_configured():
        book_names_list = [b["name"] for b in books]
        for name in book_names_list:
            if "wikipedia" in name:
                result = [name]
                set_routing(cache_key, ",".join(result))
                return result
        result = [book_names_list[0]] if book_names_list else []
        if result:
            set_routing(cache_key, ",".join(result))
        return result

    is_definitional = _is_definitional_query(query)
    intent_hint = (
        "This is a definitional or overview query — prefer encyclopedic sources like Wikipedia over Q&A threads."
        if is_definitional else
        "This is a specific or technical query — Stack Exchange and technical references may be appropriate."
    )

    book_list = "\n".join(
        f"- {b['name']}: {b['title']} — {b['summary'][:100]}"
        for b in books
    )

    prompt = (
        f"You are a search router. Given a user query and a list of available Kiwix offline "
        f"knowledge bases, return up to {max_books} book names that best match the query, "
        f"ranked by relevance, as a comma-separated list. "
        f"Return ONLY the exact book names separated by commas. No explanation, no punctuation other than commas.\n\n"
        f"Query: {query}\n\n"
        f"Intent: {intent_hint}\n\n"
        f"Available books:\n{book_list}\n\n"
        f"Best book names (comma-separated, most relevant first):"
    )

    raw = complete(prompt, max_tokens=150) or ""

    book_names = {b["name"] for b in books}
    chosen = []

    for candidate in raw.split(","):
        candidate = candidate.strip().strip(".")
        if not candidate:
            continue
        if candidate in book_names:
            chosen.append(candidate)
        else:
            for name in book_names:
                if candidate in name or name in candidate:
                    if name not in chosen:
                        chosen.append(name)
                    break

    chosen = chosen[:max_books]
    if chosen:
        _LOGGER.info("LLM selected books: %s", chosen)
        set_routing(cache_key, ",".join(chosen))
        return chosen

    if raw:
        _LOGGER.warning("LLM returned no valid books from '%s', falling back", raw)

    # Fallback — Wikipedia first
    book_names_list = [b["name"] for b in books]
    for name in book_names_list:
        if "wikipedia" in name:
            result = [name]
            set_routing(cache_key, ",".join(result))
            return result
    result = [book_names_list[0]] if book_names_list else []
    if result:
        set_routing(cache_key, ",".join(result))
    return result


# ---------------------------------------------------------------------------
# Search and fetch
# ---------------------------------------------------------------------------

def _search_book(query: str, book: str, limit: int | None = None) -> list:
    if limit is None:
        limit = settings.kiwix_search_limit
    try:
        response = requests.get(
            f"{settings.kiwix_url}/search",
            params={"pattern": query, "books.name": book, "limit": limit},
            timeout=5,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        results_div = soup.find("div", class_="results")
        if not results_div:
            _LOGGER.debug("No results div in Kiwix response for book=%s query=%s", book, query[:50])
            return []
        results = []
        for item in results_div.find_all("li"):
            a_tag = item.find("a")
            cite_tag = item.find("cite")
            if not a_tag:
                continue
            url = f"{settings.kiwix_url}{a_tag.get('href', '')}"
            if "/questions/tagged/" in url:
                continue
            results.append({
                "title": a_tag.get_text(strip=True),
                "excerpt": cite_tag.get_text(strip=True) if cite_tag else "",
                "url": url,
                "book": book,
            })
        _LOGGER.debug("Kiwix search returned %d results from %s", len(results), book)
        return results
    except Exception as e:
        _LOGGER.warning("Kiwix search failed for book=%s: %s", book, e)
        return []


def _fetch_article(url: str, max_chars: int = 3000) -> str:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "table", ".toc", "#toc"]):
            tag.decompose()
        content = (
            soup.find("div", class_="mw-parser-output")
            or soup.find("div", id="mw-content-text")
            or soup.find("article")
            or soup.find("div", class_="post-content")
            or soup.find("div", id="question")
            or soup.find("body")
        )
        if not content:
            _LOGGER.warning("Could not find article content at %s", url)
            return ""
        text = content.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:max_chars].strip()
    except Exception as e:
        _LOGGER.warning("Failed to fetch article from %s: %s", url, e)
        return ""


# Stop words to strip before scoring — same set as freshrss for consistency
_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "about", "what", "which", "who", "whom",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "it", "they", "them", "their",
    "tell", "explain", "describe", "define", "give", "show", "find",
    "get", "how", "why", "when", "where",
}


def _stem(word: str) -> str:
    """Reduce a word to its approximate stem by stripping common suffixes.
    Handles plural and verb forms — safe suffixes only, no semantic changes.
    Examples: marsupials→marsupial, foxes→fox, batteries→battery,
              computing→comput, computed→comput
    """
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("es") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _is_definitional_query(query: str) -> bool:
    """Return True if the query is asking for a definition or overview.
    These queries benefit from encyclopedic sources like Wikipedia over Q&A threads.
    """
    q = query.lower().strip()
    definitional_patterns = [
        "what is", "what are", "what was", "what were",
        "tell me about", "explain", "describe", "define",
        "how does", "how do", "how did",
        "who is", "who was", "who were",
        "history of", "overview of", "introduction to",
    ]
    return any(q.startswith(p) or p in q for p in definitional_patterns)


def _score_result(result: dict, query: str, primary_book: str) -> int:
    """Score a search result by relevance to the query.

    Scoring breakdown:
    - Exact title match (case-insensitive): +20
    - Stemmed title match (plural/suffix variations): +15
    - Title starts with query (after stop word removal): +10
    - Each query word in title (stop words removed): +5 each
    - Each query word in excerpt (stop words removed, normalized): +1 each
    - Wikipedia bonus: +8 for definitional queries, +3 for all others
    - Primary book bonus: +2
    - List/index article penalty: -10 for titles starting with "List of", "Lists of", "Index of", etc.
    """
    query_lower = query.lower().strip()
    title_lower = result["title"].lower().strip()
    excerpt_lower = result["excerpt"].lower()
    book = result.get("book", "")

    # Strip stop words from query for word-level scoring
    query_words = set(query_lower.split()) - _STOP_WORDS

    title_words = set(title_lower.split()) - _STOP_WORDS
    excerpt_words = set(excerpt_lower.split()) - _STOP_WORDS

    score = 0

    # Exact title match — strongest signal
    if query_lower == title_lower:
        score += 20

    # Stemmed match — catches plural/suffix variations
    # Check full query AND individual meaningful query words against title stem
    # e.g. "what are galaxies" → "galaxies" → stems to "galaxy" → matches "Galaxy" title
    elif _stem(query_lower) == _stem(title_lower):
        score += 15
    elif any(_stem(w) == _stem(title_lower) for w in query_words if len(w) > 3):
        score += 15

    # Title starts with a meaningful query term
    if any(title_lower.startswith(w) for w in query_words if len(w) > 3):
        score += 10

    # Word-level title hits with stemming
    stemmed_query_words = {_stem(w) for w in query_words}
    stemmed_title_words = {_stem(w) for w in title_words}
    title_hits = len(stemmed_query_words & stemmed_title_words)
    score += title_hits * 5

    # Word-level excerpt hits — normalize by excerpt length to avoid bias
    stemmed_excerpt_words = {_stem(w) for w in excerpt_words}
    excerpt_hits = len(stemmed_query_words & stemmed_excerpt_words)
    excerpt_len = max(len(excerpt_words), 1)
    score += int((excerpt_hits / excerpt_len) * 10)

    # Penalize list and index articles — these are navigation pages not content
    list_prefixes = ("list of", "lists of", "index of", "outline of", "category:")
    if any(title_lower.startswith(p) for p in list_prefixes):
        score -= 10
        score += 8 if _is_definitional_query(query) else 3

    # Primary book bonus
    if book == primary_book:
        score += 2

    return score


def _get_disambiguation_candidates(query: str, search_terms: str) -> list[str]:
    """
    Ask the LLM for 2-3 candidate disambiguation terms for an ambiguous word.
    Returns just the candidate word lists — actual searching and scoring
    happens in the caller, since blind LLM term selection has proven
    unreliable (it can't see Kiwix's actual index, so it guesses wrong
    in unpredictable ways — broad terms get drowned by unrelated category
    pages, narrow terms can collide with completely different topics).

    Result is cached in the routing cache so repeated queries skip the LLM call.
    """
    from app.llm import complete

    get_routing, set_routing = _get_routing_fns()
    cache_key = f"disambig_candidates:{search_terms}"
    cached = get_routing(cache_key)
    if cached:
        candidates = [c.strip() for c in cached.split("|") if c.strip()]
        if candidates:
            _LOGGER.info("Routing cache hit for disambiguation candidates: '%s' -> %s", search_terms, candidates)
            return candidates

    prompt = (
        f"The word '{search_terms}' could refer to multiple unrelated things. "
        f"Given the full question \"{query}\", list 3 different candidate search "
        f"phrases that might find the article the user actually means — each "
        f"phrase should be the original word plus ONE additional clarifying word, "
        f"and the 3 candidates should try genuinely different angles (e.g. a broad "
        f"field name, a more specific synonym, and the word alone with no qualifier). "
        f"Respond with ONLY the 3 phrases separated by '|', no explanation. "
        f"Example for 'galaxy': 'galaxy astronomy|galaxy spiral|galaxy'\n\n"
        f"Candidates for '{search_terms}':"
    )

    raw = complete(prompt, max_tokens=40) or ""
    candidates = [c.strip().strip(".").strip('"') for c in raw.split("|") if c.strip()]

    # Always include the bare original term as a guaranteed fallback candidate
    if search_terms not in candidates:
        candidates.append(search_terms)

    # Sanity filter — drop any candidate that's empty, too long, or doesn't
    # contain the original word at all
    original_word = search_terms.lower()
    valid_candidates = []
    for c in candidates:
        if not c or len(c.split()) > 3:
            continue
        if original_word not in c.lower():
            continue
        valid_candidates.append(c)

    if not valid_candidates:
        valid_candidates = [search_terms]

    # Cap at 3 candidates to bound the number of extra Kiwix searches
    valid_candidates = valid_candidates[:3]

    set_routing(cache_key, "|".join(valid_candidates))
    _LOGGER.info("Disambiguation candidates for '%s': %s", search_terms, valid_candidates)
    return valid_candidates


def _should_disambiguate(query: str, search_terms: str, selected_books: list[str]) -> bool:
    """
    Decide whether a query is eligible for multi-candidate disambiguation.

    Only triggers when:
    - The query is definitional ("what is X", "tell me about X")
    - Wikipedia was the selected book (encyclopedic ambiguity, not Q&A)
    - The search term is a single word — multi-word terms already carry
      enough context to disambiguate themselves
    - An LLM backend is configured
    """
    if not settings.llm_url or not settings.llm_model:
        return False
    if not _is_definitional_query(query):
        return False
    if not any("wikipedia" in b for b in selected_books):
        return False
    if len(search_terms.split()) != 1:
        return False
    return True


def search(query: str) -> str:
    books = get_books()

    if not books:
        return "No books available in Kiwix catalog."

    if settings.llm_url and settings.llm_model:
        selected_books = _pick_books_with_llm(query, books, max_books=2)
    else:
        # Fallback — Wikipedia first
        fallback = next((b["name"] for b in books if "wikipedia" in b["name"]), books[0]["name"])
        selected_books = [fallback]

    if not selected_books:
        return "Could not determine which Kiwix book to search."

    # Strip stop words and stem remaining words before sending to Kiwix
    # Stemming fixes disambiguation — "galaxies" → "galaxy", "batteries" → "battery"
    # so Kiwix's search engine finds the right articles instead of brand name matches
    # Keep original query for scoring so context is preserved
    search_terms = " ".join(
        _stem(w) for w in query.lower().split()
        if w not in _STOP_WORDS and len(w) > 1
    ) or query

    # For short definitional queries resolved to Wikipedia where the search
    # term is a single ambiguous word, try multiple disambiguation candidates
    # and let real Kiwix results + scoring decide which one actually works —
    # rather than trusting a single blind LLM guess about Kiwix's index
    primary_term = max(search_terms.split(), key=len) if search_terms else search_terms
    if _should_disambiguate(query, primary_term, selected_books):
        search_term_candidates = _get_disambiguation_candidates(query, primary_term)
    else:
        search_term_candidates = [search_terms]

    _LOGGER.info("Kiwix search term candidates: %s (from query: '%s')", search_term_candidates, query[:50])

    # Search each selected book with each candidate term, collect all results,
    # deduplicate by URL — scoring below picks the actual winner across everything
    all_results = []
    seen_urls = set()
    for book in selected_books:
        for term in search_term_candidates:
            for r in _search_book(term, book):
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    all_results.append(r)

    if not all_results:
        return f"No results found in {', '.join(selected_books)}."

    scored = sorted(all_results, key=lambda r: _score_result(r, query, selected_books[0]), reverse=True)
    top = scored[0]
    _LOGGER.info(
        "Selected article '%s' from %s (score=%d)",
        top["title"], top["book"], _score_result(top, query, selected_books[0])
    )

    article_text = _fetch_article(top["url"])
    if not article_text:
        # Try next best result
        for candidate in scored[1:]:
            article_text = _fetch_article(candidate["url"])
            if article_text:
                top = candidate
                break

    if not article_text:
        return f"Found {top['title']} but could not fetch article content.\nURL: {top['url']}"

    return f"# {top['title']}\nSource: {top['book']}\n\n{article_text}"
