import re
import requests
from bs4 import BeautifulSoup
from app.config import settings

BOOKS = [
    "wikipedia_en_all_maxi_2026-02",
    "wiktionary_en_all_nopic_2025-09",
    "ai.stackexchange.com_en_all_2026-02",
    "arduino.stackexchange.com_en_all_2026-02",
    "cs.stackexchange.com_en_all_2026-02",
    "datascience.stackexchange.com_en_all_2026-02",
    "dba.stackexchange.com_en_all_2026-02",
    "devops.stackexchange.com_en_all_2026-02",
    "electronics.stackexchange.com_en_all_2026-02",
    "iot.stackexchange.com_en_all_2026-02",
    "math.stackexchange.com_en_all_2026-02",
    "raspberrypi.stackexchange.com_en_all_2026-02",
    "retrocomputing.stackexchange.com_en_all_2026-02",
    "unix.stackexchange.com_en_all_2026-02",
    "devdocs_en_nginx_2026-04",
    "devdocs_en_python_2026-05",
    "freecodecamp_en_all_2026-05",
    "ifixit_en_all_2025-12",
]

KEYWORD_MAP = {
    "raspberry pi": ["raspberrypi.stackexchange.com_en_all_2026-02", "ifixit_en_all_2025-12"],
    "esp32": ["iot.stackexchange.com_en_all_2026-02", "electronics.stackexchange.com_en_all_2026-02"],
    "gpio": ["raspberrypi.stackexchange.com_en_all_2026-02", "electronics.stackexchange.com_en_all_2026-02"],
    "arduino": ["arduino.stackexchange.com_en_all_2026-02", "electronics.stackexchange.com_en_all_2026-02"],
    "repair": ["ifixit_en_all_2025-12"],
    "teardown": ["ifixit_en_all_2025-12"],
    "docker": ["devops.stackexchange.com_en_all_2026-02", "unix.stackexchange.com_en_all_2026-02"],
    "nginx": ["devdocs_en_nginx_2026-04", "unix.stackexchange.com_en_all_2026-02"],
    "bash": ["unix.stackexchange.com_en_all_2026-02"],
    "unix": ["unix.stackexchange.com_en_all_2026-02"],
    "linux": ["unix.stackexchange.com_en_all_2026-02"],
    "devops": ["devops.stackexchange.com_en_all_2026-02"],
    "python": ["devdocs_en_python_2026-05", "freecodecamp_en_all_2026-05", "cs.stackexchange.com_en_all_2026-02"],
    "coding": ["freecodecamp_en_all_2026-05", "cs.stackexchange.com_en_all_2026-02"],
    "algorithm": ["cs.stackexchange.com_en_all_2026-02", "datascience.stackexchange.com_en_all_2026-02"],
    "programming": ["cs.stackexchange.com_en_all_2026-02", "freecodecamp_en_all_2026-05"],
    "retro": ["retrocomputing.stackexchange.com_en_all_2026-02"],
    "vintage": ["retrocomputing.stackexchange.com_en_all_2026-02"],
    "machine learning": ["ai.stackexchange.com_en_all_2026-02"],
    "artificial intelligence": ["ai.stackexchange.com_en_all_2026-02"],
    "data science": ["datascience.stackexchange.com_en_all_2026-02"],
    "math": ["math.stackexchange.com_en_all_2026-02"],
    "mathematics": ["math.stackexchange.com_en_all_2026-02"],
    "wiki": ["wikipedia_en_all_maxi_2026-02"],
    "wikipedia": ["wikipedia_en_all_maxi_2026-02"],
    "definition": ["wiktionary_en_all_nopic_2025-09"],
    "word": ["wiktionary_en_all_nopic_2025-09"],
}


def _get_relevant_books(query: str) -> list:
    query_lower = query.lower()
    book_scores = {}
    matched = set()
    for keyword, books in KEYWORD_MAP.items():
        if keyword in query_lower:
            for book in books:
                book_scores[book] = book_scores.get(book, 0) + 1
                matched.add(book)
    prioritized = [b for b, _ in sorted(book_scores.items(), key=lambda x: x[1], reverse=True)]
    prioritized += [b for b in BOOKS if b not in matched]
    return prioritized


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
    book = _get_relevant_books(query)[0]
    results = _search_book(query, book)
    if not results:
        return "No results found in Kiwix knowledge base."
    top = results[0]
    article_text = _fetch_article(top["url"])
    if not article_text:
        return f"Found {top['title']} but could not fetch article content. URL: {top['url']}"
    return f"# {top['title']}\nSource: {top['book']}\n\n{article_text}"
