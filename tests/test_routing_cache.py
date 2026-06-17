"""
Tests for routing cache functions in app/router.py.
Covers _get_routing, _set_routing, clear_routing_cache, get_routing_cache_stats,
and load_routing_cache corruption handling.
No network calls required.
"""
import time
import json
import pytest


class TestRoutingCacheBasics:
    """Tests for basic routing cache get/set operations."""

    def setup_method(self):
        from app.router import clear_routing_cache, _get_routing, _set_routing
        self.get = _get_routing
        self.set = _set_routing
        self.clear = clear_routing_cache
        self.clear()

    def test_miss_returns_none(self):
        assert self.get("source:what is nitrogen") is None

    def test_hit_returns_decision(self):
        self.set("source:what is nitrogen", "kiwix")
        assert self.get("source:what is nitrogen") == "kiwix"

    def test_case_insensitive_key(self):
        self.set("source:What Is Nitrogen", "kiwix")
        assert self.get("source:what is nitrogen") == "kiwix"

    def test_strips_outer_whitespace(self):
        # _routing_cache_key strips leading/trailing whitespace from full key
        self.set("  source:nitrogen  ", "kiwix")
        assert self.get("source:nitrogen") == "kiwix"

    def test_different_queries_independent(self):
        self.set("source:nitrogen", "kiwix")
        self.set("source:weather", "forecast")
        assert self.get("source:nitrogen") == "kiwix"
        assert self.get("source:weather") == "forecast"

    def test_source_and_books_independent(self):
        self.set("source:nitrogen", "kiwix")
        self.set("books:nitrogen", "wikipedia_en_all_maxi_2026-02")
        assert self.get("source:nitrogen") == "kiwix"
        assert self.get("books:nitrogen") == "wikipedia_en_all_maxi_2026-02"

    def test_overwrite_existing(self):
        self.set("source:nitrogen", "kiwix")
        self.set("source:nitrogen", "web")
        assert self.get("source:nitrogen") == "web"

    def test_expired_returns_none(self):
        from app.router import _routing_cache, ROUTING_CACHE_TTL
        key = "source:expired query"
        _routing_cache[key] = ("kiwix", time.time() - ROUTING_CACHE_TTL - 1)
        assert self.get("source:expired query") is None

    def test_expired_entry_removed_from_cache(self):
        from app.router import _routing_cache, ROUTING_CACHE_TTL
        key = "source:expired query"
        _routing_cache[key] = ("kiwix", time.time() - ROUTING_CACHE_TTL - 1)
        self.get("source:expired query")
        assert key not in _routing_cache


class TestRoutingCacheClear:
    """Tests for clear_routing_cache."""

    def setup_method(self):
        from app.router import clear_routing_cache, _set_routing
        self.clear = clear_routing_cache
        self.set = _set_routing
        self.clear()

    def test_clear_returns_count(self):
        self.set("source:query one", "kiwix")
        self.set("source:query two", "forecast")
        count = self.clear()
        assert count == 2

    def test_clear_empties_cache(self):
        from app.router import _routing_cache
        self.set("source:query", "kiwix")
        self.clear()
        assert len(_routing_cache) == 0

    def test_clear_empty_cache_returns_zero(self):
        count = self.clear()
        assert count == 0


class TestRoutingCacheStats:
    """Tests for get_routing_cache_stats."""

    def setup_method(self):
        from app.router import clear_routing_cache, _set_routing, get_routing_cache_stats
        self.clear = clear_routing_cache
        self.set = _set_routing
        self.stats = get_routing_cache_stats
        self.clear()

    def test_empty_cache_returns_empty_list(self):
        assert self.stats() == []

    def test_stats_contains_query_and_decision(self):
        self.set("source:nitrogen", "kiwix")
        entries = self.stats()
        assert len(entries) == 1
        assert entries[0]["query"] == "source:nitrogen"
        assert entries[0]["decision"] == "kiwix"

    def test_stats_contains_age_and_expiry(self):
        self.set("source:nitrogen", "kiwix")
        entries = self.stats()
        assert "age_seconds" in entries[0]
        assert "ttl_seconds" in entries[0]
        assert "expires_in" in entries[0]

    def test_stats_ttl_is_routing_cache_ttl(self):
        from app.router import ROUTING_CACHE_TTL
        self.set("source:nitrogen", "kiwix")
        entries = self.stats()
        assert entries[0]["ttl_seconds"] == ROUTING_CACHE_TTL

    def test_multiple_entries_all_returned(self):
        self.set("source:nitrogen", "kiwix")
        self.set("source:weather", "forecast")
        self.set("books:nitrogen", "wikipedia_en_all_maxi_2026-02")
        assert len(self.stats()) == 3


class TestLoadRoutingCache:
    """Tests for load_routing_cache — corruption and edge case handling."""

    def setup_method(self):
        from app.router import clear_routing_cache
        clear_routing_cache()

    def test_loads_valid_entries(self, tmp_path):
        from app.router import load_routing_cache, _routing_cache, ROUTING_CACHE_TTL
        import app.router as router_module

        cache_file = tmp_path / "routing_cache.json"
        now = time.time()
        data = {
            "source:nitrogen": ["kiwix", now],
            "books:nitrogen": ["wikipedia_en_all_maxi_2026-02", now],
        }
        cache_file.write_text(json.dumps(data))

        original = router_module.ROUTING_CACHE_FILE
        router_module.ROUTING_CACHE_FILE = str(cache_file)
        try:
            load_routing_cache()
            assert len(_routing_cache) == 2
        finally:
            router_module.ROUTING_CACHE_FILE = original

    def test_skips_expired_entries(self, tmp_path):
        from app.router import load_routing_cache, _routing_cache, ROUTING_CACHE_TTL
        import app.router as router_module

        cache_file = tmp_path / "routing_cache.json"
        now = time.time()
        data = {
            "source:fresh": ["kiwix", now],
            "source:expired": ["forecast", now - ROUTING_CACHE_TTL - 1],
        }
        cache_file.write_text(json.dumps(data))

        original = router_module.ROUTING_CACHE_FILE
        router_module.ROUTING_CACHE_FILE = str(cache_file)
        try:
            load_routing_cache()
            assert len(_routing_cache) == 1
            assert "source:fresh" in _routing_cache
            assert "source:expired" not in _routing_cache
        finally:
            router_module.ROUTING_CACHE_FILE = original

    def test_handles_corrupted_json(self, tmp_path):
        from app.router import load_routing_cache, _routing_cache
        import app.router as router_module

        cache_file = tmp_path / "routing_cache.json"
        cache_file.write_text("this is not json at all")

        original = router_module.ROUTING_CACHE_FILE
        router_module.ROUTING_CACHE_FILE = str(cache_file)
        try:
            load_routing_cache()
            assert len(_routing_cache) == 0
        finally:
            router_module.ROUTING_CACHE_FILE = original

    def test_handles_missing_file(self, tmp_path):
        from app.router import load_routing_cache, _routing_cache
        import app.router as router_module

        original = router_module.ROUTING_CACHE_FILE
        router_module.ROUTING_CACHE_FILE = str(tmp_path / "nonexistent.json")
        try:
            load_routing_cache()
            assert len(_routing_cache) == 0
        finally:
            router_module.ROUTING_CACHE_FILE = original

    def test_handles_malformed_entries(self, tmp_path):
        from app.router import load_routing_cache, _routing_cache
        import app.router as router_module

        cache_file = tmp_path / "routing_cache.json"
        now = time.time()
        data = {
            "source:good": ["kiwix", now],
            "source:bad_no_timestamp": ["kiwix"],
            "source:bad_not_list": "kiwix",
            "source:bad_wrong_types": [123, "not_a_timestamp"],
        }
        cache_file.write_text(json.dumps(data))

        original = router_module.ROUTING_CACHE_FILE
        router_module.ROUTING_CACHE_FILE = str(cache_file)
        try:
            load_routing_cache()
            assert len(_routing_cache) == 1
            assert "source:good" in _routing_cache
        finally:
            router_module.ROUTING_CACHE_FILE = original
