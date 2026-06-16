"""
Tests for app/sources/searxng.py
Uses unittest.mock to avoid real network calls.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestSearxngGuard:
    """Tests for URL guard — short-circuits cleanly when not configured."""

    def test_returns_not_configured_when_url_blank(self):
        from app.sources import searxng
        from app.config import settings
        original = settings.searxng_url
        settings.searxng_url = ""
        try:
            result = searxng.search("test query")
            assert "not configured" in result.lower()
        finally:
            settings.searxng_url = original


class TestSearxngSearch:
    """Tests for search() with mocked HTTP responses."""

    def _mock_response(self, results: list) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"results": results}
        mock.raise_for_status.return_value = None
        return mock

    def test_returns_formatted_results(self):
        from app.sources import searxng
        mock_results = [
            {"title": "Nginx Docs", "url": "https://nginx.org", "content": "Official nginx documentation."},
            {"title": "Nginx Tutorial", "url": "https://example.com", "content": "How to configure nginx."},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("nginx")
        assert "Nginx Docs" in result
        assert "nginx.org" in result
        assert "Nginx Tutorial" in result

    def test_returns_max_five_results(self):
        from app.sources import searxng
        mock_results = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": f"Content {i}"}
            for i in range(10)
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("test")
        # Should only include first 5
        assert "Result 4" in result
        assert "Result 5" not in result

    def test_empty_results_returns_no_results_message(self):
        from app.sources import searxng
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response([])):
            result = searxng.search("xyzzy nothing")
        assert "no results" in result.lower()

    def test_connection_error_returns_error_message(self):
        from app.sources import searxng
        import requests
        with patch("app.sources.searxng.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            result = searxng.search("test")
        assert "error" in result.lower()

    def test_results_separated_by_divider(self):
        from app.sources import searxng
        mock_results = [
            {"title": "Result A", "url": "https://a.com", "content": "Content A"},
            {"title": "Result B", "url": "https://b.com", "content": "Content B"},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("test")
        assert "---" in result
