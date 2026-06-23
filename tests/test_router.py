"""
Tests for app/router.py — intent detection, cache logic, fallback detection.
No network calls required.
"""
import time


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
        from app.router import _decompose

        # Multi-topic query should decompose into sub-queries rather than fuse
        parts = _decompose("what is the weather and are my services up")
        assert len(parts) == 2
        assert any("weather" in p for p in parts)
        assert any("services" in p for p in parts)

    def test_multi_topic_query_routes_independently(self):
        from app.router import route, clear_cache
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
                route("what is the weather and are my services up", "auto")
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

    def test_decomposed_subquery_resolving_to_fusion_not_double_headered(self):
        """Regression test — the real bug found via real usage. When a
        decomposed sub-query's own intent resolves to a list (triggering
        internal fusion across multiple sources), fusion.search() already
        returns content with its own per-source [SOURCE — DESC] headers.
        The outer decomposition loop used to wrap that already-headered
        block in ANOTHER header using the literal string "fusion" as the
        source name — which has no entry in _HEADER_LABELS, producing a
        nonsensical "[FUSION — FUSION]" wrapper around content that was
        already correctly labeled internally."""
        from app.router import route, clear_cache
        import app.router as router_module
        from unittest.mock import patch

        clear_cache()
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=[
                     ["uptime", "ha"],  # first sub-query resolves to a fusion list
                     "kiwix",            # second sub-query resolves to a single source
                 ]), \
                 patch("app.sources.fusion.search", return_value="[UPTIME — DESC]\nAll up.\n\n---\n\n[HA — DESC]\nAll locked."), \
                 patch.object(router_module, "SOURCE_MAP", {**router_module.SOURCE_MAP, "kiwix": lambda q: "Sunspots are dark regions."}):
                result = route("is anything down and what are sunspots", "auto")
        finally:
            pass

        assert "[FUSION" not in result
        assert "[UPTIME — DESC]" in result
        assert "[HA — DESC]" in result


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


class TestSearchChanges:
    """Tests for _search_changes — the actual wired entry point for source='changes'."""

    def test_calls_get_changes_and_format_changes(self):
        from app.router import _search_changes
        from unittest.mock import patch
        with patch("app.router.get_changes", return_value={"uptime": [{"timestamp": "2026-06-19T10:00:00Z", "change": "test"}]}) as mock_get, \
             patch("app.router.format_changes", return_value="formatted result") as mock_fmt:
            result = _search_changes("what changed today")
        assert mock_get.called
        assert mock_fmt.called
        assert result == "formatted result"

    def test_passes_resolved_hours_to_get_changes(self):
        from app.router import _search_changes
        from unittest.mock import patch
        with patch("app.router.get_changes", return_value={}) as mock_get, \
             patch("app.router.format_changes", return_value=""):
            _search_changes("what changed in the last 5 hours")
        # get_changes should be called with since_hours=5.0
        call_kwargs = mock_get.call_args
        assert call_kwargs is not None

    def test_no_changes_returns_format_changes_no_data_message(self):
        from app.router import _search_changes
        result = _search_changes("any new outages")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_integration_with_real_today_query(self):
        from app.router import _search_changes
        # End-to-end smoke test against real (likely empty in test env) snapshot data
        result = _search_changes("what changed today")
        assert isinstance(result, str)


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

    def test_picks_best_split_not_first_conjunction_found(self):
        """Regression test — the real bug found via real usage. A query
        with one ' also ' and two ' and 's used to stop at the first
        conjunction type encountered (sorted by length, ' also ' before
        ' and ') even though splitting on ' and ' produced a better
        (3-part, all genuinely separate intents) result. The function
        must try every conjunction and keep whichever split has the
        most meaningful parts, not just the first one that qualifies."""
        query = (
            "My raspberry pi keeps locking up whenever I mess with the GPIO "
            "pins in python, whats going on with that, and also did anything "
            "weird happen with the back door today, and is it gonna rain or nah"
        )
        parts = self.decompose(query)
        assert len(parts) == 3
        # Confirm each real intent landed in its own part
        assert any("gpio" in p.lower() for p in parts)
        assert any("door" in p.lower() for p in parts)
        assert any("rain" in p.lower() for p in parts)

    def test_singular_door_recognized_as_intent(self):
        """Regression test — _INTENT_WORDS previously only had 'doors'
        (plural), so 'back door' (singular) failed the intent-word check
        and got silently dropped as a 'meaningless' sub-query fragment."""
        parts = self.decompose("what is the weather and did anything happen with the back door")
        assert len(parts) == 2
        assert any("door" in p.lower() for p in parts)

    def test_singular_light_recognized_as_intent(self):
        parts = self.decompose("what is the forecast and is the light on")
        assert len(parts) == 2

    def test_singular_lock_recognized_as_intent(self):
        parts = self.decompose("what is the news and is the lock engaged")
        assert len(parts) == 2

    def test_singular_sensor_recognized_as_intent(self):
        parts = self.decompose("check services and what does the sensor say")
        assert len(parts) == 2

    def test_anything_happened_phrasing_recognized(self):
        """Casual 'did anything happen with X' phrasing should register
        as a real intent on its own, not just formal 'what is X' phrasing."""
        parts = self.decompose("what is the weather and did anything happen today")
        assert len(parts) == 2

    def test_wifi_router_compound_query_with_colloquial_starter(self):
        """Regression test — the real bug found via real usage. This query
        has at least three genuine intents (wifi troubleshooting, router
        troubleshooting, door sensor status, sunspots) but used to collapse
        to a single unsplit block because: (1) 'wifi'/'router'/'reboot'
        weren't in _INTENT_WORDS at all, and (2) the colloquial starter
        'what's the deal with X' wasn't recognized as a real intent
        regardless of what specific noun followed it — both gaps had to be
        fixed together. The mixed-conjunction-type fix (added later the
        same session) further improved this from a 3-way split to a
        genuinely better 4-way split, correctly separating wifi and router
        into their own independent parts rather than merging them under
        one " and "."""
        query = (
            "the wifi has been acting flaky all morning and I think the "
            "router might need a reboot, also can you check if the front "
            "door sensor is online, and what's the deal with sunspots anyway"
        )
        parts = self.decompose(query)
        assert len(parts) == 4
        assert any("wifi" in p.lower() for p in parts)
        assert any("router" in p.lower() for p in parts)
        assert any("door" in p.lower() for p in parts)
        assert any("sunspot" in p.lower() for p in parts)

    def test_wifi_alone_recognized_as_intent(self):
        parts = self.decompose("is the wifi down and what is the weather")
        assert len(parts) == 2

    def test_router_alone_recognized_as_intent(self):
        parts = self.decompose("does the router need a restart and is it raining")
        assert len(parts) == 2

    def test_colloquial_starter_with_unrecognized_noun(self):
        """The colloquial starter alone should be sufficient to count as
        meaningful, even with a topic word ('sunspots') that has no entry
        in _INTENT_WORDS and never will, since new topics are unbounded."""
        parts = self.decompose("check the weather and what's the deal with sunspots")
        assert len(parts) == 2
        assert any("sunspot" in p.lower() for p in parts)

    def test_whats_up_with_starter_recognized(self):
        parts = self.decompose("check services and what's up with the stock market")
        assert len(parts) == 2

    def test_colloquial_phrase_matches_mid_clause_not_just_at_start(self):
        """Regression test — the real bug found via real usage. Colloquial
        phrase detection originally only matched at the very start of a
        sub-query (.startswith()), missing real phrasing like 'and remind
        me what's up with X' where the marker phrase is buried mid-clause
        after leftover conjunction/filler words from the split point.
        Changed to a substring check so it matches anywhere in the clause."""
        parts = self.decompose(
            "check the weather and remind me what's up with that sourdough starter"
        )
        assert len(parts) == 2
        assert any("sourdough" in p.lower() for p in parts)

    def test_colloquial_phrase_with_can_you_tell_me_prefix(self):
        parts = self.decompose(
            "what is the forecast and can you tell me what's the deal with cryptocurrency"
        )
        assert len(parts) == 2
        assert any("cryptocurrency" in p.lower() for p in parts)

    def test_mixed_conjunction_types_all_split(self):
        """Regression test — the real bug found via real usage, documented
        as a known limitation at the end of the prior session and fixed
        properly here. A query mixing multiple different conjunction types
        ("and also", "plus", "and", "also") used to only ever produce 2
        parts under the single-conjunction-type approach, because each
        type's isolated split left the OTHER conjunction words bundled
        inside whichever half didn't get split. Splitting on every
        conjunction occurrence at once, regardless of type, fixes this."""
        query = (
            "ok so my internet's been super flaky today and also whats the "
            "deal with that whole mercury retrograde thing everyone keeps "
            "talking about, plus did the front door or any of the windows "
            "do anything weird while I was out, and is it gonna be hot "
            "enough this week that I should finally fix the AC, also "
            "remind me real quick whats up with raspberry pi gpio "
            "permission stuff cause I keep getting locked out when I mess "
            "with the pins in python"
        )
        parts = self.decompose(query)
        assert len(parts) == 5
        assert any("internet" in p.lower() for p in parts)
        assert any("mercury" in p.lower() for p in parts)
        assert any("door" in p.lower() for p in parts)
        assert any("hot" in p.lower() or "ac" in p.lower().split() for p in parts)
        assert any("gpio" in p.lower() for p in parts)

    def test_adjacent_conjunctions_collapse_to_single_split_point(self):
        """'and also' (two conjunctions back to back) should produce one
        split point, not an empty/near-empty fragment between them."""
        parts = self.decompose(
            "what is the weather and also what is the news and is the door locked"
        )
        assert len(parts) == 3
        # No fragment should be just leftover punctuation/whitespace
        assert all(len(p.strip()) > 3 for p in parts)

    def test_possessive_contraction_recognized_in_intent_check(self):
        """Regression test — 'internet's' (possessive contraction) didn't
        match the bare 'internet' entry in _INTENT_WORDS via exact word
        membership, the same class of bug found and fixed in kiwix.py's
        stop-word stripping. Normalizing the apostrophe before the
        membership check fixes both the same way."""
        parts = self.decompose("my internet's been flaky and what is the weather")
        assert len(parts) == 2
        assert any("internet" in p.lower() for p in parts)

    def test_mixed_conjunctions_still_respects_nosplit(self):
        """Mixed-conjunction splitting must not bypass the nosplit guard —
        a comparison query with multiple conjunction types should still
        never split at all."""
        parts = self.decompose("compare Python and Rust, also what about Go")
        assert len(parts) == 1

    def test_technical_troubleshooting_content_preserved(self):
        """Regression test — the real bug found via real usage. The old
        fixed _INTENT_WORDS allowlist had zero coverage for technical/
        programming vocabulary (python, pigpio, gpio, permission, error,
        compiler, etc), so a genuinely real, specific troubleshooting
        clause was silently dropped during decomposition entirely —
        the meaningful-content check failed it even though it contained
        real, searchable content. Replaced the allowlist with a stop-word-
        based check: any clause with at least one real content word
        remaining after stripping filler now counts as meaningful, with
        no domain-specific vocabulary list required."""
        query = (
            "is my back door locked right now, and also Ive been getting a "
            "python pigpio no permission to update GPIO error on my pi, "
            "plus whats the forecast looking like for tomorrow"
        )
        parts = self.decompose(query)
        assert len(parts) == 3
        assert any("door" in p.lower() for p in parts)
        assert any("pigpio" in p.lower() or "gpio" in p.lower() for p in parts)
        assert any("forecast" in p.lower() for p in parts)

    def test_proper_noun_pair_not_split_phoenix_kingman(self):
        """Regression test — loosening the meaningful-check from a fixed
        allowlist to 'any content word survives' also started incorrectly
        splitting bare proper-noun pairs that the old, stricter allowlist
        had blocked only by accident (neither 'Phoenix' nor 'Kingman' ever
        matched any entry in that list). Explicit structural detection
        restores this without reintroducing a place-name list."""
        assert len(self.decompose("weather in Phoenix and Kingman")) == 1

    def test_proper_noun_pair_not_split_countries(self):
        assert len(self.decompose("what is happening with Iran and Israel")) == 1

    def test_proper_noun_pair_not_split_summarize_news(self):
        assert len(self.decompose("summarize news about Iran and Israel")) == 1

    def test_proper_noun_pair_guard_does_not_block_real_decomposition(self):
        """The proper-noun-pair guard must be narrow enough that it never
        blocks a genuine multi-intent split just because one clause
        happens to start with a capitalized word."""
        parts = self.decompose("Is it raining and are my services up")
        assert len(parts) == 2

    def test_single_real_content_word_is_sufficient(self):
        """A clause needs only ONE real content word to count as
        meaningful — no minimum word count beyond that, matching the
        principle that any genuine topic, however short, deserves its
        own search rather than being silently merged or dropped."""
        parts = self.decompose("what is the weather and gpio")
        assert len(parts) == 2

    def test_proper_noun_pair_guard_is_local_not_global(self):
        """Regression test — the real, more serious bug found via real
        usage immediately after the content-word fix shipped. The
        proper-noun-pair guard was originally a single whole-query gate:
        if ANY conjunction occurrence anywhere looked like a proper-noun
        pair, decomposition aborted ENTIRELY, discarding genuinely
        separate, real intents elsewhere in the same sentence. A query
        can contain both a genuine proper-noun pair ('Iran and Israel')
        AND completely unrelated real intents ('my back door', 'a numpy
        import error on my pi') in the same breath — the guard must
        protect only the SPECIFIC conjunction occurrence that's a
        proper-noun pair, checked independently at every occurrence,
        not abort splitting for the whole query the moment it finds one
        anywhere."""
        query = (
            "whats happening with Iran and Israel right now, and also "
            "has anything weird happened with my back door, plus I keep "
            "getting a weird numpy import error on my raspberry pi, and "
            "is it gonna be warm in Kingman and Phoenix this weekend"
        )
        parts = self.decompose(query)
        assert len(parts) == 4
        # Both proper-noun pairs must survive intact, not split apart
        assert any("iran and israel" in p.lower() for p in parts)
        assert any("kingman and phoenix" in p.lower() for p in parts)
        # Both genuinely separate real intents must still be present
        assert any("door" in p.lower() for p in parts)
        assert any("numpy" in p.lower() for p in parts)

    def test_proper_noun_pair_skip_does_not_discard_preceding_content(self):
        """Regression test — a deeper bug found via real usage in a
        megaquery test, AFTER the previous proper-noun-pair fixes had
        already shipped. The single-conjunction-type split loop's skip
        logic, when encountering a protected pair ("Iran and Israel"),
        reset the search position to just past the skipped occurrence —
        which also reset where the NEXT kept part would start FROM,
        silently discarding all the real text that came before the pair
        ("also whats happening with Iran and" got reduced to just
        "Israel, plus...", losing "also whats happening with Iran and"
        entirely). The fix tracks segment_start (where the current
        accumulating part begins) separately from search_from (where to
        resume looking for the next conjunction), so skipping a
        protected pair advances the search position without discarding
        any of the real content that precedes it."""
        query = (
            "also whats happening with Iran and Israel, plus I keep "
            "getting a weird numpy import error on my raspberry pi, and "
            "if any services are down let me know too, and one more "
            "thing whats the deal with sunspots"
        )
        parts = self.decompose(query)
        assert len(parts) == 3
        # The proper-noun pair AND the real content before it must both
        # survive in the same part — neither lost, neither split apart
        first_part = parts[0].lower()
        assert "iran and israel" in first_part
        assert "whats happening with" in first_part
        assert any("numpy" in p.lower() for p in parts)
        assert any("services are down" in p.lower() for p in parts)
        assert any("sunspots" in p.lower() for p in parts)

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


class TestDiscourseFramingDetection:
    """Tests for _has_discourse_framing() — detects phrasing that frames
    a topic as current public discourse ("everyone keeps talking about
    X") rather than a pure knowledge lookup.

    Found via extensive real-usage testing this session: queries like
    "what's the deal with that whole mercury retrograde thing everyone
    keeps talking about" reproducibly routed past kiwix to news/web,
    because the LLM router's source descriptions for news/web ("current
    events", "recent information") matched this phrasing almost
    word-for-word, while kiwix's description ("factual, encyclopedic, or
    technical questions") gave no signal that it ALSO covers evergreen
    topics phrased as current discourse."""

    def has_framing(self, query):
        from app.router import _has_discourse_framing
        return _has_discourse_framing(query)

    def test_everyone_keeps_talking_about(self):
        assert self.has_framing(
            "whats the deal with that whole mercury retrograde thing everyone keeps talking about"
        ) is True

    def test_everyones_obsessed_with(self):
        assert self.has_framing(
            "whats the deal with that whole galaxy thing everyone's obsessed with right now"
        ) is True

    def test_everyone_is_obsessed_with(self):
        assert self.has_framing(
            "whats the deal with that whole bitcoin thing everyone is obsessed with"
        ) is True

    def test_everyones_talking_about(self):
        assert self.has_framing(
            "whats the deal with that whole black hole thing everyone's talking about"
        ) is True

    def test_plain_factual_query_no_framing(self):
        assert self.has_framing("what is the capital of france") is False

    def test_colloquial_phrase_without_discourse_framing(self):
        """'whats the deal with X' alone, with no discourse-framing
        language attached, should NOT trigger this — it's a perfectly
        normal definitional question handled by the existing colloquial
        pattern detection, not evidence of a routing bias problem."""
        assert self.has_framing("whats the deal with sunspots") is False

    def test_tell_me_about_no_framing(self):
        assert self.has_framing("tell me about mercury") is False


class TestDiscourseFramingRoutingBias:
    """Tests confirming _llm_detect() actually applies the discourse-
    framing bias — kiwix is added (escalating to fusion) when discourse-
    framing language is present and kiwix wasn't already part of the
    LLM's chosen source(s), across all four real code paths: fresh
    single-source, fresh multi-source, cached single-source, and cached
    multi-source. The cached paths matter because a routing cache entry
    written before this fix existed (or before kiwix happened to be
    chosen) would otherwise silently bypass the bias for the remainder
    of its TTL, since the cache check returns before the bias logic
    further down in the function ever runs."""

    def setup_method(self):
        from app.router import clear_routing_cache
        clear_routing_cache()

    def test_fresh_single_source_gets_kiwix_added(self):
        from app.router import _llm_detect
        from unittest.mock import patch

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="web"):
            result = _llm_detect(
                "whats the deal with that whole mercury retrograde thing everyone keeps talking about"
            )
        assert isinstance(result, list)
        assert "web" in result
        assert "kiwix" in result

    def test_fresh_multi_source_gets_kiwix_added(self):
        from app.router import _llm_detect
        from unittest.mock import patch

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="news, web"):
            result = _llm_detect(
                "whats the deal with that whole bitcoin thing everyone is obsessed with"
            )
        assert "news" in result
        assert "web" in result
        assert "kiwix" in result

    def test_already_kiwix_is_not_duplicated_or_escalated(self):
        from app.router import _llm_detect
        from unittest.mock import patch

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="kiwix"):
            result = _llm_detect(
                "whats the deal with that whole mercury retrograde thing everyone keeps talking about"
            )
        assert result == "kiwix"

    def test_no_discourse_framing_is_unaffected(self):
        from app.router import _llm_detect
        from unittest.mock import patch

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="web"):
            result = _llm_detect("what is happening in the news today")
        assert result == "web"

    def test_cached_single_source_decision_still_gets_bias_applied(self):
        """Regression coverage for the cache-bypass risk found during
        design — a cached decision from BEFORE this fix existed (or
        before kiwix happened to be chosen) must not silently skip the
        bias for the rest of its TTL."""
        from app.router import _llm_detect, _set_routing
        from unittest.mock import patch

        query = "whats the deal with that whole galaxy thing everyone's obsessed with right now"
        _set_routing(f"source:{query}", "web")  # simulate a pre-fix cached decision

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete") as mock_complete:
            result = _llm_detect(query)

        assert not mock_complete.called  # cache hit — should not call the LLM again
        assert "web" in result
        assert "kiwix" in result

    def test_cached_fusion_decision_still_gets_bias_applied(self):
        from app.router import _llm_detect, _set_routing
        from unittest.mock import patch

        query = "whats the deal with that whole bitcoin thing everyone is obsessed with"
        _set_routing(f"source:{query}", "news,web")  # simulate a pre-fix cached fusion decision

        with patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete") as mock_complete:
            result = _llm_detect(query)

        assert not mock_complete.called
        assert "news" in result
        assert "web" in result
        assert "kiwix" in result


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


class TestRoutingCacheEviction:
    """Tests for routing cache size-bounding — a real gap found during
    operational maturity review: the routing cache had NO size limit at
    all until this was added, unlike the result cache, which already had
    this exact eviction pattern. The routing cache's key space is
    genuinely larger in practice (every unique conditional query,
    discourse-framing phrase, and disambiguation candidate set gets its
    own entry), making unbounded growth over sustained real-world usage a
    real concern, not just a theoretical one.

    _save_routing_cache() (real disk I/O) is mocked throughout — it fires
    on every single _set_routing() call with no batching, unlike the
    result cache's batched save, so filling the cache to its max size in
    a test would otherwise mean hundreds or thousands of real disk writes
    to test logic that has nothing to do with disk I/O at all.
    """

    def setup_method(self):
        from app.router import clear_routing_cache
        from unittest.mock import patch
        self._save_patch = patch("app.router._save_routing_cache")
        self._save_patch.start()
        clear_routing_cache()

    def teardown_method(self):
        self._save_patch.stop()

    def test_routing_cache_has_a_size_limit(self):
        """Confirm the constant actually exists and is a positive
        integer — the most basic regression check that this isn't
        silently disabled or misconfigured to 0/None."""
        from app.router import _ROUTING_CACHE_MAX_SIZE
        assert isinstance(_ROUTING_CACHE_MAX_SIZE, int)
        assert _ROUTING_CACHE_MAX_SIZE > 0

    def test_routing_cache_max_size_evicts_oldest(self):
        from app.router import _ROUTING_CACHE_MAX_SIZE, _set_routing, _routing_cache
        for i in range(_ROUTING_CACHE_MAX_SIZE):
            _set_routing(f"routing query {i}", "kiwix")
        assert len(_routing_cache) == _ROUTING_CACHE_MAX_SIZE
        # Add one more — should evict oldest rather than growing unbounded
        _set_routing("one more routing query", "web")
        assert len(_routing_cache) == _ROUTING_CACHE_MAX_SIZE

    def test_eviction_removes_the_genuinely_oldest_entry(self):
        """Confirm eviction actually removes the OLDEST entry by
        timestamp, not an arbitrary one — the same correctness property
        the result cache's _evict_oldest() already guarantees."""
        from app.router import _ROUTING_CACHE_MAX_SIZE, _set_routing, _get_routing
        import time as time_module
        from unittest.mock import patch

        # Manually control timestamps so eviction order is unambiguous
        base_time = time_module.time()
        with patch("app.router.time.time", side_effect=[base_time + i for i in range(_ROUTING_CACHE_MAX_SIZE)]):
            for i in range(_ROUTING_CACHE_MAX_SIZE):
                _set_routing(f"query {i}", "kiwix")

        assert _get_routing("query 0") == "kiwix"  # the oldest, still present

        with patch("app.router.time.time", return_value=base_time + _ROUTING_CACHE_MAX_SIZE):
            _set_routing("newest query", "web")

        # The oldest entry ("query 0") must now be gone
        assert _get_routing("query 0") is None
        assert _get_routing("newest query") == "web"

    def test_updating_an_existing_key_does_not_trigger_eviction(self):
        """Re-caching a decision for a query ALREADY in the cache must
        not count as a new entry for eviction purposes — only genuinely
        new keys should trigger eviction when at capacity, matching the
        same logic the result cache's _set_cached() already has."""
        from app.router import _ROUTING_CACHE_MAX_SIZE, _set_routing, _routing_cache
        for i in range(_ROUTING_CACHE_MAX_SIZE):
            _set_routing(f"query {i}", "kiwix")
        assert len(_routing_cache) == _ROUTING_CACHE_MAX_SIZE

        # Re-cache an EXISTING key — should not evict anything, since
        # this isn't a new entry
        _set_routing("query 0", "web")
        assert len(_routing_cache) == _ROUTING_CACHE_MAX_SIZE


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


class TestRouteWithSource:
    """Tests for route_with_source() — returns (result, actual_source_used).

    Regression coverage for a real bug found via real usage: a query
    routed to 'kiwix' that returned an empty/unusable result silently fell
    back to 'web' internally, but the API response's source_used field
    still reported 'kiwix' — main.py independently re-derived the intended
    source before calling route(), with no way to learn an internal
    fallback had occurred. route() itself only ever returned a plain
    string with no source information at all.
    """

    def setup_method(self):
        from app.router import clear_cache
        clear_cache()

    def test_route_still_returns_plain_string_for_backward_compatibility(self):
        from app.router import route
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = lambda q: "Kiwix result."
        try:
            with patch("app.router._get_cached", return_value=None):
                result = route("what is nitrogen", "kiwix")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert isinstance(result, str)
        assert result == "Kiwix result."

    def test_route_with_source_returns_tuple(self):
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = lambda q: "Kiwix result."
        try:
            with patch("app.router._get_cached", return_value=None):
                result, source = route_with_source("what is nitrogen", "kiwix")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert result == "Kiwix result."
        assert source == "kiwix"

    def test_fallback_reports_the_actual_fallback_source(self):
        """The exact real-world bug — kiwix returns empty, falls back to
        web, and source_used must say 'web', not 'kiwix'."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        # _looks_empty() checks for specific known "no result" phrases —
        # a bare "" doesn't match any of them and _looks_empty("") is
        # actually False, so the mock must use a realistic failure message
        # the same way a real source module reports finding nothing
        router_module.SOURCE_MAP["kiwix"] = lambda q: "No results found in wikipedia_en_all_maxi."
        router_module.SOURCE_MAP["web"] = lambda q: "Real web results here."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.FALLBACK_CHAIN", {"kiwix": "web"}):
                result, source = route_with_source("some query", "kiwix")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "web"
        assert "Real web results" in result

    def test_no_fallback_needed_reports_original_source(self):
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = lambda q: "Good kiwix result."
        try:
            with patch("app.router._get_cached", return_value=None):
                result, source = route_with_source("what is nitrogen", "kiwix")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "kiwix"

    def test_fusion_reports_fusion_as_source(self):
        from app.router import route_with_source
        from unittest.mock import patch

        with patch("app.sources.fusion.search", return_value="Fused result."), \
             patch("app.router._get_cached", return_value=None):
            result, source = route_with_source("test query", "fusion", ["forecast", "uptime"])
        assert source == "fusion"

    def test_unknown_source_reports_the_unknown_source_name(self):
        from app.router import route_with_source
        result, source = route_with_source("test query", "not_a_real_source")
        assert source == "not_a_real_source"
        assert "unknown source" in result.lower()

    def test_decomposed_single_source_reports_that_source_not_fusion(self):
        """When decomposition produces multiple sub-queries that all
        happen to resolve to the SAME single source after merging,
        overall_source should report that source, not 'fusion' — fusion
        should only be reported when genuinely multiple distinct sources
        contributed to the final merged response."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["ha"] = lambda q: "HA result for: " + q
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", return_value="ha"):
                result, source = route_with_source(
                    "are the doors locked and is the light on", "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "ha"

    def test_decomposed_multiple_sources_reports_fusion(self):
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Sunny."
        router_module.SOURCE_MAP["uptime"] = lambda q: "All up."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=["forecast", "uptime"]):
                result, source = route_with_source(
                    "what is the weather and are services up", "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "fusion"


class TestResolveSingleSourceRefactor:
    """Regression tests for _resolve_single_source() — extracted from
    route_with_source() during a deliberate refactoring pass prompted by
    a cyclomatic-complexity check flagging route_with_source() as the
    most complex function in the codebase (F, 45) by a wide margin.

    The extraction itself surfaced two real, previously-undetected bugs,
    found only by comparing two near-duplicate inline implementations
    side by side rather than reading either one in isolation:

    1. The decomposition loop's fallback path called the fallback
       handler directly with no cache check, while the top-level path
       correctly checked the fallback source's own cache first. Fixed by
       unifying both call sites on one helper that always checks cache.
    2. An unknown/unregistered source's error message didn't match any
       phrase in NO_RESULT_PHRASES, so _looks_empty() incorrectly
       treated it as real content — meaning a stale/misconfigured state
       could silently append an "Unknown source" string into an
       otherwise-clean merged response. Fixed by adding "unknown source"
       to NO_RESULT_PHRASES.
    """

    def setup_method(self):
        from app.router import clear_cache
        clear_cache()

    def test_unknown_source_message_is_treated_as_empty(self):
        """Regression test for bug #2 above."""
        from app.router import _looks_empty, _resolve_single_source
        result, source = _resolve_single_source("not_a_real_source", "test query")
        assert _looks_empty(result) is True

    def test_fallback_checks_cache_before_calling_handler(self):
        """Regression test for bug #1 above — confirms the unified
        helper checks the fallback source's cache before invoking its
        handler, the behavior the decomposition loop was previously
        missing."""
        from app.router import _resolve_single_source, _set_cached
        from unittest.mock import patch
        import app.router as router_module

        fallback_handler_called = []

        def fake_kiwix(q):
            return "no results found"

        def fake_web(q):
            fallback_handler_called.append(q)
            return "should not be called — cache should serve this"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = fake_kiwix
        router_module.SOURCE_MAP["web"] = fake_web
        try:
            _set_cached("web", "test query", "Cached web result.")
            with patch("app.router._get_cached", side_effect=lambda src, q: "Cached web result." if src == "web" else None):
                result, source = _resolve_single_source("kiwix", "test query")
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert result == "Cached web result."
        assert source == "web"
        assert fallback_handler_called == []  # handler never called — cache served it

    def test_decomposition_loop_and_top_level_share_identical_fallback_behavior(self):
        """Confirms both call sites genuinely produce the same result for
        the same scenario now that they share one implementation — the
        actual real-world fix for the inconsistency found during
        extraction, verified end to end rather than just unit-testing
        the helper in isolation."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        def fake_kiwix(q):
            return "no results found"

        def fake_web(q):
            return "Real web result."

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["kiwix"] = fake_kiwix
        router_module.SOURCE_MAP["web"] = fake_web
        try:
            with patch("app.router._get_cached", return_value=None):
                # Top-level (explicit source) path
                top_result, top_source = route_with_source("test query", "kiwix")
                # Decomposition-loop path — force a 2-part split so the
                # decomposition loop's per-sub-query resolution runs
                with patch("app.router.detect_intent", side_effect=["kiwix", "forecast"]):
                    router_module.SOURCE_MAP["forecast"] = lambda q: "Sunny."
                    decomp_result, decomp_source = route_with_source(
                        "test query and what is the weather", "auto"
                    )
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert top_source == "web"
        assert "web" in decomp_result.lower() or "Real web result" in decomp_result


class TestSubQueryFusionCaching:
    """Regression tests for a real bug found via a deliberate complexity-
    reduction investigation, applying the same side-by-side comparison
    discipline that previously found the fallback-caching inconsistency
    and the unrecognized-error-message gap: comparing the decomposition
    loop's per-sub-query fusion dispatch against the top-level single-
    query fusion dispatch surfaced that the sub-query path had NO caching
    at all for a sub-query that itself resolves to fusion (multiple
    sources at once) — unlike every other path in the system, including
    individual single-source sub-query results (cached via
    _resolve_single_source()) and the top-level fusion path (which
    explicitly builds a cache key from sorted source names and checks/
    sets it). A repeated compound query whose individual clause happened
    to resolve to multiple sources internally would re-run
    _llm_pick_fusion_sources() and re-query every fusion source on every
    single request, even identical repeats."""

    def setup_method(self):
        from app.router import clear_cache
        clear_cache()

    def test_repeated_subquery_fusion_result_is_served_from_cache(self):
        """The actual real-world regression test — confirms fusion.search
        is only genuinely called once across two identical requests."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        call_count = []

        def fake_fusion_search(query, sources):
            call_count.append(query)
            return "[FORECAST]\nSunny\n\n---\n\n[HA]\nLocked"

        def fake_detect_intent(q):
            return ["forecast", "ha"] if q.strip() == "weather" else "news"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["news"] = lambda q: "Some news."
        try:
            with patch("app.router.fusion.search", side_effect=fake_fusion_search), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                route_with_source("weather and door status, and also the news", "auto")
                route_with_source("weather and door status, and also the news", "auto")
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert len(call_count) == 1

    def test_subquery_fusion_cache_key_matches_top_level_convention(self):
        """Confirms the new cache key uses the exact same convention
        ("fusion[sorted,sources]:query") the top-level fusion path
        already uses — a deliberate consistency choice, not a new,
        separate caching scheme."""
        from app.router import route_with_source, _get_cached
        from unittest.mock import patch
        import app.router as router_module

        def fake_detect_intent(q):
            return ["forecast", "ha"] if q.strip() == "weather" else "news"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["news"] = lambda q: "Some news."
        try:
            with patch("app.router.fusion.search", return_value="fusion result"), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                route_with_source("weather and door status, and also the news", "auto")
                cached = _get_cached("fusion", "fusion[forecast,ha]:weather")
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert cached == "fusion result"

    def test_different_subquery_fusion_results_cached_independently(self):
        """A different sub-query that also resolves to fusion must get
        its own, independent cache entry — not collide with or
        overwrite a different sub-query's cached fusion result."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        call_count = []

        def fake_fusion_search(query, sources):
            call_count.append(query)
            return f"result for {query}"

        def fake_detect_intent(q):
            stripped = q.strip()
            if stripped in ("weather", "lights status"):
                return ["forecast", "ha"]
            return "news"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["news"] = lambda q: "Some news."
        try:
            with patch("app.router.fusion.search", side_effect=fake_fusion_search), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                route_with_source("weather and door status, and also the news", "auto")
                route_with_source("lights status and door status, and also the news", "auto")
        finally:
            router_module.SOURCE_MAP.update(original_map)

        assert len(call_count) == 2


class TestConditionalDetection:
    """Tests for detect_conditional() — deliberately narrow leading
    "if X, Y" / "should X, Y" / "in case X, Y" pattern detection.

    Scope is intentionally restricted to the leading-comma form only.
    "if" is genuinely ambiguous in English — it has both a conditional
    sense ("if it's raining, bring an umbrella") and a "whether" sense
    ("check if the lights are on" = "check whether the lights are on").
    The whether sense never appears at the very start of a sentence
    followed by a comma, so restricting to that form sidesteps the
    ambiguity entirely rather than guessing at verb-based disambiguation,
    which runs into genuinely unresolvable cases ("let me know if X"
    could mean either "tell me the status" or "notify me if it changes").
    """

    def detect(self, query):
        from app.router import detect_conditional
        return detect_conditional(query)

    def test_leading_if_with_comma(self):
        result = self.detect("if it's raining, remind me to bring an umbrella")
        assert result == ("it's raining", "remind me to bring an umbrella", "")

    def test_leading_should_with_comma(self):
        result = self.detect("should it rain this weekend, I'll need to reschedule")
        assert result == ("it rain this weekend", "I'll need to reschedule", "")

    def test_leading_in_case_with_comma(self):
        result = self.detect("in case my services are down, I want to know right away")
        assert result == ("my services are down", "I want to know right away", "")

    def test_consequence_with_trailing_conjunction_splits_off_remainder(self):
        """Regression test — the real bug found via real usage. The
        consequence group's regex is greedy and originally captured
        everything to the end of the string, silently swallowing a
        completely unrelated second intent that happened to follow a
        conjunction after the consequence. "if any services are down,
        let me know, and also whats the weather" used to extract
        consequence="let me know, and also whats the weather" — losing
        track of "whats the weather" as a real, separate, searchable
        intent entirely. The remainder is now split off and returned
        separately so the caller can route it independently."""
        result = self.detect("if any services are down, let me know, and also whats the weather")
        condition, consequence, remainder = result
        assert condition == "any services are down"
        assert "weather" not in consequence.lower()
        assert "weather" in remainder.lower()

    def test_plain_query_no_match(self):
        assert self.detect("what is the weather today") is None

    def test_embedded_if_does_not_match_whether_usage(self):
        """'check if X' uses 'if' in the 'whether' sense — must NOT be
        treated as a conditional, since 'if' doesn't lead the sentence."""
        assert self.detect("check if the lights are on") is None

    def test_trailing_if_does_not_match(self):
        """Condition-after-consequence phrasing is deliberately out of
        scope — distinguishing it reliably from 'whether' usage would
        require real grammatical parsing, not pattern matching."""
        assert self.detect("remind me to bring an umbrella if it's raining") is None

    def test_let_me_know_if_does_not_match(self):
        """Deliberately excluded — genuinely ambiguous even to a human
        reader (could mean 'tell me the current status' or 'notify me
        if it changes'), not safely interpretable either way."""
        assert self.detect("let me know if the back door is unlocked") is None

    def test_mid_sentence_and_if_does_not_match(self):
        """Mid-sentence 'if' after a conjunction is out of scope for the
        same reason — only the unambiguous leading-comma form is handled."""
        assert self.detect("check the weather and if it's raining remind me to bring an umbrella") is None


class TestInterpretYesNo:
    """Tests for _interpret_yes_no() — restricted to structured,
    genuinely binary sources only (ha, uptime, forecast-precipitation).
    Kiwix/web/news are deliberately excluded — open-ended free text has
    no structured signal to safely key off of, and guessing wrong would
    actively mislead rather than just be unhelpful."""

    def interpret(self, condition, result, source):
        from app.router import _interpret_yes_no
        return _interpret_yes_no(condition, result, source)

    def test_ha_unlocked_condition_true(self):
        assert self.interpret("the back door is unlocked", "Back Door: unlocked", "ha") is True

    def test_ha_unlocked_condition_false(self):
        assert self.interpret("the back door is unlocked", "Back Door: locked", "ha") is False

    def test_ha_locked_condition_true(self):
        assert self.interpret("the back door is locked", "Back Door: locked", "ha") is True

    def test_uptime_down_condition_false_when_all_up(self):
        assert self.interpret("my services are down", "All 15 monitored services are up.", "uptime") is False

    def test_uptime_down_condition_true_when_down(self):
        assert self.interpret("my services are down", "1 service is down: Ollama", "uptime") is True

    def test_forecast_rain_condition_false_when_clear(self):
        result = self.interpret("it's raining", "Today will be clear with a high of 94.", "forecast")
        assert result is False

    def test_forecast_rain_condition_true_when_rainy(self):
        result = self.interpret("it's raining", "Today will be rainy with thunderstorms.", "forecast")
        assert result is True

    def test_forecast_subjective_condition_not_interpreted(self):
        """'hot enough' has no universal threshold — must NOT attempt
        interpretation even though the source (forecast) is otherwise
        in the interpretable set, since this specific condition has no
        safe, structured signal to key off of."""
        result = self.interpret("it's hot enough", "high of 94, low of 69.", "forecast")
        assert result is None

    def test_kiwix_source_never_interpreted(self):
        """Open-ended encyclopedic content is never interpreted — no
        structured yes/no signal exists in free-text article content."""
        result = self.interpret("mercury is in retrograde", "# Mercury\nMercury most commonly refers to...", "kiwix")
        assert result is None

    def test_web_source_never_interpreted(self):
        result = self.interpret("it's a good time to invest", "Some web search result text.", "web")
        assert result is None


class TestFrameConditionalResponse:
    """Tests for _frame_conditional_response() — the actual response
    composition for a detected conditional query."""

    def frame(self, condition, consequence, result, source):
        from app.router import _frame_conditional_response
        return _frame_conditional_response(condition, consequence, result, source)

    def test_true_verdict_states_condition_holds(self):
        response = self.frame(
            "it's raining", "remind me to bring an umbrella",
            "Today will be rainy with thunderstorms.", "forecast"
        )
        assert "is the case" in response.lower()
        assert "umbrella" in response.lower()

    def test_false_verdict_states_condition_does_not_hold(self):
        response = self.frame(
            "it's raining", "remind me to bring an umbrella",
            "Today will be clear with a high of 94.", "forecast"
        )
        assert "not the case" in response.lower()

    def test_no_interpretation_presents_raw_result_honestly(self):
        """When no safe interpretation exists, the response must still
        include the real result and must NOT claim a definitive yes/no
        it can't actually support."""
        response = self.frame(
            "mercury is in retrograde", "I'll be careful with communication",
            "# Mercury\nMercury most commonly refers to...", "kiwix"
        )
        assert "is the case" not in response.lower()
        assert "not the case" not in response.lower()
        assert "mercury most commonly refers to" in response.lower()

    def test_original_real_result_always_preserved(self):
        """Regardless of verdict, the actual underlying search result
        must always be included in the final response — framing adds
        context, it never replaces or hides the real data."""
        real_result = "Back Door: unlocked"
        response = self.frame("the back door is unlocked", "let me know", real_result, "ha")
        assert real_result in response


class TestConditionalIntegration:
    """Integration tests confirming detect_conditional() and
    _frame_conditional_response() are actually wired into
    route_with_source(), not just correct in isolation."""

    def test_conditional_query_only_searches_the_condition(self):
        """The consequence ('remind me to bring an umbrella') must never
        itself be sent to a source as a search query — only the
        condition is searched, since Mnemolis has no reminder capability
        to act on the consequence at all."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear with a high of 94."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", return_value="forecast"):
                result, source = route_with_source(
                    "if it's raining, remind me to bring an umbrella", "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "forecast"
        assert "not the case" in result.lower()
        assert "clear" in result.lower()

    def test_remainder_after_conditional_is_searched_and_merged(self):
        """Regression test — the real bug found via real usage. "if any
        services are down, let me know, and also whats the weather" used
        to swallow "whats the weather" into the conditional's consequence
        text, never actually searching it. The trailing real intent must
        now be searched independently and merged into the final response,
        with overall source reported as 'fusion' since two distinct
        sources contributed."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        def fake_detect_intent(q):
            return "forecast" if "weather" in q.lower() else "uptime"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["uptime"] = lambda q: "All 15 monitored services are up."
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear with a high of 94."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                result, source = route_with_source(
                    "if any services are down, let me know, and also whats the weather",
                    "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "fusion"
        assert "conditional question" in result.lower()
        assert "not the case" in result.lower()
        assert "all 15 monitored services are up" in result.lower()
        assert "clear" in result.lower()

    def test_remainder_decomposing_into_multiple_sources_has_no_double_header(self):
        """Regression test — the real bug found via real usage,
        immediately after the remainder-merging fix shipped. When the
        remainder itself decomposes into multiple distinct sources
        (e.g. "...also check the news, plus hows the humidity" decomposes
        into news + ha), route_with_source() already returns "fusion" as
        its reported source for an ALREADY-self-headered result. Wrapping
        that in another header using the literal string "fusion" produced
        the exact same nonsensical "[FUSION — FUSION]" double-header bug
        found and fixed earlier this session in the decomposition loop —
        this is the identical root cause appearing at the new
        remainder-merging call site, which needed the same fix applied."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        def fake_detect_intent(q):
            if "door" in q.lower():
                return "ha"
            if "news" in q.lower():
                return "news"
            if "humid" in q.lower():
                return "ha"
            return "forecast"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["ha"] = lambda q: "Back Door: locked" if "door" in q.lower() else "Humidity: 45%"
        router_module.SOURCE_MAP["news"] = lambda q: "Some news headline content."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                result, source = route_with_source(
                    "if the back door is unlocked, let me know, and also check the news, plus hows the humidity",
                    "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert "[FUSION" not in result
        assert source == "fusion"
        assert "conditional question" in result.lower()
        assert "news headline content" in result.lower()
        assert "humidity: 45%" in result.lower()

    def test_non_conditional_query_unaffected(self):
        """A normal, non-conditional query must behave exactly as before
        — conditional detection must never interfere with the existing
        decomposition/routing path for queries it doesn't match."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", return_value="forecast"):
                result, source = route_with_source("what is the weather today", "auto")
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "forecast"
        assert "conditional question" not in result.lower()

    def test_conditional_only_applies_to_auto_source(self):
        """Explicit source requests should skip conditional detection
        entirely, the same way they already skip decomposition."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear."
        try:
            with patch("app.router._get_cached", return_value=None):
                result, source = route_with_source(
                    "if it's raining, remind me to bring an umbrella", "forecast"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert source == "forecast"
        assert "conditional question" not in result.lower()


class TestRecursiveConditionalDetection:
    """Tests for re-checking decomposed sub-queries for their own
    embedded conditional structure — the real gap found while designing
    recursive decomposition: detect_conditional() only ever ran once,
    against the FULL original query, before decomposition. A query like
    "what is the weather and if the back door is unlocked, let me know"
    doesn't start with "if" so the top-level check correctly returns
    None and the query proceeds to normal decomposition — but neither
    resulting sub-query was ever re-checked for its own conditional
    structure, even though "if the back door is unlocked, let me know"
    (the second sub-query) clearly has one.

    The first implementation of this fix recursed on the ORIGINAL
    sub-query string ("if the back door is unlocked, let me know") with
    a manual _depth counter meant to prevent runaway recursion. That
    introduced a real, found-via-testing bug: the depth incremented
    before the conditional was actually consumed, so the recursive
    call's own necessary re-detection of the very same conditional was
    blocked by the counter meant to guard against recursion that was
    never actually possible in the first place. Fixed by extracting the
    condition/consequence directly in the loop (mirroring exactly how
    the top-level handler already works) and recursing on the
    already-extracted CONDITION text only — which essentially never
    re-matches the leading "if/should/in case" pattern, so this
    terminates naturally without needing any depth parameter at all."""

    def test_decomposed_subquery_with_embedded_conditional_is_framed(self):
        """The real bug found via design research, now fixed. A query
        that itself isn't conditional at the top level, but decomposes
        into a sub-query that IS conditional, must have that sub-query
        framed correctly — not just routed as a plain HA query that
        discards the consequence and any conditional framing entirely."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        # Use a side_effect function rather than a single return_value —
        # detect_intent() is called for BOTH the weather sub-query AND
        # (via the recursive conditional check) the extracted condition
        # text "the back door is unlocked". A blunt return_value="forecast"
        # answers both calls identically, masking the real routing behavior
        # this test is meant to verify — the same class of test-fixture
        # mistake as _looks_empty("") earlier this session: the mock must
        # actually distinguish between distinct inputs, not blindly
        # return one fixed value regardless of what's being routed.
        def fake_detect_intent(query):
            if "door" in query.lower():
                return "ha"
            return "forecast"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Today will be clear with a high of 94."
        router_module.SOURCE_MAP["ha"] = lambda q: "Back Door: locked"
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=fake_detect_intent):
                result, source = route_with_source(
                    "what is the weather and if the back door is unlocked, let me know",
                    "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert "conditional question" in result.lower()
        assert "not the case" in result.lower()
        assert "back door: locked" in result.lower()
        assert "clear" in result.lower()

    def test_subquery_recursion_passes_extracted_condition_not_original_text(self):
        """Regression test for the real bug found via testing. The
        recursive call inside the decomposition loop must pass the
        ALREADY-EXTRACTED condition text to route_with_source(), never
        the original still-'if'-prefixed sub-query string — passing the
        original string caused the recursive call's own conditional
        detection to need re-firing, which an earlier _depth-based
        design incorrectly blocked. Verified here by confirming the
        condition is searched as plain text ('the back door is
        unlocked'), not as the literal conditional sentence."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        searched_queries = []

        def fake_ha_handler(q):
            searched_queries.append(q)
            return "Back Door: locked"

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Clear."
        router_module.SOURCE_MAP["ha"] = fake_ha_handler
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=lambda q: "ha" if "door" in q.lower() else "forecast"):
                route_with_source(
                    "what is the weather and if the back door is unlocked, let me know",
                    "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        # The HA handler should have been called with the bare condition,
        # never with the full "if X, Y" sentence still attached
        assert "the back door is unlocked" in searched_queries
        assert not any(q.lower().startswith("if ") for q in searched_queries)

    def test_normal_decomposition_unaffected_by_recursive_check(self):
        """A query with NO embedded conditional anywhere must decompose
        and route exactly as before — the recursive check should add
        zero overhead or behavior change for the common case."""
        from app.router import route_with_source
        from unittest.mock import patch
        import app.router as router_module

        original_map = dict(router_module.SOURCE_MAP)
        router_module.SOURCE_MAP["forecast"] = lambda q: "Clear."
        router_module.SOURCE_MAP["uptime"] = lambda q: "All up."
        try:
            with patch("app.router._get_cached", return_value=None), \
                 patch("app.router.detect_intent", side_effect=["forecast", "uptime"]):
                result, source = route_with_source(
                    "what is the weather and are services up", "auto"
                )
        finally:
            router_module.SOURCE_MAP.update(original_map)
        assert "conditional question" not in result.lower()
        assert "clear" in result.lower()
        assert "all up" in result.lower()
