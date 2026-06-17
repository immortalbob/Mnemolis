"""
Tests for app/sources/kiwix.py — scoring and search term cleaning.
No network calls required.
"""
import pytest


# ---------------------------------------------------------------------------
# Stop word filtering / search term cleaning
# ---------------------------------------------------------------------------

class TestStem:
    """Tests for the _stem suffix-stripping function."""

    def setup_method(self):
        from app.sources.kiwix import _stem
        self.stem = _stem

    def test_strips_plural_s(self):
        assert self.stem("marsupials") == "marsupial"

    def test_strips_es(self):
        assert self.stem("foxes") == "fox"

    def test_strips_ies(self):
        assert self.stem("batteries") == "battery"

    def test_strips_ing(self):
        assert self.stem("computing") == "comput"

    def test_strips_ed(self):
        assert self.stem("computed") == "comput"

    def test_preserves_short_words(self):
        # Words too short to strip safely
        assert self.stem("as") == "as"
        assert self.stem("is") == "is"

    def test_no_suffix_unchanged(self):
        assert self.stem("nitrogen") == "nitrogen"

    def test_same_stem_for_singular_plural(self):
        assert self.stem("marsupial") == self.stem("marsupials")
        assert self.stem("fox") == self.stem("foxes")
        assert self.stem("battery") == self.stem("batteries")


class TestSearchTermCleaning:
    """Tests for stop word stripping before Kiwix search."""

    def setup_method(self):
        from app.sources.kiwix import _STOP_WORDS
        self.stop_words = _STOP_WORDS

    def _clean(self, query: str) -> str:
        """Replicate the search term cleaning logic from search()."""
        return " ".join(
            w for w in query.lower().split()
            if w not in self.stop_words and len(w) > 1
        ) or query

    def test_strips_what_is(self):
        assert self._clean("what is molybdenum") == "molybdenum"

    def test_strips_how_do_i(self):
        assert self._clean("how do I configure nginx") == "configure nginx"

    def test_strips_tell_me_about(self):
        assert self._clean("tell me about photosynthesis") == "photosynthesis"

    def test_strips_explain(self):
        assert self._clean("explain the water cycle") == "water cycle"

    def test_preserves_meaningful_words(self):
        assert self._clean("nginx reverse proxy configuration") == "nginx reverse proxy configuration"

    def test_falls_back_to_original_if_all_stop_words(self):
        # Query that is entirely stop words should return original
        result = self._clean("what is it")
        assert result == "what is it"

    def test_multi_word_query(self):
        result = self._clean("what is the capital of France")
        assert "france" in result
        assert "what" not in result
        assert "is" not in result
        assert "the" not in result


# ---------------------------------------------------------------------------
# Result scoring
# ---------------------------------------------------------------------------

class TestScoreResult:
    """Tests for _score_result — article relevance scoring."""

    def setup_method(self):
        from app.sources.kiwix import _score_result
        self.score = _score_result

    def _result(self, title: str, excerpt: str = "", book: str = "wikipedia_en_all_maxi_2026-02") -> dict:
        return {"title": title, "excerpt": excerpt, "url": f"http://kiwix/{title}", "book": book}

    def test_exact_title_match_scores_highest(self):
        exact = self._result("Molybdenum")
        partial = self._result("Molybdenum tetrachloride")
        unrelated = self._result("Climax mine")
        query = "molybdenum"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(exact, query, primary) > self.score(partial, query, primary)
        assert self.score(exact, query, primary) > self.score(unrelated, query, primary)

    def test_title_match_beats_excerpt_only(self):
        title_match = self._result("Photosynthesis", excerpt="plants convert light")
        excerpt_only = self._result("Biology overview", excerpt="photosynthesis is the process")
        query = "photosynthesis"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(title_match, query, primary) > self.score(excerpt_only, query, primary)

    def test_primary_book_bonus(self):
        primary_result = self._result("Nginx", book="devdocs_en_nginx_2026-04")
        secondary_result = self._result("Nginx", book="unix.stackexchange.com_en_all_2026-02")
        query = "nginx"
        primary = "devdocs_en_nginx_2026-04"
        assert self.score(primary_result, query, primary) > self.score(secondary_result, query, primary)

    def test_stop_words_dont_inflate_score(self):
        # After stop word removal "what is it" has no meaningful words
        # title "What Is The" has no meaningful words either
        # so only primary book bonus (2) should apply
        result = self._result("What Is The")
        query = "what is it"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(result, query, primary) <= 2

    def test_multi_word_query_scores_better_with_more_hits(self):
        full_match = self._result("Nginx Reverse Proxy Configuration")
        partial_match = self._result("Nginx Overview")
        query = "nginx reverse proxy configuration"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(full_match, query, primary) > self.score(partial_match, query, primary)

    def test_zero_score_for_completely_unrelated(self):
        # Primary book bonus (+2) applies even to unrelated results so minimum is 2
        result = self._result("Ancient Roman Architecture", excerpt="columns and arches")
        query = "molybdenum"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(result, query, primary) <= 2

    def test_excerpt_contributes_to_score(self):
        no_excerpt = self._result("Chemistry overview", excerpt="")
        with_excerpt = self._result("Chemistry overview", excerpt="molybdenum is a transition metal element")
        query = "molybdenum"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(with_excerpt, query, primary) > self.score(no_excerpt, query, primary)

    def test_stemmed_title_match_scores_high(self):
        # "marsupials" query should match "Marsupial" title via stemming
        stemmed = self._result("Marsupial")
        unstemmed = self._result("Marsupials")
        query = "marsupials"
        primary = "wikipedia_en_all_maxi_2026-02"
        # Both should score well — stemmed match (+15) vs exact match (+20)
        assert self.score(stemmed, query, primary) >= 15
        assert self.score(unstemmed, query, primary) >= 20

    def test_stemmed_scores_higher_than_unrelated(self):
        # Stemmed match should beat a completely unrelated article
        marsupial = self._result("Marsupial")
        unrelated = self._result("Ancient Roman Architecture")
        query = "marsupials"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(marsupial, query, primary) > self.score(unrelated, query, primary)

    def test_plural_query_matches_singular_title(self):
        # Plural query word should match singular title word via stemming
        result = self._result("Battery Chemistry", excerpt="batteries store energy")
        query = "batteries"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(result, query, primary) > 0
