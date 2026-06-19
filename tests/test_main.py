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
