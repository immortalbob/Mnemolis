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


class TestKeywordDetectMulti:
    """Tests for multi-keyword detection that escalates to fusion."""

    def setup_method(self):
        from app.router import _keyword_detect
        self.detect = _keyword_detect

    def test_two_sources_returns_list(self):
        result = self.detect("what is the weather and are my services up")
        assert isinstance(result, list)
        assert "forecast" in result
        assert "uptime" in result

    def test_weather_and_news_returns_list(self):
        result = self.detect("what is the weather and latest news headlines")
        assert isinstance(result, list)
        assert "forecast" in result
        assert "news" in result

    def test_single_source_still_returns_string(self):
        result = self.detect("what is the weather tomorrow")
        assert isinstance(result, str)
        assert result == "forecast"

    def test_no_match_still_returns_none(self):
        result = self.detect("what is molybdenum")
        assert result is None

    def test_list_has_no_duplicates(self):
        result = self.detect("what is the weather and are my services up")
        assert isinstance(result, list)
        assert len(result) == len(set(result))

    def test_three_sources_returns_list(self):
        result = self.detect("weather forecast latest news and are my services up")
        assert isinstance(result, list)
        assert len(result) >= 2


class TestNewUptimeTriggers:
    """Regression tests for expanded uptime trigger list."""

    def setup_method(self):
        from app.router import _keyword_detect
        self.detect = _keyword_detect

    def test_my_services(self):
        assert self.detect("check my services") == "uptime"

    def test_services_up(self):
        assert self.detect("are my services up") == "uptime"

    def test_services_down(self):
        assert self.detect("are any services down") == "uptime"

    def test_anything_down(self):
        assert self.detect("is anything down right now") == "uptime"

    def test_everything_up(self):
        assert self.detect("is everything up") == "uptime"

    def test_everything_down(self):
        assert self.detect("is everything down") == "uptime"

    def test_network_down(self):
        assert self.detect("is the network down") == "uptime"

    def test_network_up(self):
        assert self.detect("is the network up") == "uptime"

    def test_whats_offline(self):
        assert self.detect("what's offline right now") == "uptime"

    def test_server_status(self):
        assert self.detect("what is the server status") == "uptime"

    def test_server_down(self):
        assert self.detect("is the server down") == "uptime"

    def test_check_services(self):
        assert self.detect("check services for me") == "uptime"

    def test_is_my_network(self):
        assert self.detect("is my network ok") == "uptime"

    def test_anything_offline(self):
        assert self.detect("is anything offline right now") == "uptime"

    def test_is_it_running(self):
        assert self.detect("is it running on my network") == "uptime"

    def test_is_it_up(self):
        assert self.detect("is it up") == "uptime"

    def test_are_they_up(self):
        assert self.detect("are they up") == "uptime"


class TestAutoFusionEscalation:
    """Tests for auto routing escalating to fusion on multi-topic queries."""

    def test_multi_topic_query_decomposes(self):
        from app.router import route, _decompose
        from unittest.mock import patch
        from app.sources import forecast, uptime_kuma

        # Multi-topic query should decompose into sub-queries rather than fuse
        parts = _decompose("what is the weather and are my services up")
        assert len(parts) == 2
        assert any("weather" in p for p in parts)
        assert any("services" in p for p in parts)

    def test_multi_topic_query_routes_independently(self):
        from app.router import route, clear_cache, clear_routing_cache
        import app.router as router_module
        from unittest.mock import patch, MagicMock

        clear_cache()
        mock_forecast = MagicMock(return_value="Sunny today.")
        mock_uptime = MagicMock(return_value="All services up.")
        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = mock_forecast
        router_module.SOURCE_MAP["uptime"] = mock_uptime
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router._keyword_detect", return_value=None), \
                 patch("app.router._llm_detect", side_effect=["forecast", "uptime"]):
                result = route("what is the weather and are my services up", "auto")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert mock_forecast.called
        assert mock_uptime.called

    def test_single_topic_does_not_use_fusion(self):
        from app.router import route
        from unittest.mock import patch
        from app.sources import fusion

        with patch.object(fusion, "search") as mock_fusion:
            route("what is the weather tomorrow", "auto")
        assert not mock_fusion.called


class TestResolveChangesHours:
    """Tests for _resolve_changes_hours time-window phrase parsing."""

    def setup_method(self):
        from app.router import _resolve_changes_hours
        self.resolve = _resolve_changes_hours

    def test_explicit_hour_count(self):
        assert self.resolve("changes in the last 3 hours") == 3.0

    def test_explicit_hour_count_different_number(self):
        assert self.resolve("what changed in the last 12 hours") == 12.0

    def test_today_defaults_to_24(self):
        assert self.resolve("what changed today") == 24.0

    def test_week_defaults_to_168(self):
        assert self.resolve("what changed this week") == 168.0

    def test_yesterday_defaults_to_48(self):
        assert self.resolve("what changed since yesterday") == 48.0

    def test_no_phrase_defaults_to_24(self):
        assert self.resolve("any new outages") == 24.0

    def test_this_morning_returns_positive_hours(self):
        result = self.resolve("what changed this morning")
        assert result > 0

    def test_while_at_work_returns_positive_hours(self):
        result = self.resolve("anything changed while i was at work")
        assert result > 0

    def test_tonight_returns_positive_hours(self):
        result = self.resolve("what's happening tonight")
        assert result > 0

    def test_morning_uses_configured_start_hour(self):
        from app.config import settings
        original = settings.morning_start_hour
        settings.morning_start_hour = 6
        result = self.resolve("what changed this morning")
        settings.morning_start_hour = original
        assert result > 0

    def test_work_uses_configured_start_hour(self):
        from app.config import settings
        original = settings.work_start_hour
        settings.work_start_hour = 9
        result = self.resolve("since i've been at work")
        settings.work_start_hour = original
        assert result > 0

    def test_explicit_hour_count_takes_priority_over_today(self):
        # "today" appears but explicit hour count should win
        result = self.resolve("what changed today in the last 2 hours")
        assert result == 2.0


class TestHoursSince:
    """Tests for _hours_since helper."""

    def setup_method(self):
        from app.router import _hours_since
        self.hours_since = _hours_since

    def test_returns_positive_float(self):
        result = self.hours_since(6)
        assert isinstance(result, float)
        assert result > 0

    def test_never_returns_zero_or_negative(self):
        from datetime import datetime
        current_hour = datetime.now().hour
        # Even at the exact current hour, should return a small positive value
        result = self.hours_since(current_hour)
        assert result > 0

    def test_future_hour_looks_back_to_yesterday(self):
        from datetime import datetime
        # An hour later than now should wrap to yesterday, giving ~24h - delta
        future_hour = (datetime.now().hour + 1) % 24
        result = self.hours_since(future_hour)
        assert result > 0
        assert result < 25


class TestDecompose:
    """Tests for _decompose conjunction splitting."""

    def setup_method(self):
        from app.router import _decompose
        self.decompose = _decompose

    def test_weather_and_services_splits(self):
        parts = self.decompose("what is the weather and are my services up")
        assert len(parts) == 2

    def test_single_query_not_split(self):
        assert self.decompose("what is molybdenum") == ["what is molybdenum"]

    def test_compare_not_split(self):
        assert len(self.decompose("compare Python and Rust")) == 1

    def test_location_not_split(self):
        assert len(self.decompose("weather in Phoenix and Kingman")) == 1

    def test_country_names_not_split(self):
        assert len(self.decompose("what is happening with Iran and Israel")) == 1

    def test_also_conjunction(self):
        parts = self.decompose("check services also what is the forecast")
        assert len(parts) == 2

    def test_triple_split(self):
        parts = self.decompose("house status and weather and are services up")
        assert len(parts) == 3

    def test_indoor_air_and_doors(self):
        parts = self.decompose("indoor air quality and are the doors locked")
        assert len(parts) == 2

    def test_summarize_not_split(self):
        assert len(self.decompose("summarize news about Iran and Israel")) == 1

    def test_temperature_and_lights(self):
        parts = self.decompose("what is the temperature and are the lights on")
        assert len(parts) == 2

    def test_battery_and_security(self):
        parts = self.decompose("battery status and security status")
        assert len(parts) == 2

    def test_explicit_source_not_decomposed(self):
        from app.router import route, clear_cache
        import app.router as router_module
        from unittest.mock import patch, MagicMock

        clear_cache()
        mock_kiwix = MagicMock(return_value="Kiwix result.")
        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = mock_kiwix
        try:
            with patch("app.router._get_cached", return_value=None):
                result = route("what is the weather and are my services up", "kiwix")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert mock_kiwix.called
        assert result == "Kiwix result."


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


class TestLlmPickFusionSources:
    """Tests for _llm_pick_fusion_sources LLM fusion source selection."""

    def test_returns_default_when_llm_not_configured(self):
        from app.router import _llm_pick_fusion_sources
        from unittest.mock import patch
        with patch("app.llm.is_configured", return_value=False):
            result = _llm_pick_fusion_sources("what is the weather")
        assert result == ["kiwix", "web"]

    def test_returns_llm_sources_when_valid(self):
        from app.router import _llm_pick_fusion_sources, clear_routing_cache
        from unittest.mock import patch
        clear_routing_cache()
        with patch("app.llm.is_configured", return_value=True):
            with patch("app.llm.complete", return_value="forecast, uptime"):
                with patch("app.router._get_routing", return_value=None):
                    result = _llm_pick_fusion_sources("whats the weather and services up")
        assert "forecast" in result
        assert "uptime" in result

    def test_falls_back_to_default_on_invalid_llm_response(self):
        from app.router import _llm_pick_fusion_sources, clear_routing_cache
        from unittest.mock import patch
        clear_routing_cache()
        with patch("app.llm.is_configured", return_value=True):
            with patch("app.llm.complete", return_value="invalid_source, another_bad"):
                with patch("app.router._get_routing", return_value=None):
                    result = _llm_pick_fusion_sources("some query xyz")
        assert result == ["kiwix", "web"]

    def test_caps_at_three_sources(self):
        from app.router import _llm_pick_fusion_sources, clear_routing_cache
        from unittest.mock import patch
        clear_routing_cache()
        with patch("app.llm.is_configured", return_value=True):
            with patch("app.llm.complete", return_value="forecast, uptime, news, kiwix"):
                with patch("app.router._get_routing", return_value=None):
                    result = _llm_pick_fusion_sources("complex query abc")
        assert len(result) <= 3

    def test_uses_routing_cache_when_available(self):
        from app.router import _llm_pick_fusion_sources
        from unittest.mock import patch
        with patch("app.router._get_routing", return_value="forecast,uptime"):
            result = _llm_pick_fusion_sources("cached query")
        assert "forecast" in result
        assert "uptime" in result
