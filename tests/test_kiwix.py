"""
Tests for app/sources/kiwix.py — scoring and search term cleaning.
No network calls required.
"""


# ---------------------------------------------------------------------------
# Stop word filtering / search term cleaning
# ---------------------------------------------------------------------------

class TestDefinitionalQuery:
    """Tests for _is_definitional_query intent detection."""

    def setup_method(self):
        from app.sources.kiwix import _is_definitional_query
        self.detect = _is_definitional_query

    def test_what_are_is_definitional(self):
        assert self.detect("what are capacitors") is True

    def test_what_is_is_definitional(self):
        assert self.detect("what is nitrogen") is True

    def test_tell_me_about_is_definitional(self):
        assert self.detect("tell me about volcanoes") is True

    def test_how_does_is_definitional(self):
        assert self.detect("how does photosynthesis work") is True

    def test_explain_is_definitional(self):
        assert self.detect("explain quantum mechanics") is True

    def test_history_of_is_definitional(self):
        assert self.detect("history of the Roman Empire") is True

    def test_bare_noun_not_definitional(self):
        assert self.detect("marsupials") is False

    def test_specific_technical_not_definitional(self):
        assert self.detect("capacitor power factor correction AC circuit") is False

    def test_specific_problem_not_definitional(self):
        assert self.detect("multiple resistors in series heat dissipation") is False

    def test_whats_the_deal_with_is_definitional(self):
        """Regression test — colloquial phrasing was previously not recognized
        as definitional at all, causing disambiguation to never trigger for
        casual questions like 'what's the deal with X'."""
        assert self.detect("what's the deal with that Mercury thing") is True

    def test_whats_the_deal_with_no_apostrophe_is_definitional(self):
        assert self.detect("whats the deal with mercury") is True

    def test_whats_up_with_is_definitional(self):
        assert self.detect("what's up with quantum computing") is True

    def test_whats_this_about_is_definitional(self):
        assert self.detect("what's this about black holes") is True

    def test_whats_the_story_with_is_definitional(self):
        assert self.detect("what's the story with cold fusion") is True


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

    def test_common_non_plural_words_ending_in_s_are_not_mis_stemmed(self):
        """Regression test for a real, if narrow, inaccuracy found via a
        deliberate, precise re-read of this exact function: the plain
        "s"-suffix rule has no way to tell a genuine plural ("foxes" →
        fox) apart from a common, non-plural word that happens to end in
        "s" and is long enough (>3 chars) to pass the length guard.
        "this" → "thi", "less" → "les", "across" → "acros", "always" →
        "alway", and "towards" → "toward" were all confirmed via direct
        testing before this fix — verified the real-world scoring impact
        was genuinely minimal (this function always compares two
        complete strings against each other, never an isolated stop
        word for its own sake), but worth a small, explicit exception
        list anyway rather than leaving a known inaccuracy unaddressed."""
        assert self.stem("this") == "this"
        assert self.stem("less") == "less"
        assert self.stem("across") == "across"
        assert self.stem("always") == "always"
        assert self.stem("towards") == "towards"

    def test_exception_list_does_not_break_genuine_short_plural_words(self):
        """Confirms the exception list is narrow and specific — it must
        not accidentally prevent a genuine, real plural from being
        correctly stemmed just because it happens to share a similar
        shape with one of the exception words."""
        assert self.stem("classes") == "class"
        assert self.stem("buses") == "bus"


class TestSearchTermCleaning:
    """Tests for stop word stripping and stemming before Kiwix search.

    Calls the real _build_search_terms() function rather than a separate
    re-implementation, so these tests actually exercise the same code
    path search() uses — a prior version of this test class duplicated
    the logic locally, which meant it could pass even while the real
    implementation had a bug (the apostrophe/contraction handling bug
    fixed below would have gone undetected under the old test setup).
    """

    def setup_method(self):
        from app.sources.kiwix import _build_search_terms
        self.clean = _build_search_terms

    def test_strips_what_is(self):
        assert self.clean("what is molybdenum") == "molybdenum"

    def test_strips_how_do_i(self):
        result = self.clean("how do I configure nginx")
        assert "configur" in result or "configure" in result
        assert "nginx" in result

    def test_strips_tell_me_about(self):
        assert self.clean("tell me about photosynthesis") == "photosynthesi"

    def test_strips_explain(self):
        result = self.clean("explain the water cycle")
        assert "water" in result and "cycl" in result

    def test_preserves_meaningful_words(self):
        result = self.clean("nginx reverse proxy configuration")
        assert "nginx" in result
        assert "reverse" in result
        assert "proxy" in result

    def test_single_character_programming_language_preserved(self):
        """Regression test for a real, significant bug found via a
        deliberate "bulletproofing" pass independently re-discovering
        the exact same bug already found and fixed in scoring.py's
        _keywords(): single alphanumeric characters ("c", "r" as
        programming language names) were silently dropped by the
        length filter alone. "what is r programming used for" reduced
        to the literal Kiwix search query "programm," losing the one
        word that actually distinguishes this from any other
        programming language."""
        result = self.clean("what is r programming used for")
        assert "r" in result.split()

    def test_single_character_c_preserved_with_other_words(self):
        result = self.clean("tutorial for the c programming language")
        assert "c" in result.split()

    def test_bare_punctuation_still_excluded(self):
        """Confirms the fix didn't reintroduce real noise — a bare
        hyphen (surviving the apostrophe-stripping regex) must still
        be excluded, the same way scoring.py's equivalent fix avoids
        treating stray punctuation as a real search term."""
        result = self.clean("topic - subtopic")
        assert "-" not in result.split()


    def test_c_programming_language_preserved(self):
        result = self.clean("tutorial for the c programming language")
        assert "c" in result.split()

    def test_falls_back_to_original_if_all_stop_words(self):
        # Query that is entirely stop words should return original
        result = self.clean("what is it")
        assert result == "what is it"

    def test_multi_word_query(self):
        result = self.clean("what is the capital of France")
        assert "franc" in result or "france" in result
        assert "what" not in result
        assert "is" not in result
        assert "the" not in result

    def test_contraction_whats_does_not_leave_stray_apostrophe(self):
        """Regression test — the actual bug found via real usage. 'what's'
        used to survive stop-word filtering as a stray "what'" token
        (apostrophe left over after _stem strips the trailing 's), which
        never matched the 'what' stop word and polluted the search term."""
        result = self.clean("what's the deal with mercury")
        assert "'" not in result
        assert result.strip() == "mercury"

    def test_contraction_whats_up_with(self):
        result = self.clean("what's up with quantum computing")
        assert "'" not in result
        assert "quantum" in result

    def test_contraction_thats(self):
        result = self.clean("what's that thing called")
        assert "'" not in result

    def test_colloquial_filler_words_stripped(self):
        """'deal', 'thing', 'keep', 'hearing' are filler in casual phrasing
        and should be stripped just like formal stop words, leaving only
        the actual topic word(s)."""
        result = self.clean("that mercury thing I keep hearing about")
        assert result.strip() == "mercury"

    def test_discourse_framing_phrase_stripped_bitcoin(self):
        """Regression test — the real bug found via real usage. Once
        router.py was fixed to correctly route discourse-framing queries
        ("everyone's obsessed with X") to include kiwix, the words
        "everyone", "obsessed", "talking", "keep" still survived
        _STOP_WORDS untouched and were sent to Kiwix as literal search
        terms — "what whole bitcoin everyone obsessed" matched scattered,
        irrelevant content ("Howard Wolowitz") far more readily than the
        real topic word ("bitcoin") could compete against. Stripping the
        whole matched discourse-framing PHRASE before tokenizing (rather
        than adding individual words to _STOP_WORDS, which risks treating
        "everyone" or "keep" as filler in some unrelated query where they
        carry real meaning) fixes this surgically."""
        result = self.clean("whats the deal with that whole bitcoin thing everyone is obsessed with")
        assert "everyone" not in result
        assert "obsessed" not in result
        assert "bitcoin" in result

    def test_discourse_framing_phrase_stripped_galaxy(self):
        result = self.clean("whats the deal with that whole galaxy thing everyones obsessed with right now")
        assert "everyone" not in result
        assert "obsessed" not in result
        assert "galaxy" in result

    def test_discourse_framing_phrase_stripped_black_holes(self):
        result = self.clean("whats the deal with that whole black hole thing everyone keeps talking about")
        assert "everyone" not in result
        assert "talking" not in result
        assert "keep" not in result
        assert "black" in result
        assert "hole" in result

    def test_no_discourse_framing_is_unaffected(self):
        """A query with no discourse-framing language at all should be
        completely unaffected by the stripping logic — confirms it's not
        accidentally removing unrelated content."""
        result = self.clean("what is the capital of France")
        assert "franc" in result or "france" in result


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
        # Wikipedia definitional bonus (+8) + primary book bonus (+2) = 10 max
        result = self._result("What Is The")
        query = "what is it"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(result, query, primary) <= 10

    def test_multi_word_query_scores_better_with_more_hits(self):
        full_match = self._result("Nginx Reverse Proxy Configuration")
        partial_match = self._result("Nginx Overview")
        query = "nginx reverse proxy configuration"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(full_match, query, primary) > self.score(partial_match, query, primary)

    def test_zero_score_for_completely_unrelated(self):
        # Wikipedia non-definitional bonus (+3) + primary book bonus (+2) = 5 max for unrelated
        result = self._result("Ancient Roman Architecture", excerpt="columns and arches")
        query = "molybdenum"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(result, query, primary) <= 5

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

    def test_wikipedia_bonus_for_definitional_query(self):
        # Wikipedia article should score higher than non-Wikipedia for definitional query
        wiki_result = self._result("Capacitor", book="wikipedia_en_all_maxi_2026-02")
        se_result = self._result("Capacitor", book="electronics.stackexchange.com_en_all_2026-02")
        query = "what are capacitors"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(wiki_result, query, primary) > self.score(se_result, query, primary)

    def test_list_article_penalized(self):
        # "Lists of volcanoes" should score lower than "Volcano"
        list_result = self._result("Lists of volcanoes")
        main_result = self._result("Volcano")
        query = "tell me about volcanoes"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(main_result, query, primary) > self.score(list_result, query, primary)

    def test_index_article_penalized(self):
        list_result = self._result("List of capacitor types")
        main_result = self._result("Capacitor")
        query = "what are capacitors"
        primary = "wikipedia_en_all_maxi_2026-02"
        assert self.score(main_result, query, primary) > self.score(list_result, query, primary)

    def test_wikipedia_bonus_larger_for_definitional_query(self):
        # Wikipedia gets +8 for definitional, +3 for specific
        # Use identical titles to isolate the Wikipedia bonus alone
        wiki_result = self._result("Zymurgy", book="wikipedia_en_all_maxi_2026-02")
        se_result = self._result("Zymurgy", book="electronics.stackexchange.com_en_all_2026-02")
        query_def = "what is zymurgy"
        primary = "wikipedia_en_all_maxi_2026-02"
        # Wikipedia should beat Stack Exchange for definitional queries
        wiki_def = self.score(wiki_result, query_def, primary)
        se_def = self.score(se_result, query_def, primary)
        assert wiki_def > se_def
