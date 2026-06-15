import re
import requests
from app.config import settings


def _get_token() -> str | None:
    try:
        resp = requests.post(
            f"{settings.freshrss_url}/api/greader.php/accounts/ClientLogin",
            data={"Email": settings.freshrss_user, "Passwd": settings.freshrss_api_password},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        for line in resp.text.splitlines():
            if line.startswith("Auth="):
                return line[5:]
    except Exception:
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
            return f"Error: FreshRSS returned {resp.status_code}"
        items = resp.json().get("items", [])
        if not items:
            return "No recent articles found in FreshRSS."
        results = []
        for item in items:
            title = item.get("title", "No title")
            source = item.get("origin", {}).get("title", "Unknown source")
            summary = re.sub(r"<[^>]+>", "", item.get("summary", {}).get("content", ""))[:300].strip()
            results.append(f"**{title}** ({source})\n{summary}")
        return "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Error fetching FreshRSS articles: {e}"
