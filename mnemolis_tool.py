"""
Mnemolis Search Tool for Open WebUI
Connects to Mnemolis — a unified local knowledge search API for self-hosted homelabs.

Install in Open WebUI: Workspace → Tools → Create Tool
Paste this file contents into the tool editor and save.
"""

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        MNEMOLIS_URL: str = Field(
            default="http://mnemolis:8000",
            description="Base URL of the Mnemolis container. Default assumes Open WebUI and Mnemolis share a Docker network (internal container port 8000). If reaching it from outside Docker, use the host-mapped port instead, e.g. http://your-host:8888",
        )

    def __init__(self):
        self.valves = self.Valves()

    def search(
        self, query: str, source: str = "auto", fusion_sources: list[str] | None = None
    ) -> str:
        """
        Search across local knowledge sources via Mnemolis.
        Use this tool for any question that requires looking something up.

        SOURCE SELECTION (what to use when):
        - auto: Default. Let Mnemolis decide. Use this unless you know exactly what you want.
        - kiwix: Offline knowledge — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs.
                 Use for factual questions, definitions, explanations ("what is X", "how does Y work").
        - forecast: 3-day weather forecast. Use for weather questions with time references ("tomorrow", "tonight").
        - news: Recent RSS articles. Use for current events, headlines ("what's happening", "latest news").
        - web: Live web search via SearXNG. Use when you need up-to-date information beyond offline sources.
        - uptime: Service monitoring. Use for status questions ("is X up", "are my services running").
        - ha: Home Assistant entity states. Use for home state questions ("what's the house status", "are the doors locked").
        - changes: What changed recently. Use for "what changed today", "any new outages", "what happened",
                   "this morning", "while at work", "since yesterday", "in the last N hours".
        - fusion: Query multiple sources at once. Pair with fusion_sources parameter.

        :param query: The full question or search query
        :param source: The source to query. Default is "auto".
        :param fusion_sources: List of sources to fuse when source="fusion".
                               Example: ["forecast", "uptime", "ha"] for weather + services + house status.
        :return: Result text from the selected source
        """
        try:
            payload = {"query": query, "source": source}
            if fusion_sources:
                payload["fusion_sources"] = fusion_sources

            resp = requests.post(
                f"{self.valves.MNEMOLIS_URL}/search",
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", "No result returned from Mnemolis.")
        except requests.exceptions.ConnectionError:
            return "Error: Could not connect to Mnemolis. Is the container running?"
        except Exception as e:
            return f"Error: {e}"
