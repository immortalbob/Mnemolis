import requests
from app.config import settings


def search(query: str) -> str:
    """Search the web via local SearXNG instance."""
    try:
        resp = requests.get(
            f"{settings.searxng_url}/search",
            params={"q": query, "format": "json", "language": "en"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return "No results found via web search."
        output = []
        for r in results[:5]:
            title = r.get("title", "No title")
            url = r.get("url", "")
            content = r.get("content", "").strip()
            output.append(f"**{title}**\n{content}\n{url}")
        return "\n\n---\n\n".join(output)
    except Exception as e:
        return f"Error reaching SearXNG: {e}"
