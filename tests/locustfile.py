"""
Mnemolis Load Testing — Locust
Tests realistic query patterns across all sources under concurrent load.

Run:
    locust -f tests/locustfile.py --host http://your-host:8888

Then open http://localhost:8089 and configure:
    - Users: 10
    - Spawn rate: 2
    - Run for 60 seconds

p95 targets:
    Single source, cache hit:      < 100ms
    Single source, cache miss:     < 4s
    Fusion (2 sources):            < 8s
    10 concurrent users:           < 10% error rate
"""
from locust import HttpUser, task, between
import random


KIWIX_QUERIES = [
    "what is nitrogen",
    "how does photosynthesis work",
    "explain docker networking",
    "what is molybdenum",
    "history of the Roman Empire",
    "what are capacitors",
    "how do resistors work",
    "explain the solar system",
    "what is machine learning",
    "how does wifi work",
]

# Short, single-word-after-stemming queries that trigger the disambiguation
# path (definitional + single ambiguous word + Wikipedia selected) — the
# original KIWIX_QUERIES above are all multi-word and never exercise this
KIWIX_DISAMBIGUATION_QUERIES = [
    "what is a galaxy",
    "what is mercury",
    "what are batteries",
    "what is a server",
]

WEB_QUERIES = [
    "best practices for home network security",
    "how to reset a forgotten wifi router password",
    "current mortgage interest rates explained",
    "how to make sourdough bread starter from scratch",
    "docker compose syntax",
]

AUTO_QUERIES = [
    "what is the weather this weekend",
    "are all my services up",
    "latest news headlines",
    "what is nitrogen",
    "is anything down on my network",
    "do I need an umbrella tomorrow",
]

FUSION_QUERIES = [
    ("what is the weather and are my services up", ["forecast", "uptime"]),
    ("latest news and weather forecast", ["news", "forecast"]),
    ("what is the weather and latest headlines", ["forecast", "news"]),
    ("check my services and what is the forecast", ["uptime", "forecast"]),
]

HA_QUERIES = [
    "house status summary",
    "are the doors locked",
    "battery status",
    "what lights are on in the living room",
    "indoor air quality",
    "security status",
]


class MnemolisSingleSourceUser(HttpUser):
    """Simulates single-source queries — most common usage pattern."""
    wait_time = between(1, 3)

    @task(4)
    def kiwix_search(self):
        """Kiwix encyclopedic queries — highest weight, most common."""
        self.client.post("/search", json={
            "query": random.choice(KIWIX_QUERIES),
            "source": "kiwix"
        }, name="/search [kiwix]")

    @task(2)
    def kiwix_disambiguation(self):
        """Short ambiguous queries — exercises the multi-candidate
        disambiguation + scoring path, not just plain Kiwix lookup."""
        self.client.post("/search", json={
            "query": random.choice(KIWIX_DISAMBIGUATION_QUERIES),
            "source": "kiwix"
        }, name="/search [kiwix_disambiguation]")

    @task(3)
    def auto_routing(self):
        """Auto-routed queries — tests routing intelligence."""
        self.client.post("/search", json={
            "query": random.choice(AUTO_QUERIES),
            "source": "auto"
        }, name="/search [auto]")

    @task(3)
    def web_search(self):
        """Web search queries — exercises confidence-aware scoring and,
        for 3+ word queries, multi-query expansion via SearXNG."""
        self.client.post("/search", json={
            "query": random.choice(WEB_QUERIES),
            "source": "web"
        }, name="/search [web]")

    @task(2)
    def forecast(self):
        """Weather forecast queries."""
        self.client.post("/search", json={
            "query": "what is the weather today",
            "source": "forecast"
        }, name="/search [forecast]")

    @task(2)
    def news(self):
        """RSS news queries."""
        self.client.post("/search", json={
            "query": "latest news headlines",
            "source": "news"
        }, name="/search [news]")

    @task(1)
    def uptime(self):
        """Service status queries."""
        self.client.post("/search", json={
            "query": "are all services up",
            "source": "uptime"
        }, name="/search [uptime]")

    @task(1)
    def ha_status(self):
        """Home Assistant entity queries."""
        self.client.post("/search", json={
            "query": random.choice(HA_QUERIES),
            "source": "ha"
        }, name="/search [ha]")

    @task(1)
    def cache_hit(self):
        """Repeated query — should always be a cache hit after first run."""
        self.client.post("/search", json={
            "query": "what is nitrogen",
            "source": "kiwix"
        }, name="/search [cache_hit]")

    @task(1)
    def health_check(self):
        """Health endpoint — lightweight monitoring check."""
        self.client.get("/health", name="/health")


class MnemolisFusionUser(HttpUser):
    """Simulates fusion queries — higher latency per request."""
    wait_time = between(2, 5)

    @task(3)
    def fusion_explicit(self):
        """Explicit fusion with specified sources."""
        query, sources = random.choice(FUSION_QUERIES)
        self.client.post("/search", json={
            "query": query,
            "source": "fusion",
            "fusion_sources": sources
        }, name="/search [fusion_explicit]")

    @task(2)
    def fusion_auto(self):
        """Auto fusion — LLM picks sources."""
        self.client.post("/search", json={
            "query": "what is the weather and are my services up",
            "source": "fusion"
        }, name="/search [fusion_auto]")

    @task(1)
    def fusion_triple(self):
        """Triple source fusion — highest load per request."""
        self.client.post("/search", json={
            "query": "check my services whats the weather and any news headlines",
            "source": "fusion",
            "fusion_sources": ["forecast", "uptime", "news"]
        }, name="/search [fusion_triple]")
