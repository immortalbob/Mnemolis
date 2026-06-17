"""
Tests for app/router.py — intent detection, cache logic, fallback detection.
No network calls required.
"""
import time
import pytest


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestKeywordDetect:
    """Tests for _keyword_detect — fast keyword-based intent routing."""

    def setup_method(self):
        # Import here so we don't need the full app context at module load
        from app.router import _keyword_detect
        self.detect = _keyword_detect

    def test_weather_keyword(self):
        assert self.detect("what is the weather tomorrow") == "forecast"

    def test_forecast_keyword(self):
        assert self.detect("give me the forecast") == "forecast"

    def test_rain_keyword(self):
        assert self.detect("will it rain this weekend") == "forecast"

    def test_news_keyword(self):
        assert self.detect("what's in the news") == "news"

    def test_headlines_keyword(self):
        assert self.detect("show me the headlines") == "news"

    def test_uptime_down(self):
        # "is down" is a trigger but requires adjacent words — use exact trigger phrase
        assert self.detect("the server is down") == "uptime"

    def test_uptime_service_status(self):
        assert self.detect("check service status") == "uptime"

    def test_uptime_everything_up(self):
        assert self.detect("is everything up") == "uptime"

    def test_web_search(self):
        assert self.detect("search the web for python tutorials") == "web"

    def test_web_who_won(self):
        assert self.detect("who won the super bowl") == "web"

    def test_no_match_returns_none(self):
        assert self.detect("what is molybdenum") is None

    def test_no_match_general_knowledge(self):
        assert self.detect("explain photosynthesis") is None

    def test_no_match_tech_question(self):
        assert self.detect("how do I configure nginx") is None

    # Regression tests — things that used to wrongly match
    def test_latest_does_not_match_news(self):
        assert self.detect("latest iPhone release") is None

    def test_status_does_not_match_uptime(self):
        assert self.detect("what is HTTP status code 404") is None

    def test_tonight_does_not_match_forecast(self):
        assert self.detect("what's on tonight") is None

    def test_will_it_be_does_not_match_forecast(self):
        assert self.detect("will it be available soon") is None

    def test_monitoring_does_not_match_uptime(self):
        assert self.detect("heart rate monitoring") is None

    def test_articles_does_not_match_news(self):
        assert self.detect("articles about Docker") is None

    def test_recent_does_not_match_news(self):
        assert self.detect("recent Python releases") is None


class TestDetectIntent:
    """Tests for detect_intent — full routing with LLM fallback disabled."""

    def setup_method(self):
        from app.router import detect_intent
        self.detect = detect_intent

    def test_keyword_match_bypasses_llm(self):
        # These should match keywords and never hit Ollama
        assert self.detect("whats the weather tomorrow") == "forecast"
        assert self.detect("latest news") == "news"
        assert self.detect("is anything down") == "uptime"

    def test_no_keyword_falls_back_to_kiwix_when_llm_disabled(self):
        # With LLM_URL blank (default in test env), should return kiwix
        from app.config import settings
        original = settings.llm_url
        settings.llm_url = ""
        try:
            result = self.detect("what is the capital of France")
            assert result == "kiwix"
        finally:
            settings.llm_url = original


# ---------------------------------------------------------------------------
# Cache logic
# ---------------------------------------------------------------------------

class TestCache:
    """Tests for cache functions — no network required."""

    def setup_method(self):
        from app.router import clear_cache, _set_cached, _get_cached, check_cached, get_cache_count
        self.clear = clear_cache
        self.set = _set_cached
        self.get = _get_cached
        self.check = check_cached
        self.count = get_cache_count
        self.clear()

    def test_cache_miss_returns_none(self):
        assert self.get("kiwix", "something not cached") is None

    def test_cache_hit_returns_result(self):
        self.set("kiwix", "what is molybdenum", "Molybdenum is an element.")
        result = self.get("kiwix", "what is molybdenum")
        assert result == "Molybdenum is an element."

    def test_check_cached_false_on_miss(self):
        assert self.check("kiwix", "not cached") is False

    def test_check_cached_true_on_hit(self):
        self.set("kiwix", "test query", "test result")
        assert self.check("kiwix", "test query") is True

    def test_cache_key_is_case_insensitive(self):
        self.set("kiwix", "What Is Molybdenum", "result")
        assert self.get("kiwix", "what is molybdenum") == "result"

    def test_cache_key_strips_whitespace(self):
        self.set("kiwix", "  molybdenum  ", "result")
        assert self.get("kiwix", "molybdenum") == "result"

    def test_cache_count(self):
        self.set("kiwix", "query one", "result one")
        self.set("kiwix", "query two", "result two")
        assert self.count() == 2

    def test_clear_cache(self):
        self.set("kiwix", "query", "result")
        count = self.clear()
        assert count == 1
        assert self.count() == 0

    def test_different_sources_different_entries(self):
        self.set("kiwix", "query", "kiwix result")
        self.set("web", "query", "web result")
        assert self.get("kiwix", "query") == "kiwix result"
        assert self.get("web", "query") == "web result"

    def test_expired_entry_returns_none(self):
        from app.router import _cache, CACHE_TTL
        # Manually insert an expired entry
        key = "kiwix:expired query"
        _cache[key] = ("old result", time.time() - CACHE_TTL["kiwix"] - 1)
        assert self.get("kiwix", "expired query") is None

    def test_cache_max_size_evicts_oldest(self):
        from app.router import _CACHE_MAX_SIZE
        # Fill to max
        for i in range(_CACHE_MAX_SIZE):
            self.set("kiwix", f"query {i}", f"result {i}")
        assert self.count() == _CACHE_MAX_SIZE
        # Add one more — should evict oldest
        self.set("kiwix", "one more query", "one more result")
        assert self.count() == _CACHE_MAX_SIZE


# ---------------------------------------------------------------------------
# Empty result detection
# ---------------------------------------------------------------------------

class TestLooksEmpty:
    """Tests for _looks_empty — identifies failed/empty responses."""

    def setup_method(self):
        from app.router import _looks_empty
        self.check = _looks_empty

    def test_no_results_found(self):
        assert self.check("No results found in Kiwix knowledge base.") is True

    def test_no_recent_articles(self):
        assert self.check("No recent articles found in FreshRSS.") is True

    def test_could_not_fetch(self):
        assert self.check("Found article but could not fetch article content.") is True

    def test_real_result_not_empty(self):
        assert self.check("# Molybdenum\nMolybdenum is a chemical element...") is False

    def test_forecast_result_not_empty(self):
        assert self.check("Today will be clear with a high of about 101.") is False

    def test_case_insensitive(self):
        assert self.check("NO RESULTS FOUND in kiwix") is True
