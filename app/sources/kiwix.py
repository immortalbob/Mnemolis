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

def _pick_book_with_llm(query: str, books: list[dict]) -> str:
    """Ask Ollama to pick the best book for the query. Returns book name."""
    if not books:
        return ""

    book_list = "\n".join(
        f"- {b['name']}: {b['title']} — {b['summary'][:100]}"
        for b in books
    )

    prompt = (
        f"You are a search router. Given a user query and a list of available Kiwix offline "
        f"knowledge bases, return ONLY the exact book name (the full identifier) that best "
        f"matches the query. No explanation, no punctuation, just the book name.\n\n"
        f"Query: {query}\n\n"
        f"Available books:\n{book_list}\n\n"
        f"Best book name:"
    )

    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 100},
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
        chosen = raw.strip(".").strip()

        # Validate against known books
        book_names = {b["name"] for b in books}
        if chosen in book_names:
            _LOGGER.info("LLM selected book: %s", chosen)
            return chosen

        # Fuzzy match — LLM might return partial name
        for name in book_names:
            if chosen in name or name in chosen:
                _LOGGER.info("LLM fuzzy matched: %s -> %s", chosen, name)
                return name

        _LOGGER.warning("LLM returned unknown book '%s', falling back", chosen)

    except Exception as e:
        _LOGGER.warning("LLM book selection failed: %s", e)

    # Fallback — Wikipedia first
    book_names_list = [b["name"] for b in books]
    for name in book_names_list:
        if "wikipedia" in name:
            return name
    return book_names_list[0] if book_names_list else ""


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
        book = _pick_book_with_llm(query, books)
    else:
        # Fallback — Wikipedia if available
        book = next((b["name"] for b in books if "wikipedia" in b["name"]), books[0]["name"])

    if not book:
        return "Could not determine which Kiwix book to search."

    results = _search_book(query, book)
    if not results:
        return f"No results found in {book}."

    top = results[0]
    article_text = _fetch_article(top["url"])
    if not article_text:
        return f"Found {top['title']} but could not fetch article content.\nURL: {top['url']}"

    return f"# {top['title']}\nSource: {top['book']}\n\n{article_text}"
