"""
Tests for app/sources/searxng.py
Uses unittest.mock to avoid real network calls.
"""
from unittest.mock import patch, MagicMock


class TestSearxngQueryExpansion:
    """Integration tests for multi-query expansion in search()."""

    def setup_method(self):
        from app.config import settings
        self._orig_url = settings.llm_url
        self._orig_model = settings.llm_model
        self._orig_threshold = settings.web_news_score_threshold
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"
        settings.web_news_score_threshold = -100

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model
        settings.web_news_score_threshold = self._orig_threshold

    def _mock_response(self, results: list) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"results": results}
        mock.raise_for_status.return_value = None
        return mock

    def test_searches_both_original_and_alternate_query(self):
        from app.sources import searxng
        from unittest.mock import patch

        queried_params = []

        def fake_get(url, params=None, **kwargs):
            queried_params.append(params["q"])
            return self._mock_response([
                {"title": f"Result for {params['q']}", "url": f"https://example.com/{params['q']}", "content": "python programming content here"}
            ])

        with patch("app.sources.searxng.requests.get", side_effect=fake_get), \
             patch("app.sources.searxng.get_alternate_phrasing", return_value="top python coding tips"):
            searxng.search("best python programming guide")

        assert "best python programming guide" in queried_params
        assert "top python coding tips" in queried_params

    def test_merges_results_from_both_queries(self):
        from app.sources import searxng
        from unittest.mock import patch

        def fake_fetch(query):
            if "alternate" in query:
                return [{"title": "Alternate Result", "url": "https://example.com/alt", "content": "python programming alternate content"}]
            return [{"title": "Primary Result", "url": "https://example.com/primary", "content": "python programming primary content"}]

        with patch.object(searxng, "_fetch_searxng", side_effect=fake_fetch), \
             patch.object(searxng, "get_alternate_phrasing", return_value="alternate python query"):
            result = searxng.search("python programming guide")

        assert "Primary Result" in result
        assert "Alternate Result" in result

    def test_dedupes_www_variant_urls(self):
        """Regression test — 'https://www.x.com/page/' and 'https://x.com/page'
        are the same article but weren't deduped by raw string comparison."""
        from app.sources import searxng
        from unittest.mock import patch

        def fake_fetch(query):
            if "alternate" in query:
                return [{"title": "Sourdough Guide", "url": "https://zoebakes.com/2025/04/21/sourdough/", "content": "sourdough starter recipe content"}]
            return [{"title": "Sourdough Guide", "url": "https://www.zoebakes.com/2025/04/21/sourdough/", "content": "sourdough starter recipe content"}]

        with patch.object(searxng, "_fetch_searxng", side_effect=fake_fetch), \
             patch.object(searxng, "get_alternate_phrasing", return_value="alternate sourdough query"):
            result = searxng.search("sourdough starter recipe")

        assert result.count("Sourdough Guide") == 1

    def test_dedupes_same_url_across_queries(self):
        from app.sources import searxng
        from unittest.mock import patch

        call_count = {"n": 0}

        def fake_fetch(query):
            call_count["n"] += 1
            return [{"title": "Same Result", "url": "https://example.com/same", "content": "python programming content here"}]

        with patch.object(searxng, "_fetch_searxng", side_effect=fake_fetch), \
             patch.object(searxng, "get_alternate_phrasing", return_value="alternate python query"):
            result = searxng.search("python programming guide")

        assert call_count["n"] == 2  # both queries fetched
        assert result.count("Same Result") == 1  # but deduped to one

    def test_no_expansion_when_not_eligible(self):
        from app.sources import searxng
        from unittest.mock import patch

        with patch.object(searxng, "_fetch_searxng", return_value=[
                {"title": "Single Result", "url": "https://example.com/x", "content": "python programming content here"}
             ]) as mock_fetch, \
             patch.object(searxng, "get_alternate_phrasing", return_value=None):
            searxng.search("python programming guide")

        # Only the primary query should be fetched when no alternate is available
        assert mock_fetch.call_count == 1

    def test_falls_back_gracefully_when_alternate_fetch_fails(self):
        from app.sources import searxng
        from unittest.mock import patch

        def fake_fetch(query):
            if "alternate" in query:
                return None  # alternate search genuinely fails (connection error etc)
            return [{"title": "Primary Result", "url": "https://example.com/primary", "content": "python programming content here"}]

        with patch.object(searxng, "_fetch_searxng", side_effect=fake_fetch), \
             patch.object(searxng, "get_alternate_phrasing", return_value="alternate python query"):
            result = searxng.search("python programming guide")

        assert "Primary Result" in result


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

    def setup_method(self):
        from app.config import settings
        self._orig_threshold = settings.web_news_score_threshold
        self._orig_top_n = settings.web_news_top_n
        # Use a permissive threshold for most tests so generic mock content
        # (which has little real keyword overlap) isn't scored out entirely —
        # tests that specifically test scoring/filtering set their own values
        settings.web_news_score_threshold = -100

    def teardown_method(self):
        from app.config import settings
        settings.web_news_score_threshold = self._orig_threshold
        settings.web_news_top_n = self._orig_top_n

    def _mock_response(self, results: list) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"results": results}
        mock.raise_for_status.return_value = None
        return mock

    def test_returns_formatted_results(self):
        from app.sources import searxng
        mock_results = [
            {"title": "Nginx Docs", "url": "https://nginx.org", "content": "Official nginx documentation and configuration guide."},
            {"title": "Nginx Tutorial", "url": "https://example.com", "content": "How to configure nginx for production use."},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("nginx")
        assert "Nginx Docs" in result
        assert "nginx.org" in result
        assert "Nginx Tutorial" in result

    def test_respects_configured_top_n(self):
        from app.sources import searxng
        from app.config import settings
        settings.web_news_top_n = 5
        mock_results = [
            {"title": f"Python Result {i}", "url": f"https://example.com/{i}", "content": f"Python programming content number {i} here."}
            for i in range(10)
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("python programming")
        # Should be capped at the configured top_n, not the old hardcoded 5
        assert result.count("Python Result") == 5

    def test_empty_results_returns_no_results_message(self):
        from app.sources import searxng
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response([])):
            result = searxng.search("xyzzy nothing")
        assert "no results" in result.lower()

    def test_all_results_below_threshold_returns_message(self):
        from app.sources import searxng
        from app.config import settings
        settings.web_news_score_threshold = 1000  # impossibly high, nothing survives
        mock_results = [
            {"title": "Some Result", "url": "https://example.com", "content": "Some content"},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("test query")
        assert "no sufficiently relevant" in result.lower()

    def test_connection_error_returns_error_message(self):
        from app.sources import searxng
        import requests
        with patch("app.sources.searxng.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            result = searxng.search("test")
        assert "error" in result.lower()

    def test_fetch_returns_none_on_failure_not_empty_list(self):
        """Regression test — _fetch_searxng must distinguish 'request failed'
        (None) from 'request succeeded with zero results' ([]) so search()
        can surface a real error message instead of silently reporting
        'no results found' when SearXNG is actually unreachable."""
        from app.sources import searxng
        import requests
        with patch("app.sources.searxng.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            result = searxng._fetch_searxng("test")
        assert result is None

    def test_fetch_returns_empty_list_on_genuinely_empty_results(self):
        from app.sources import searxng
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status.return_value = None
        with patch("app.sources.searxng.requests.get", return_value=mock_resp):
            result = searxng._fetch_searxng("test")
        assert result == []

    def test_results_separated_by_divider(self):
        from app.sources import searxng
        mock_results = [
            {"title": "Result A", "url": "https://a.com", "content": "Detailed content about result topic A here."},
            {"title": "Result B", "url": "https://b.com", "content": "Detailed content about result topic B here."},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("result topic")
        assert "---" in result

    def test_relevant_result_ranks_above_irrelevant(self):
        from app.sources import searxng
        mock_results = [
            {"title": "Unrelated Cooking Recipe", "url": "https://cooking.com", "content": "How to bake bread at home."},
            {"title": "Python Programming Guide", "url": "https://python.com", "content": "Complete python programming tutorial and guide."},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("python programming guide")
        # The relevant result should appear first despite being listed second in raw results
        assert result.index("Python Programming Guide") < result.index("Unrelated Cooking Recipe")

    def test_generic_homepage_result_filtered_out(self):
        from app.sources import searxng
        from app.config import settings
        settings.web_news_score_threshold = 0
        mock_results = [
            {"title": "Home", "url": "https://example.com/", "content": "Welcome"},
            {"title": "Python GPIO Setup Tutorial", "url": "https://example.com/tutorial", "content": "Complete tutorial covering python gpio setup on raspberry pi."},
        ]
        with patch("app.sources.searxng.requests.get", return_value=self._mock_response(mock_results)):
            result = searxng.search("python gpio setup")
        assert "Python GPIO Setup Tutorial" in result
        assert result.count("**Home**") == 0
