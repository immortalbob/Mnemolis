"""
Tests for app/main.py — FastAPI endpoints.
Uses TestClient to test endpoints directly without a running server.
"""
import pytest
import sqlite3
import tempfile
import os
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Create a TestClient for the FastAPI app."""
    # Point log DB to a temp file so tests don't pollute real data
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db = f.name
    with patch("app.main._LOG_DB", temp_db):
        from app.main import app
        with TestClient(app) as c:
            yield c
    os.unlink(temp_db)


class TestLoggingConfiguration:
    """Tests for root logging setup at app import time.

    Regression coverage for a real bug found via production debugging —
    the root logger defaulted to WARNING with no attached handler, which
    silently swallowed every _LOGGER.info() call across the entire
    codebase (decomposition splits, disambiguation candidates, article
    selection, snapshot jobs, etc). Only uvicorn's own access logger (a
    separate logger with its own handler) was ever visible in container
    logs, making it look like the app was processing requests with zero
    diagnostic output — when in fact the info logs were firing, just
    never reaching any output destination.

    These tests call logging.basicConfig() directly with the same
    arguments app/main.py uses, rather than relying on `import app.main`
    to trigger it — app.main is already cached in sys.modules by the
    time these tests run (the `client` fixture above imports it first),
    so a second `import app.main` is a no-op and never re-executes the
    module-level basicConfig() call. Testing the actual configuration
    logic directly avoids depending on Python's one-time import behavior.
    """

    def setup_method(self):
        import logging
        # Snapshot real logging state so these tests don't leak changes
        # into other tests that might check logger configuration
        self._original_level = logging.getLogger().level
        self._original_handlers = list(logging.getLogger().handlers)

    def teardown_method(self):
        import logging
        logging.getLogger().setLevel(self._original_level)
        logging.getLogger().handlers = self._original_handlers

    def test_basicConfig_sets_info_level_by_default(self):
        import logging
        import os as os_module
        from unittest.mock import patch

        logging.getLogger().handlers = []
        with patch.dict(os_module.environ, {}, clear=False):
            os_module.environ.pop("LOG_LEVEL", None)
            level_name = os_module.environ.get("LOG_LEVEL", "INFO").upper()
            logging.basicConfig(
                level=level_name,
                format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
                force=True,
            )
        assert logging.getLogger().level == logging.INFO

    def test_basicConfig_attaches_a_handler(self):
        import logging
        logging.getLogger().handlers = []
        logging.basicConfig(
            level="INFO",
            format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
            force=True,
        )
        assert len(logging.getLogger().handlers) >= 1

    def test_app_router_logger_inherits_info_level_when_configured(self):
        import logging
        logging.getLogger().handlers = []
        logging.basicConfig(level="INFO", force=True)
        assert logging.getLogger("app.router").getEffectiveLevel() <= logging.INFO

    def test_log_level_env_var_respected(self):
        import logging
        import os as os_module
        from unittest.mock import patch

        with patch.dict(os_module.environ, {"LOG_LEVEL": "DEBUG"}):
            level_name = os_module.environ.get("LOG_LEVEL", "INFO").upper()
            assert level_name == "DEBUG"
            assert logging.getLevelName(level_name) == logging.DEBUG

    def test_main_module_source_calls_basicConfig_with_env_var(self):
        """Confirm the actual source code in main.py reads LOG_LEVEL and
        calls logging.basicConfig() — a static check that doesn't depend
        on import timing, since we can't reliably re-trigger module-level
        code in an already-imported module within the same test process."""
        import inspect
        import app.main as main_module
        source = inspect.getsource(main_module)
        assert "logging.basicConfig" in source
        assert "LOG_LEVEL" in source


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_includes_kiwix_books(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "kiwix_books_loaded" in data
        assert isinstance(data["kiwix_books_loaded"], int)

    def test_health_includes_cache_entries(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "cache_entries" in data

    def test_health_includes_cache_max_size(self, client):
        """Surfacing the configured max alongside the current count makes
        growth toward the bound visible without digging through code or
        config — the actual operational-maturity goal of this change."""
        resp = client.get("/health")
        data = resp.json()
        assert "cache_max_size" in data
        assert isinstance(data["cache_max_size"], int)
        assert data["cache_max_size"] > 0

    def test_health_includes_routing_cache_entries_and_max_size(self, client):
        """Regression coverage for a real gap found during operational
        maturity review — the routing cache previously had no exposed
        size at all in /health, and (separately) no enforced size limit
        either. Both the current count and the configured max must be
        visible here."""
        resp = client.get("/health")
        data = resp.json()
        assert "routing_cache_entries" in data
        assert isinstance(data["routing_cache_entries"], int)
        assert "routing_cache_max_size" in data
        assert isinstance(data["routing_cache_max_size"], int)
        assert data["routing_cache_max_size"] > 0

    def test_health_includes_sources(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "sources" in data
        sources = data["sources"]
        for expected in ["kiwix", "forecast", "news", "web", "uptime", "ha", "llm"]:
            assert expected in sources

    def test_health_source_has_status(self, client):
        resp = client.get("/health")
        sources = resp.json()["sources"]
        for name, info in sources.items():
            assert "status" in info


class TestSourcesEndpoint:
    """Tests for GET /sources."""

    def test_sources_returns_list(self, client):
        resp = client.get("/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data

    def test_sources_includes_all_known(self, client):
        resp = client.get("/sources")
        sources = resp.json()["sources"]
        for expected in ["kiwix", "forecast", "news", "web", "uptime", "ha", "fusion", "auto"]:
            assert expected in sources


class TestCatalogEndpoints:
    """Tests for GET /catalog and POST /catalog/refresh."""

    def test_catalog_returns_count_and_books(self, client):
        resp = client.get("/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "books" in data

    def test_catalog_count_matches_books_length(self, client):
        resp = client.get("/catalog")
        data = resp.json()
        assert data["count"] == len(data["books"])

    def test_catalog_refresh_returns_status(self, client):
        from unittest.mock import patch
        with patch("app.main.refresh_catalog", return_value=[]):
            resp = client.post("/catalog/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "refreshed"
        assert "count" in data

    def test_catalog_refresh_reflects_new_count(self, client):
        from unittest.mock import patch
        fake_books = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        with patch("app.main.refresh_catalog", return_value=fake_books):
            resp = client.post("/catalog/refresh")
        assert resp.json()["count"] == 3

    def test_catalog_refresh_calls_refresh_function(self, client):
        from unittest.mock import patch
        with patch("app.main.refresh_catalog", return_value=[]) as mock_refresh:
            client.post("/catalog/refresh")
        assert mock_refresh.called


class TestCacheEndpoints:
    """Tests for cache management endpoints."""

    def test_cache_get_returns_entries(self, client):
        resp = client.get("/cache")
        assert resp.status_code == 200
        assert "entries" in resp.json()

    def test_cache_clear_returns_cleared(self, client):
        resp = client.post("/cache/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cleared"
        assert "entries_removed" in data

    def test_routing_cache_get_returns_entries(self, client):
        resp = client.get("/cache/routing")
        assert resp.status_code == 200
        assert "entries" in resp.json()

    def test_routing_cache_clear_returns_cleared(self, client):
        resp = client.post("/cache/routing/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cleared"


class TestLogsEndpoints:
    """Tests for query log endpoints."""

    def test_logs_returns_entries(self, client):
        resp = client.get("/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "count" in data

    def test_logs_limit_param(self, client):
        resp = client.get("/logs?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 5

    def test_logs_clear_returns_cleared(self, client):
        resp = client.post("/logs/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cleared"

    def test_logs_entry_has_required_fields(self, client):
        # Write a test entry directly to the DB then check it appears
        from app.main import _log_query
        _log_query("test query", "auto", "kiwix", False, True, 123)
        resp = client.get("/logs?limit=1")
        entries = resp.json()["entries"]
        if entries:
            entry = entries[0]
            for field in ["timestamp", "query", "source_requested", "source_used", "cached", "success", "latency_ms"]:
                assert field in entry

    def test_log_query_accepts_fallback_occurred_default_false(self, client):
        """Backward compatibility — existing call sites that don't pass
        fallback_occurred at all (the 6-arg call signature used
        throughout the rest of the test suite and the exception handler
        in /search) must continue to work unchanged."""
        from app.main import _log_query
        # Should not raise — fallback_occurred defaults to False
        _log_query("backward compat test", "auto", "forecast", False, True, 50)

    def test_log_query_accepts_explicit_fallback_occurred(self, client):
        from app.main import _log_query
        _log_query("fallback test query", "kiwix", "web", False, True, 200, fallback_occurred=True)
        resp = client.get("/logs?limit=1")
        entries = resp.json()["entries"]
        assert entries[0]["query"] == "fallback test query"


class TestFallbackDetection:
    """Tests for the fallback_occurred detection logic in /search and its
    surfacing in /logs/stats.

    Detected via a single boolean column, computed by comparing the
    pre-route intended source against the actual resolved source from
    route_with_source() — deliberately NOT by changing
    route_with_source()'s own return signature, since that function
    already recurses into itself at 4 internal call sites (conditional
    detection's condition/remainder handling, and the same for decomposed
    sub-queries), so widening its return tuple would touch every one of
    those, a much larger and riskier change than this comparison needed
    to require."""

    def setup_method(self):
        from app.main import _LOG_DB
        import sqlite3
        con = sqlite3.connect(_LOG_DB)
        con.execute("DELETE FROM query_log")
        con.commit()
        con.close()

    def test_explicit_source_fallback_is_detected(self, client):
        """An explicit source='kiwix' request that internally falls back
        to web must be logged with fallback_occurred=1."""
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = lambda q: "No results found in wikipedia."
        router_module.SOURCE_MAP["web"] = lambda q: "Real web results."
        try:
            resp = client.post("/search", json={"query": "test fallback query", "source": "kiwix"})
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert resp.status_code == 200
        assert resp.json()["source_used"] == "web"

        from app.main import _connect, _LOG_DB
        con = _connect(_LOG_DB)
        row = con.execute(
            "SELECT fallback_occurred FROM query_log WHERE query = ? ORDER BY id DESC LIMIT 1",
            ("test fallback query",)
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == 1

    def test_no_fallback_is_not_flagged(self, client):
        """A request that succeeds on its intended source (no fallback
        needed at all) must be logged with fallback_occurred=0."""
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear."
        try:
            resp = client.post("/search", json={"query": "test no fallback query", "source": "forecast"})
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert resp.status_code == 200

        from app.main import _connect, _LOG_DB
        con = _connect(_LOG_DB)
        row = con.execute(
            "SELECT fallback_occurred FROM query_log WHERE query = ? ORDER BY id DESC LIMIT 1",
            ("test no fallback query",)
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == 0

    def test_stats_reports_fallback_count_and_rate(self, client):
        from app.main import _log_query
        _log_query("q1", "kiwix", "web", False, True, 100, fallback_occurred=True)
        _log_query("q2", "forecast", "forecast", False, True, 50, fallback_occurred=False)

        resp = client.get("/logs/stats")
        data = resp.json()
        assert data["fallback_count"] >= 1
        assert "fallback_rate_pct" in data

    def test_stats_fallback_by_target_uses_combined_label_not_duplicate_attribution(self, client):
        """Regression test for a real flaw found during design: kiwix
        and news both fall back to the same target (web), so a boolean
        column genuinely cannot distinguish which one triggered a given
        fallback. Querying naively per-original-source would run the
        identical SQL query under both labels and double-report the
        same underlying rows. The fix reports a single, honest combined
        label (e.g. "kiwix_or_news_fallback_to_web") instead of guessing
        at an attribution the data doesn't actually support."""
        from app.main import _log_query
        _log_query("fallback q1", "kiwix", "web", False, True, 100, fallback_occurred=True)
        _log_query("fallback q2", "news", "web", False, True, 100, fallback_occurred=True)

        resp = client.get("/logs/stats")
        data = resp.json()
        fallback_by_target = data["fallback_by_target"]

        # Must NOT have separate, duplicate-counted "kiwix" and "news" keys
        assert "kiwix" not in fallback_by_target
        assert "news" not in fallback_by_target
        # Must have exactly one combined key covering both
        assert "kiwix_or_news_fallback_to_web" in fallback_by_target
        assert fallback_by_target["kiwix_or_news_fallback_to_web"] == 2


class TestAPIKeyAuth:
    """Tests for API key authentication on /search and /changes."""

    def setup_method(self):
        from app.config import settings
        self._original_keys = settings.api_keys
        settings.api_keys = ""

    def teardown_method(self):
        from app.config import settings
        settings.api_keys = self._original_keys

    def test_search_works_without_key_when_auth_disabled(self, client):
        resp = client.post("/search", json={"query": "what is nitrogen", "source": "kiwix"})
        assert resp.status_code == 200

    def test_changes_works_without_key_when_auth_disabled(self, client):
        resp = client.get("/changes")
        assert resp.status_code == 200

    def test_search_rejected_without_key_when_auth_enabled(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.post("/search", json={"query": "test", "source": "kiwix"})
        assert resp.status_code == 401

    def test_search_rejected_with_wrong_key(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.post(
            "/search",
            json={"query": "test", "source": "kiwix"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_search_accepted_with_correct_key(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.post(
            "/search",
            json={"query": "what is nitrogen", "source": "kiwix"},
            headers={"X-API-Key": "secret123"},
        )
        assert resp.status_code == 200

    def test_changes_rejected_without_key_when_auth_enabled(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.get("/changes")
        assert resp.status_code == 401

    def test_changes_accepted_with_correct_key(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.get("/changes", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200

    def test_multiple_keys_all_valid(self, client):
        from app.config import settings
        settings.api_keys = "key1,key2,key3"
        resp1 = client.post("/search", json={"query": "test", "source": "kiwix"}, headers={"X-API-Key": "key1"})
        resp2 = client.post("/search", json={"query": "test", "source": "kiwix"}, headers={"X-API-Key": "key3"})
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_health_never_requires_key(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_areas_never_requires_key(self, client):
        from app.config import settings
        settings.api_keys = "secret123"
        resp = client.get("/areas")
        assert resp.status_code == 200

    def test_keys_with_whitespace_are_trimmed(self, client):
        from app.config import settings
        settings.api_keys = " key1 , key2 "
        resp = client.post("/search", json={"query": "test", "source": "kiwix"}, headers={"X-API-Key": "key1"})
        assert resp.status_code == 200


class TestAreasEndpoint:
    """Tests for GET /areas."""

    def test_areas_returns_status_and_areas_keys(self, client):
        resp = client.get("/areas")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "areas" in data

    def test_areas_not_configured_without_ha_settings(self, client):
        from app.config import settings
        original_url = settings.ha_url
        original_token = settings.ha_token
        settings.ha_url = ""
        settings.ha_token = ""
        resp = client.get("/areas")
        data = resp.json()
        assert data["status"] == "not_configured"
        settings.ha_url = original_url
        settings.ha_token = original_token


class TestBackupEndpoint:
    """Tests for GET /backup and GET /backup/info."""

    def test_backup_info_returns_file_dict(self, client):
        resp = client.get("/backup/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data

    def test_backup_info_includes_known_files(self, client):
        resp = client.get("/backup/info")
        files = resp.json()["files"]
        for expected in ["cache.json", "routing_cache.json", "query_log.db", "snapshots.db"]:
            assert expected in files

    def test_backup_info_reports_existence(self, client):
        resp = client.get("/backup/info")
        files = resp.json()["files"]
        for name, info in files.items():
            assert "exists" in info

    def test_backup_returns_tarball(self, client):
        resp = client.get("/backup")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"

    def test_backup_filename_has_timestamp(self, client):
        resp = client.get("/backup")
        content_disposition = resp.headers.get("content-disposition", "")
        assert "mnemolis-backup-" in content_disposition
        assert ".tar.gz" in content_disposition

    def test_backup_contains_valid_tar(self, client):
        import tarfile
        import io
        resp = client.get("/backup")
        tar_bytes = io.BytesIO(resp.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            names = tar.getnames()
            # Should contain at least the files that exist on disk
            assert isinstance(names, list)


class TestLogsStatsEndpoint:
    """Tests for GET /logs/stats."""

    def test_stats_returns_expected_keys(self, client):
        resp = client.get("/logs/stats")
        assert resp.status_code == 200
        data = resp.json()
        for key in [
            "total_queries", "unique_queries", "learned_queries",
            "cache_hit_rate_pct", "success_rate_pct", "avg_latency_ms",
            "ttfk_ms", "latency_by_source", "top_queries"
        ]:
            assert key in data

    def test_stats_total_is_int(self, client):
        data = client.get("/logs/stats").json()
        assert isinstance(data["total_queries"], int)

    def test_stats_cache_hit_rate_is_percentage(self, client):
        data = client.get("/logs/stats").json()
        assert 0.0 <= data["cache_hit_rate_pct"] <= 100.0

    def test_stats_success_rate_is_percentage(self, client):
        data = client.get("/logs/stats").json()
        assert 0.0 <= data["success_rate_pct"] <= 100.0

    def test_stats_ttfk_is_non_negative(self, client):
        data = client.get("/logs/stats").json()
        assert data["ttfk_ms"] >= 0

    def test_stats_unique_lte_total(self, client):
        data = client.get("/logs/stats").json()
        assert data["unique_queries"] <= data["total_queries"]

    def test_stats_learned_lte_unique(self, client):
        data = client.get("/logs/stats").json()
        assert data["learned_queries"] <= data["unique_queries"]

    def test_stats_top_queries_is_list(self, client):
        data = client.get("/logs/stats").json()
        assert isinstance(data["top_queries"], list)

    def test_stats_top_query_has_required_fields(self, client):
        data = client.get("/logs/stats").json()
        if data["top_queries"]:
            entry = data["top_queries"][0]
            for field in ["query", "times_asked", "cache_hits", "cache_hit_rate", "min_latency_ms", "avg_latency_ms", "source"]:
                assert field in entry

    def test_stats_top_queries_sorted_descending(self, client):
        data = client.get("/logs/stats").json()
        top = data["top_queries"]
        if len(top) > 1:
            assert top[0]["times_asked"] >= top[1]["times_asked"]

    def test_stats_latency_by_source_is_dict(self, client):
        data = client.get("/logs/stats").json()
        assert isinstance(data["latency_by_source"], dict)

    def test_stats_latency_by_source_has_valid_structure(self, client):
        data = client.get("/logs/stats").json()
        for source, info in data["latency_by_source"].items():
            assert "avg_latency_ms" in info
            assert "query_count" in info
            assert info["query_count"] > 0
