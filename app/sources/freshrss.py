import re
import logging
import requests
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Words that indicate a general "give me everything" news request
# — skip filtering for these so Jarvis gets a full feed summary
_GENERAL_QUERIES = {
    "news", "headlines", "articles", "feeds", "latest", "recent",
    "what's happening", "whats happening", "my feeds", "rss",
}

# Stop words to ignore when scoring article relevance
_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "about", "what", "which", "who", "whom",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "it", "they", "them", "their",
}


def _is_general_query(query: str) -> bool:
    """Return True if the query is a general news request with no specific topic."""
    words = set(query.lower().split())
    meaningful = words - _STOP_WORDS
    return not meaningful or meaningful.issubset(_GENERAL_QUERIES)


def _score_article(title: str, summary: str, query_words: set) -> int:
    """Score an article by keyword overlap with the query."""
    title_words = set(title.lower().split()) - _STOP_WORDS
    summary_words = set(summary.lower().split()) - _STOP_WORDS
    title_hits = len(query_words & title_words)
    summary_hits = len(query_words & summary_words)
    return title_hits * 3 + summary_hits


def _get_token() -> str | None:
    try:
        resp = requests.post(
            f"{settings.freshrss_url}/api/greader.php/accounts/ClientLogin",
            data={"Email": settings.freshrss_user, "Passwd": settings.freshrss_api_password},
            timeout=5,
        )
        if resp.status_code != 200:
            _LOGGER.warning("FreshRSS auth failed: HTTP %d", resp.status_code)
            return None
        for line in resp.text.splitlines():
            if line.startswith("Auth="):
                return line[5:]
        _LOGGER.warning("FreshRSS auth response missing Auth= token")
        return None
    except Exception as e:
        _LOGGER.warning("FreshRSS auth request failed: %s", e)
        return None


def search(query: str) -> str:
    token = _get_token()
    if not token:
        return "Error: Could not authenticate with FreshRSS. Check credentials."
    try:
        resp = requests.get(
            f"{settings.freshrss_url}/api/greader.php/reader/api/0/stream/contents/reading-list",
            headers={"Authorization": f"GoogleLogin auth={token}"},
            params={"n": settings.freshrss_max_articles, "output": "json"},
            timeout=10,
        )
        if resp.status_code != 200:
            _LOGGER.warning("FreshRSS articles request failed: HTTP %d", resp.status_code)
            return f"Error: FreshRSS returned {resp.status_code}"

        items = resp.json().get("items", [])
        if not items:
            return "No recent articles found in FreshRSS."

        # Parse all articles
        articles = []
        for item in items:
            title = item.get("title", "No title")
            source = item.get("origin", {}).get("title", "Unknown source")
            summary = re.sub(r"<[^>]+>", "", item.get("summary", {}).get("content", ""))[:300].strip()
            articles.append((title, source, summary))

        # General query — return everything, no filtering
        if _is_general_query(query):
            _LOGGER.info("FreshRSS general query — returning all %d articles", len(articles))
            results = [f"**{t}** ({s})\n{x}" for t, s, x in articles]
            return "\n\n---\n\n".join(results)

        # Specific query — score and filter by relevance
        query_words = set(query.lower().split()) - _STOP_WORDS
        scored = []
        for title, source, summary in articles:
            score = _score_article(title, summary, query_words)
            if score > 0:
                scored.append((score, title, source, summary))

        scored.sort(key=lambda x: x[0], reverse=True)

        if scored:
            _LOGGER.info(
                "FreshRSS filtered %d relevant articles from %d for query: %s",
                len(scored), len(articles), query[:50]
            )
            results = [f"**{t}** ({s})\n{x}" for _, t, s, x in scored]
            return "\n\n---\n\n".join(results)

        # Nothing matched — fall back to full list with a note
        _LOGGER.info("FreshRSS no relevant articles found for query '%s', returning all", query[:50])
        results = [f"**{t}** ({s})\n{x}" for t, s, x in articles]
        return "No articles specifically about that topic — here are the latest:\n\n" + "\n\n---\n\n".join(results)

    except Exception as e:
        _LOGGER.error("FreshRSS fetch error: %s", e)
        return f"Error fetching FreshRSS articles: {e}"
