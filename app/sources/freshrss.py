import re
import time
import logging
import requests
from app.config import settings
from app.scoring import filter_and_rank

_LOGGER = logging.getLogger(__name__)

# Words that indicate a general "give me everything" news request
# — skip filtering for these so Jarvis gets a full feed summary
_GENERAL_QUERIES = {
    "news", "headlines", "feeds",
    "what's happening", "whats happening", "my feeds", "rss",
}

# Stop words to ignore when checking for a general query
_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "about", "what", "which", "who", "whom",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "it", "they", "them", "their",
}

# Recency bonus tiers — newer articles score higher, encouraging fresher
# news to surface over older articles with similar keyword relevance
_RECENCY_TIERS = [
    (3600, 15),       # published within the last hour
    (21600, 10),      # within 6 hours
    (86400, 5),       # within 24 hours
]


def _is_general_query(query: str) -> bool:
    """Return True if the query is a general news request with no specific topic."""
    query_lower = query.lower().strip()
    # Check if the full query matches a general phrase
    if query_lower in _GENERAL_QUERIES:
        return True
    # Check if all meaningful words (after stop word removal) are general terms
    words = set(query_lower.split())
    meaningful = words - _STOP_WORDS
    return not meaningful or meaningful.issubset(_GENERAL_QUERIES)


def _recency_bonus(published_unix: int | None) -> int:
    """Return a recency bonus based on how long ago an article was published."""
    if not published_unix:
        return 0
    age_seconds = time.time() - published_unix
    if age_seconds < 0:
        return 0
    for max_age, bonus in _RECENCY_TIERS:
        if age_seconds <= max_age:
            return bonus
    return 0


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
    if not settings.freshrss_url or not settings.freshrss_user:
        return "FreshRSS is not configured. Set FRESHRSS_URL and FRESHRSS_USER."
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

        # Parse all articles, including published timestamp for recency scoring
        articles = []
        for item in items:
            title = item.get("title", "No title")
            source = item.get("origin", {}).get("title", "Unknown source")
            summary = re.sub(r"<[^>]+>", "", item.get("summary", {}).get("content", ""))[:300].strip()
            published = item.get("published")
            articles.append({
                "title": title,
                "source": source,
                "content": summary,
                "url": item.get("canonical", [{}])[0].get("href", "") if item.get("canonical") else "",
                "_recency_bonus": _recency_bonus(published),
            })

        # General query — return everything, no filtering
        if _is_general_query(query):
            _LOGGER.info("FreshRSS general query — returning all %d articles", len(articles))
            results = [f"**{a['title']}** ({a['source']})\n{a['content']}" for a in articles]
            return "\n\n---\n\n".join(results)

        # Specific query — score and filter by relevance using shared scoring,
        # which includes keyword overlap, generic-result penalty, and recency
        ranked = filter_and_rank(
            articles,
            query,
            score_threshold=settings.web_news_score_threshold,
            top_n=settings.web_news_top_n,
        )

        if ranked:
            _LOGGER.info(
                "FreshRSS filtered %d relevant articles from %d for query: %s",
                len(ranked), len(articles), query[:50]
            )
            results = [f"**{a['title']}** ({a['source']})\n{a['content']}" for a in ranked]
            return "\n\n---\n\n".join(results)

        # Nothing matched — fall back to full list with a note
        _LOGGER.info("FreshRSS no relevant articles found for query '%s', returning all", query[:50])
        results = [f"**{a['title']}** ({a['source']})\n{a['content']}" for a in articles]
        return "No articles specifically about that topic — here are the latest:\n\n" + "\n\n---\n\n".join(results)

    except Exception as e:
        _LOGGER.error("FreshRSS fetch error: %s", e)
        return f"Error fetching FreshRSS articles: {e}"
