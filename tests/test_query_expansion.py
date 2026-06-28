"""
Tests for app/query_expansion.py — alternate query phrasing for web search.
"""
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

    def test_accepts_response_at_exactly_twice_original_length(self):
        """The length-sanity check is `len(alternate.split()) > word_count
        * 2` — strictly greater than, so a rephrasing that's EXACTLY
        double the original's word count should still be accepted, not
        rejected. The existing overly-long test above uses 50 words
        against a 4-word query, comfortably past the boundary in either
        direction — never actually exercising the literal "exactly 2x"
        edge documented in the wiki as "more than twice... is discarded"
        (implying exactly twice is not "more than" and should pass)."""
        from app.query_expansion import get_alternate_phrasing
        # 3-word query, 6-word response = exactly 2x
        response = "one two three four five six"
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value=response):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop here")
        assert result == response

    def test_rejects_response_just_over_twice_original_length(self):
        """The immediately adjacent case to the test above — one word
        past the exact-2x boundary must be rejected, confirming the
        boundary is tight rather than accidentally off by one in either
        direction."""
        from app.query_expansion import get_alternate_phrasing
        # 3-word query, 7-word response = just over 2x
        response = "one two three four five six seven"
        with patch("app.query_expansion._get_routing_fns") as mock_fns, \
             patch("app.llm.complete", return_value=response):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = get_alternate_phrasing("best laptop here")
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
