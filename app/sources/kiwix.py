import re
import logging
import requests
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from app.config import settings

_LOGGER = logging.getLogger(__name__)

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
    """Ask Ollama to pick the best books for the query. Returns ranked list of book names."""
    if not books:
        return []

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
        f"Available books:\n{book_list}\n\n"
        f"Best book names (comma-separated, most relevant first):"
    )

    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 150},
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Handle thinking models (qwen3 etc) that return empty response with thinking field
        raw = data.get("response", "").strip()
        if not raw:
            thinking = data.get("thinking", "")
            lines = [l.strip() for l in thinking.splitlines() if l.strip()]
            raw = lines[-1] if lines else ""

        book_names = {b["name"] for b in books}
        chosen = []

        for candidate in raw.split(","):
            candidate = candidate.strip().strip(".")
            if candidate in book_names:
                chosen.append(candidate)
            else:
                # Fuzzy match
                for name in book_names:
                    if candidate in name or name in candidate:
                        if name not in chosen:
                            chosen.append(name)
                        break

        chosen = chosen[:max_books]
        if chosen:
            _LOGGER.info("LLM selected books: %s", chosen)
            return chosen

        _LOGGER.warning("LLM returned no valid books from '%s', falling back", raw)

    except Exception as e:
        _LOGGER.warning("LLM book selection failed: %s", e)

    # Fallback — Wikipedia first
    book_names_list = [b["name"] for b in books]
    for name in book_names_list:
        if "wikipedia" in name:
            return [name]
    return [book_names_list[0]] if book_names_list else []


# ---------------------------------------------------------------------------
# Search and fetch
# ---------------------------------------------------------------------------

def _search_book(query: str, book: str, limit: int = 5) -> list:
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
        return results
    except Exception:
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
            return ""
        text = content.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:max_chars].strip()
    except Exception:
        return ""


def search(query: str) -> str:
    books = get_books()

    if not books:
        return "No books available in Kiwix catalog."

    if settings.ollama_url and settings.ollama_model:
        selected_books = _pick_books_with_llm(query, books, max_books=2)
    else:
        # Fallback — Wikipedia first
        fallback = next((b["name"] for b in books if "wikipedia" in b["name"]), books[0]["name"])
        selected_books = [fallback]

    if not selected_books:
        return "Could not determine which Kiwix book to search."

    # Search each selected book, collect results, deduplicate by URL
    all_results = []
    seen_urls = set()
    for book in selected_books:
        for r in _search_book(query, book, limit=3):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    if not all_results:
        return f"No results found in {', '.join(selected_books)}."

    # Score results — prefer results whose title contains query words
    query_words = set(query.lower().split())
    def _score(r):
        title_words = set(r["title"].lower().split())
        excerpt_words = set(r["excerpt"].lower().split())
        title_hits = len(query_words & title_words)
        excerpt_hits = len(query_words & excerpt_words)
        # Bonus for primary book (first in selected_books)
        primary_bonus = 2 if r["book"] == selected_books[0] else 0
        return title_hits * 3 + excerpt_hits + primary_bonus

    scored = sorted(all_results, key=_score, reverse=True)
    top = scored[0]
    _LOGGER.info(
        "Selected article '%s' from %s (score=%d)",
        top["title"], top["book"], _score(top)
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
