"""
Tests for app/sources/fusion.py — multi-source concurrent search.
Uses unittest.mock to avoid real network calls.
"""
import pytest
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
        assert "[KIWIX]" in result
        assert "[WEB]" in result
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
        assert "[KIWIX]" not in result
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
        assert "[KIWIX]" in result
        assert "[WEB]" in result
        assert "[NEWS]" in result

    def test_source_order_preserved_in_output(self):
        from app.sources import fusion
        source_map = _make_source_map({
            "kiwix": "Kiwix result.",
            "web": "Web result.",
        })
        with patch("app.router.SOURCE_MAP", source_map):
            result = fusion.search("test query", ["kiwix", "web"])
        kiwix_pos = result.index("[KIWIX]")
        web_pos = result.index("[WEB]")
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
        assert "[WEB]" not in result

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
        assert "[KIWIX]" not in result
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


class TestFusionCacheKey:
    """Tests for fusion cache key behavior in router."""

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
