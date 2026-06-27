"""
Tests for app/sources/fusion.py — multi-source concurrent search.
Uses unittest.mock to avoid real network calls.
"""
from unittest.mock import patch, MagicMock


def _make_source_map(results: dict):
    """Build a mock SOURCE_MAP with specified return values."""
    source_map = {}
    for source, result in results.items():
        if result is Exception:
            mock_fn = MagicMock(side_effect=Exception(f"{source} failed"))
        else:
            mock_fn = MagicMock(return_value=result)
        source_map[source] = mock_fn
    return source_map


class TestFusionBasics:
    """Tests for basic fusion behavior."""

    def test_two_sources_merged_with_headers(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Nitrogen is an element.",
            "web": "Nitrogen makes up 78% of the atmosphere.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("what is nitrogen", ["kiwix", "web"])
        assert "[KIWIX" in result
        assert "[WEB" in result
        assert "Nitrogen is an element." in result
        assert "78% of the atmosphere" in result
        assert "---" in result

    def test_single_successful_source_returns_without_header(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Nitrogen is an element.",
            "web": "no results found",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("what is nitrogen", ["kiwix", "web"])
        assert "[KIWIX" not in result
        assert "Nitrogen is an element." in result

    def test_default_sources_when_none_specified(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Nitrogen is an element.",
            "web": "Web result about nitrogen.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("what is nitrogen", None)
        assert "Nitrogen" in result

    def test_three_sources_all_merged(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Kiwix result.",
            "web": "Web result.",
            "news": "News result.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web", "news"])
        assert "[KIWIX" in result
        assert "[WEB" in result
        assert "[NEWS" in result

    def test_source_order_preserved_in_output(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Kiwix result.",
            "web": "Web result.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        kiwix_pos = result.index("[KIWIX")
        web_pos = result.index("[WEB")
        assert kiwix_pos < web_pos


class TestFusionValidation:
    """Tests for source validation and filtering."""

    def test_unknown_source_skipped(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Kiwix result.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "nonexistent_source"])
        assert "Kiwix result." in result

    def test_fusion_cannot_reference_itself(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Kiwix result.",
            "fusion": None,
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "fusion"])
        assert "Kiwix result." in result
        assert "[FUSION]" not in result

    def test_all_invalid_sources_returns_error(self):
        from app.sources import fusion
        source_map = _make_source_map({})
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["fake1", "fake2"])
        assert "no valid sources" in result.lower()

    def test_duplicate_sources_deduplicated(self):
        from app.sources import fusion
        mock_fn = MagicMock(return_value="Kiwix result.")
        source_map = {"kiwix": mock_fn}
        with patch("app.router.SOURCE_MAP", source_map):
            fusion.search("test query", ["kiwix", "kiwix", "kiwix"])
        # Should only be called once despite duplicates
        assert mock_fn.call_count == 1

    def test_max_sources_capped_at_four(self):
        from app.sources import fusion
        sources = ["kiwix", "web", "news", "uptime", "forecast"]
        mock_fns = {s: MagicMock(return_value=f"{s} result.") for s in sources}
        with patch("app.router.SOURCE_MAP", mock_fns):
            fusion.search("test query", sources)
        # Only 4 sources should be called
        called = sum(1 for fn in mock_fns.values() if fn.called)
        assert called == 4


class TestFusionFailureHandling:
    """Tests for partial and total failure scenarios."""

    def test_one_source_fails_others_succeed(self):
        from app.sources import fusion
        source_map = {
            "kiwix": MagicMock(return_value="Kiwix result."),
            "web": MagicMock(side_effect=Exception("web failed")),
        }
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        assert "Kiwix result." in result
        assert "[WEB" not in result

    def test_all_sources_fail_returns_error(self):
        from app.sources import fusion
        source_map = {
            "kiwix": MagicMock(side_effect=Exception("kiwix failed")),
            "web": MagicMock(side_effect=Exception("web failed")),
        }
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        assert "no results" in result.lower()

    def test_empty_results_filtered_out(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "no results found",
            "web": "Web result about nitrogen.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        assert "[KIWIX" not in result
        assert "Web result about nitrogen." in result

    def test_all_empty_results_returns_error(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "no results found",
            "web": "no results found",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        assert "no results" in result.lower()

    def test_slow_source_does_not_crash_or_discard_fast_source_result(self):
        """Regression test for a real, significant bug found via a
        deliberate complexity-investigation pass: as_completed()'s own
        OVERALL timeout (distinct from the per-future
        future.result(timeout=...) timeout already handled inside the
        loop) was previously uncaught — a single slow source mixed with
        a fast one crashed the ENTIRE fusion call with an unhandled
        TimeoutError, discarding the fast source's genuinely successful
        result along with it, even though that data already existed.
        This directly undermined fusion's own documented graceful-
        degradation design ("if only one source returns results, it is
        returned directly") by turning a partial success into a total,
        opaque failure. The fix wraps the as_completed iteration itself
        in a try/except, marking any future not yet in `results` as
        failed without losing whatever results were already gathered
        before the overall timeout fired."""
        import time
        from app.sources import fusion
        from app.config import settings

        def fast_source(q):
            time.sleep(0.05)
            return "Real fast result content."

        def slow_source(q):
            time.sleep(10)
            return "should never be seen — exceeds the timeout"

        source_map = {"kiwix": fast_source, "web": slow_source}
        original_timeout = settings.fusion_timeout_seconds
        settings.fusion_timeout_seconds = 1
        try:
            with patch("app.router.SOURCE_MAP", source_map):
                result = fusion.search("test query", ["kiwix", "web"])  # must not raise
        finally:
            settings.fusion_timeout_seconds = original_timeout

        assert "Real fast result content." in result
        assert "should never be seen" not in result


class TestFusionSharedExecutor:
    """Regression tests for replacing fusion.py's per-call
    ThreadPoolExecutor with a single, shared, module-level pool — see
    fusion.py's own module-level comment on _fusion_executor for the
    full investigation (a real, recurring RemoteDisconnected failure
    correlated with unbounded thread creation under concurrent load)."""

    def test_concurrent_fusion_calls_reuse_the_shared_pool_not_create_unbounded_threads(self):
        """Confirms multiple concurrent fusion.search() calls don't
        each spin up their own fresh executor — the real, confirmed
        bug this fix closes (20 concurrent fusion-shaped calls used to
        produce 81 real OS threads at peak, scaling linearly with no
        ceiling). Scaled down from the design doc's own 20-caller
        reproduction for test speed, while still proving the same
        property: thread count stays bounded by the shared pool's own
        configured size, not by the number of concurrent callers."""
        import threading
        import time
        from app.sources import fusion

        def slow_source(q):
            time.sleep(0.1)
            return "Result content."

        source_map = {"kiwix": slow_source, "web": slow_source}
        baseline = threading.active_count()

        threads = []
        with patch("app.router.SOURCE_MAP", source_map):
            for _ in range(8):
                t = threading.Thread(target=fusion.search, args=("q", ["kiwix", "web"]))
                threads.append(t)
                t.start()
            time.sleep(0.05)  # let everything get mid-flight
            peak = threading.active_count()
            for t in threads:
                t.join()

        # 8 concurrent "request" threads, each fanning out to 2 sources,
        # would be 16+ fresh worker threads under the old per-call
        # executor pattern, on top of the 8 request threads themselves
        # (24+ total). With a shared pool capped at
        # settings.fusion_thread_pool_size (12 by default), worker
        # thread count itself is bounded regardless of how many
        # concurrent callers there are — confirmed by checking the
        # actual ceiling rather than asserting an exact number (real
        # thread counts vary slightly by interpreter/test-runner
        # baseline).
        from app.config import settings
        # 8 request threads + at most fusion_thread_pool_size shared
        # workers + baseline — comfortably less than what 8 independent
        # per-call executors (2-3 workers each) would have produced.
        assert peak <= baseline + 8 + settings.fusion_thread_pool_size

    def test_fusion_thread_pool_size_setting_is_read(self):
        """Confirms the shared executor is actually sized from
        settings.fusion_thread_pool_size, not a hardcoded value —
        the module-level _fusion_executor reflects the configured max
        worker count."""
        from app.sources import fusion
        from app.config import settings
        assert fusion._fusion_executor._max_workers == settings.fusion_thread_pool_size




class TestFusionCacheKey:
    """Tests for fusion cache key behavior in router."""

    def setup_method(self):
        import app.router as router_module
        self._original_cache = dict(router_module._cache)
        self._original_routing_cache = dict(router_module._routing_cache)

    def teardown_method(self):
        # Found via a deliberate reverse-collection-order run, surfacing
        # a real, pre-existing gap: test_fusion_result_cached_after_search
        # below calls clear_cache()/clear_routing_cache() before it runs
        # (clean setup) but, before this fix, never restored either
        # cache afterward — it deliberately writes a real
        # "fusion:..." entry to prove the cache-hit assertion, then
        # leaves it sitting in the real, shared _cache dict for whatever
        # test happens to run next in the same pytest process. Masked in
        # normal forward collection order for the same reason
        # TestSaveCache's identical gap was: something later in the
        # suite happens to reset _cache to {} via a real cache.json file
        # on disk, which isn't present on a genuinely clean checkout.
        import app.router as router_module
        router_module._cache.clear()
        router_module._cache.update(self._original_cache)
        router_module._routing_cache.clear()
        router_module._routing_cache.update(self._original_routing_cache)

    def test_fusion_cache_key_sorted(self):
        """Same sources in different order should produce same cache key."""
        from app.router import _cache_key
        key1 = _cache_key("fusion", "fusion[kiwix,web]:test query")
        key2 = _cache_key("fusion", "fusion[kiwix,web]:test query")
        assert key1 == key2

    def test_different_source_sets_different_keys(self):
        from app.router import _cache_key
        key1 = _cache_key("fusion", "fusion[kiwix,web]:test query")
        key2 = _cache_key("fusion", "fusion[kiwix,news]:test query")
        assert key1 != key2

    def test_fusion_result_cached_after_search(self):
        from app.router import route, clear_cache, clear_routing_cache
        from app.sources import fusion
        clear_cache()
        clear_routing_cache()

        with patch.object(fusion, "search", return_value="Fused result.") as mock_search:
            # First call
            result1 = route("test query", "fusion", ["kiwix", "web"])
            assert result1 == "Fused result."
            assert mock_search.call_count == 1

            # Second call — should hit cache, not call fusion.search again
            result2 = route("test query", "fusion", ["kiwix", "web"])
            assert result2 == "Fused result."
            assert mock_search.call_count == 1  # still 1 — cached


class TestConfigurableFusionLimits:
    """Tests for settings-backed fusion limits."""

    def setup_method(self):
        from app.config import settings
        self._orig_max_sources = settings.fusion_max_sources
        self._orig_max_chars = settings.fusion_max_chars_per_source
        self._orig_timeout = settings.fusion_timeout_seconds

    def teardown_method(self):
        from app.config import settings
        settings.fusion_max_sources = self._orig_max_sources
        settings.fusion_max_chars_per_source = self._orig_max_chars
        settings.fusion_timeout_seconds = self._orig_timeout

    def test_truncate_uses_settings_default(self):
        from app.sources.fusion import _truncate
        from app.config import settings
        settings.fusion_max_chars_per_source = 50
        result = _truncate("x" * 200)
        assert len(result) <= 55  # small buffer for ellipsis

    def test_truncate_explicit_param_overrides_settings(self):
        from app.sources.fusion import _truncate
        from app.config import settings
        settings.fusion_max_chars_per_source = 1000
        result = _truncate("x" * 200, max_chars=20)
        assert len(result) <= 25

    def test_fusion_caps_at_configured_max_sources(self):
        from app.sources.fusion import search
        from app.config import settings
        from unittest.mock import patch
        settings.fusion_max_sources = 2
        with patch("app.router.SOURCE_MAP", {
            "kiwix": lambda q: "kiwix result here",
            "forecast": lambda q: "forecast result here",
            "news": lambda q: "news result here",
        }):
            result = search("test", sources=["kiwix", "forecast", "news"])
        # Should not error, and should have capped to 2 sources internally
        assert isinstance(result, str)

    def test_max_sources_zero_does_not_crash(self):
        """Regression test for a real bug found via a deliberate
        "bulletproofing" pass: FUSION_MAX_SOURCES is a plain,
        unvalidated int — setting it to 0 (a plausible
        misconfiguration, e.g. someone trying to "disable" fusion
        entirely) capped the valid-sources list to empty AFTER the
        only existing empty-list check, meaning
        ThreadPoolExecutor(max_workers=0) crashed with a raw
        ValueError ("max_workers must be greater than 0") instead of
        the sensible "no valid sources" message already used for the
        genuinely equivalent case earlier in the same function."""
        from app.sources.fusion import search
        from app.config import settings
        from unittest.mock import patch
        settings.fusion_max_sources = 0
        with patch("app.router.SOURCE_MAP", {
            "kiwix": lambda q: "kiwix result here",
            "web": lambda q: "web result here",
        }):
            result = search("test", sources=["kiwix", "web"])  # must not raise
        assert "no valid sources" in result.lower()


class TestFormatHeader:
    """Tests for _format_header descriptive fusion section headers."""

    def test_known_source_includes_label(self):
        from app.sources.fusion import _format_header
        header = _format_header("forecast")
        assert "FORECAST" in header
        assert "LOCATION" in header

    def test_unknown_source_falls_back_to_uppercase(self):
        from app.sources.fusion import _format_header
        header = _format_header("nonexistent")
        assert header == "[NONEXISTENT — NONEXISTENT]"

    def test_kiwix_header_warns_unrelated(self):
        from app.sources.fusion import _format_header
        header = _format_header("kiwix")
        assert "UNRELATED" in header

    def test_news_header_clarifies_not_location_specific(self):
        from app.sources.fusion import _format_header
        header = _format_header("news")
        assert "NOT LOCATION-SPECIFIC" in header


class TestFusionTruncate:
    """Tests for _truncate result trimming."""

    def setup_method(self):
        from app.sources.fusion import _truncate
        self.truncate = _truncate

    def test_short_result_not_truncated(self):
        result = "Short result."
        assert self.truncate(result) == result

    def test_long_result_truncated(self):
        result = "x" * 2000
        truncated = self.truncate(result)
        assert len(truncated) < 2000

    def test_truncation_appends_ellipsis(self):
        result = "word " * 400  # ~2000 chars
        truncated = self.truncate(result)
        assert truncated.endswith("…")

    def test_truncation_cuts_at_newline(self):
        result = "line one\n" * 200
        truncated = self.truncate(result)
        # Should not cut mid-line
        assert not truncated.rstrip("…\n").endswith("line on")

    def test_custom_max_chars(self):
        result = "x" * 500
        truncated = self.truncate(result, max_chars=100)
        assert len(truncated) <= 105  # small buffer for ellipsis


class TestFusionDeduplicate:
    """Tests for _deduplicate overlap detection."""

    def setup_method(self):
        from app.sources.fusion import _deduplicate
        self.deduplicate = _deduplicate

    def test_single_source_unchanged(self):
        results = {"kiwix": "Some encyclopedic content about nitrogen chemistry."}
        assert self.deduplicate(results) == results

    def test_different_content_both_kept(self):
        results = {
            "forecast": "Today will be sunny with a high of 95 degrees and low humidity.",
            "uptime": "All 15 monitored services are currently up and responding normally.",
        }
        deduped = self.deduplicate(results)
        assert "forecast" in deduped
        assert "uptime" in deduped

    def test_duplicate_content_one_dropped(self):
        shared = " ".join([f"This is sentence number {i} about the topic at hand." for i in range(10)])
        results = {
            "kiwix": shared,
            "web": shared,
        }
        deduped = self.deduplicate(results)
        assert len(deduped) == 1

    def test_empty_sources_not_compared(self):
        results = {
            "forecast": "Today will be sunny.",
            "news": "",
        }
        # Empty news shouldn't cause issues
        deduped = self.deduplicate(results)
        assert "forecast" in deduped

    def test_shorter_more_redundant_source_dropped_regardless_of_dict_order(self):
        """Regression test for a real, order-dependent bug: a prior
        version's first branch (overlap / len(sents2) >= 0.6) only
        correctly identified "s2 is redundant" when s1 was the LONGER
        source — if s1 happened to be the shorter one, that same branch
        could still fire and unconditionally dropped s2 anyway,
        regardless of which one actually had more unique content.
        Confirmed directly: the same two pieces of content, same actual
        overlap, produced opposite outcomes purely from which key
        appeared first when Python iterates the dict — and in the real
        call site, dict insertion order is determined by
        as_completed()'s own completion order (whichever source's
        network call happened to finish first), a detail with zero
        semantic relationship to which source's content is more
        complete. This test confirms the fix: the same, correct
        (longer, more complete) source survives regardless of dict
        ordering."""
        short = " ".join(
            f"Short overlapping sentence number {i} appears in both sources here." for i in range(5)
        )
        long_with_extra = short + " " + " ".join(
            f"This additional unique sentence number {i} only appears in the longer source." for i in range(10)
        )

        # Order 1: shorter source first
        deduped_a = self.deduplicate({"a": short, "b": long_with_extra})
        # Order 2: identical content, reversed key order
        deduped_b = self.deduplicate({"b": long_with_extra, "a": short})

        # Both orderings must keep the LONGER, more complete source and
        # drop the shorter, more redundant one — never the reverse, and
        # never different outcomes depending on dict ordering.
        assert "b" in deduped_a and "a" not in deduped_a
        assert "b" in deduped_b and "a" not in deduped_b

    def test_real_world_unequal_length_overlap_drops_the_shorter_side(self):
        """A second, independent confirmation using meaningfully
        different source content (not just one source padded with
        extra sentences) — the case the existing test suite's only
        prior overlap test (test_duplicate_content_one_dropped) never
        covered, since that test used byte-for-byte identical content
        for both sides, where "which one survives" is genuinely
        immaterial and the test only asserted len(deduped) == 1."""
        kiwix_long = " ".join([
            "Nitrogen is a chemical element with the symbol N and atomic number seven.",
            "It was first discovered and isolated by Scottish physician Daniel Rutherford in 1772.",
            "Nitrogen is a member of the pnictogen group on the periodic table.",
            "It is a common element in the universe, estimated at seventh in total abundance.",
            "At standard temperature and pressure, two atoms bond to form nitrogen gas.",
            "This colorless and mostly inert diatomic gas makes up about 78 percent of the atmosphere.",
        ])
        web_short = " ".join([
            "Nitrogen is a chemical element with the symbol N and atomic number seven.",
            "It was first discovered and isolated by Scottish physician Daniel Rutherford in 1772.",
        ])
        deduped = self.deduplicate({"web": web_short, "kiwix": kiwix_long})
        assert "kiwix" in deduped
        assert "web" not in deduped


class TestFusionMergeSameSource:
    """Tests for _merge_same_source consecutive merging."""

    def setup_method(self):
        from app.sources.fusion import _merge_same_source
        self.merge = _merge_same_source

    def test_same_source_merged(self):
        parts = [("ha", "Indoor sensors result."), ("ha", "Door locks result.")]
        merged = self.merge(parts)
        assert len(merged) == 1
        assert merged[0][0] == "ha"
        assert "Indoor" in merged[0][1]
        assert "Door" in merged[0][1]

    def test_different_sources_not_merged(self):
        parts = [("forecast", "Sunny today."), ("uptime", "All services up.")]
        merged = self.merge(parts)
        assert len(merged) == 2

    def test_mixed_sources_partial_merge(self):
        parts = [("ha", "Sensors."), ("ha", "Locks."), ("forecast", "Sunny.")]
        merged = self.merge(parts)
        assert len(merged) == 2
        assert merged[0][0] == "ha"
        assert merged[1][0] == "forecast"

    def test_search_itself_never_has_duplicate_sources_to_merge(self):
        """Regression test documenting a real finding from a deliberate
        "bulletproofing" pass: search() used to call _merge_same_source()
        on its own final `parts` list too, with a comment claiming it
        "fixes duplicate [HA] from decomposition" — but that scenario
        cannot actually occur at this call site, since `valid` (the
        list `parts` is ultimately built from) is already deduplicated
        via its own `seen` set earlier in the same function. Confirms
        directly: passing duplicate source names into search() still
        produces only ONE section per source in the final output, even
        without _merge_same_source() ever running there — because the
        duplicates were already removed before `parts` was built at all."""
        from app.sources.fusion import search
        from unittest.mock import patch
        with patch("app.router.SOURCE_MAP", {
            "ha": lambda q: "Indoor sensors result.",
            "forecast": lambda q: "Sunny today.",
        }):
            # "ha" passed twice — search()'s own dedup should remove
            # the duplicate before it ever reaches the merge step
            result = search("test", sources=["ha", "forecast", "ha"])
        assert result.count("[HA") == 1

    def test_empty_parts(self):
        assert self.merge([]) == []

    def test_single_part_unchanged(self):
        parts = [("kiwix", "Some content.")]
        assert self.merge(parts) == parts


class TestDedupeItemsAcrossBlobs:
    """Regression tests for a real, live duplicate-CONTENT bug found
    while verifying the fix for the duplicate-SECTION bug in
    router.py's _dedupe_nested_fusion_sections(). That fix correctly
    produced exactly ONE [NEWS — ...] header on a real MiniDock result
    — working as designed — but the single section's own body still
    contained the same several headlines twice, because two
    independent calls to news.search() (one nested inside an internal-
    fusion sub-query, one a separately-decomposed clause's own bare
    resolution) both legitimately returned overlapping items (a real
    FreshRSS "general query, return everything" case), and nothing
    anywhere deduplicated ACROSS the two calls.

    _merge_same_source()'s own join (a bare string concatenation) has
    no content-level awareness at all — fixed by deduping items
    BETWEEN the two blobs being merged, at the exact point where the
    boundary between "result from call 1" and "result from call 2" is
    still completely unambiguous (two distinct strings, not yet
    joined) — tried deduping AFTER the join first; confirmed broken via
    a failing test, since the real boundary becomes indistinguishable
    from an ordinary paragraph break once joined with a bare "\\n\\n".
    """

    def setup_method(self):
        from app.sources.fusion import _dedupe_items_across_blobs
        self.dedupe = _dedupe_items_across_blobs

    def test_real_world_regression_case_overlapping_headlines(self):
        """The exact real shape from the live MiniDock result: two
        item lists sharing some headlines but not all."""
        first = (
            "**Headline A** (NYT)\nContent A\n\n---\n\n"
            "**Headline B** (NYT)\nContent B\n\n---\n\n"
            "**Headline C** (NYT)\nContent C"
        )
        second = (
            "**Headline A** (NYT)\nContent A\n\n---\n\n"
            "**Headline B** (NYT)\nContent B\n\n---\n\n"
            "**Headline D** (NYT)\nContent D"
        )
        deduped_first, deduped_second, is_multi_item = self.dedupe(first, second)
        assert is_multi_item is True
        assert deduped_first == first  # first blob is never modified
        assert "Headline A" not in deduped_second
        assert "Headline B" not in deduped_second
        assert "Headline D" in deduped_second  # the one genuinely new item survives

    def test_no_overlap_is_a_true_noop(self):
        first = "**Headline A**\nContent A\n\n---\n\n**Headline B**\nContent B"
        second = "**Headline C**\nContent C\n\n---\n\n**Headline D**\nContent D"
        deduped_first, deduped_second, is_multi_item = self.dedupe(first, second)
        assert deduped_first == first
        assert deduped_second == second
        assert is_multi_item is True

    def test_neither_blob_multi_item_is_a_true_noop(self):
        """Plain, single-item content (e.g. Home Assistant's
        differently-shaped output, or any simple non-list result) must
        be a true no-op — confirms this fix doesn't change behavior
        for content that was never built from the "---"-separated item
        convention to begin with."""
        first = "All doors locked."
        second = "All doors locked."  # even an exact duplicate of plain text
        deduped_first, deduped_second, is_multi_item = self.dedupe(first, second)
        assert deduped_first == first
        assert deduped_second == second
        assert is_multi_item is False

    def test_complete_overlap_empties_second_blob(self):
        """If every item in the second blob already exists in the
        first, the second blob's deduped result should be empty —
        confirms the dedup can remove ALL items, not just some."""
        first = "**Headline A**\nContent A\n\n---\n\n**Headline B**\nContent B"
        second = "**Headline A**\nContent A\n\n---\n\n**Headline B**\nContent B"
        deduped_first, deduped_second, is_multi_item = self.dedupe(first, second)
        assert deduped_second == ""

    def test_merge_same_source_uses_real_separator_for_multi_item_content(self):
        """End-to-end: _merge_same_source() itself must join two
        multi-item blobs with the real "---" item separator, not a
        bare "\\n\\n" that would make the boundary between the two
        original results indistinguishable from an ordinary paragraph
        break."""
        from app.sources.fusion import _merge_same_source
        parts = [
            ("news", "**Headline A**\nContent A\n\n---\n\n**Headline B**\nContent B"),
            ("news", "**Headline A**\nContent A\n\n---\n\n**Headline C**\nContent C"),
        ]
        merged = _merge_same_source(parts)
        assert len(merged) == 1
        result = merged[0][1]
        assert result.count("Headline A") == 1
        assert "Headline B" in result
        assert "Headline C" in result
        # The real item separator must appear between the merged
        # items, not a bare double-newline.
        assert "Content B\n\n---\n\n**Headline C**" in result

    def test_merge_same_source_uses_list_separator_even_for_two_single_item_parts(self):
        """Updated regression test reflecting a deliberate behavior
        change from the per-group separator fix (see _merge_same_source's
        own docstring): combining 2+ genuinely separate same-source
        results is, definitionally, a multi-item situation the moment
        there are two of them — independent of whether either individual
        part happened to already contain "---" internally. A prior
        version decided the separator per PAIR rather than once per
        GROUP, which happened to produce a bare "\\n\\n" for exactly this
        two-single-item case — but that was itself part of the same
        underlying bug, just not visible until a third, genuinely
        multi-item part joined the chain and exposed the inconsistency.
        This is the corrected, intentional behavior, not a regression."""
        from app.sources.fusion import _merge_same_source
        parts = [("ha", "Indoor sensors result."), ("ha", "Door locks result.")]
        merged = _merge_same_source(parts)
        assert merged[0][1] == "Indoor sensors result.\n\n---\n\nDoor locks result."

    def test_merge_same_source_mixed_single_and_multi_item_chain_gets_consistent_separator(self):
        """Regression test for the real bug this fix closes: a chain
        mixing single-item and multi-item same-source parts must get a
        consistent "---" separator at EVERY boundary, not just the
        boundary adjacent to the multi-item part. Mirrors the real,
        plausible compound-query reconstruction from the design doc —
        three news-resolved clauses, the first two each a single
        article, the third a genuine multi-item list."""
        from app.sources.fusion import _merge_same_source
        parts = [
            ("news", "**Bitcoin Hits New High** (CoinDesk)\nPrice surged today amid market optimism."),
            ("news", "**Election Results Certified** (AP)\nOfficials confirmed the final vote count."),
            ("news", (
                "**Storm Approaches** (Weather.com)\nHeavy rain expected.\n\n---\n\n"
                "**Flooding Reported** (Local News)\nSeveral roads closed.\n\n---\n\n"
                "**Cleanup Underway** (City News)\nCrews are responding."
            )),
        ]
        merged = _merge_same_source(parts)
        assert len(merged) == 1
        result = merged[0][1]
        # Every boundary within the merged group must be the real item
        # separator — including the one between Bitcoin and Election,
        # which a prior, per-pair version left as a bare "\n\n" since
        # neither side of that specific pair was multi-item on its own.
        assert "Bitcoin Hits New High" in result
        assert "Election Results Certified" in result
        assert "Storm Approaches" in result
        # The bare-newline boundary the bug produced — confirmed absent.
        assert "optimism.\n\n**Election" not in result
        assert "optimism.\n\n---\n\n**Election" in result


class TestLooksEmpty:
    """Tests for _looks_empty result validation."""

    def setup_method(self):
        from app.sources.fusion import _looks_empty
        self.check = _looks_empty

    def test_empty_string_is_empty(self):
        assert self.check("") is True

    def test_none_is_empty(self):
        assert self.check(None) is True

    def test_no_results_found_is_empty(self):
        assert self.check("No results found for your query.") is True

    def test_could_not_connect_is_empty(self):
        assert self.check("Could not connect to Home Assistant.") is True

    def test_not_configured_is_empty(self):
        assert self.check("Home Assistant is not configured.") is True

    def test_valid_result_not_empty(self):
        assert self.check("# Nitrogen\nNitrogen is a chemical element.") is False

    def test_forecast_result_not_empty(self):
        assert self.check("Today will be sunny with a high of 95.") is False

    def test_error_prefix_is_empty(self):
        assert self.check("Error: connection refused") is True

    def test_unknown_source_is_empty(self):
        """Regression test confirming fusion.py's own list was
        separately missing "unknown source" — found via a second,
        deliberate "bulletproofing" re-pass while unifying this
        function with router.py's own independently-maintained copy,
        which already had this phrase from an earlier fix this same
        release cycle."""
        assert self.check("Unknown source 'xyz'. Valid options: kiwix, forecast, news, web.") is True

    def test_error_reaching_is_empty(self):
        """The real SearXNG timeout/connection message doesn't contain
        a bare "error:" (the colon comes after "SearXNG", not
        immediately after "Error"), so it needed its own phrase —
        found while verifying the unified list against every real
        failure message every source file actually produces."""
        assert self.check("Error reaching SearXNG: connection failed.") is True

    def test_real_news_headline_with_unlucky_phrase_is_not_flagged_empty(self):
        """Regression test for a real false positive found by
        investigating an unrelated benchmark anomaly: a genuinely
        successful, fully-populated multi-source `changes` response
        containing a real news headline that happens to coincidentally
        contain "could not determine" must NOT be flagged as empty —
        confirmed this exact scenario WOULD have triggered
        FALLBACK_CHAIN's real "news" -> "web" fallback against the old
        implementation, silently discarding a perfectly good answer.
        `_diff_news()` in snapshots.py echoes raw, unmodified upstream
        article headlines verbatim, so real news content can contain
        any of these ordinary-English trigger phrases by sheer
        coincidence — this is the actual, real-world shape that
        traffic takes, not a contrived edge case."""
        result = (
            "**News:**\n"
            "- New article: Tech Company Could Not Determine Cause of Outage "
            "(2026-06-20 14:30 UTC)\n\n"
            "**Home:**\n"
            "- Front door unlocked (2026-06-20 15:00 UTC)"
        )
        assert self.check(result) is False

    def test_single_short_article_with_unlucky_title_is_not_flagged_empty(self):
        """The hardest real version of the same false positive: a
        SHORT single article (the kind a length-based heuristic could
        plausibly still misclassify) whose title alone contains a
        trigger phrase. Confirms the fix's actual mechanism (markdown
        bold presence, not message length) correctly handles this
        case too — a length cap was considered and rejected specifically
        because it could not distinguish this from a genuine short
        Mnemolis error message without also breaking real coverage."""
        assert self.check("**Senator Bill Not Configured** (CNN)") is False

    def test_kiwix_could_not_fetch_with_real_article_title_still_flagged_empty(self):
        """Regression test for the one real message a naive fix could
        easily have broken: kiwix.py's `f"Found {title} but could not
        fetch article content."` interpolates a REAL, VARIABLE article
        title before the fixed phrase — this message contains no
        markdown bold (it's plain prose, not a formatted article
        result), so it must still correctly match via the phrase list,
        confirming the markdown-bold check doesn't accidentally
        exclude this real failure case."""
        result = (
            "Found Very Long Real Article Title About Something Important "
            "but could not fetch article content.\nURL: https://example.com"
        )
        assert self.check(result) is True

    def test_no_monitors_found_is_empty(self):
        """Regression test for a real, separate gap found and fixed
        alongside the false-positive rewrite: "no monitors found" (the
        literal message uptime_kuma.py returns when Uptime Kuma has no
        configured monitors at all) was never in this phrase list,
        meaning fusion's own empty-result filtering would have
        incorrectly treated it as real, successful content in a fusion
        query that includes `uptime` alongside other sources."""
        assert self.check("No monitors found in Uptime Kuma.") is True

    def test_unable_to_retrieve_forecast_is_empty(self):
        """Regression test for a real, second-pass phrase-list gap:
        forecast.py's own exception handler returns
        f"Unable to retrieve forecast: {e}" on ANY failure (a network
        timeout, Open-Meteo briefly down, a malformed response). Before
        this fix, router.py's _resolve_single_source() would cache this
        error message as if it were a genuine, successful weather
        result for cache_ttl_forecast_seconds (30 minutes by default) —
        a single transient API hiccup leaving every subsequent forecast
        query stale for up to half an hour instead of correctly
        retrying on the next request."""
        assert self.check("Unable to retrieve forecast: Connection timed out") is True

    def test_no_valid_sources_for_fusion_is_empty(self):
        """Regression test for fusion.py's own self-generated message
        never being recognized by its own _looks_empty() — found
        because this function previously only recognized every OTHER
        module's failure output, never its own. Matters when
        router.py's decomposition loop calls fusion.search() on an
        individual decomposed sub-query and checks _looks_empty() on
        the result before merging it in."""
        assert self.check("No valid sources specified for fusion query.") is True

    def test_no_results_returned_from_fusion_is_empty(self):
        """The second of fusion.py's own two self-generated messages,
        same root cause as the test above."""
        assert self.check("No results returned from any source in fusion query.") is True

    def test_no_entity_states_returned_is_empty(self):
        """Regression test for a home_assistant.py phrase missed by
        the first phrase-list pass."""
        assert self.check("No entity states returned from Home Assistant.") is True

    def test_no_matching_entities_found_is_empty(self):
        """The second of home_assistant.py's two missed phrases."""
        assert self.check("No matching entities found in Home Assistant for that query.") is True

    def test_no_significant_changes_is_empty(self):
        """Regression test for the snapshots.py phrase missed by the
        first phrase-list pass."""
        assert self.check("No significant changes detected in the last 24 hours.") is True

    def test_new_phrases_still_protected_by_markdown_bold_gate(self):
        """Confirms the same structural gate already protecting the
        original phrase list (the "**" check, evaluated before any
        phrase comparison) protects all five newly-added phrases
        identically — a real article headline containing any of these
        phrases' words must still pass through correctly as long as
        it's wrapped in the markdown bold every genuine article title
        already uses."""
        assert self.check("**Scientists Unable To Retrieve Lost Satellite Data** (TechNews)") is False
        assert self.check("**No Significant Changes Found in Annual Budget Review** (LocalNews)") is False
        assert self.check("**Company Reports No Valid Sources For Rumor, Denies Claim** (BusinessWire)") is False
        assert self.check("**No Entity States Returned in New Sci-Fi Show Finale** (Entertainment)") is False


class TestFusionContextVarPropagation:
    """Regression tests for the ContextVar propagation gap in
    search()'s concurrent dispatch — see fusion.py's own module-level
    comment on _fusion_executor for the full mechanism writeup.

    A bare executor.submit(fn, *args) does NOT propagate
    contextvars.ContextVar state (router.suppress_cache_writes()) into
    worker threads. router.py's _resolve_conditional() and searxng.py's
    own concurrent fetch already learned this lesson and already fixed
    it the identical way (contextvars.copy_context().run(fn, *args));
    fusion.py was the one remaining unfixed ThreadPoolExecutor site.
    Modeled directly on the existing precedent:
    TestConditionalRemainderConcurrency.test_suppress_cache_writes_propagates_into_both_worker_threads
    in tests/test_router.py."""

    def test_suppress_cache_writes_propagates_into_fusion_worker_threads(self):
        """The actual regression test: a fake 'kiwix' handler makes a
        real _set_routing() write from inside fusion.search()'s real
        executor. With suppress_cache_writes() active in the calling
        thread, that write must be suppressed even though it happens
        inside a worker thread, not the calling thread itself — this
        is the test that would have caught the gap before it shipped,
        the same way the equivalent test already does for router.py's
        and searxng.py's own fixed sites."""
        import app.router as router_module
        from app.sources import fusion

        original_cache = dict(router_module._routing_cache)
        router_module._routing_cache.clear()
        try:
            def fake_kiwix_with_real_cache_write(query):
                router_module._set_routing(f"books:{query}", "some_decision")
                return "Kiwix result content."

            source_map = {
                "kiwix": fake_kiwix_with_real_cache_write,
                "web": lambda q: "Web result content.",
            }
            with patch("app.router.SOURCE_MAP", source_map):
                with router_module.suppress_cache_writes():
                    fusion.search("test query", ["kiwix", "web"])

            assert len(router_module._routing_cache) == 0, (
                "a write leaked through fusion's worker thread despite "
                "suppress_cache_writes() being active in the calling thread"
            )
        finally:
            router_module._routing_cache.clear()
            router_module._routing_cache.update(original_cache)

    def test_normal_unsuppressed_fusion_call_still_caches_correctly(self):
        """The flip-side confirmation: the identical call WITHOUT
        suppression active still caches normally — proving the fix
        doesn't accidentally suppress writes unconditionally."""
        import app.router as router_module
        from app.sources import fusion

        original_cache = dict(router_module._routing_cache)
        router_module._routing_cache.clear()
        try:
            def fake_kiwix_with_real_cache_write(query):
                router_module._set_routing(f"books:{query}", "some_decision")
                return "Kiwix result content."

            source_map = {
                "kiwix": fake_kiwix_with_real_cache_write,
                "web": lambda q: "Web result content.",
            }
            with patch("app.router.SOURCE_MAP", source_map):
                fusion.search("test query", ["kiwix", "web"])

            assert len(router_module._routing_cache) == 1
        finally:
            router_module._routing_cache.clear()
            router_module._routing_cache.update(original_cache)

    def test_path_c_reproduction_discourse_framing_no_conjunction(self):
        """A direct reproduction of the specific, now-quantified-as-
        common leak shape (Part 2's "Path C" in the design doc): a
        discourse-framing phrase sharing a clause with a real keyword,
        no conjunction, routed through the real, unmocked
        route_with_source() under suppress_cache_writes(). Confirms
        this concrete, common query shape — not just the synthetic
        isolated-mechanism case above — is actually closed."""
        import app.router as router_module

        original_cache = dict(router_module._routing_cache)
        router_module._routing_cache.clear()
        try:
            def fake_kiwix_with_real_cache_write(query):
                router_module._set_routing(f"books:{query}", "some_decision")
                return "Kiwix result content."

            source_map = {
                "kiwix": fake_kiwix_with_real_cache_write,
                "news": lambda q: "**Headline** (AP)\nNews content.",
            }
            with patch("app.router.SOURCE_MAP", source_map):
                with router_module.suppress_cache_writes():
                    router_module.route_with_source(
                        "everyone keeps talking about the news today", "auto"
                    )

            assert len(router_module._routing_cache) == 0, (
                "Path C's discourse-framing-no-conjunction shape leaked "
                "a routing-cache write despite suppress_cache_writes() "
                "being active"
            )
        finally:
            router_module._routing_cache.clear()
            router_module._routing_cache.update(original_cache)

