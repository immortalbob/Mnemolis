"""
Tests for app/scoring.py — shared relevance scoring for web and news results.
"""
import pytest


class TestNormalizeUrl:
    """Tests for normalize_url() — URL deduplication normalization."""

    def test_strips_scheme(self):
        from app.scoring import normalize_url
        assert normalize_url("https://example.com/page") == normalize_url("http://example.com/page")

    def test_strips_www(self):
        from app.scoring import normalize_url
        assert normalize_url("https://www.example.com/page") == normalize_url("https://example.com/page")

    def test_strips_trailing_slash(self):
        from app.scoring import normalize_url
        assert normalize_url("https://example.com/page/") == normalize_url("https://example.com/page")

    def test_strips_query_string(self):
        from app.scoring import normalize_url
        assert normalize_url("https://example.com/page?ref=abc") == normalize_url("https://example.com/page")

    def test_strips_fragment(self):
        from app.scoring import normalize_url
        assert normalize_url("https://example.com/page#section") == normalize_url("https://example.com/page")

    def test_case_insensitive(self):
        from app.scoring import normalize_url
        assert normalize_url("https://Example.com/Page") == normalize_url("https://example.com/page")

    def test_empty_string_returns_empty(self):
        from app.scoring import normalize_url
        assert normalize_url("") == ""

    def test_real_world_duplicate_case(self):
        """The exact case that surfaced this bug — same article, www vs no-www."""
        from app.scoring import normalize_url
        a = "https://www.zoebakes.com/2025/04/21/how-to-make-sourdough-starter-from-scratch/"
        b = "https://zoebakes.com/2025/04/21/how-to-make-sourdough-starter-from-scratch/"
        assert normalize_url(a) == normalize_url(b)

    def test_different_pages_remain_different(self):
        from app.scoring import normalize_url
        a = normalize_url("https://example.com/page-one")
        b = normalize_url("https://example.com/page-two")
        assert a != b


class TestKeywords:
    """Tests for _keywords() stemmed extraction."""

    def test_extracts_meaningful_words(self):
        from app.scoring import _keywords
        result = _keywords("python raspberry pi gpio setup")
        assert "python" in result
        assert "raspberri" in result or "raspberry" in result

    def test_strips_stop_words(self):
        from app.scoring import _keywords
        result = _keywords("what is the best laptop")
        assert "best" in result
        assert "laptop" in result
        assert "what" not in result
        assert "the" not in result

    def test_strips_punctuation(self):
        from app.scoring import _keywords
        result = _keywords("Hello, world! How's it going?")
        assert "hello" in result or "hello," not in result

    def test_empty_string_returns_empty_set(self):
        from app.scoring import _keywords
        assert _keywords("") == set()

    def test_stemming_normalizes_plurals(self):
        from app.scoring import _keywords
        plural = _keywords("batteries")
        singular = _keywords("battery")
        assert plural == singular


class TestIsGenericResult:
    """Tests for _is_generic_result() homepage/about-page detection."""

    def test_detects_homepage_title(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result("Home", "Welcome", "http://example.com/") is True

    def test_detects_welcome_to_pattern(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result("Welcome to Acme Corp", "content", "http://acme.com") is True

    def test_detects_official_site_pattern(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result("Acme - Official Site", "buy our stuff", "http://acme.com") is True

    def test_detects_generic_content_phrases(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result(
            "Acme Corp",
            "This website uses cookies to improve your experience.",
            "http://acme.com/page"
        ) is True

    def test_detects_bare_domain_with_short_content(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result("Acme Corp", "Buy stuff here", "http://acme.com/") is True

    def test_does_not_flag_real_article(self):
        from app.scoring import _is_generic_result
        result = _is_generic_result(
            "How to Configure GPIO Pins on Raspberry Pi",
            "This tutorial covers the basic steps to set up GPIO pins on your Raspberry Pi for common projects involving LEDs and sensors.",
            "http://example.com/blog/raspberry-pi-gpio-tutorial"
        )
        assert result is False

    def test_does_not_flag_specific_path_with_short_content(self):
        from app.scoring import _is_generic_result
        # Has a real path, so the bare-domain-root rule shouldn't apply
        result = _is_generic_result("Quick Tip", "Use GPIO 17.", "http://example.com/tips/gpio-17")
        assert result is False

    def test_404_page_detected(self):
        from app.scoring import _is_generic_result
        assert _is_generic_result("404 - Page Not Found", "", "http://example.com/broken") is True


class TestScoreTextResult:
    """Tests for score_text_result() — the main scoring function."""

    def test_higher_score_for_more_keyword_overlap(self):
        from app.scoring import score_text_result
        high = score_text_result(
            "raspberry pi gpio setup",
            "Raspberry Pi GPIO Setup Guide",
            "Complete guide to setting up GPIO pins on Raspberry Pi.",
        )
        low = score_text_result(
            "raspberry pi gpio setup",
            "Unrelated Article About Cooking",
            "This article has nothing to do with electronics.",
        )
        assert high > low

    def test_exact_title_match_scores_highly(self):
        from app.scoring import score_text_result
        score = score_text_result("python gpio", "Python GPIO", "some content here")
        assert score >= 15

    def test_generic_result_penalized(self):
        from app.scoring import score_text_result
        generic = score_text_result("raspberry pi", "Home", "Welcome", "http://example.com/")
        real = score_text_result(
            "raspberry pi",
            "Raspberry Pi Tutorial",
            "Learn about raspberry pi gpio configuration in this guide.",
            "http://example.com/tutorial"
        )
        assert real > generic

    def test_recency_bonus_increases_score(self):
        from app.scoring import score_text_result
        without_bonus = score_text_result("test query", "Test Article", "test content here", recency_bonus=0)
        with_bonus = score_text_result("test query", "Test Article", "test content here", recency_bonus=10)
        assert with_bonus == without_bonus + 10

    def test_empty_query_does_not_crash(self):
        from app.scoring import score_text_result
        score = score_text_result("", "Some Title", "Some content")
        assert isinstance(score, int)

    def test_empty_title_and_content_does_not_crash(self):
        from app.scoring import score_text_result
        score = score_text_result("query", "", "")
        assert isinstance(score, int)

    def test_no_overlap_scores_low(self):
        from app.scoring import score_text_result
        score = score_text_result("zzz qqq xxx", "completely different words here", "nothing matches at all")
        assert score <= 0


class TestFilterAndRank:
    """Tests for filter_and_rank() — threshold + top-N filtering."""

    def _result(self, title, content, url="http://example.com/article"):
        return {"title": title, "content": content, "url": url}

    def test_drops_results_below_threshold(self):
        from app.scoring import filter_and_rank
        results = [
            self._result("Raspberry Pi GPIO Guide", "Complete guide to gpio setup on raspberry pi devices."),
            self._result("Home", "Welcome", "http://spam.com/"),
        ]
        filtered = filter_and_rank(results, "raspberry pi gpio", score_threshold=0, top_n=10)
        titles = [r["title"] for r in filtered]
        assert "Raspberry Pi GPIO Guide" in titles
        assert "Home" not in titles

    def test_caps_at_top_n(self):
        from app.scoring import filter_and_rank
        results = [
            self._result(f"Python Article {i}", f"python programming article number {i} content")
            for i in range(20)
        ]
        filtered = filter_and_rank(results, "python programming", score_threshold=-100, top_n=5)
        assert len(filtered) == 5

    def test_sorts_by_score_descending(self):
        from app.scoring import filter_and_rank
        results = [
            self._result("Unrelated", "nothing relevant here at all"),
            self._result("Python GPIO Setup", "python gpio setup raspberry pi tutorial guide"),
        ]
        filtered = filter_and_rank(results, "python gpio setup", score_threshold=-100, top_n=10)
        assert filtered[0]["title"] == "Python GPIO Setup"

    def test_empty_results_returns_empty(self):
        from app.scoring import filter_and_rank
        assert filter_and_rank([], "query", score_threshold=0, top_n=10) == []

    def test_all_results_below_threshold_returns_empty(self):
        from app.scoring import filter_and_rank
        results = [self._result("Home", "Welcome", "http://spam.com/")]
        filtered = filter_and_rank(results, "completely unrelated topic", score_threshold=0, top_n=10)
        assert filtered == []

    def test_custom_field_keys(self):
        from app.scoring import filter_and_rank
        results = [{"headline": "Python Tutorial", "body": "python tutorial content here", "link": "http://x.com/a"}]
        filtered = filter_and_rank(
            results, "python tutorial", score_threshold=-100, top_n=10,
            title_key="headline", content_key="body", url_key="link"
        )
        assert len(filtered) == 1

    def test_recency_bonus_field_respected(self):
        from app.scoring import filter_and_rank
        results = [
            {"title": "Old Article", "content": "python content here", "url": "http://x.com/old", "_recency_bonus": 0},
            {"title": "New Article", "content": "python content here", "url": "http://x.com/new", "_recency_bonus": 20},
        ]
        filtered = filter_and_rank(results, "python content", score_threshold=-100, top_n=10)
        assert filtered[0]["title"] == "New Article"

    def test_does_not_mutate_input_dicts(self):
        from app.scoring import filter_and_rank
        results = [self._result("Python Guide", "python guide content")]
        original_keys = set(results[0].keys())
        filter_and_rank(results, "python guide", score_threshold=-100, top_n=10)
        assert set(results[0].keys()) == original_keys


class TestConfigDefaults:
    """Tests for new web/news scoring config defaults."""

    def test_score_threshold_default(self):
        from app.config import Settings
        s = Settings(_env_file=None)
        assert s.web_news_score_threshold == 0

    def test_top_n_default(self):
        from app.config import Settings
        s = Settings(_env_file=None)
        assert s.web_news_top_n == 10
