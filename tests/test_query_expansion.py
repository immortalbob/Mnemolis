"""
Tests for app/query_expansion.py — alternate query phrasing for web search.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestGetAlternatePhrasing:
    """Tests for get_alternate_phrasing()."""

    def setup_method(self):
        from app.config import settings
        self._orig_url = settings.llm_url
        self._orig_model = settings.llm_model
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model

    def test_returns_none_when_llm_not_configured(self):
        from app.query_expansion import get_alternate_phrasing
        from app.config import settings
        settings.llm_url = ""
        result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_returns_none_for_short_query(self):
        from app.query_expansion import get_alternate_phrasing
        result = get_alternate_phrasing("nginx config")
        assert result is None

    def test_returns_none_for_single_word(self):
        from app.query_expansion import get_alternate_phrasing
        result = get_alternate_phrasing("nginx")
        assert result is None

    def test_uses_routing_cache_when_available(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns:
            mock_get_routing = MagicMock(return_value="top laptops for software development")
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result == "top laptops for software development"

    def test_llm_generates_alternate_phrasing(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value="top laptops for software development"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result == "top laptops for software development"
        mock_set_routing.assert_called_once()

    def test_rejects_empty_llm_response(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value=""):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_rejects_none_llm_response(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value=None):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_rejects_overly_long_response(self):
        from app.query_expansion import get_alternate_phrasing
        long_response = " ".join(["word"] * 50)
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value=long_response):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_rejects_identical_response(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value="best laptop for programming"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_rejects_identical_response_case_insensitive(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value="BEST LAPTOP FOR PROGRAMMING"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result is None

    def test_strips_quotes_from_response(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value='"top laptops for coding"'):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop for programming")
        assert result == "top laptops for coding"

    def test_exactly_three_words_is_eligible(self):
        from app.query_expansion import get_alternate_phrasing
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value="different wording entirely"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop programming")
        assert result == "different wording entirely"
