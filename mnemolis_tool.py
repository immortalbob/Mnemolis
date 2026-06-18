"""
Mnemolis Bridge Tool for Open WebUI
Connects to the Mnemolis container and routes queries to the appropriate source.

Install in Open WebUI: Workspace → Tools → Create Tool
Paste this file contents into the tool editor and save.
"""

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        MINISEARCH_URL: str = Field(
            default="http://mnemolis:8000",
            description="Base URL of the Mnemolis container"
        )

    def __init__(self):
        self.valves = self.Valves()

    def search(self, query: str, source: str = "auto") -> str:
        """
        Search across local and remote knowledge sources via Mnemolis.
        Use this tool for any question that requires looking something up.

        Sources:
        - auto: Mnemolis picks the best source based on the query (default)
        - kiwix: Offline knowledge base — Wikipedia, Stack Exchange, iFixit, FreeCodeCamp, DevDocs
        - forecast: 3-day weather forecast — use for future conditions, tomorrow, tonight, upcoming weather
        - news: Recent articles from RSS feeds — use for news, headlines, recent articles
        - web: Web search via SearXNG — not yet implemented

        :param query: The full question or search query
        :param source: The source to query. Default is "auto".
        :return: Result text from the selected source
        """
        try:
            resp = requests.post(
                f"{self.valves.MINISEARCH_URL}/search",
                json={"query": query, "source": source},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("result", "No result returned from Mnemolis.")
        except requests.exceptions.ConnectionError:
            return "Error: Could not connect to Mnemolis. Is the container running?"
        except Exception as e:
            return f"Error: {e}"
