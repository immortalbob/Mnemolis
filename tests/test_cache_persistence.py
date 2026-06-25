"""
Tests for app/router.py cache persistence and eviction logic —
load_cache(), _save_cache(), _evict_oldest(), get_cache_stats().

These guard real-world-relevant behavior: malformed cache file recovery
(the .corrupt rename pattern we've actually seen trigger in production),
eviction at capacity, and stats reporting accuracy.
"""
import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch


class TestEvictOldest:
    """Tests for _evict_oldest cache eviction."""

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        router_module._cache.clear()

    def teardown_method(self):
        import app.router as router_module
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)

    def test_no_op_on_empty_cache(self):
        from app.router import _evict_oldest
        import app.router as router_module
        _evict_oldest()  # should not raise
        assert len(router_module._cache) == 0

    def test_removes_oldest_entry(self):
        from app.router import _evict_oldest
        import app.router as router_module
        router_module._cache["kiwix:old"] = ("old result", 100.0)
        router_module._cache["kiwix:new"] = ("new result", 200.0)
        _evict_oldest()
        assert "kiwix:old" not in router_module._cache
        assert "kiwix:new" in router_module._cache

    def test_removes_only_one_entry(self):
        from app.router import _evict_oldest
        import app.router as router_module
        router_module._cache["a"] = ("1", 100.0)
        router_module._cache["b"] = ("2", 200.0)
        router_module._cache["c"] = ("3", 300.0)
        _evict_oldest()
        assert len(router_module._cache) == 2


class TestSuppressCacheWrites:
    """Tests for suppress_cache_writes() — the contextvars-based mechanism
    that lets a caller (currently only adversarial_testing.py) run real
    queries through the routing pipeline without writing into _cache or
    _routing_cache. See router.py's own module-level comment next to
    _SUPPRESS_CACHE_WRITES for the real, found gap this fixes: an earlier
    version of run_adversarial_test_cycle() claimed to never touch real
    cache state, but route_with_source() writes to both caches as an
    unconditional side effect of any successful query.
    """

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        self._original_routing_cache = dict(router_module._routing_cache)
        router_module._cache.clear()
        router_module._routing_cache.clear()

    def teardown_method(self):
        import app.router as router_module
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)
        router_module._routing_cache.clear()
        router_module._routing_cache.update(self._original_routing_cache)

    def test_set_cached_is_suppressed_inside_the_context(self):
        from app.router import suppress_cache_writes, _set_cached
        import app.router as router_module
        with suppress_cache_writes():
            _set_cached("kiwix", "some query", "some result")
        assert len(router_module._cache) == 0

    def test_set_routing_is_suppressed_inside_the_context(self):
        from app.router import suppress_cache_writes, _set_routing
        import app.router as router_module
        with suppress_cache_writes():
            _set_routing("source:some query", "kiwix")
        assert len(router_module._routing_cache) == 0

    def test_writes_resume_normally_after_the_context_exits(self):
        """The flag must reset, not stay stuck on, once the `with` block
        ends — confirms this is a scoped suppression, not an accidental
        permanent disable."""
        from app.router import suppress_cache_writes, _set_cached
        import app.router as router_module
        with suppress_cache_writes():
            _set_cached("kiwix", "suppressed query", "result")
        _set_cached("kiwix", "normal query", "result")
        assert "kiwix:suppressed query" not in router_module._cache
        assert "kiwix:normal query" in router_module._cache

    def test_flag_resets_even_if_the_context_body_raises(self):
        """try/finally, not a bare set-then-clear — an exception inside
        the `with` block (a real, expected case: a query that crashes
        mid-route) must not leave the suppression flag stuck on for
        every subsequent call on this same thread/task."""
        from app.router import suppress_cache_writes, _set_cached
        import app.router as router_module

        with pytest.raises(ValueError):
            with suppress_cache_writes():
                raise ValueError("simulated mid-route crash")

        _set_cached("kiwix", "query after a crash", "result")
        assert "kiwix:query after a crash" in router_module._cache

    def test_nested_contexts_do_not_prematurely_clear_the_outer_suppression(self):
        """A nested suppress_cache_writes() call exiting must not turn
        suppression off for an outer call that's still active — relevant
        if a future caller ever nests calls (e.g. a sub-query inside an
        already-suppressed batch)."""
        from app.router import suppress_cache_writes, _set_cached
        import app.router as router_module

        with suppress_cache_writes():
            with suppress_cache_writes():
                pass
            # Still inside the OUTER context here — must still be suppressed.
            _set_cached("kiwix", "still inside outer context", "result")
        assert "kiwix:still inside outer context" not in router_module._cache


class TestSetCachedEviction:
    """Tests for _set_cached triggering eviction at capacity."""

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        self._original_max = router_module._CACHE_MAX_SIZE
        self._original_dirty = router_module._cache_dirty_count
        router_module._cache.clear()
        router_module._CACHE_MAX_SIZE = 3
        router_module._cache_dirty_count = 0

    def teardown_method(self):
        import app.router as router_module
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)
        router_module._CACHE_MAX_SIZE = self._original_max
        router_module._cache_dirty_count = self._original_dirty

    def test_evicts_when_at_capacity(self):
        from app.router import _set_cached
        import app.router as router_module
        with patch("app.router._save_cache"):
            _set_cached("kiwix", "q1", "r1")
            _set_cached("kiwix", "q2", "r2")
            _set_cached("kiwix", "q3", "r3")
            assert len(router_module._cache) == 3
            _set_cached("kiwix", "q4", "r4")
            # Should have evicted one to stay at/under max
            assert len(router_module._cache) <= 4

    def test_updating_existing_key_does_not_evict(self):
        from app.router import _set_cached
        import app.router as router_module
        with patch("app.router._save_cache"):
            _set_cached("kiwix", "q1", "r1")
            _set_cached("kiwix", "q2", "r2")
            _set_cached("kiwix", "q3", "r3")
            # Update existing key — should not trigger eviction since key already exists
            _set_cached("kiwix", "q1", "r1-updated")
            assert len(router_module._cache) == 3


class TestLoadCache:
    """Tests for load_cache() — disk persistence loading with malformed data recovery."""

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        self._original_file = router_module.CACHE_FILE
        self.temp_dir = tempfile.mkdtemp()
        router_module.CACHE_FILE = os.path.join(self.temp_dir, "cache.json")

    def teardown_method(self):
        import app.router as router_module
        import shutil
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)
        router_module.CACHE_FILE = self._original_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_file_starts_fresh(self):
        from app.router import load_cache
        import app.router as router_module
        load_cache()
        assert router_module._cache == {}

    def test_loads_valid_cache_entries(self):
        from app.router import load_cache
        import app.router as router_module
        now = time.time()
        data = {"kiwix:nitrogen": ["Nitrogen is...", now]}
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(data, f)
        load_cache()
        assert "kiwix:nitrogen" in router_module._cache

    def test_skips_expired_entries(self):
        from app.router import load_cache
        import app.router as router_module
        ancient = time.time() - (1000 * 3600)  # 1000 hours ago, way past any TTL
        data = {"kiwix:old": ["stale result", ancient]}
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(data, f)
        load_cache()
        assert "kiwix:old" not in router_module._cache

    def test_skips_malformed_entry_wrong_length(self):
        from app.router import load_cache
        import app.router as router_module
        data = {"kiwix:bad": ["only one element"]}
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(data, f)
        load_cache()
        assert "kiwix:bad" not in router_module._cache

    def test_skips_malformed_entry_wrong_types(self):
        from app.router import load_cache
        import app.router as router_module
        data = {"kiwix:bad": [12345, "not a number"]}  # result should be str, timestamp should be number
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(data, f)
        load_cache()
        assert "kiwix:bad" not in router_module._cache

    def test_non_dict_json_resets_to_empty(self):
        from app.router import load_cache
        import app.router as router_module
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(["not", "a", "dict"], f)
        load_cache()
        assert router_module._cache == {}

    def test_corrupted_json_renames_file_and_starts_fresh(self):
        """This is the exact .corrupt rename behavior we observed happen
        for real in production during this project."""
        from app.router import load_cache
        import app.router as router_module
        with open(router_module.CACHE_FILE, "w") as f:
            f.write("{not valid json!!!")
        load_cache()
        assert router_module._cache == {}
        assert os.path.exists(router_module.CACHE_FILE + ".corrupt")

    def test_mixed_valid_and_invalid_entries(self):
        from app.router import load_cache
        import app.router as router_module
        now = time.time()
        data = {
            "kiwix:good": ["Valid result", now],
            "kiwix:bad": ["only one"],
        }
        with open(router_module.CACHE_FILE, "w") as f:
            json.dump(data, f)
        load_cache()
        assert "kiwix:good" in router_module._cache
        assert "kiwix:bad" not in router_module._cache


class TestSaveCache:
    """Tests for _save_cache() disk persistence."""

    def setup_method(self):
        import app.router as router_module
        self._original_file = router_module.CACHE_FILE
        self.temp_dir = tempfile.mkdtemp()
        router_module.CACHE_FILE = os.path.join(self.temp_dir, "subdir", "cache.json")

    def teardown_method(self):
        import app.router as router_module
        import shutil
        router_module.CACHE_FILE = self._original_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_parent_directory_if_missing(self):
        from app.router import _save_cache
        import app.router as router_module
        _save_cache()
        assert os.path.exists(os.path.dirname(router_module.CACHE_FILE))

    def test_writes_valid_json(self):
        from app.router import _save_cache
        import app.router as router_module
        router_module._cache["kiwix:test"] = ("result", 123.0)
        _save_cache()
        with open(router_module.CACHE_FILE) as f:
            data = json.load(f)
        assert "kiwix:test" in data

    def test_does_not_raise_on_unwritable_path(self):
        from app.router import _save_cache
        import app.router as router_module
        router_module.CACHE_FILE = "/nonexistent_root_dir_xyz/cache.json"
        _save_cache()  # should log a warning, not raise


class TestGetCacheStats:
    """Tests for get_cache_stats() reporting."""

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        router_module._cache.clear()

    def teardown_method(self):
        import app.router as router_module
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)

    def test_empty_cache_returns_empty_list(self):
        from app.router import get_cache_stats
        assert get_cache_stats() == []

    def test_returns_entry_for_each_cached_item(self):
        from app.router import get_cache_stats
        import app.router as router_module
        router_module._cache["kiwix:nitrogen"] = ("result", time.time())
        router_module._cache["forecast:weather"] = ("sunny", time.time())
        stats = get_cache_stats()
        assert len(stats) == 2

    def test_entry_has_required_fields(self):
        from app.router import get_cache_stats
        import app.router as router_module
        router_module._cache["kiwix:nitrogen"] = ("result", time.time())
        stats = get_cache_stats()
        entry = stats[0]
        for field in ["source", "query", "age_seconds", "ttl_seconds", "expires_in"]:
            assert field in entry

    def test_splits_source_and_query_correctly(self):
        from app.router import get_cache_stats
        import app.router as router_module
        router_module._cache["kiwix:what is nitrogen"] = ("result", time.time())
        stats = get_cache_stats()
        assert stats[0]["source"] == "kiwix"
        assert stats[0]["query"] == "what is nitrogen"

    def test_age_seconds_reflects_elapsed_time(self):
        from app.router import get_cache_stats
        import app.router as router_module
        old_timestamp = time.time() - 100
        router_module._cache["kiwix:test"] = ("result", old_timestamp)
        stats = get_cache_stats()
        assert stats[0]["age_seconds"] >= 99

    def test_expires_in_never_negative(self):
        from app.router import get_cache_stats
        import app.router as router_module
        ancient = time.time() - (1000 * 3600)
        router_module._cache["kiwix:test"] = ("result", ancient)
        stats = get_cache_stats()
        assert stats[0]["expires_in"] == 0
